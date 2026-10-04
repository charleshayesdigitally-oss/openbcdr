"""Import findings produced by a prompt-only agent into the local registry.

Where the agent runs without this codebase bound to it - a chat agent, a Project,
anything with no code execution - it can still do the judgment work, but it has no
memory between runs and, more seriously, nothing verifies its evidence. This
module is the bridge: the agent emits structured findings, and everything the
code half provides gets re-imposed here on the way in.

What importing restores that prompt-only mode does not have:

1. **Quote verification.** Every claim of full or partial coverage is checked
   against the stored plan text before it is accepted. A claim whose quote is not
   in the plan is downgraded to insufficient_evidence exactly as it would be on a
   local run. This is the anti-fabrication mechanism, and without an import step
   it simply is not present.
2. **Deduplication.** Gaps are fingerprinted, so re-evaluating the same plan next
   quarter references the existing gap instead of opening a second one with a new
   identifier.
3. **Severity, routing and deadlines** from the curated index, not from the
   agent's opinion.
4. **The audit trail**, including who imported what and when.

Coherence findings from the agent are deliberately NOT imported. Those checks are
deterministic and free to run locally; taking a model's word for arithmetic is
the one thing this design exists to avoid. Run `coherence` instead.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .analyzers.compliance import verify_quote
from .models import CoverageFinding


class ImportError_(ValueError):
    """Raised when the payload cannot be trusted enough to import."""


REQUIRED_TOP = ("plan_id", "findings")
COVERAGE = ("full", "partial", "gap", "insufficient_evidence")


def load_payload(path: Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    # Agents commonly wrap JSON in a fenced block. Tolerate that rather than
    # making a human strip it by hand and risk them editing the content.
    if "```" in raw:
        parts = raw.split("```")
        for chunk in parts:
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                raw = c
                break
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ImportError_("payload is not valid JSON: " + str(e)) from e

    missing = [k for k in REQUIRED_TOP if k not in data]
    if missing:
        raise ImportError_("payload is missing required field(s): " + ", ".join(missing))
    if not isinstance(data["findings"], list):
        raise ImportError_("`findings` must be a list")
    return data


def to_findings(payload: dict[str, Any], plan_text: str,
                known_req_ids: set[str]) -> tuple[list[CoverageFinding], dict[str, list]]:
    """Convert payload findings to verified CoverageFindings.

    Returns (findings, problems). `problems` is reported, never silently dropped.
    """
    problems: dict[str, list] = {"unknown_req_id": [], "bad_coverage": [],
                                 "unverified_quote": [], "duplicate_req_id": []}
    out: list[CoverageFinding] = []
    seen: set[str] = set()

    for raw in payload["findings"]:
        req_id = str(raw.get("req_id", "")).strip()
        if not req_id:
            problems["unknown_req_id"].append("(blank req_id)")
            continue
        if req_id not in known_req_ids:
            # The agent asserted a requirement the index does not contain. That is
            # the failure the index exists to prevent - surface it, do not import.
            problems["unknown_req_id"].append(req_id)
            continue
        if req_id in seen:
            problems["duplicate_req_id"].append(req_id)
            continue
        seen.add(req_id)

        coverage = str(raw.get("coverage", "")).strip()
        if coverage not in COVERAGE:
            problems["bad_coverage"].append(req_id + " -> " + repr(coverage))
            continue

        quote = str(raw.get("evidence_quote", "") or "")
        verified = verify_quote(quote, plan_text)
        rationale = str(raw.get("rationale", "") or "")

        if coverage in ("full", "partial") and not verified:
            problems["unverified_quote"].append(req_id)
            coverage = "insufficient_evidence"
            rationale = ("UNVERIFIED EVIDENCE - the agent claimed coverage but its quote "
                         "does not appear in the plan text. Original rationale: " + rationale)

        out.append(CoverageFinding(
            req_id=req_id,
            coverage=coverage,  # type: ignore[arg-type]
            rationale=rationale,
            evidence_quote=quote,
            plan_section=str(raw.get("plan_section", "") or ""),
            recommended_action=str(raw.get("recommended_action", "") or ""),
            evidence_verified=verified,
        ))

    return out, problems


def render_summary(payload: dict[str, Any], findings: list[CoverageFinding],
                   problems: dict[str, list], created: int, duplicate: int,
                   coherence_ignored: int) -> str:
    L: list[str] = []
    L.append("IMPORTED FINDINGS")
    L.append("=" * 72)
    L.append("Plan: " + str(payload.get("plan_id", "?"))
             + ("  version " + str(payload["plan_version"]) if payload.get("plan_version") else ""))
    if payload.get("evaluated_on"):
        L.append("Evaluated: " + str(payload["evaluated_on"]))
    L.append("")

    cur = payload.get("standards_currency") or []
    if cur:
        L.append("Standards currency as reported by the agent:")
        for c in cur:
            # Pad AND truncate. A long version string otherwise runs into the
            # status column and the table stops being readable at a glance,
            # which is the only thing this block is for.
            src = str(c.get("source", "?"))[:26].ljust(28)
            ver = str(c.get("version", "?"))[:24].ljust(26)
            L.append("  " + src + ver + str(c.get("status", "?")))
        stale = [c for c in cur if str(c.get("status", "")).upper() != "CONFIRMED CURRENT"]
        if stale:
            L.append("  " + str(len(stale)) + " source(s) NOT confirmed current - findings")
            L.append("  drawn from them are provisional.")

        # A status of CONFIRMED CURRENT with nothing retrieved behind it is a
        # declaration, not a verification. The instructions forbid it; this is
        # what makes the prohibition detectable rather than merely stated.
        unbacked = [c for c in cur
                    if str(c.get("status", "")).upper() == "CONFIRMED CURRENT"
                    and not str(c.get("url", "")).strip()]
        if unbacked:
            L.append("")
            L.append("  WARNING - " + str(len(unbacked)) + " source(s) claim CONFIRMED CURRENT")
            L.append("  with no URL retrieved: "
                     + ", ".join(str(c.get("source", "?")) for c in unbacked))
            L.append("  Treat those as UNCONFIRMED. The agent restated the index rather than")
            L.append("  checking the source, which is the failure the status field exists to")
            L.append("  expose.")
    else:
        L.append("No standards-currency block in the payload. The agent was supposed to")
        L.append("emit one on every evaluation; its absence is itself a finding about the run.")
    L.append("")

    L.append("Findings accepted: " + str(len(findings)))
    L.append("  gaps opened:      " + str(created))
    L.append("  already tracked:  " + str(duplicate))
    if coherence_ignored:
        L.append("")
        L.append("Ignored " + str(coherence_ignored) + " coherence finding(s) from the payload.")
        L.append("Those checks are deterministic - run `coherence` locally instead of taking")
        L.append("the agent's word for arithmetic.")

    flagged = {k: v for k, v in problems.items() if v}
    if flagged:
        L.append("")
        L.append("=" * 72)
        L.append("NOT IMPORTED - these need a human")
        for kind, items in flagged.items():
            label = {
                "unknown_req_id": "Requirement not in the index (the agent invented or "
                                  "misquoted an ID)",
                "bad_coverage": "Coverage value not recognised",
                "duplicate_req_id": "Same requirement reported twice",
                "unverified_quote": "Coverage claimed but the quote is not in the plan "
                                    "(downgraded, still imported)",
            }[kind]
            L.append("")
            L.append("  " + label + ": " + str(len(items)))
            for i in items[:10]:
                L.append("     " + str(i))
            if len(items) > 10:
                L.append("     ... and " + str(len(items) - 10) + " more")
        L.append("=" * 72)

    L.append("")
    L.append("Nothing here is closed, approved or notified. Review with `gaps`.")
    return "\n".join(L)
