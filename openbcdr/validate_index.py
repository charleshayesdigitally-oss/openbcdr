"""Validation walker: work through unvalidated requirement records one at a time.

The index is the one place the agent may assert what a regulator requires, so
every record needs a human who has read the primary source. Hand-editing a
300-entry JSON file is how that job gets started and abandoned. This turns it
into a queue you can work in twenty-minute sittings.

Design decisions worth knowing:

- **Nothing is validated without a validator name and a confirmation date.**
  "Validated by someone, at some point" is not an audit trail.
- **The record is saved after every decision**, not at the end. A session that
  gets interrupted keeps its work.
- **Editing the text is the normal path, not the exception.** A machine-drafted
  requirement that survives validation completely unchanged is more likely to
  mean the validator skimmed than that the draft was perfect.
- **Skip is a first-class outcome.** A record you cannot confirm today should
  stay unvalidated rather than be waved through to clear the queue.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .models import Requirement

SEVERITIES = ("critical", "high", "medium", "low")


def load_file(path: Path) -> tuple[dict, list[Requirement]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = data["requirements"] if isinstance(data, dict) else data
    envelope = data if isinstance(data, dict) else {"requirements": records}
    return envelope, [Requirement(**r) for r in records]


def save_file(path: Path, envelope: dict, records: Iterable[Requirement]) -> None:
    envelope = dict(envelope)
    envelope["requirements"] = [r.model_dump(mode="json") for r in records]
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def pending(records: Iterable[Requirement]) -> list[Requirement]:
    return [r for r in records if not r.validated_by_human]


def apply_validation(
    record: Requirement,
    validator: str,
    requirement_text: str | None = None,
    section: str | None = None,
    severity: str | None = None,
    applicability: list[str] | None = None,
    as_of_version: str | None = None,
    on_date: date | None = None,
) -> Requirement:
    """Return the record as validated. Raises rather than accept a bad input."""
    if not validator.strip():
        raise ValueError("a validator name is required - 'validated by someone' is not a record")
    if severity is not None and severity not in SEVERITIES:
        raise ValueError("severity must be one of " + str(SEVERITIES))

    if requirement_text is not None and requirement_text.strip():
        record.requirement = requirement_text.strip()
    if section is not None and section.strip():
        record.section = section.strip()
    if severity is not None:
        record.gap_severity = severity  # type: ignore[assignment]
    if applicability:
        record.applicability = applicability
    if as_of_version is not None and as_of_version.strip():
        record.as_of_version = as_of_version.strip()

    if not record.section.strip():
        raise ValueError(
            "a validated record needs a section citation - without one a finding "
            "cannot be explained, and an unexplainable finding must not be raised")
    if not record.as_of_version.strip():
        raise ValueError(
            "a validated record needs as_of_version - a requirement confirmed "
            "against an unnamed edition cannot be re-checked when the source changes")

    record.last_confirmed = on_date or date.today()
    record.validated_by = validator.strip()
    record.validated_by_human = True
    return record


def render_record(r: Requirement, index: int, total: int) -> str:
    L = [
        "=" * 72,
        "[" + str(index) + " of " + str(total) + "]  " + r.req_id,
        "=" * 72,
        "SOURCE      " + r.source,
        "SECTION     " + (r.section or "(none - must be supplied)"),
        "MANDATORY   " + ("yes" if r.mandatory else "no"),
        "SEVERITY    " + r.gap_severity + "   (provisional until you set it)",
        "APPLIES TO  " + ", ".join(r.applicability),
        "VERSION     " + (r.as_of_version or "(none - must be supplied)"),
        "",
        "REQUIREMENT AS DRAFTED:",
    ]
    for line in _wrap(r.requirement, 68):
        L.append("  " + line)
    if r.source_quote:
        L.append("")
        L.append("DRAFTED FROM THIS PASSAGE (verified against the source text):")
        for line in _wrap(r.source_quote, 68):
            L.append("  | " + line)
    L.append("")
    L.append("Open the primary source and confirm the section and the obligation.")
    return "\n".join(L)


def _wrap(text: str, width: int) -> list[str]:
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out or [""]


def walk(
    path: Path,
    validator: str,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    limit: int | None = None,
    audit=None,
) -> dict[str, int]:
    """Interactive pass over the unvalidated records. Returns a tally."""
    envelope, records = load_file(path)
    queue = pending(records)
    if not queue:
        say("Nothing pending - every record in " + str(path) + " is validated.")
        return {"validated": 0, "skipped": 0, "remaining": 0}

    total = len(queue)
    say(str(total) + " record(s) pending validation in " + str(path))
    say("Enter accepts the drafted value. Type `skip` to leave a record unvalidated,")
    say("or `quit` to stop - work is saved after every record.")
    say("")

    tally = {"validated": 0, "skipped": 0, "remaining": total}
    for i, rec in enumerate(queue, 1):
        if limit is not None and tally["validated"] + tally["skipped"] >= limit:
            break
        say(render_record(rec, i, total))

        answer = ask("Confirmed against the primary source? [y / skip / quit] ").strip().lower()
        if answer in ("q", "quit"):
            break
        if answer in ("s", "skip", "n", "no", ""):
            tally["skipped"] += 1
            say("  left unvalidated.\n")
            continue

        section = ask("  Section citation [" + (rec.section or "required") + "]: ")
        version = ask("  Edition / revision [" + (rec.as_of_version or "required") + "]: ")
        severity = ask("  Severity if unmet [" + rec.gap_severity + "]: ").strip().lower()
        say("  Requirement text - paste a correction, or Enter to keep as drafted:")
        text = ask("  > ")

        try:
            apply_validation(
                rec, validator=validator,
                requirement_text=text or None,
                section=section or None,
                severity=severity or None,
                as_of_version=version or None,
            )
        except ValueError as e:
            say("  NOT VALIDATED: " + str(e) + "\n")
            tally["skipped"] += 1
            continue

        save_file(path, envelope, records)
        if audit is not None:
            audit.log(validator, "requirement_validated", rec.req_id,
                      source=rec.source, section=rec.section,
                      as_of_version=rec.as_of_version, severity=rec.gap_severity)
        tally["validated"] += 1
        say("  validated by " + validator + " on " + str(rec.last_confirmed) + "\n")

    tally["remaining"] = len(pending(records))
    save_file(path, envelope, records)
    return tally


def status(records: Iterable[Requirement], today: date | None = None) -> str:
    """Coverage and staleness by source. Read-only."""
    today = today or date.today()
    recs = list(records)
    by_source: dict[str, dict[str, int]] = {}
    for r in recs:
        s = by_source.setdefault(r.source, {"total": 0, "validated": 0, "stale": 0})
        s["total"] += 1
        if r.validated_by_human:
            s["validated"] += 1
            if r.last_confirmed and (today - r.last_confirmed).days > 92:
                s["stale"] += 1

    L = ["STANDARDS INDEX STATUS", "=" * 72, ""]
    tot = len(recs)
    val = sum(1 for r in recs if r.validated_by_human)
    L.append("Records: " + str(tot) + "   Validated: " + str(val)
             + "   Pending: " + str(tot - val))
    if val < tot:
        L.append("")
        L.append("Any evaluation drawing on a pending record will withhold its score.")
    L.append("")
    L.append("By source:")
    for src, s in sorted(by_source.items()):
        line = ("  " + src[:40].ljust(42) + str(s["validated"]).rjust(4) + " / "
                + str(s["total"]).ljust(5))
        if s["stale"]:
            line += "  " + str(s["stale"]) + " confirmed >1 quarter ago"
        L.append(line)
    stale_total = sum(s["stale"] for s in by_source.values())
    if stale_total:
        L.append("")
        L.append(str(stale_total) + " validated record(s) are more than a quarter old.")
        L.append("Re-confirm them against their primary sources before the next evaluation.")
    return "\n".join(L)
