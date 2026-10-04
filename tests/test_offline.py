"""The deterministic half. No API key, no network, no spend.

Every check here asserts against the synthetic plan in samples/, which has
defects planted on purpose. If a coherence check silently stops firing, this
catches it.

    python tests/test_offline.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

# Tests must not depend on whether THIS machine has a local deny list. The
# missing-local refusal is tested explicitly in [6], with this unset.
import os  # noqa: E402
os.environ.setdefault("BCDR_ALLOW_NO_LOCAL_PATTERNS", "1")

from openbcdr import boundary, report, standards, triage  # noqa: E402
from openbcdr.analyzers import coherence  # noqa: E402
from openbcdr.analyzers.compliance import verify_quote  # noqa: E402
from openbcdr.models import CoverageFinding  # noqa: E402
from openbcdr.store import Store  # noqa: E402
from load_sample import SAMPLE  # noqa: E402

PLAN_TEXT = (ROOT / "samples" / "sample_plan.md").read_text(encoding="utf-8")
TODAY = date(2026, 9, 4)  # pinned - a moving "today" makes staleness tests flap

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def test_coherence_finds_every_planted_defect() -> None:
    print("\n[1] coherence - every planted defect, and nothing invented")
    issues = coherence.run_all(SAMPLE, today=TODAY, finra_member=True)
    checks_hit = {i.check for i in issues}

    for label, want in [
        ("RTO dependency conflict (Payment Processing 2h needs Core Banking 4h)", "rto_dependency"),
        ("critical system with no objectives (Treasury Workstation)", "rto_coverage"),
        ("stale contact (J. Smith, 2024-11-15)", "contact_currency"),
        ("role with no backup contact", "contact_backup"),
        ("unresolved TBD in a procedure", "procedure_unresolved"),
        ("procedure missing owner and success criteria", "procedure_incomplete"),
        ("procedure references a system not in inventory (OldCore)", "procedure_stale_system"),
        ("scenario with no procedure (pandemic)", "procedure_missing"),
        ("critical vendor missing recovery expectation (FIS)", "vendor_coverage"),
        ("critical vendor with no SLA", "vendor_sla"),
        ("current plan version never tested", "test_version"),
        ("scenarios planned but never exercised", "test_scenario_gap"),
        ("BIA out of date", "bia_freshness"),
    ]:
        check("finds " + label, want in checks_hit)

    # The compliant vendor must NOT be flagged - a checker that flags everything
    # is as useless as one that flags nothing.
    check("does not flag the compliant vendor (Regional Telco)",
          not any("Regional Telco" in i.title for i in issues))
    check("does not flag the complete procedure (5.1 Data Center Outage)",
          not any(i.check == "procedure_incomplete" and "5.1" in i.title for i in issues))
    check("does not flag the current contacts",
          not any(i.check == "contact_currency" and ("Rivera" in i.title or "Bhatt" in i.title)
                  for i in issues))

    # Last test 2025-10-20 vs pinned today 2026-09-04 is 319 days - inside the
    # annual window, so the FINRA test check must stay quiet.
    check("annual-test check stays quiet at 319 days",
          "test_annual" not in checks_hit)
    # Approved 2026-01-15, ~7.6 months - inside the annual review window.
    check("plan-freshness check stays quiet at ~8 months",
          "plan_freshness" not in checks_hit)

    summary = coherence.summarize(issues)
    check("severity summary totals reconcile",
          summary["total"] == len(issues)
          == summary["critical"] + summary["high"] + summary["medium"] + summary["low"],
          str(summary))


def test_rto_rule_direction() -> None:
    print("\n[2] the RTO rule points the right way (spec 6.2A is self-contradictory)")
    from openbcdr.models import PlanExtract, RtoRpo

    bad = PlanExtract(plan_id="P", rto_rpo=[
        RtoRpo(system="A", rto_hours=4, feeds=["B"]),
        RtoRpo(system="B", rto_hours=2),
    ])
    good = PlanExtract(plan_id="P", rto_rpo=[
        RtoRpo(system="A", rto_hours=2, feeds=["B"]),
        RtoRpo(system="B", rto_hours=4),
    ])
    check("flags a dependency that cannot recover in time",
          any(i.check == "rto_dependency" for i in coherence.check_rto_dependencies(bad)))
    check("stays quiet when the dependency recovers first",
          not any(i.check == "rto_dependency" for i in coherence.check_rto_dependencies(good)))


def test_evidence_verification() -> None:
    print("\n[3] evidence verification - tolerant of formatting, not of invention")
    cases = [
        ("verbatim across a source line break", True,
         "The Application Owner is authorised to communicate with internal stakeholders\nduring a declared event"),
        ("whitespace collapsed", True,
         "Last exercise:   2025-10-20,  data center outage scenario"),
        ("smart quotes and en-dash normalised", True,
         "Trigger: loss of primary data center confirmed by Technology Lead."),
        ("plausible but invented", False,
         "Ransomware recovery is tested annually with the core provider."),
        ("real words, reordered into a false claim", False,
         "The plan documents recovery time objectives for FIS."),
        ("empty", False, ""),
        ("whitespace only", False, "   \n  "),
    ]
    for label, want, quote in cases:
        check("quote " + label + " -> " + ("accepted" if want else "rejected"),
              verify_quote(quote, PLAN_TEXT) is want)


def test_sla_and_routing() -> None:
    print("\n[4] business-day SLAs and severity routing")
    # 2026-09-04 is a Friday.
    check("critical = 15 business days -> 2026-09-25",
          triage.deadline_for("critical", TODAY) == date(2026, 9, 25),
          str(triage.deadline_for("critical", TODAY)))
    check("high = 30 business days -> 2026-10-16",
          triage.deadline_for("high", TODAY) == date(2026, 10, 16),
          str(triage.deadline_for("high", TODAY)))
    check("low has no deadline", triage.deadline_for("low", TODAY) is None)
    check("critical escalates after 5 business days -> 2026-09-11",
          triage.escalation_date("critical", TODAY) == date(2026, 9, 11))
    check("weekends are skipped, never counted",
          triage.add_business_days(date(2026, 9, 4), 1) == date(2026, 9, 7))
    check("critical routes to risk owner AND compliance",
          triage.route("critical") == ["risk_owner", "compliance_officer"])
    check("low routes to the digest only", triage.route("low") == ["weekly_digest"])


def test_gap_registry_and_audit() -> None:
    print("\n[5] gap registry dedupe, decisions, and the hash-chained audit trail")
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "t.sqlite3")
        store = Store(db)
        store.save_plan_version("APP_PAYPROC_v4", "4.0", "sandbox", "samples/sample_plan.md",
                                PLAN_TEXT, SAMPLE.model_dump(mode="json"))
        issues = coherence.run_all(SAMPLE, today=TODAY)

        created, dup = triage.persist(store, triage.gaps_from_issues(issues, "APP_PAYPROC_v4", TODAY))
        check("first run opens a gap per issue", created == len(issues) and dup == 0,
              str((created, dup)))
        created2, dup2 = triage.persist(store, triage.gaps_from_issues(issues, "APP_PAYPROC_v4", TODAY))
        check("re-run opens nothing new (fingerprint dedupe)",
              created2 == 0 and dup2 == len(issues), str((created2, dup2)))

        before = len(store.open_gaps("APP_PAYPROC_v4"))
        store.decide_gap("GAP-2026-0001", actor="risk_owner", decision="defer",
                         note="reorg in flight", new_deadline="2026-10-31")
        after = len(store.open_gaps("APP_PAYPROC_v4"))
        check("a deferred gap leaves the open list", after == before - 1, str((before, after)))

        for bad in ("ignore", "delete", ""):
            try:
                store.decide_gap("GAP-2026-0002", "x", bad)
                check("rejects invalid decision '" + bad + "'", False, "accepted it")
            except ValueError:
                check("rejects invalid decision '" + bad + "'", True)

        ok, seq = store.verify_chain()
        check("audit chain intact on a clean database", ok and seq is None)
        n = store.db.execute("SELECT COUNT(*) c FROM audit_events").fetchone()["c"]
        check("every action wrote an audit event", n >= len(issues) + 2, str(n))
        store.close()

        # Tamper: edit a row's payload.
        raw = sqlite3.connect(db)
        raw.execute("UPDATE audit_events SET detail_json=replace(detail_json,'critical','low')")
        raw.commit()
        raw.close()
        s2 = Store(db)
        ok, seq = s2.verify_chain()
        check("edited audit row breaks the chain", not ok, "chain still verified")
        s2.close()

    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "t2.sqlite3")
        store = Store(db)
        for i in range(6):
            store.log("actor", "action" + str(i))
        store.db.execute("DELETE FROM audit_events WHERE seq=3")
        store.db.commit()
        ok, seq = store.verify_chain()
        check("deleted audit row breaks the chain", not ok, "chain still verified")
        store.close()


def test_boundary_matrix() -> None:
    print("\n[6] boundary guard, full matrix")
    cases = [
        ("sandbox, clean, no attestation", "a synthetic plan", "sandbox", False, False),
        ("sandbox, clean, attested", "a synthetic plan", "sandbox", True, True),
        ("sandbox, deny-list hit, attested", "INTERNAL USE ONLY plan", "sandbox", True, False),
        ("internal, clean", "the institution's own plan", "internal", False, True),
        ("internal, third-party confidential", "PROPRIETARY AND CONFIDENTIAL", "internal", False, False),
    ]
    for label, text, mode, att, want in cases:
        got = boundary.scan(text, mode, att).allowed
        check(label + " -> " + ("allow" if want else "refuse"), got is want)
    try:
        boundary.scan("x", "work", True)
        check("an unknown mode is rejected, not silently allowed", False, "accepted 'work'")
    except ValueError:
        check("an unknown mode is rejected, not silently allowed", True)

    # Organisation-specific patterns live ONLY in the gitignored local file, so a
    # regression in loading it would silently drop them. Prove it loads, and prove
    # it is the local file doing the refusing (same text passes without it).
    from openbcdr import config
    saved = config.BOUNDARY_PATTERNS_LOCAL
    saved_env = os.environ.get("BCDR_ALLOW_NO_LOCAL_PATTERNS")
    with tempfile.TemporaryDirectory() as td:
        absent = Path(td) / "absent.local.txt"
        local = Path(td) / "boundary_patterns.local.txt"
        local.write_text("zz-sentinel-org\n", encoding="utf-8")
        bom = Path(td) / "bom.local.txt"
        bom.write_bytes("zz-sentinel-org\n".encode("utf-8-sig"))  # pattern on line 1, after the BOM
        crlf = Path(td) / "crlf.local.txt"
        crlf.write_bytes(b"# test\r\nzz-sentinel-org\r\n")
        text = "zz-sentinel-org continuity plan"
        try:
            # Missing local file: sandbox fails closed unless explicitly acknowledged.
            config.BOUNDARY_PATTERNS_LOCAL = absent
            os.environ.pop("BCDR_ALLOW_NO_LOCAL_PATTERNS", None)
            res = boundary.scan("a synthetic plan", "sandbox", True)
            check("sandbox REFUSES a clean attested doc when the local file is missing",
                  res.allowed is False and res.missing_local)
            check("missing-local refusal says how to fix it",
                  "boundary_patterns.local.txt" in res.reason and "BCDR_ALLOW_NO_LOCAL_PATTERNS" in res.reason)
            check("internal mode is unaffected by a missing local file",
                  boundary.scan("the institution's own plan", "internal", False).allowed is True)
            for label, body in (("empty", ""), ("comments only", "# nothing yet\n\n")):
                hollow = Path(td) / ("hollow-" + label.replace(" ", "-") + ".local.txt")
                hollow.write_text(body, encoding="utf-8")
                config.BOUNDARY_PATTERNS_LOCAL = hollow
                res = boundary.scan("a synthetic plan", "sandbox", True)
                check("sandbox REFUSES when the local file is " + label,
                      res.allowed is False and res.missing_local)
            config.BOUNDARY_PATTERNS_LOCAL = absent
            os.environ["BCDR_ALLOW_NO_LOCAL_PATTERNS"] = "0"
            check("only the exact value 1 acknowledges generic-only protection",
                  boundary.scan("a synthetic plan", "sandbox", True).allowed is False)
            os.environ["BCDR_ALLOW_NO_LOCAL_PATTERNS"] = "1"
            check("acknowledged: sentinel text passes with no local file (control)",
                  boundary.scan(text, "sandbox", True).allowed is True)

            for label, path in (("LF", local), ("UTF-8 BOM", bom), ("CRLF", crlf)):
                config.BOUNDARY_PATTERNS_LOCAL = path
                check("local patterns file (" + label + ") is loaded and refuses in sandbox",
                      boundary.scan(text, "sandbox", True).allowed is False)
        finally:
            config.BOUNDARY_PATTERNS_LOCAL = saved
            if saved_env is None:
                os.environ.pop("BCDR_ALLOW_NO_LOCAL_PATTERNS", None)
            else:
                os.environ["BCDR_ALLOW_NO_LOCAL_PATTERNS"] = saved_env


def test_transfer_bundle_excludes_local_files() -> None:
    print("\n[6b] transfer bundle never carries local-only files")
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_transfer", ROOT / "tools" / "make_transfer.py")
    mt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mt)
    saved = mt.ROOT
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        (fake / "config").mkdir()
        (fake / "config" / "boundary_patterns.txt").write_text("generic\n", encoding="utf-8")
        (fake / "config" / "boundary_patterns.local.txt").write_text("org\n", encoding="utf-8")
        (fake / "config" / "other.LOCAL.txt").write_text("org\n", encoding="utf-8")
        (fake / "CLAUDE.local.md").write_text("machine notes\n", encoding="utf-8")
        (fake / "org" / "acme.LOCAL.d").mkdir(parents=True)
        (fake / "org" / "acme.LOCAL.d" / "plan.md").write_text("org plan\n", encoding="utf-8")
        for d in (".git", ".venv", ".CLAUDE", "export"):
            (fake / d).mkdir()
            (fake / d / "stray.txt").write_text(d + "\n", encoding="utf-8")
        (fake / "openbcdr-source.txt").write_text("an old bundle\n", encoding="utf-8")
        (fake / "unpack.py").write_text("# an old unpacker\n", encoding="utf-8")
        try:
            mt.ROOT = fake
            rels = [rel for rel, _ in mt.collect(exclude=fake / "export")]
            try:
                mt.build(fake)
                refused_root = False
            except SystemExit:
                refused_root = True
        finally:
            mt.ROOT = saved
    check("tracked patterns file IS packed", "openbcdr/config/boundary_patterns.txt" in rels, str(rels))
    check("local-only files and local-marked directories are NOT packed, any case",
          not any(".local." in r.lower() for r in rels), str(rels))
    for d in (".git", ".venv", ".claude"):
        check(d + " contents are NOT packed (any case)",
              not any("/" + d + "/" in r.lower() for r in rels), str(rels))
    check("the output directory is NOT packed", not any("/export/" in r for r in rels), str(rels))
    check("a previous bundle and unpacker are NOT packed",
          not any(r.endswith(("openbcdr-source.txt", "unpack.py")) for r in rels), str(rels))
    check("--out at the project root is refused", refused_root)


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".emf", ".wmf", ".tif", ".tiff")


def _texts_of(p: Path) -> tuple[list[str], list[str]]:
    """Every readable text in a file: the file itself, or EVERY member of an
    archive (.docx is a zip), with XML tags also stripped so a name split across
    runs is caught. Returns (texts, unreadable) - an unknown binary member is
    reported as unreadable rather than silently counted as clean. Image members
    (a .docx thumbnail) carry no text a pattern scan could read, so they are the
    one binary type skipped."""
    import re
    import zipfile
    if zipfile.is_zipfile(p):
        texts, bad = [], []
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.endswith("/") or n.lower().endswith(IMAGE_EXT):
                    continue
                data = z.read(n)
                if b"\0" in data:
                    bad.append(n)
                    continue
                t = data.decode("utf-8", "replace")
                texts += [t, re.sub(r"<[^>]+>", "", t)]
        return texts, bad
    data = p.read_bytes()
    if b"\0" in data:
        return [], [p.name]
    return [data.decode("utf-8", "replace")], []


def _scan_tracked(root: Path, pats: list) -> tuple[int, int, list[str]]:
    """Scan every file git tracks under root against pats.
    Returns (hits, scanned, unreadable)."""
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                             check=True).stdout.decode("utf-8").split("\0")
    hits = 0
    scanned = 0
    unread = []
    for rel in tracked:
        p = root / rel
        if not rel or not p.is_file():
            continue
        texts, bad = _texts_of(p)
        if bad:
            unread.extend(rel + ("" if b == rel else "!" + b) for b in bad)
            continue
        scanned += 1
        hits += sum(1 for pat in pats for t in texts if pat.search(t))
    return hits, scanned, unread


def test_tracked_files_clean_of_local_patterns() -> None:
    """Automates 'scan before committing': no TRACKED text file may match the
    organisation-specific patterns. Prints counts only, never the patterns."""
    print("\n[6c] tracked files carry nothing from the local deny list")
    import re
    import shutil
    import subprocess
    import zipfile
    from openbcdr import config

    # The scanner itself, on fixtures, so a narrowing of what it reads shows up
    # even on a machine that skips the real scan below.
    with tempfile.TemporaryDirectory() as td:
        z = Path(td) / "a.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("word/_rels/document.xml.rels", "<r>zz-sentinel-org</r>")
            zf.writestr("notes/secret.txt", "owner: zz-sentinel-org")
            zf.writestr("word/document.xml", "<w:t>zz-sentinel</w:t><w:t>-org</w:t>")
        texts, bad = _texts_of(z)
        sentinel = re.compile("zz-sentinel-org")
        check("scanner reads every archive member (rels, txt, split XML runs)",
              not bad and sum(1 for t in texts if sentinel.search(t)) >= 4,
              str(sum(1 for t in texts if sentinel.search(t))) + " hit(s)")
        zb = Path(td) / "b.zip"
        with zipfile.ZipFile(zb, "w") as zf:
            zf.writestr("docProps/thumbnail.jpeg", b"\xff\xd8\0\0image")
            zf.writestr("customXml/blob.bin", b"\0\0binary")
        check("scanner skips image members but reports other binaries as unreadable",
              _texts_of(zb)[1] == ["customXml/blob.bin"], str(_texts_of(zb)[1]))

    # The scan itself, proven on a synthetic repo with a synthetic deny list, so
    # its rejection path runs everywhere (CI has no private list by design).
    if shutil.which("git"):
        import re as _re
        with tempfile.TemporaryDirectory() as td:
            syn = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=syn, check=True)
            (syn / "clean.md").write_text("generic text\n", encoding="utf-8")
            subprocess.run(["git", "add", "clean.md"], cwd=syn, check=True)
            sentinel = [_re.compile("zz-sentinel-org", _re.IGNORECASE)]
            h0, s0, u0 = _scan_tracked(syn, sentinel)
            check("synthetic scan: clean tracked file has zero hits", h0 == 0 and s0 == 1 and not u0)
            (syn / "leak.md").write_text("Owner: ZZ-Sentinel-Org team\n", encoding="utf-8")
            subprocess.run(["git", "add", "leak.md"], cwd=syn, check=True)
            h1, _, _ = _scan_tracked(syn, sentinel)
            check("synthetic scan: a tracked file naming the sentinel org is caught", h1 == 1)
            (syn / "untracked.md").write_text("zz-sentinel-org\n", encoding="utf-8")
            h2, _, _ = _scan_tracked(syn, sentinel)
            check("synthetic scan: untracked files are not counted", h2 == 1)

    local = Path(config.BOUNDARY_PATTERNS_LOCAL)
    if not local.exists() or not (ROOT / ".git").exists() or not shutil.which("git"):
        print("  SKIP  needs a local deny list, a git checkout and git on PATH")
        return
    pats = [re.compile(ln.strip(), re.IGNORECASE)
            for ln in local.read_text(encoding="utf-8-sig").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    check("local deny list has at least one pattern (" + str(len(pats)) + ")", len(pats) > 0)
    hits, scanned, unread = _scan_tracked(ROOT, pats)
    check("scanned every tracked file (" + str(scanned) + ", unreadable binaries: " + str(len(unread)) + ")",
          scanned > 0 and not unread, ", ".join(unread))
    check("zero local-pattern matches in tracked files", hits == 0, str(hits) + " match(es)")


def test_report_refuses_to_overstate() -> None:
    print("\n[7] the report will not overstate what the pipeline knows")
    reqs = standards.load()
    findings = [CoverageFinding(req_id=r.req_id, coverage="full", rationale="ok",
                                evidence_quote="Next review due: 2027-01-15",
                                plan_section="header", recommended_action="",
                                evidence_verified=True) for r in reqs]

    text = report.render("P", "4.0", findings, reqs, today=TODAY)
    check("withholds the score while the index is unvalidated",
          "NOT EXAMINER-READY" in text and "Overall Score" not in text)

    examiner = report.render("P", "4.0", findings, reqs, today=TODAY, examiner_facing=True)
    check("refuses examiner-facing output outright",
          "Report generation stopped" in examiner)

    for r in reqs:
        r.validated_by_human = True
    validated = report.render("P", "4.0", findings, reqs, today=TODAY)
    check("shows a score once the index is validated",
          "Overall Score: 100/100" in validated and "NOT EXAMINER-READY" not in validated)

    try:
        standards.assert_validated(standards.load())
        check("assert_validated raises on the shipped seed index", False, "did not raise")
    except standards.UnvalidatedStandards:
        check("assert_validated raises on the shipped seed index", True)

    unverified = [CoverageFinding(req_id=reqs[0].req_id, coverage="insufficient_evidence",
                                  rationale="UNVERIFIED EVIDENCE - made it up",
                                  evidence_quote="a quote that is not in the plan",
                                  plan_section="", recommended_action="", evidence_verified=False)]
    t = report.render("P", "4.0", unverified, reqs, today=TODAY)
    check("surfaces evidence-verification failures in their own section",
          "EVIDENCE VERIFICATION FAILURES" in t)


def test_applicability_overlay() -> None:
    print("\n[8] jurisdictional overlay keeps out-of-scope obligations out")
    reqs = standards.load()
    member = standards.applicable(reqs, {"all_banks": True, "finra_member": True, "fed_member": True})
    non_member = standards.applicable(reqs, {"all_banks": True, "finra_member": False, "fed_member": True})
    finra_only = [r for r in reqs if r.applicability == ["finra_member"]]
    check("FINRA rules apply to a member firm",
          all(r in member for r in finra_only) and len(finra_only) > 0)
    check("FINRA rules drop out for a non-member",
          not any(r in non_member for r in finra_only))
    check("non-member scope is strictly smaller", len(non_member) < len(member))


def test_calibration_loop() -> None:
    print("\n[9] the calibration loop - reject capture and repeat-offender detection")
    from openbcdr import calibrate

    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "c.sqlite3")
        store = Store(db)
        store.save_plan_version("P", "4.0", "sandbox", "x", PLAN_TEXT,
                                SAMPLE.model_dump(mode="json"))
        issues = coherence.run_all(SAMPLE, today=TODAY)
        triage.persist(store, triage.gaps_from_issues(issues, "P", TODAY))
        gaps = store.open_gaps("P")

        check("every gap carries an origin_key for calibration to group by",
              all(g["origin_key"] for g in gaps))

        # reject is fail-closed: a reason code and a note are both mandatory.
        gid = gaps[0]["gap_id"]
        for label, kwargs in [
            ("no reason code", {"note": "wrong"}),
            ("bad reason code", {"note": "wrong", "reason_code": "because"}),
            ("reason but no note", {"reason_code": "not_applicable"}),
            ("reason but blank note", {"reason_code": "not_applicable", "note": "   "}),
        ]:
            try:
                store.decide_gap(gid, "risk_owner", "reject", **kwargs)
                check("reject refused: " + label, False, "it was accepted")
            except ValueError:
                check("reject refused: " + label, True)
        try:
            store.decide_gap(gid, "risk_owner", "close", reason_code="duplicate")
            check("reason_code refused on a non-reject decision", False, "accepted")
        except ValueError:
            check("reason_code refused on a non-reject decision", True)

        # An empty registry must say so rather than report a rate off nothing.
        empty = calibrate.render(calibrate.collect(store))
        check("no decisions reports insufficient data, not a rate",
              "No decisions on file" in empty and "%" not in empty)

        # Concentrate rejections on ONE origin to prove detection fires, and
        # spread a few elsewhere to prove it does not fire below the floor.
        by_origin: dict[str, list] = {}
        for g in gaps:
            by_origin.setdefault(g["origin_key"], []).append(g["gap_id"])
        repeated = next(k for k, v in by_origin.items() if len(v) >= 3)
        singles = [v[0] for k, v in by_origin.items() if k != repeated][:8]

        for g in by_origin[repeated][:3]:
            store.decide_gap(g, "risk_owner", "reject", note="Owned by the IR plan.",
                             reason_code="not_applicable")
        for g in singles:
            store.decide_gap(g, "risk_owner", "accept")

        stats = calibrate.collect(store)
        check("rejections counted", stats["rejected"] == 3, str(stats["rejected"]))
        check("acceptances counted", stats["accepted"] == len(singles), str(stats["accepted"]))
        check("precision computed once past the sample floor",
              stats["precision"] is not None and stats["total_decisions"] >= 10)

        off = calibrate.offenders(stats)
        check("the repeated origin is flagged as an offender",
              len(off) == 1 and off[0]["origin"] == repeated, str(off))
        check("single rejections are NOT flagged",
              all(o["total"] >= calibrate.MIN_SAMPLES_PER_ITEM for o in off))

        text = calibrate.render(stats)
        check("coherence offenders get coherence advice, not requirement advice",
              "coherence.py" in text and "applicability tags to adjust" in text)
        check("report states it changes nothing",
              "This report changes nothing" in text)

        # Only the latest decision per gap counts, or deferring then closing
        # would double-count one outcome and deflate precision.
        before = calibrate.collect(store)["total_decisions"]
        store.decide_gap(singles[0], "risk_owner", "close")
        after = calibrate.collect(store)["total_decisions"]
        check("a re-decided gap counts once, not twice", before == after,
              str(before) + " -> " + str(after))

        ok, _ = store.verify_chain()
        check("audit chain survives the whole calibration flow", ok)
        store.close()


def test_thin_samples_report_no_rate() -> None:
    print("\n[10] thin samples are reported as thin, never estimated")
    from openbcdr import calibrate

    with tempfile.TemporaryDirectory() as td:
        store = Store(str(Path(td) / "t.sqlite3"))
        store.save_plan_version("P", "1", "sandbox", "x", PLAN_TEXT,
                                SAMPLE.model_dump(mode="json"))
        issues = coherence.run_all(SAMPLE, today=TODAY)
        triage.persist(store, triage.gaps_from_issues(issues, "P", TODAY))
        gaps = store.open_gaps("P")
        for g in gaps[:3]:
            store.decide_gap(g["gap_id"], "risk_owner", "reject", note="no",
                             reason_code="other")
        text = calibrate.render(calibrate.collect(store))
        check("refuses a precision figure below the sample floor",
              "INSUFFICIENT DATA" in text)
        check("does not print a made-up percentage", "Precision (" not in text)
        store.close()


if __name__ == "__main__":
    test_coherence_finds_every_planted_defect()
    test_rto_rule_direction()
    test_evidence_verification()
    test_sla_and_routing()
    test_gap_registry_and_audit()
    test_boundary_matrix()
    test_transfer_bundle_excludes_local_files()
    test_tracked_files_clean_of_local_patterns()
    test_report_refuses_to_overstate()
    test_applicability_overlay()
    test_calibration_loop()
    test_thin_samples_report_no_rate()
    print("\n" + ("=" * 60))
    if FAILURES:
        print(str(len(FAILURES)) + " FAILED:")
        for f in FAILURES:
            print("  - " + f)
        raise SystemExit(1)
    print("All offline checks passed. No API key used, no network, no spend.")
