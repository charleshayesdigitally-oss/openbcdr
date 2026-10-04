"""Standards & Regulatory Index (spec section 5.2).

Requirements are curated records on disk, not model output. The index is the
one place the agent is allowed to assert what a regulator requires, and every
record carries `validated_by_human` - false until someone has read the primary
source. Report generation refuses to produce examiner-facing output when any
requirement in scope is still unvalidated.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from . import config
from .models import Requirement


class UnvalidatedStandards(RuntimeError):
    """Raised when examiner-facing output is requested over unvalidated records."""


def load(directory: Path | None = None) -> list[Requirement]:
    directory = Path(directory or config.STANDARDS_DIR)
    reqs: list[Requirement] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data["requirements"] if isinstance(data, dict) else data
        for rec in records:
            req = Requirement(**rec)
            if req.req_id in seen:
                raise ValueError("duplicate req_id in standards index: " + req.req_id)
            seen.add(req.req_id)
            reqs.append(req)
    if not reqs:
        raise FileNotFoundError("no requirement records found under " + str(directory))
    return reqs


def applicable(reqs: Iterable[Requirement], profile: dict[str, bool]) -> list[Requirement]:
    """Jurisdictional overlay (spec section 2.3).

    `profile` keys are applicability tags the institution satisfies, e.g.
    {"all_banks": True, "finra_member": True, "sifi": False}. A requirement
    applies when ANY of its applicability tags is true for this institution.
    This is what stops SIFI-level obligations firing at a mid-size bank.
    """
    out = []
    for r in reqs:
        if any(profile.get(tag, False) for tag in r.applicability):
            out.append(r)
    return out


def assert_validated(reqs: Iterable[Requirement]) -> None:
    bad = [r.req_id for r in reqs if not r.validated_by_human]
    if bad:
        raise UnvalidatedStandards(
            str(len(bad)) + " requirement record(s) have not been confirmed against the "
            "primary source and cannot back an examiner-facing report: "
            + ", ".join(bad[:8]) + ("..." if len(bad) > 8 else "")
        )


def coverage_stats(reqs: Iterable[Requirement]) -> dict[str, int]:
    reqs = list(reqs)
    return {
        "total": len(reqs),
        "validated": sum(1 for r in reqs if r.validated_by_human),
        "mandatory": sum(1 for r in reqs if r.mandatory),
    }
