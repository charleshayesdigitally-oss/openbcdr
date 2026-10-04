"""Compliance Analyzer (spec section 6.1).

Measures plan coverage against the standards index. This is the analyzer where
the model earns its place - "does this plan's language satisfy this obligation"
is a judgment call over prose.

The guardrail is evidence verification. Any finding of `full` or `partial`
coverage must carry a verbatim quote from the plan, and that quote is checked
against the source text in Python before the finding is allowed to stand. A
quote that does not appear is not a near-miss - it is the model inventing
coverage, which in a compliance tool is the worst possible failure. Those
findings are forced to `insufficient_evidence` and flagged for human review.
"""
from __future__ import annotations

import re
from typing import Iterable

from .. import config, llm
from ..models import CoverageBatch, CoverageFinding, Requirement

SYSTEM = """You assess whether a business continuity plan covers specific regulatory requirements.

For each requirement you are given, decide:
- "full": the plan clearly and completely addresses the requirement.
- "partial": the plan addresses the requirement but incompletely (e.g. states an objective but no evidence it is achievable, covers some systems but not all).
- "gap": the plan does not address the requirement at all.
- "insufficient_evidence": you cannot tell from the document provided.

Hard rules:
- For "full" and "partial" you MUST supply evidence_quote: a span copied CHARACTER FOR CHARACTER from the plan document. Do not paraphrase, tidy, join separated sentences, or fix typos. Your quote is checked against the source text; a quote that does not appear invalidates the finding.
- For "gap" leave evidence_quote as an empty string.
- Assess only against the requirement text given. Do not import obligations you know from elsewhere, and do not soften a gap because the plan seems well written.
- plan_section is the section number or heading where the evidence sits, or "" if none applies.
- recommended_action is one concrete sentence naming what would close the gap. For "full", leave it empty.
- Absence of evidence is a gap, not a pass. A plan that never mentions the topic is "gap", not "insufficient_evidence"; reserve "insufficient_evidence" for cases where the document appears to be truncated or the section is referenced but not included.

Return one finding per requirement, using the exact req_id given."""


