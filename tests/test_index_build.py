"""The index-building tools: decomposer and validation walker.

The decomposer runs against a stub transport, so no key and no spend. The walker
never calls the API at all.

    python tests/test_index_build.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openbcdr import decompose, llm, validate_index  # noqa: E402
from openbcdr.models import Requirement  # noqa: E402
from openbcdr.store import Store  # noqa: E402

FAILURES: list[str] = []

SOURCE = """3.2 Business Impact Analysis

The institution must identify critical business functions and determine the
impact of disruption on those functions.

Management should consider the interdependencies between business functions
when performing this analysis.

3.3 Testing

The institution must test its continuity plans at least annually and must
document the results of each test.
"""


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label
          + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


class _Text:
    type = "text"

    def __init__(self, text): self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Response:
    def __init__(self, payload):
        self.content = [_Text(json.dumps(payload))]
        self.stop_reason = "end_turn"
        self.stop_details = None
        self.usage = _Usage()


class _Stub:
    def __init__(self, responder):
        self.calls = []
        self.messages = self
        self.responder = responder

    def create(self, **kw):
        self.calls.append(kw)
        return self.responder(kw, len(self.calls))


def test_decomposer_discards_unquotable_candidates() -> None:
    print("\n[1] decomposer - a candidate it cannot quote is discarded, not shown")

    def responder(kw, n):
        return _Response({"candidates": [
            {   # real quote, lifted verbatim from SOURCE
                "section": "3.2", "requirement": "Identify critical business functions.",
                "source_quote": "identify critical business functions and determine the",
                "mandatory": True, "applicability": ["all_banks"], "tags": ["bia"],
                "proposed_severity": "critical", "reasoning": "obligation language",
            },
            {   # plausible, fluent, and nowhere in the source
                "section": "3.4", "requirement": "Maintain a ransomware playbook.",
                "source_quote": "The institution must maintain a ransomware playbook.",
                "mandatory": True, "applicability": ["all_banks"], "tags": ["cyber"],
                "proposed_severity": "high", "reasoning": "invented",
            },
            {   # second real one
                "section": "3.3", "requirement": "Document the results of each test.",
                "source_quote": "document the results of each test",
                "mandatory": True, "applicability": ["all_banks"], "tags": ["testing"],
                "proposed_severity": "critical", "reasoning": "obligation language",
            },
        ]})

    llm._client = _Stub(responder)
    records, stats = decompose.decompose(SOURCE, "FFIEC BCM Booklet", "2019", "FFIEC")

    check("the invented requirement never reaches the index",
          not any("ransomware" in r.requirement.lower() for r in records))
    check("the two quotable requirements are kept", len(records) == 2, str(len(records)))
    check("the discard is counted, not hidden", stats["quote_failed"] == 1, str(stats))
    check("every record ships unvalidated",
          all(not r.validated_by_human for r in records))
    check("source and version are stamped on each record",
          all(r.source == "FFIEC BCM Booklet" and r.as_of_version == "2019" for r in records))
    check("req_ids are sequential and prefixed",
          [r.req_id for r in records] == ["FFIEC-0001", "FFIEC-0002"],
          str([r.req_id for r in records]))

    env = decompose.to_index(records, "FFIEC BCM Booklet", stats)
    check("the written index warns it is unvalidated",
          any("NOT VALIDATED" in line for line in env["_README"]))
    check("the written index reports the discard count",
          any("discarded" in line for line in env["_README"]))


def test_decomposer_dedupes_across_chunk_overlap() -> None:
    print("\n[2] decomposer - chunk overlap does not duplicate a requirement")

    def responder(kw, n):
        return _Response({"candidates": [{
            "section": "3.2", "requirement": "Identify critical business functions.",
            "source_quote": "identify critical business functions",
            "mandatory": True, "applicability": ["all_banks"], "tags": [],
            "proposed_severity": "critical", "reasoning": "x",
        }]})

    llm._client = _Stub(responder)
    # Force several chunks over the same text so the same candidate recurs.
    records, stats = decompose.decompose(SOURCE, "S", "1", "S", chunk_size=200)
    check("more than one chunk was processed", stats["chunks"] > 1, str(stats["chunks"]))
    check("the repeated requirement appears once", len(records) == 1, str(len(records)))
    check("duplicates are counted", stats["duplicates"] >= 1, str(stats))


def test_validation_refuses_incomplete_records() -> None:
    print("\n[3] walker - a record cannot be validated into an unusable state")
    base = dict(req_id="R1", source="S", section="", requirement="Do the thing.")

    try:
        validate_index.apply_validation(Requirement(**base), validator="")
        check("refuses an empty validator name", False, "accepted")
    except ValueError:
        check("refuses an empty validator name", True)

    try:
        validate_index.apply_validation(Requirement(**base), validator="J. Rivera",
                                        as_of_version="2019")
        check("refuses a record with no section citation", False, "accepted")
    except ValueError:
        check("refuses a record with no section citation", True)

    try:
        validate_index.apply_validation(Requirement(**base), validator="J. Rivera",
                                        section="3.2")
        check("refuses a record with no edition", False, "accepted")
    except ValueError:
        check("refuses a record with no edition", True)

    try:
        validate_index.apply_validation(Requirement(**base), validator="J. Rivera",
                                        section="3.2", as_of_version="2019",
                                        severity="catastrophic")
        check("refuses an invalid severity", False, "accepted")
    except ValueError:
        check("refuses an invalid severity", True)

    r = validate_index.apply_validation(
        Requirement(**base), validator="J. Rivera", section="3.2",
        as_of_version="2019", severity="critical",
        requirement_text="Identify critical business functions.",
        on_date=date(2026, 9, 4))
    check("a complete validation succeeds", r.validated_by_human)
    check("the confirmation date is stamped", r.last_confirmed == date(2026, 9, 4))
    check("the corrected text is kept",
          r.requirement == "Identify critical business functions.")
    check("severity is set as a policy decision", r.gap_severity == "critical")


def test_walker_session() -> None:
    print("\n[4] walker - a scripted session, saved per record and audited")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "idx.json"
        recs = [Requirement(req_id="R" + str(i), source="FFIEC BCM Booklet",
                            section="3." + str(i), requirement="Draft text " + str(i),
                            as_of_version="2019") for i in (1, 2, 3)]
        validate_index.save_file(path, {"_README": ["draft"]}, recs)
        store = Store(str(Path(td) / "a.sqlite3"))

        # validate the first, skip the second, quit on the third
        answers = iter([
            "y", "3.1", "2019", "critical", "Identify critical business functions.",
            "skip",
            "quit",
        ])
        said: list[str] = []
        tally = validate_index.walk(path, "J. Rivera", ask=lambda p: next(answers),
                                    say=said.append, audit=store)

        check("one validated, one skipped",
              tally["validated"] == 1 and tally["skipped"] == 1, str(tally))
        check("two remain pending", tally["remaining"] == 2, str(tally))

        _env, after = validate_index.load_file(path)
        done = [r for r in after if r.validated_by_human]
        check("the decision was written to disk", len(done) == 1)
        check("the corrected text persisted",
              done[0].requirement == "Identify critical business functions.")
        check("the skipped record stays unvalidated",
              not [r for r in after if r.req_id == "R2"][0].validated_by_human)

        events = list(store.db.execute(
            "SELECT * FROM audit_events WHERE action='requirement_validated'"))
        check("validation is written to the audit trail", len(events) == 1)
        check("the audit records who validated it",
              events and events[0]["actor"] == "J. Rivera")
        ok, _ = store.verify_chain()
        check("audit chain intact", ok)
        store.close()


def test_status_reporting() -> None:
    print("\n[5] status - pending and stale records are both surfaced")
    recs = [
        Requirement(req_id="A", source="FFIEC BCM Booklet", section="3.2",
                    requirement="x", as_of_version="2019",
                    validated_by_human=True, last_confirmed=date(2026, 8, 20)),
        Requirement(req_id="B", source="FFIEC BCM Booklet", section="3.3",
                    requirement="x", as_of_version="2019",
                    validated_by_human=True, last_confirmed=date(2025, 1, 10)),
        Requirement(req_id="C", source="FINRA Rule 4370", section="4370(a)",
                    requirement="x", as_of_version="current"),
    ]
    out = validate_index.status(recs, today=date(2026, 9, 4))
    check("counts validated and pending", "Validated: 2" in out and "Pending: 1" in out)
    check("warns that a pending record withholds the score",
          "withhold its score" in out)
    check("flags the record confirmed over a quarter ago",
          "1 confirmed >1 quarter ago" in out or "confirmed >1 quarter ago" in out)
    check("tells you to re-confirm stale records", "Re-confirm them" in out)

    clean = validate_index.status([recs[0]], today=date(2026, 9, 4))
    check("says nothing about staleness when nothing is stale",
          "Re-confirm them" not in clean)


if __name__ == "__main__":
    test_decomposer_discards_unquotable_candidates()
    test_decomposer_dedupes_across_chunk_overlap()
    test_validation_refuses_incomplete_records()
    test_walker_session()
    test_status_reporting()
    print("\n" + "=" * 60)
    if FAILURES:
        print(str(len(FAILURES)) + " FAILED:")
        for f in FAILURES:
            print("  - " + f)
        raise SystemExit(1)
    print("Index-building tools OK. Decomposer ran on a stub; no live API call.")
