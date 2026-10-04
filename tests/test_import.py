"""Importing findings from a prompt-only agent.

The bridge exists because an agent running without this codebase has no quote
verification, no deduplication and no registry. These tests assert the import
step puts all three back, and that it refuses what it should refuse.

    python tests/test_import.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

from openbcdr import import_findings, standards, triage  # noqa: E402
from openbcdr.models import PlanExtract  # noqa: E402
from openbcdr.store import Store  # noqa: E402

PLAN = (ROOT / "samples" / "sample_plan.md").read_text(encoding="utf-8")
FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label
          + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def payload(findings, currency=None, coherence=None):
    return {"plan_id": "P", "plan_version": "1.0", "evaluated_on": "2026-09-05",
            "standards_currency": currency or [],
            "findings": findings,
            "coherence_findings": coherence or []}


def test_payload_parsing() -> None:
    print("\n[1] payload parsing")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "f.json"

        # Agents habitually wrap JSON in a fence. Making a human strip it by hand
        # is an invitation to edit the content while they are in there.
        p.write_text("```json\n" + json.dumps(payload([])) + "\n```", encoding="utf-8")
        check("a fenced JSON block is accepted", import_findings.load_payload(p)["plan_id"] == "P")

        p.write_text(json.dumps(payload([])), encoding="utf-8")
        check("bare JSON is accepted", import_findings.load_payload(p)["plan_id"] == "P")

        p.write_text("not json at all", encoding="utf-8")
        try:
            import_findings.load_payload(p)
            check("invalid JSON is refused", False, "accepted")
        except ValueError:
            check("invalid JSON is refused", True)

        p.write_text(json.dumps({"plan_id": "P"}), encoding="utf-8")
        try:
            import_findings.load_payload(p)
            check("payload with no findings list is refused", False, "accepted")
        except ValueError:
            check("payload with no findings list is refused", True)


def test_verification_and_rejection() -> None:
    print("\n[2] the import re-imposes what prompt-only mode lacks")
    # Each failure mode gets its OWN req_id. Reusing one made a case that was both
    # a duplicate and a bad enum, and the dedup check fires first - so the test
    # was measuring ordering, not the rule it claimed to measure.
    known = {"R-REAL", "R-GAP", "R-ENUM"}
    data = payload([
        {"req_id": "R-REAL", "coverage": "full",
         "evidence_quote": "Next review due: 2027-01-15", "rationale": "ok"},
        {"req_id": "R-REAL", "coverage": "gap", "evidence_quote": "", "rationale": "dupe"},
        {"req_id": "R-GAP", "coverage": "full",
         "evidence_quote": "Backups are air-gapped and tested weekly.", "rationale": "invented"},
        {"req_id": "R-NOT-IN-INDEX", "coverage": "gap", "evidence_quote": "", "rationale": "x"},
        {"req_id": "R-ENUM", "coverage": "sort-of", "evidence_quote": "", "rationale": "x"},
    ])
    findings, problems = import_findings.to_findings(data, PLAN, known)

    check("a real quote verifies",
          any(f.req_id == "R-REAL" and f.coverage == "full" and f.evidence_verified
              for f in findings))
    check("a fabricated quote is downgraded, not accepted as coverage",
          any(f.req_id == "R-GAP" and f.coverage == "insufficient_evidence"
              and not f.evidence_verified for f in findings))
    check("the downgrade is reported, not silent", problems["unverified_quote"] == ["R-GAP"])
    check("a requirement outside the index is refused",
          problems["unknown_req_id"] == ["R-NOT-IN-INDEX"])
    check("a repeated requirement is refused", problems["duplicate_req_id"] == ["R-REAL"])
    check("an unrecognised coverage value is refused",
          len(problems["bad_coverage"]) == 1 and "sort-of" in problems["bad_coverage"][0] and "R-ENUM" in problems["bad_coverage"][0])
    check("nothing invented reaches the findings list",
          not any(f.req_id == "R-NOT-IN-INDEX" for f in findings))


def test_end_to_end_dedup_and_audit() -> None:
    print("\n[3] registry, dedup and audit trail")
    with tempfile.TemporaryDirectory() as td:
        store = Store(str(Path(td) / "t.sqlite3"))
        store.save_plan_version("P", "1.0", "sandbox", "x", PLAN,
                                PlanExtract(plan_id="P").model_dump(mode="json"))
        reqs = standards.applicable(standards.load(), {"all_banks": True, "finra_member": True,
                                                       "fed_member": True, "sifi": False})
        known = {r.req_id for r in reqs}
        target = next(r.req_id for r in reqs if r.source.startswith("FFIEC"))

        data = payload([{"req_id": target, "coverage": "gap", "evidence_quote": "",
                         "rationale": "not addressed", "recommended_action": "do it"}])
        findings, _ = import_findings.to_findings(data, PLAN, known)
        store.save_findings(1, findings)
        c1, d1 = triage.persist(store, triage.gaps_from_findings(findings, reqs, "P"))
        check("first import opens the gap", c1 == 1 and d1 == 0, str((c1, d1)))

        # The whole point of importing rather than re-reading a chat transcript.
        findings2, _ = import_findings.to_findings(data, PLAN, known)
        c2, d2 = triage.persist(store, triage.gaps_from_findings(findings2, reqs, "P"))
        check("re-importing the same finding opens nothing new",
              c2 == 0 and d2 == 1, str((c2, d2)))

        ok, _ = store.verify_chain()
        check("audit chain intact after import", ok)
        store.close()


def test_summary_is_honest() -> None:
    print("\n[4] the summary does not overstate what happened")
    known = {"R-GAP"}
    data = payload([{"req_id": "R-GAP", "coverage": "gap", "evidence_quote": "", "rationale": "x"}],
                   coherence=[{"check": "rto_dependency", "severity": "high", "title": "t"}])
    findings, problems = import_findings.to_findings(data, PLAN, known)

    text = import_findings.render_summary(data, findings, problems, 1, 0, 1)
    check("coherence findings from the agent are refused, and it says why",
          "Ignored 1 coherence finding" in text and "deterministic" in text)
    check("states plainly that nothing was closed or notified",
          "Nothing here is closed, approved or notified" in text)

    no_currency = payload([{"req_id": "R-GAP", "coverage": "gap",
                            "evidence_quote": "", "rationale": "x"}])
    t2 = import_findings.render_summary(no_currency, findings, problems, 0, 0, 0)
    check("a missing currency block is called out rather than ignored",
          "No standards-currency block" in t2)

    stale = payload([{"req_id": "R-GAP", "coverage": "gap", "evidence_quote": "", "rationale": "x"}],
                    currency=[{"source": "NIST", "version": "Rev. 1", "status": "UNCONFIRMED"}])
    t3 = import_findings.render_summary(stale, findings, problems, 0, 0, 0)
    check("an unconfirmed source makes its findings provisional, and says so",
          "NOT confirmed current" in t3)

    unbacked = payload([], currency=[
        {"source": "FFIEC BCM Booklet", "version": "Nov 2019", "status": "CONFIRMED CURRENT",
         "url": "", "found_at_source": "", "checked": "2026-09-05"},
        {"source": "FINRA Rule 4370", "version": "current", "status": "CONFIRMED CURRENT",
         "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4370",
         "found_at_source": "current text", "checked": "2026-09-05"}])
    t5 = import_findings.render_summary(unbacked, [], {}, 0, 0, 0)
    check("CONFIRMED CURRENT with no URL retrieved is flagged as unverified",
          "claim CONFIRMED CURRENT" in t5 and "FFIEC BCM Booklet" in t5)
    check("a properly cited source is not flagged",
          "FINRA Rule 4370" not in t5.split("WARNING")[1])

    long_ver = payload([], currency=[{"source": "FINRA Rule 4370",
                                      "version": "current as retrieved 2026-09-04",
                                      "status": "CONFIRMED CURRENT"}])
    t4 = import_findings.render_summary(long_ver, [], {}, 0, 0, 0)
    row = [ln for ln in t4.splitlines() if "FINRA" in ln][0]
    check("a long version string does not run into the status column",
          "  CONFIRMED CURRENT" in row, repr(row))


if __name__ == "__main__":
    test_payload_parsing()
    test_verification_and_rejection()
    test_end_to_end_dedup_and_audit()
    test_summary_is_honest()
    print("\n" + "=" * 60)
    if FAILURES:
        print(str(len(FAILURES)) + " FAILED:")
        for f in FAILURES:
            print("  - " + f)
        raise SystemExit(1)
    print("Import bridge OK. No API key used, no network, no spend.")
