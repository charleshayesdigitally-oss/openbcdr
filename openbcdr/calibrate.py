"""Calibration (spec section 10, Phase 4: "review false positives; tune relevance
scoring"; and section 12's target of >=85% of high alerts actionable).

Read-only. This module computes what the risk team's own decisions say about the
agent's precision, and it changes nothing. Tuning is a human editing requirement
text, a severity, or a prompt - never the agent adjusting itself between runs.

Two rules govern everything here:

1. **Refuse to compute a rate from too few samples.** A "67% rejection rate"
   drawn from three decisions is worse than reporting nothing, because it looks
   like a measurement and will be quoted as one. Every figure below states its
   sample size, and thin samples are reported as insufficient rather than
   estimated.
2. **A rejection is not a failure until it repeats.** One dismissed finding is
   a bad day; the same requirement dismissed three times is badly worded
   requirement text. Only the second kind is actionable.
"""
from __future__ import annotations

from typing import Any

# Below these, report "insufficient data" rather than a percentage.
MIN_SAMPLES_OVERALL = 10
MIN_SAMPLES_PER_ITEM = 3

# Spec section 12: >=85% of High alerts should be actionable.
PRECISION_TARGET = 85

REJECTING = ("reject",)
# Decisions that indicate the finding was legitimate work, whatever its outcome.
ACCEPTING = ("accept", "modify", "defer", "exception", "close")


def _rate(numer: int, denom: int) -> float | None:
    return None if denom == 0 else round(100.0 * numer / denom, 1)


def collect(store, plan_id: str | None = None) -> dict[str, Any]:
    """Gather calibration statistics. Pure read; nothing is written."""
    where, args = "", []
    if plan_id:
        where = " WHERE g.plan_id = ?"
        args = [plan_id]

    rows = list(store.db.execute(
        "SELECT d.*, g.plan_id FROM decisions d"
        " LEFT JOIN gaps g ON g.gap_id = d.gap_id" + where, args))

    # Only the latest decision per gap counts - a gap deferred then closed is
    # one outcome, not two, and counting both would inflate the denominator.
    latest: dict[str, Any] = {}
    for r in rows:
        prev = latest.get(r["gap_id"])
        if prev is None or r["ts"] >= prev["ts"]:
            latest[r["gap_id"]] = r
    decisions = list(latest.values())

    total = len(decisions)
    rejected = [d for d in decisions if d["decision"] in REJECTING]
    accepted = [d for d in decisions if d["decision"] in ACCEPTING]

    by_reason: dict[str, int] = {}
    for d in rejected:
        key = d["reason_code"] or "unspecified"
        by_reason[key] = by_reason.get(key, 0) + 1

    by_severity: dict[str, dict[str, int]] = {}
    for d in decisions:
        sev = d["severity_at_decision"] or "unknown"
        slot = by_severity.setdefault(sev, {"total": 0, "rejected": 0})
        slot["total"] += 1
        if d["decision"] in REJECTING:
            slot["rejected"] += 1

    by_origin: dict[str, dict[str, Any]] = {}
    for d in decisions:
        key = d["origin_key"] or "(unattributed)"
        slot = by_origin.setdefault(
            key, {"total": 0, "rejected": 0, "source": d["gap_source"] or "", "reasons": {}})
        slot["total"] += 1
        if d["decision"] in REJECTING:
            slot["rejected"] += 1
            rc = d["reason_code"] or "unspecified"
            slot["reasons"][rc] = slot["reasons"].get(rc, 0) + 1

    # Evidence verification is the fabrication signal, and it is independent of
    # any human decision - it is measured at the moment a finding is produced.
    fnd = store.db.execute(
        "SELECT COUNT(*) AS n,"
        " SUM(CASE WHEN coverage IN ('full','partial') THEN 1 ELSE 0 END) AS claims,"
        " SUM(CASE WHEN evidence_verified = 1 THEN 1 ELSE 0 END) AS verified,"
        " SUM(CASE WHEN rationale LIKE 'UNVERIFIED EVIDENCE%' THEN 1 ELSE 0 END) AS downgraded"
        " FROM findings").fetchone()

    return {
        "plan_id": plan_id,
        "total_decisions": total,
        "rejected": len(rejected),
        "accepted": len(accepted),
        "rejection_rate": _rate(len(rejected), total),
        "precision": _rate(len(accepted), total),
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "by_severity": by_severity,
        "by_origin": by_origin,
        "findings_total": fnd["n"] or 0,
        "coverage_claims": fnd["claims"] or 0,
        "evidence_verified": fnd["verified"] or 0,
        "evidence_downgraded": fnd["downgraded"] or 0,
        "downgrade_rate": _rate(fnd["downgraded"] or 0, fnd["claims"] or 0),
    }


