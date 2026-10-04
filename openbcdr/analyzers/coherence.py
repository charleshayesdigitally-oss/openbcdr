"""Coherence Analyzer (spec section 6.2). NO MODEL CALLS - by design.

Every check here has exactly one correct answer derivable from the structured
record: an inequality between two numbers, a date subtraction, the presence of
a marker string, a set difference. Handing those to an LLM buys nothing and
costs the two things this layer exists to provide - reproducibility and the
ability to show an examiner why the finding is true.

Deterministic also means free and instant, so this can run on every save while
the compliance pass runs nightly.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from .. import config
from ..models import PlanExtract

UNRESOLVED = re.compile(r"\b(TBD|TODO|TBC|FIXME|XXX|\[\s*\]|<insert[^>]*>|PLACEHOLDER)\b", re.I)


class Issue:
    __slots__ = ("check", "severity", "title", "detail", "section", "evidence")

    def __init__(self, check: str, severity: str, title: str, detail: str,
                 section: str = "", evidence: str = ""):
        self.check = check
        self.severity = severity
        self.title = title
        self.detail = detail
        self.section = section
        self.evidence = evidence

    def __repr__(self) -> str:
        return "<Issue " + self.severity + " " + self.check + ": " + self.title + ">"


# ------------------------------------------------------- A. RTO/RPO dependency

def check_rto_dependencies(plan: PlanExtract) -> list[Issue]:
    """If A feeds B, A must recover at least as fast as B: RTO(A) <= RTO(B).

    The spec's own worked example is backwards as written - it says core
    banking RTO 4h and payments RTO 2h means "payment processing cannot recover
    before core banking", which is the conflict, but the rule it states above
    it reads the other way round. The rule implemented here is the one that
    holds: a dependency cannot be restored before the thing it depends on.
    """
    issues: list[Issue] = []
    rto = {e.system: e for e in plan.rto_rpo}
    for entry in plan.rto_rpo:
        if entry.rto_hours is None:
            continue
        for dependent in entry.feeds:
            dep = rto.get(dependent)
            if dep is None:
                issues.append(Issue(
                    "rto_dependency", "medium",
                    "Dependency target has no RTO: " + dependent,
                    entry.system + " is documented as feeding " + dependent
                    + ", but " + dependent + " has no documented RTO to check against.",
                ))
                continue
            if dep.rto_hours is None:
                continue
            if entry.rto_hours > dep.rto_hours:
                issues.append(Issue(
                    "rto_dependency", "high",
                    "RTO conflict: " + dependent + " cannot meet its objective",
                    dependent + " has an RTO of " + str(dep.rto_hours) + "h but depends on "
                    + entry.system + ", whose RTO is " + str(entry.rto_hours)
                    + "h. The dependency recovers after the system that needs it.",
                    evidence=entry.system + " RTO=" + str(entry.rto_hours) + "h -> "
                             + dependent + " RTO=" + str(dep.rto_hours) + "h",
                ))
    for entry in plan.rto_rpo:
        if entry.rpo_hours is not None and entry.rto_hours is not None:
            if entry.rpo_hours > entry.rto_hours:
                issues.append(Issue(
                    "rpo_sanity", "medium",
                    "RPO exceeds RTO for " + entry.system,
                    "RPO " + str(entry.rpo_hours) + "h is greater than RTO "
                    + str(entry.rto_hours) + "h. Confirm this is intended; it usually "
                    "indicates the two were set independently.",
                ))
    for system in plan.critical_systems:
        if system not in rto:
            issues.append(Issue(
                "rto_coverage", "critical",
                "No RTO/RPO documented for critical system: " + system,
                "The plan designates " + system + " as critical but documents no recovery "
                "objectives for it.",
            ))
    return issues


# ------------------------------------------------------------ B. Contacts

def check_contacts(plan: PlanExtract, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []

    for c in plan.contacts:
        if c.last_verified is None:
            issues.append(Issue(
                "contact_currency", "medium",
                "Contact never verified: " + c.name,
                "No verification date recorded for " + c.name
                + (" (" + c.role + ")" if c.role else "") + ".",
            ))
            continue
        age = (today - c.last_verified).days
        if age > config.CONTACT_STALE_HIGH_DAYS:
            sev = "high"
        elif age > config.CONTACT_STALE_MEDIUM_DAYS:
            sev = "medium"
        else:
            continue
        issues.append(Issue(
            "contact_currency", sev,
            "Stale contact: " + c.name,
            c.name + (" (" + c.role + ")" if c.role else "") + " last verified "
            + c.last_verified.isoformat() + ", " + str(age) + " days ago.",
        ))

    # Every critical role needs a primary and a backup.
    roles: dict[str, dict[str, bool]] = {}
    for c in plan.contacts:
        if not c.role:
            continue
        slot = roles.setdefault(c.role, {"primary": False, "backup": False})
        slot["backup" if c.is_backup else "primary"] = True
    for role, slot in sorted(roles.items()):
        if not slot["backup"]:
            issues.append(Issue(
                "contact_backup", "high",
                "No backup contact for role: " + role,
                "Role " + role + " has a primary contact but no documented backup. "
                "An escalation path with a single point of failure is not an escalation path.",
            ))
        if not slot["primary"]:
            issues.append(Issue(
                "contact_backup", "medium",
                "Backup listed with no primary for role: " + role,
                "Role " + role + " lists only a backup contact.",
            ))

    for c in plan.contacts:
        if not c.phone and not c.email:
            issues.append(Issue(
                "contact_reachability", "high",
                "No contact method for " + c.name,
                "Neither phone nor email is recorded, so this contact cannot be reached "
                "during an event.",
            ))
    return issues


# ------------------------------------------------- C. Procedure completeness

def check_procedures(plan: PlanExtract) -> list[Issue]:
    issues: list[Issue] = []
    inventory = {s.lower() for s in plan.system_inventory} | {s.lower() for s in plan.critical_systems}

    covered = {p.scenario.lower() for p in plan.procedures if p.scenario}
    for scenario in plan.scenarios_covered:
        if scenario.lower() not in covered:
            issues.append(Issue(
                "procedure_missing", "high",
                "Scenario has no documented procedure: " + scenario,
                "The plan lists " + scenario + " as a covered scenario but no recovery "
                "procedure is documented for it.",
            ))

    for p in plan.procedures:
        missing = []
        if not p.has_trigger_condition:
            missing.append("trigger condition")
        if not p.has_step_by_step:
            missing.append("step-by-step actions")
        if not p.responsible_party:
            missing.append("named responsible party")
        if not p.has_success_criteria:
            missing.append("success criteria")
        if missing:
            issues.append(Issue(
                "procedure_incomplete", "medium",
                "Incomplete procedure: " + p.name,
                "Missing " + ", ".join(missing) + ".",
                section=p.name,
            ))

        found = UNRESOLVED.findall(p.raw_text or "")
        if found:
            issues.append(Issue(
                "procedure_unresolved", "high",
                "Unresolved placeholder in procedure: " + p.name,
                "Procedure text contains " + ", ".join(sorted({f if isinstance(f, str) else f[0] for f in found}))
                + ". A procedure with an open placeholder is not executable under pressure.",
                section=p.name,
            ))

        for sysname in p.systems_referenced:
            if sysname.lower() not in inventory:
                issues.append(Issue(
                    "procedure_stale_system", "medium",
                    "Procedure references a system not in inventory: " + sysname,
                    "Procedure '" + p.name + "' references " + sysname
                    + ", which does not appear in the plan's system inventory. Confirm "
                    "whether this is a renamed or decommissioned system.",
                    section=p.name,
                ))
    return issues


# ------------------------------------------------------- D. Vendor coverage

def check_vendors(plan: PlanExtract) -> list[Issue]:
    issues: list[Issue] = []
    for v in plan.critical_vendors:
        missing = []
        if not v.contact_documented:
            missing.append("contact")
        if not v.rto_documented:
            missing.append("recovery time expectation")
        if not v.escalation_documented:
            missing.append("escalation procedure")
        if missing:
            issues.append(Issue(
                "vendor_coverage", "critical" if "recovery time expectation" in missing else "high",
                "Critical vendor incompletely documented: " + v.name,
                v.name + (" (" + v.service + ")" if v.service else "") + " is missing "
                + ", ".join(missing) + ".",
            ))
        if not v.sla_on_file:
            issues.append(Issue(
                "vendor_sla", "high",
                "No SLA on file for critical vendor: " + v.name,
                "The plan names " + v.name + " as critical but references no SLA.",
            ))
    return issues


# ----------------------------------------------------------- E. Test coverage

def check_tests(plan: PlanExtract, today: date | None = None,
                finra_member: bool = True) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []

    dated = [t for t in plan.tests if t.test_date]
    if not dated:
        issues.append(Issue(
            "test_coverage", "critical",
            "No test records documented",
            "The plan documents no exercise or test history.",
        ))
        return issues

    latest = max(dated, key=lambda t: t.test_date)  # type: ignore[arg-type]
    days = (today - latest.test_date).days  # type: ignore[operator]
    if finra_member and days > 365:
        issues.append(Issue(
            "test_annual", "critical",
            "No test within the last 12 months",
            "Most recent documented test is " + latest.test_date.isoformat()  # type: ignore[union-attr]
            + ", " + str(days) + " days ago. FINRA Rule 4370 requires annual testing.",
        ))

    if plan.plan_version and latest.plan_version_tested and \
            latest.plan_version_tested != plan.plan_version:
        issues.append(Issue(
            "test_version", "high",
            "Current plan version has never been tested",
            "Latest test covered version " + latest.plan_version_tested
            + "; the current plan is version " + plan.plan_version + ".",
        ))

    for t in dated:
        if not t.lessons_documented:
            issues.append(Issue(
                "test_lessons", "medium",
                "Test result without documented lessons: " + t.test_date.isoformat(),  # type: ignore[union-attr]
                "A test was run but no lessons identified were recorded, so the exercise "
                "cannot demonstrate improvement.",
            ))

    tested = {t.scenario.lower() for t in plan.tests if t.scenario}
    for scenario in plan.scenarios_covered:
        if scenario.lower() not in tested:
            issues.append(Issue(
                "test_scenario_gap", "medium",
                "Scenario planned but never tested: " + scenario,
                "The plan covers " + scenario + " but no test record exercises it.",
            ))
    return issues


# ------------------------------------------------------------ plan freshness

def check_freshness(plan: PlanExtract, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    if plan.last_approved is None:
        issues.append(Issue("plan_freshness", "high", "No approval date on plan",
                            "The plan records no approval date, so its currency cannot be established."))
    else:
        months = (today - plan.last_approved).days / 30.44
        if months > config.PLAN_STALE_MONTHS:
            issues.append(Issue(
                "plan_freshness", "high",
                "Plan is stale",
                "Last approved " + plan.last_approved.isoformat() + ", roughly "
                + str(int(months)) + " months ago.",
            ))
        elif months > 12:
            issues.append(Issue(
                "plan_freshness", "medium",
                "Plan is past its annual review",
                "Last approved " + plan.last_approved.isoformat() + ", roughly "
                + str(int(months)) + " months ago.",
            ))
    if plan.next_review_due and plan.next_review_due < today:
        issues.append(Issue("plan_freshness", "high", "Scheduled review date has passed",
                            "next_review_due was " + plan.next_review_due.isoformat() + "."))
    if plan.bia_last_updated:
        months = (today - plan.bia_last_updated).days / 30.44
        if months > 12:
            issues.append(Issue(
                "bia_freshness", "high", "Business Impact Analysis is out of date",
                "BIA last updated " + plan.bia_last_updated.isoformat() + ", roughly "
                + str(int(months)) + " months ago.",
            ))
    if not plan.approver:
        issues.append(Issue("plan_approval", "low", "No approver recorded",
                            "The plan does not name who approved it."))
    return issues


def run_all(plan: PlanExtract, today: date | None = None,
            finra_member: bool = True) -> list[Issue]:
    issues: list[Issue] = []
    issues += check_rto_dependencies(plan)
    issues += check_contacts(plan, today)
    issues += check_procedures(plan)
    issues += check_vendors(plan)
    issues += check_tests(plan, today, finra_member)
    issues += check_freshness(plan, today)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: (order.get(i.severity, 4), i.check, i.title))
    return issues


def summarize(issues: Iterable[Issue]) -> dict[str, int]:
    out = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
    for i in issues:
        out[i.severity] = out.get(i.severity, 0) + 1
        out["total"] += 1
    return out
