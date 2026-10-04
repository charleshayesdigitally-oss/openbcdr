"""Severity triage and routing (spec section 7.2), plus gap creation.

The agent proposes; a human disposes. Nothing here closes a gap, approves an
exception, or edits a plan - it assigns a severity, a route, and a deadline,
and writes an auditable record. Every state change after that comes through
`Store.decide_gap` with a named actor.

Deduplication is by fingerprint, so a nightly re-run does not manufacture a new
gap for a condition already sitting with a risk owner.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Iterable

from . import config
from .analyzers.coherence import Issue
from .models import CoverageFinding, Gap, Requirement

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def add_business_days(start: date, days: int) -> date:
    """Business days only - weekends skipped. Holidays are not modelled; wire a
    calendar here before these deadlines drive an escalation clock in anger."""
    d = start
    remaining = days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d


def route(severity: str) -> list[str]:
    return config.ROUTING.get(severity, ["bcdr_pm"])


def deadline_for(severity: str, today: date | None = None) -> date | None:
    days, _escalate = config.SLA.get(severity, (None, None))
    if days is None:
        return None
    return add_business_days(today or date.today(), days)


def escalation_date(severity: str, today: date | None = None) -> date | None:
    _days, escalate = config.SLA.get(severity, (None, None))
    if escalate is None:
        return None
    return add_business_days(today or date.today(), escalate)


def _fingerprint(plan_id: str, source: str, key: str) -> str:
    return hashlib.sha256("|".join([plan_id, source, key]).encode("utf-8")).hexdigest()[:32]


def gaps_from_findings(
    findings: Iterable[CoverageFinding],
    requirements: Iterable[Requirement],
    plan_id: str,
    today: date | None = None,
) -> list[tuple[Gap, str]]:
    """Turn non-covering compliance findings into proposed gaps. -> [(gap, fingerprint)]"""
    today = today or date.today()
    by_id = {r.req_id: r for r in requirements}
    out: list[tuple[Gap, str]] = []

    for f in findings:
        if f.coverage == "full":
            continue
        req = by_id.get(f.req_id)
        if req is None:
            continue

        if f.coverage == "gap":
            severity = req.gap_severity
        elif f.coverage == "partial":
            # Partial coverage steps down one level - documented but incomplete
            # is an examination comment, not an examination failure.
            step = {"critical": "high", "high": "medium", "medium": "low", "low": "low"}
            severity = step[req.gap_severity]
        else:  # insufficient_evidence - a human has to look, not a deadline clock
            severity = "medium"

        title = ("No documented coverage: " if f.coverage == "gap" else
                 "Partial coverage: " if f.coverage == "partial" else
                 "Coverage could not be established: ") + req.section

        gap = Gap(
            identified_date=today,
            identified_by="Agent (Compliance Analyzer)",
            plan_id=plan_id,
            plan_section=f.plan_section,
            title=title,
            description=f.rationale,
            standard_ref=req.source + " " + req.section,
            regulatory_ref="; ".join(req.related_regulations),
            severity=severity,
            status="open",
            assigned_to=", ".join(route(severity)),
            deadline=deadline_for(severity, today),
            resolution_notes="",
            source="compliance",
            origin_key=req.req_id,
        )
        out.append((gap, _fingerprint(plan_id, "compliance", req.req_id)))
    return out


def gaps_from_issues(
    issues: Iterable[Issue],
    plan_id: str,
    today: date | None = None,
) -> list[tuple[Gap, str]]:
    today = today or date.today()
    out: list[tuple[Gap, str]] = []
    for i in issues:
        gap = Gap(
            identified_date=today,
            identified_by="Agent (Coherence Analyzer)",
            plan_id=plan_id,
            plan_section=i.section,
            title=i.title,
            description=i.detail + (("\nEvidence: " + i.evidence) if i.evidence else ""),
            standard_ref="",
            regulatory_ref="",
            severity=i.severity,
            status="open",
            assigned_to=", ".join(route(i.severity)),
            deadline=deadline_for(i.severity, today),
            source="coherence",
            origin_key="coherence:" + i.check,
        )
        out.append((gap, _fingerprint(plan_id, "coherence:" + i.check, i.title)))
    return out


def persist(store, proposals: Iterable[tuple[Gap, str]]) -> tuple[int, int]:
    """Write proposed gaps. Returns (created, already_open)."""
    created = duplicate = 0
    year = date.today().year
    for gap, fp in proposals:
        gap.gap_id = store.next_gap_id(year)
        _gid, was_created = store.upsert_gap(gap, fp)
        if was_created:
            created += 1
        else:
            duplicate += 1
    return created, duplicate


def notification_plan(gaps: Iterable) -> list[dict]:
    """What would be sent, for whom, and when - without sending anything.

    Notification is an outbound action. This returns the plan so a human can
    look at it before any mail leaves; wiring it to a transport is deliberately
    a separate, explicit step.
    """
    out = []
    for g in gaps:
        sev = g["severity"] if not hasattr(g, "severity") else g.severity
        gid = g["gap_id"] if not hasattr(g, "gap_id") else g.gap_id
        title = g["title"] if not hasattr(g, "title") else g.title
        deadline = g["deadline"] if not hasattr(g, "deadline") else g.deadline
        out.append({
            "gap_id": gid,
            "severity": sev,
            "title": title,
            "recipients": route(sev),
            "channel": "immediate" if sev in ("critical", "high") else "weekly_digest",
            "deadline": str(deadline or ""),
            "escalate_if_silent": str(escalation_date(sev) or ""),
        })
    out.sort(key=lambda r: SEVERITY_ORDER.get(r["severity"], 9))
    return out
