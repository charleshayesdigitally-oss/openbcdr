"""Compliance report rendering (spec section 6.1 sample output).

One rule governs this module: a report may not overstate what the pipeline
knows. Concretely -

- If any requirement in scope is still `validated_by_human=false`, the report
  renders with a refusal banner and the score is withheld. A compliance score
  computed from unverified requirement text is a number that looks like
  evidence and is not.
- Findings whose evidence quote failed verification are listed separately and
  never counted as coverage.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Sequence

from .analyzers import compliance
from .analyzers.coherence import Issue
from .models import CoverageFinding, Requirement

BAR = "=" * 72


def _pct(n: int, total: int) -> str:
    return str(round(100 * n / total)) + "%" if total else "0%"


def render(
    plan_id: str,
    plan_version: str,
    findings: Sequence[CoverageFinding],
    requirements: Sequence[Requirement],
    issues: Sequence[Issue] = (),
    gaps: Iterable = (),
    today: date | None = None,
    examiner_facing: bool = False,
    advisories: Sequence = (),
) -> str:
    today = today or date.today()
    unvalidated = [r.req_id for r in requirements if not r.validated_by_human]
    # The denominator is the requirement set in scope, NOT the findings handed in.
    # Scoring the findings alone made a one-of-three assessment read 100/100
    # (Codex review 2026-09-08, finding 4).
    stats = compliance.score(findings, total_in_scope=len(requirements))
    gaps_in_scope = compliance.completeness(findings, requirements, plan_version=plan_version)
    by_req = {r.req_id: r for r in requirements}

    L: list[str] = []
    L.append("COMPLIANCE ANALYSIS REPORT")
    L.append("Plan: " + plan_id + (" " + plan_version if plan_version else "")
             + "  |  Date: " + today.isoformat())
    sources = sorted({r.source for r in requirements})
    L.append("Analyzed Against: " + ", ".join(sources))
    L.append("")

    # Every reason the score must be withheld, gathered before anything is
    # rendered. An INCOMPLETE assessment is exactly as misleading as an
    # unvalidated one: both produce a number that looks like evidence.
    def _list(ids):
        return ", ".join(ids[:10]) + ("..." if len(ids) > 10 else "")

    blockers: list[list[str]] = []
    if unvalidated:
        blockers.append([
            str(len(unvalidated)) + " of " + str(len(requirements))
            + " requirement records in scope have not been confirmed against their",
            "primary source.",
            "Unvalidated: " + _list(unvalidated),
        ])
    if gaps_in_scope["missing"]:
        blockers.append([
            str(len(gaps_in_scope["missing"])) + " of " + str(len(requirements))
            + " in-scope requirements were never assessed. An assessment that",
            "skips a requirement is not a lower score, it is an unfinished report.",
            "Not assessed: " + _list(gaps_in_scope["missing"]),
        ])
    if gaps_in_scope["unknown"]:
        blockers.append([
            "Findings were supplied for requirements that are not in the selected set.",
            "Unknown: " + _list(gaps_in_scope["unknown"]),
        ])
    if gaps_in_scope["duplicate"]:
        blockers.append([
            "More than one finding was supplied for the same requirement, so which one",
            "is the answer is undefined.",
            "Duplicated: " + _list(gaps_in_scope["duplicate"]),
        ])
    if gaps_in_scope["stale"]:
        blockers.append([
            "Findings were assessed against a different revision of the plan than this",
            "report names (" + (plan_version or "unversioned") + ").",
            "Stale: " + _list(gaps_in_scope["stale"]),
        ])

    if blockers:
        L.append(BAR)
        L.append("!! NOT EXAMINER-READY - the coverage score is WITHHELD.")
        L.append("   The findings below are indicative only.")
        for reason in blockers:
            L.append("")
            for i, line in enumerate(reason):
                L.append(("   - " if i == 0 else "     ") + line)
        L.append(BAR)
        L.append("")
        if examiner_facing:
            L.append("Report generation stopped: examiner-facing output was requested over an")
            L.append("assessment that is unvalidated, incomplete, or does not match its scope.")
            return "\n".join(L)
    else:
        L.append("Overall Score: " + str(stats["score"]) + "/100")
        L.append("")

    L.append("Coverage Summary:")
    L.append("  Full Coverage:    " + str(stats["full"]).rjust(3) + " requirements ("
             + _pct(int(stats["full"]), int(stats["total"])) + ")")
    L.append("  Partial Coverage: " + str(stats["partial"]).rjust(3) + " requirements ("
             + _pct(int(stats["partial"]), int(stats["total"])) + ")")
    L.append("  Gap:              " + str(stats["gap"]).rjust(3) + " requirements ("
             + _pct(int(stats["gap"]), int(stats["total"])) + ")")
    L.append("  Unverifiable:     " + str(stats["insufficient_evidence"]).rjust(3)
             + " requirements (" + _pct(int(stats["insufficient_evidence"]), int(stats["total"]))
             + ")  <- needs human review, NOT counted as coverage")
    L.append("")

    gap_list = list(gaps)
    if gap_list:
        for sev, label in (("critical", "Critical"), ("high", "High"),
                           ("medium", "Medium"), ("low", "Low")):
            rows = [g for g in gap_list if (g["severity"] if not hasattr(g, "severity") else g.severity) == sev]
            if not rows:
                continue
            L.append(label + " Gaps (" + str(len(rows)) + "):")
            for g in rows:
                gid = g["gap_id"] if not hasattr(g, "gap_id") else g.gap_id
                title = g["title"] if not hasattr(g, "title") else g.title
                ref = g["standard_ref"] if not hasattr(g, "standard_ref") else g.standard_ref
                dl = g["deadline"] if not hasattr(g, "deadline") else g.deadline
                line = "  [" + str(gid) + "] " + str(title)
                if ref:
                    line += " - " + str(ref)
                if dl:
                    line += "  (due " + str(dl) + ")"
                L.append(line)
            L.append("")

    unverified = [f for f in findings if f.coverage == "insufficient_evidence"
                  and f.rationale.startswith("UNVERIFIED EVIDENCE")]
    if unverified:
        L.append(BAR)
        L.append("EVIDENCE VERIFICATION FAILURES (" + str(len(unverified)) + ")")
        L.append("The model asserted coverage but quoted text that is not in the plan.")
        L.append("These are excluded from the score and require manual assessment.")
        for f in unverified:
            req = by_req.get(f.req_id)
            L.append("  - " + f.req_id + (" (" + req.section + ")" if req else ""))
            L.append("      claimed quote: " + (f.evidence_quote[:110] or "(empty)"))
        L.append(BAR)
        L.append("")

    if issues:
        L.append("Coherence Findings (deterministic checks, no model involved):")
        for sev in ("critical", "high", "medium", "low"):
            rows = [i for i in issues if i.severity == sev]
            for i in rows:
                L.append("  " + sev.upper().ljust(9) + " " + i.check.ljust(24) + " " + i.title)
        L.append("")

    # Examiner-facing output stays strictly regulatory: no practice advisories.
    if advisories and not examiner_facing:
        from .analyzers.advisory import lines as _advisory_lines
        L.extend(_advisory_lines(advisories))
        L.append("")

    L.append("Method: coverage judged by model against the standards index; every")
    L.append("coverage claim quote-verified against plan source text. Coherence")
    L.append("findings are computed, not inferred. All findings require risk-owner")
    L.append("disposition before any plan change.")
    return "\n".join(L)