def _normalise(s: str) -> str:
    """Collapse whitespace and unify quote/dash characters for quote matching.

    Matching is intentionally not exact-string: a plan converted from PDF will
    differ from the model's copy by line breaks and smart quotes alone, and
    failing those would train the operator to ignore the flag. Anything beyond
    that - reworded, merged, or invented text - still fails.
    """
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace("‑", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def verify_quote(quote: str, plan_text: str) -> bool:
    if not quote.strip():
        return False
    return _normalise(quote) in _normalise(plan_text)


def _requirement_block(r: Requirement) -> str:
    return (
        "req_id: " + r.req_id + "\n"
        "source: " + r.source + " " + r.section + "\n"
        "mandatory: " + ("yes" if r.mandatory else "no") + "\n"
        "requirement: " + r.requirement
    )


def analyze(
    plan_text: str,
    requirements: Iterable[Requirement],
    batch_size: int | None = None,
    progress=None,
) -> tuple[list[CoverageFinding], dict[str, int]]:
    """Assess every requirement against the plan. Returns (findings, usage totals)."""
    reqs = list(requirements)
    size = batch_size or config.REQUIREMENTS_PER_CALL

    # Cache breakpoint discipline: instructions and the plan are identical on
    # every call and go in the system prompt behind cache_control. Only the
    # requirement batch varies, and it goes in the user turn.
    system = [
        llm.cache_block(SYSTEM),
        llm.cache_block("<plan_document>\n" + plan_text + "\n</plan_document>"),
    ]

    findings: list[CoverageFinding] = []
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    for start in range(0, len(reqs), size):
        batch = reqs[start:start + size]
        user = (
            "Assess the plan document against these "
            + str(len(batch))
            + " requirements. Return exactly one finding per requirement.\n\n"
            + "\n\n---\n\n".join(_requirement_block(r) for r in batch)
        )
        result, usage = llm.structured(CoverageBatch, system, user, max_tokens=16000)

        totals["input"] += getattr(usage, "input_tokens", 0) or 0
        totals["output"] += getattr(usage, "output_tokens", 0) or 0
        totals["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        totals["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

        by_id = {f.req_id: f for f in result.findings}
        for r in batch:
            draft = by_id.get(r.req_id)
            if draft is None:
                # A silently dropped requirement must not read as compliant.
                findings.append(CoverageFinding(
                    req_id=r.req_id, coverage="insufficient_evidence",
                    rationale="Model returned no finding for this requirement.",
                    evidence_quote="", plan_section="", recommended_action="Re-run this requirement.",
                    evidence_verified=False))
                continue

            verified = verify_quote(draft.evidence_quote, plan_text)
            finding = CoverageFinding(**draft.model_dump(), evidence_verified=verified)

            if finding.coverage in ("full", "partial") and not verified:
                finding.coverage = "insufficient_evidence"
                finding.rationale = (
                    "UNVERIFIED EVIDENCE - the model claimed coverage but its quote does not "
                    "appear in the plan text. Original rationale: " + draft.rationale
                )
                finding.recommended_action = (
                    "Human review required: confirm by hand whether the plan covers this."
                )
            findings.append(finding)

        if progress:
            progress(min(start + size, len(reqs)), len(reqs))

    return findings, totals


def completeness(
    findings: Iterable[CoverageFinding],
    requirements: Iterable[Requirement],
    plan_version: str = "",
) -> dict[str, list[str]]:
    """Is this assessment actually about the requirement set it claims to be?

    Codex review 2026-09-08, finding 4. `score()` only ever counted the findings
    it was handed, so an assessment covering ONE of three in-scope requirements
    rendered "Overall Score: 100/100". Nothing compared the findings to the
    requirement set, and a number that looks like evidence and is not is the
    exact failure this whole module's docstring says it exists to prevent.

    Returns the four ways an assessment can fail to correspond to its scope.
    Each is a reason to withhold the score, never to quietly adjust it.
    """
    fs = list(findings)
    in_scope = [r.req_id for r in requirements]
    scope_set = set(in_scope)

    seen: dict[str, int] = {}
    for f in fs:
        seen[f.req_id] = seen.get(f.req_id, 0) + 1

    return {
        # In scope, never assessed. The old code simply left these out of the maths.
        "missing": sorted(r for r in scope_set if r not in seen),
        # Assessed, but not part of the selected requirement set.
        "unknown": sorted(r for r in seen if r not in scope_set),
        # Two findings for one requirement: which one is the answer?
        "duplicate": sorted(r for r, c in seen.items() if c > 1),
        # Assessed against a different revision of the plan than this report names.
        "stale": sorted({f.req_id for f in fs
                         if plan_version and f.plan_version and f.plan_version != plan_version}),
    }


def score(findings: Iterable[CoverageFinding], total_in_scope: int | None = None) -> dict[str, object]:
    """Coverage summary. `insufficient_evidence` counts as uncovered, never as pass.

    `total_in_scope` is the number of requirements the assessment was supposed to
    cover. Without it the denominator is the number of findings supplied, which
    makes a one-of-three assessment read 100% (finding 4). Callers that know the
    scope MUST pass it.
    """
    fs = list(findings)
    n = (total_in_scope if total_in_scope is not None else len(fs)) or 1
    full = sum(1 for f in fs if f.coverage == "full")
    partial = sum(1 for f in fs if f.coverage == "partial")
    gap = sum(1 for f in fs if f.coverage == "gap")
    unclear = sum(1 for f in fs if f.coverage == "insufficient_evidence")
    return {
        "total": n if total_in_scope is not None else len(fs),
        "assessed": len(fs),
        "full": full,
        "partial": partial,
        "gap": gap,
        "insufficient_evidence": unclear,
        # Partial credit at half weight; unclear earns nothing.
        "score": round(100 * (full + 0.5 * partial) / n),
        "pct_full": round(100 * full / n),
        "pct_partial": round(100 * partial / n),
        "pct_gap": round(100 * gap / n),
    }
