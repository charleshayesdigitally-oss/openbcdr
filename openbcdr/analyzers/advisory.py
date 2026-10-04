"""Practice advisories: good practice, not requirements (Phase 4).

Ideas raised at industry conferences (DRJ Fall 2026 public sessions) that are
worth flagging but are NOT regulatory requirements. They are kept apart from
everything that counts:

- no requirement id and no curated severity;
- never scored, never opened as a gap, never routed with a deadline;
- always shown under their own heading with the label below.

Every check here is deterministic and reads only what the plan extract already
holds. The AI-dependency check is a keyword match, said so in its own text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from ..models import PlanExtract

LABEL = ("Good practice raised at industry conferences, not a regulatory requirement. "
         "Not scored and not opened as gaps.")


@dataclass(frozen=True)
class Advisory:
    check: str
    title: str
    detail: str
    idea: str   # which public idea it comes from, in plain words


# ------------------------------------------------------------ people as dependencies

_HONORIFIC = re.compile(r"^(dr|mr|mrs|ms|mx|prof|sir)\.?\s+", re.IGNORECASE)


def _person(name: str) -> str:
    """Compare people loosely: case, spacing and a leading honorific ignored."""
    n = re.sub(r"\s+", " ", name).strip().lower()
    return _HONORIFIC.sub("", n)


def _role(role: str) -> str:
    return re.sub(r"\s+", " ", role).strip().lower()


def check_key_people(plan: PlanExtract) -> list[Advisory]:
    out: list[Advisory] = []
    shown: dict[str, str] = {}
    role_shown: dict[str, str] = {}
    for c in plan.contacts:
        if c.name.strip():
            shown.setdefault(_person(c.name), c.name.strip())   # first spelling wins
        if c.role.strip():
            role_shown.setdefault(_role(c.role), c.role.strip())
    primary_roles: dict[str, set[str]] = {}
    for c in plan.contacts:
        if c.role.strip() and not c.is_backup and c.name.strip():
            primary_roles.setdefault(_person(c.name), set()).add(_role(c.role))
    for key, roles in sorted(primary_roles.items()):
        if len(roles) >= 2:
            out.append(Advisory(
                "key_person", "One person is primary for several recovery roles: " + shown[key],
                shown[key] + " is the primary contact for " + ", ".join(sorted(role_shown[r] for r in roles))
                + ". If they are unavailable, all of those roles fall to their backups at the same moment.",
                "People are dependencies too; one person carrying several roles concentrates risk."))
    by_role: dict[str, dict[str, set[str]]] = {}
    for c in plan.contacts:
        if c.role.strip() and c.name.strip():
            slot = by_role.setdefault(_role(c.role), {"primary": set(), "backup": set()})
            slot["backup" if c.is_backup else "primary"].add(_person(c.name))
    for role, slot in sorted(by_role.items()):
        for key in sorted(slot["primary"] & slot["backup"]):
            others = slot["backup"] - {key}
            out.append(Advisory(
                "key_person", "Backup is the same person as the primary: " + role_shown[role],
                shown[key] + " is listed as both primary and backup for " + role_shown[role] + ". "
                + ("That backup entry doesn't count; the other backup does."
                   if others else "Without another backup, the role has no real backup."),
                "People are dependencies too; a backup has to be a different person."))
    return out


# ------------------------------------------------- tested, but fixed or only documented?

_SHORTFALL = re.compile(
    r"\b(fail(ed|ure|s)?|partial(ly)?|not met|missed|"
    r"(rto|rpo|time|target|window|objective)s?\s+(was\s+|were\s+)?exceeded|over (the )?(rto|rpo|target))\b",
    re.IGNORECASE)
_SUCCESS = re.compile(
    r"\b(no (issues?|problems?|failures?|findings?)|without (any )?(issues?|problems?|failures?)|"
    r"passed|successful(ly)?|all objectives met|met all)\b", re.IGNORECASE)


def _fell_short(result: str) -> bool:
    return bool(_SHORTFALL.search(result)) and not _SUCCESS.search(result)


def _scenario(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def check_test_follow_through(plan: PlanExtract) -> list[Advisory]:
    out: list[Advisory] = []
    dated = sorted((t for t in plan.tests if t.test_date), key=lambda t: t.test_date)
    if not dated:
        return out
    latest = dated[-1]
    if not latest.lessons_documented:
        out.append(Advisory(
            "test_follow_through", "Latest test has no documented lessons",
            "The test on " + latest.test_date.isoformat() + " (" + (latest.scenario or "scenario not named")
            + ") records no lessons learned, so there is no way to tell whether what it found was fixed.",
            "Ask whether test findings were fixed or only written down."))
    for t in dated:
        if not (t.result and _fell_short(t.result)):
            continue
        name = _scenario(t.scenario)
        later = [x for x in dated if x.test_date > t.test_date and name and _scenario(x.scenario) == name]
        if not later:
            out.append(Advisory(
                "test_follow_through", "A test that reads like it fell short has no later retest: "
                + (t.scenario.strip() or t.test_date.isoformat()),
                "The " + t.test_date.isoformat() + " test result reads \"" + t.result.strip()[:120]
                + "\" and no later test of " + ("that scenario" if name else "an unnamed scenario")
                + " is on record.",
                "Carry open findings forward until a later test shows them fixed."))
    return out


# --------------------------------------------------------- AI in a critical path

_AI = re.compile(r"\b(AI|A\.I\.|artificial intelligence|machine learning|ML model|LLM|large language model|"
                 r"chat ?bot|chatgpt|gpt(-?\d+(\.\d+)?)?|copilot|generative|agentic|AI agent)\b", re.IGNORECASE)
_FALLBACK = re.compile(r"\b(fall ?back (to|is|process|procedure)|workaround|manually|"
                       r"manual (process|workaround|mode|entry|replies|steps?|procedure|routing)|"
                       r"work(ing)? without|switch(es)? to)\b", re.IGNORECASE)
_NO_FALLBACK = re.compile(r"\b(no|not|without (a|any))\s+(manual\s+)?(fall ?back|workaround|alternative)",
                          re.IGNORECASE)


def _covers(name: str, p) -> bool:
    text = " ".join((p.raw_text, p.name, p.scenario))
    return (name.lower() in text.lower() and bool(_FALLBACK.search(text))
            and not _NO_FALLBACK.search(text))


def check_ai_dependencies(plan: PlanExtract) -> list[Advisory]:
    found: list[tuple[str, str]] = []   # (display, name to look for in procedures)
    found += [(s, s) for s in plan.critical_systems if _AI.search(s)]
    found += [(v.name + (" (" + v.service + ")" if v.service else ""), v.name) for v in plan.critical_vendors
              if _AI.search(v.name) or _AI.search(v.service or "")]
    out: list[Advisory] = []
    for display, name in dict.fromkeys(found):
        if not any(_covers(name, p) for p in plan.procedures):
            out.append(Advisory(
                "ai_dependency", "AI tool in a critical path with no documented fallback (keyword match): "
                + display,
                display + " looks like an AI tool (a keyword match, so check it) that the plan relies on, and "
                "no procedure describes how to keep working without it. A vendor's fallback is matched by "
                "vendor name, not by service.",
                "Map AI tools as dependencies, with a tested fallback that can be manual."))
    return out


def run_all(plan: PlanExtract, today: date | None = None) -> list[Advisory]:
    return check_key_people(plan) + check_test_follow_through(plan) + check_ai_dependencies(plan)


def lines(advisories: Iterable[Advisory]) -> list[str]:
    adv = list(advisories)
    if not adv:
        return []
    out = ["Practice advisories (" + str(len(adv)) + "): " + LABEL]
    for a in adv:
        out.append("  ADVISORY  " + a.check.ljust(20) + " " + a.title)
    return out
