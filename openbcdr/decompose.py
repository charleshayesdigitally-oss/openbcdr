"""Booklet decomposer: source text in, candidate requirement records out.

Hand-decomposing a hundred-page booklet into discrete obligations is the
bottleneck that kills standards-index projects. This drafts the candidates so a
human is confirming rather than transcribing.

Three rules make it safe to use on a compliance artifact:

1. **Every candidate carries a verbatim quote of the passage it came from**, and
   the quote is checked against the source text here before the candidate is
   returned. A candidate whose quote cannot be found is discarded, not shown -
   it means the model paraphrased the source, and validating a paraphrase
   against itself is exactly the failure the index exists to prevent.
2. **Everything comes back `validated_by_human: false`.** Nothing this module
   produces can back a report until a person confirms it against the source.
3. **It proposes `gap_severity` but marks it provisional.** Severity is a policy
   decision belonging to the risk function; the model's guess is a starting
   point for that conversation, never the answer.

One requirement = one testable obligation. If you cannot picture the sentence in
a plan that would satisfy it, it is too abstract and needs splitting.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import llm
from .analyzers.compliance import verify_quote
from .models import Requirement

SYSTEM = """You decompose regulatory and standards source material into discrete, testable requirement records for a bank's business continuity program.

A requirement record is ONE obligation that a specific continuity plan either satisfies or does not. The test: can you picture the sentence in a plan that would satisfy it? If not, it is too abstract - split it, or leave it out.

Rules:
- Split a clause that contains two independently-failable obligations. "Review the plan annually and update it on material change" is TWO records: an organisation can do the first and skip the second, and each should surface as its own finding.
- Do not create a record for a heading, a definition, a statement of purpose, or background narrative. Only obligations.
- `source_quote` must be copied CHARACTER FOR CHARACTER from the text provided. Do not paraphrase, tidy, join separated sentences, or fix typos. Your quote is checked against the source; a candidate whose quote cannot be found is discarded.
- `requirement` is your own plain-language statement of what a plan must CONTAIN. Write what the plan must have, not what the regulator "expects" or "should consider".
- `section` is the exact citation as the source labels it - the booklet section, rule paragraph, or clause number. If the text does not state one, use an empty string rather than inventing a plausible number.
- `mandatory` is true only where the source uses obligation language (must, shall, is required to). Guidance phrased as "should" or "may consider" is false.
- `applicability` tags: use "all_banks" for general obligations, "finra_member" for FINRA member-firm obligations, "fed_member" for Federal Reserve supervised entities, "sifi" for anything scoped to systemically important institutions or large bank holding companies. Tag "sifi" accurately - it is what stops an obligation firing at a mid-size bank.
- `proposed_severity` is your reading of how serious an unmet obligation is: critical if a regulatory mandate is unmet or recovery is directly impaired, high if it creates examination-finding risk, medium for a documentation or best-practice weakness, low for cosmetic. This is provisional and a human will set the real value.

Return only obligations you can quote. Fewer, well-grounded records beat broad coverage."""


class Candidate(BaseModel):
    section: str
    requirement: str
    source_quote: str
    mandatory: bool
    applicability: list[str]
    tags: list[str]
    proposed_severity: str
    reasoning: str


class CandidateBatch(BaseModel):
    candidates: list[Candidate]


def _chunks(text: str, size: int, overlap: int) -> list[str]:
    """Split on paragraph boundaries, with overlap so an obligation spanning a
    chunk edge is not lost. Overlap produces duplicates; dedupe handles them."""
    paras = text.split("\n\n")
    out: list[str] = []
    buf: list[str] = []
    length = 0
    for para in paras:
        if length + len(para) > size and buf:
            out.append("\n\n".join(buf))
            keep, kept = [], 0
            for p in reversed(buf):
                if kept >= overlap:
                    break
                keep.insert(0, p)
                kept += len(p)
            buf, length = keep, kept
        buf.append(para)
        length += len(para)
    if buf:
        out.append("\n\n".join(buf))
    return out


def decompose(
    source_text: str,
    source_name: str,
    as_of_version: str = "",
    req_id_prefix: str = "REQ",
    chunk_size: int = 12000,
    progress=None,
) -> tuple[list[Requirement], dict[str, int]]:
    """Draft candidate requirements. Returns (records, stats).

    Every returned record is unvalidated. `stats` reports how many candidates
    were discarded for an unverifiable quote - a high number means the model is
    paraphrasing the source and the output should not be trusted wholesale.
    """
    chunks = _chunks(source_text, chunk_size, chunk_size // 8)
    system = [llm.cache_block(SYSTEM)]

    records: list[Requirement] = []
    stats = {"candidates": 0, "quote_failed": 0, "duplicates": 0, "kept": 0, "chunks": len(chunks)}
    seen: set[str] = set()

    for n, chunk in enumerate(chunks, 1):
        user = (
            "Decompose this source material into requirement records.\n\n"
            "Source: " + source_name
            + ("\nVersion: " + as_of_version if as_of_version else "")
            + "\n\n<source_text>\n" + chunk + "\n</source_text>"
        )
        batch, _usage = llm.structured(CandidateBatch, system, user, max_tokens=16000)

        for c in batch.candidates:
            stats["candidates"] += 1

            # The quote is checked against the WHOLE source, not just this
            # chunk - overlap means a legitimate quote can sit in a neighbour.
            if not verify_quote(c.source_quote, source_text):
                stats["quote_failed"] += 1
                continue

            key = (c.section.strip().lower() + "|" + c.requirement.strip().lower()[:120])
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)

            sev = c.proposed_severity if c.proposed_severity in (
                "critical", "high", "medium", "low") else "medium"
            records.append(Requirement(
                req_id=req_id_prefix + "-" + str(len(records) + 1).zfill(4),
                source=source_name,
                section=c.section,
                requirement=c.requirement,
                mandatory=c.mandatory,
                applicability=c.applicability or ["all_banks"],
                tags=c.tags,
                as_of_version=as_of_version,
                source_quote=c.source_quote,
                gap_severity=sev,  # provisional
                validated_by_human=False,
            ))
            stats["kept"] += 1

        if progress:
            progress(n, len(chunks), stats)

    return records, stats


def to_index(records: list[Requirement], source_name: str,
             stats: dict[str, int]) -> dict:
    """Wrap records in the index file shape, with the provenance a validator needs."""
    return {
        "_README": [
            "DRAFT - MACHINE-DECOMPOSED, NOT VALIDATED.",
            "Every record here was drafted from " + source_name + " and carries",
            "validated_by_human: false. The section numbers and requirement text are the",
            "model's reading of the source, not a confirmed citation.",
            "",
            "Before any record backs a real assessment: open the primary source, confirm",
            "the section and the obligation, correct the requirement text, set",
            "last_confirmed and as_of_version, set gap_severity as a policy decision, then",
            "flip validated_by_human. Use `python -m openbcdr validate` to work through",
            "them one at a time.",
            "",
            "Decomposition stats: " + str(stats["candidates"]) + " candidates, "
            + str(stats["kept"]) + " kept, " + str(stats["quote_failed"])
            + " discarded for an unverifiable quote, " + str(stats["duplicates"])
            + " duplicates.",
        ],
        "requirements": [r.model_dump(mode="json") for r in records],
    }