def offenders(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Origins rejected often enough to be a pattern rather than a bad day."""
    out = []
    for key, s in stats["by_origin"].items():
        if s["total"] < MIN_SAMPLES_PER_ITEM or s["rejected"] == 0:
            continue
        rate = _rate(s["rejected"], s["total"]) or 0.0
        if rate < 50:
            continue
        top = sorted(s["reasons"].items(), key=lambda kv: -kv[1])
        out.append({
            "origin": key, "source": s["source"], "total": s["total"],
            "rejected": s["rejected"], "rate": rate,
            "dominant_reason": top[0][0] if top else "unspecified",
        })
    out.sort(key=lambda r: (-r["rate"], -r["total"]))
    return out


def _fix_for(reason: str, source: str) -> str:
    """The fix depends on BOTH the reason and what produced the finding.

    A coherence check has no requirement record, no applicability tags and no
    prompt - telling someone to edit those for a deterministic check sends them
    looking for a file that does not exist. Branch on source first.
    """
    if source == "coherence":
        if reason == "not_applicable":
            return ("This deterministic check fires on a case it should not. Add the "
                    "exclusion in coherence.py - it has no applicability tags to adjust.")
        if reason == "severity_too_high":
            return "Lower the severity this check assigns, in coherence.py."
        if reason == "misread_plan":
            return ("The check read the wrong input. Most likely the extractor did not "
                    "populate the field it depends on - look at ingest before the check.")
        if reason == "duplicate":
            return ("This check overlaps another, or its fingerprint is too coarse and "
                    "collapses cases that should stay distinct.")
        return ("Read the rejection notes and tighten the check or its threshold in "
                "coherence.py. There is no prompt to blame here.")

    if reason == "wrong_requirement":
        return "Re-read the primary source and rewrite this requirement's text in the index."
    if reason == "misread_plan":
        return ("The plan does cover this. Check whether the requirement text is ambiguous, "
                "or whether the relevant plan section is reaching the analyzer at all.")
    if reason == "not_applicable":
        return "Fix this requirement's applicability tags so it stops firing at this tier."
    if reason == "severity_too_high":
        return "Lower gap_severity on this requirement record."
    if reason == "duplicate":
        return "Two requirements overlap, or the fingerprint is not catching a real duplicate."
    return "Read the rejection notes; the reason code alone does not say what to change."


def render(stats: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("CALIBRATION REPORT" + ("  |  plan: " + stats["plan_id"] if stats["plan_id"] else ""))
    L.append("=" * 72)
    L.append("")

    n = stats["total_decisions"]
    L.append("Decisions recorded: " + str(n))
    if n == 0:
        L.append("")
        L.append("No decisions on file. Calibration needs risk-owner outcomes, not agent")
        L.append("output - record them with `decide`, including `--decision reject` when a")
        L.append("finding is simply wrong. Without rejects, precision cannot be measured at")
        L.append("all, and the data cannot be reconstructed later.")
        return "\n".join(L)

    if n < MIN_SAMPLES_OVERALL:
        L.append("  INSUFFICIENT DATA for a precision figure (" + str(n) + " of "
                 + str(MIN_SAMPLES_OVERALL) + " needed). Counts below are raw, not rates.")
        L.append("  Accepted in some form: " + str(stats["accepted"])
                 + "   Rejected as wrong: " + str(stats["rejected"]))
    else:
        p = stats["precision"]
        L.append("  Precision (findings the risk team acted on): " + str(p) + "%"
                 + "   [target " + str(PRECISION_TARGET) + "%]")
        L.append("  " + ("MEETS target." if p is not None and p >= PRECISION_TARGET
                         else "BELOW target - see offenders below."))
        L.append("  Rejected as wrong: " + str(stats["rejected"]) + " of " + str(n)
                 + " (" + str(stats["rejection_rate"]) + "%)")
    L.append("")

    if stats["by_reason"]:
        L.append("Why findings were rejected:")
        for reason, count in stats["by_reason"].items():
            L.append("  " + str(count).rjust(4) + "  " + reason)
        L.append("")

    L.append("By severity assigned:")
    for sev in ("critical", "high", "medium", "low", "unknown"):
        s = stats["by_severity"].get(sev)
        if not s:
            continue
        line = "  " + sev.ljust(9) + str(s["total"]).rjust(4) + " decided, " \
               + str(s["rejected"]).rjust(3) + " rejected"
        if s["total"] >= MIN_SAMPLES_PER_ITEM:
            line += "  (" + str(_rate(s["rejected"], s["total"])) + "%)"
        else:
            line += "  (too few to rate)"
        L.append(line)
    L.append("")

    off = offenders(stats)
    if off:
        L.append("REPEAT OFFENDERS - rejected at least " + str(MIN_SAMPLES_PER_ITEM)
                 + " times and more than half the time.")
        L.append("These are the tuning targets. Everything else is noise.")
        L.append("")
        for o in off:
            L.append("  " + o["origin"] + "  [" + o["source"] + "]")
            L.append("      rejected " + str(o["rejected"]) + " of " + str(o["total"])
                     + " (" + str(o["rate"]) + "%), mostly: " + o["dominant_reason"])
            L.append("      fix -> " + _fix_for(o["dominant_reason"], o["source"]))
        L.append("")
    else:
        L.append("No repeat offenders. Nothing has been rejected often enough to be a")
        L.append("pattern rather than a one-off.")
        L.append("")

    L.append("Evidence verification (measured at production, independent of any human):")
    claims = stats["coverage_claims"]
    L.append("  Coverage claims made: " + str(claims)
             + "   Quote verified: " + str(stats["evidence_verified"])
             + "   Downgraded: " + str(stats["evidence_downgraded"]))
    if claims >= MIN_SAMPLES_OVERALL:
        L.append("  Downgrade rate: " + str(stats["downgrade_rate"]) + "%")
        if (stats["downgrade_rate"] or 0) > 10:
            L.append("  HIGH. The model is asserting coverage it cannot quote. Strengthen the")
            L.append("  verbatim-quote instruction before loosening the verifier - loosening it")
            L.append("  buys back exactly the fabrication risk it exists to catch.")
    else:
        L.append("  Too few coverage claims to rate (" + str(claims) + " of "
                 + str(MIN_SAMPLES_OVERALL) + ").")
    L.append("")

    L.append("This report changes nothing. Every fix above is a human editing a")
    L.append("requirement record, a severity, a threshold, or a prompt.")
    return "\n".join(L)
