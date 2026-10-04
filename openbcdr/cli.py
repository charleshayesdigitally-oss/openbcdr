"""Command line entry point.

    python -m openbcdr ingest samples/sample_plan.md --mode sandbox --attest-synthetic
    python -m openbcdr ingest plans/payment-processing.md --mode internal
    python -m openbcdr coherence --plan APP_PAYPROC_v4
    python -m openbcdr analyze   --plan APP_PAYPROC_v4
    python -m openbcdr gaps
    python -m openbcdr report    --plan APP_PAYPROC_v4
    python -m openbcdr decide GAP-2026-0001 --actor risk_owner --decision defer --note "..."
    python -m openbcdr decide GAP-2026-0002 --actor risk_owner --decision reject \
        --reason wrong_requirement --note "FFIEC 3.4.3 does not require this"
    python -m openbcdr calibrate
    python -m openbcdr audit-verify

`coherence` runs offline and costs nothing. `analyze` calls the API.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import calibrate, config, import_findings, ingest, report, standards, triage, validate_index
from . import profile as org_profile
from .analyzers import coherence
from .boundary import BoundaryViolation
from .models import PlanExtract
from .store import Store

DEFAULT_PROFILE = {"all_banks": True, "finra_member": True, "fed_member": True, "sifi": False}


def _load_profile(path: str | None, org=None) -> dict[str, bool]:
    """Applicability tags: an explicit --profile file wins, then the --org
    profile, then the built-in default."""
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if org is not None:
        return dict(org.applicability)
    return dict(DEFAULT_PROFILE)


def _in_scope(args) -> list:
    """Requirements that apply to this run. An empty scope is refused: a report
    over zero requirements would print a score that measures nothing."""
    reqs = standards.applicable(standards.load(),
                                _load_profile(args.profile, getattr(args, "org_profile", None)))
    if not reqs:
        raise SystemExit("No requirement in the standards index applies to this profile "
                         "(check its applicability tags). Refusing to analyse or report on an "
                         "empty scope.")
    return reqs


def _test_rules(args) -> dict:
    """Annual-test rules for coherence. Without --org, behaviour is unchanged."""
    org = getattr(args, "org_profile", None)
    finra = not args.not_finra_member
    if org is None:
        return {"finra_member": finra}
    return {"finra_member": finra and org.applicability.get("finra_member", False),
            "annual_required": org.thresholds.annual_test_required}


def _plan_from_row(row) -> PlanExtract:
    return PlanExtract.model_validate_json(row["extract_json"])


def cmd_ingest(args) -> int:
    text = ingest.read_document(Path(args.path))
    try:
        extract, usage = ingest.extract(
            text, mode=args.mode, attested=args.attest_synthetic,
            plan_id_hint=args.plan_id or "")
    except BoundaryViolation as e:
        print("BOUNDARY REFUSAL: " + str(e), file=sys.stderr)
        print("Nothing was sent to the API and nothing was stored.", file=sys.stderr)
        return 2

    store = Store(args.db)
    store.log("operator", "boundary_attestation", str(args.path),
              mode=args.mode, attested=bool(args.attest_synthetic))
    pv_id = store.save_plan_version(
        plan_id=extract.plan_id, version=extract.plan_version, mode=args.mode,
        source_path=str(args.path), raw_text=text, extract=extract.model_dump(mode="json"))
    print("Ingested " + extract.plan_id + " version " + (extract.plan_version or "?")
          + " as plan_version " + str(pv_id))
    print("  systems with objectives: " + str(len(extract.rto_rpo))
          + " | contacts: " + str(len(extract.contacts))
          + " | vendors: " + str(len(extract.critical_vendors))
          + " | procedures: " + str(len(extract.procedures)))
    print("  tokens in/out: " + str(getattr(usage, "input_tokens", "?")) + "/"
          + str(getattr(usage, "output_tokens", "?")))
    store.close()
    return 0


def cmd_coherence(args) -> int:
    store = Store(args.db)
    row = store.latest_plan_version(args.plan)
    plan = _plan_from_row(row)
    issues = coherence.run_all(plan, **_test_rules(args))
    summary = coherence.summarize(issues)

    print("Coherence: " + str(summary["total"]) + " findings "
          + "(critical " + str(summary["critical"]) + ", high " + str(summary["high"])
          + ", medium " + str(summary["medium"]) + ", low " + str(summary["low"]) + ")")
    for i in issues:
        print("  " + i.severity.upper().ljust(10) + i.check.ljust(24) + i.title)
        if args.verbose:
            print("           " + i.detail)

    if args.open_gaps:
        created, dup = triage.persist(store, triage.gaps_from_issues(issues, plan.plan_id))
        print("Gap registry: " + str(created) + " opened, " + str(dup) + " already tracked.")
    store.close()
    return 0


def cmd_analyze(args) -> int:
    store = Store(args.db)
    row = store.latest_plan_version(args.plan)
    plan = _plan_from_row(row)
    plan_text = row["raw_text"]

    reqs = _in_scope(args)
    stats = standards.coverage_stats(reqs)
    print("Standards in scope: " + str(stats["total"]) + " (" + str(stats["validated"])
          + " validated)")
    if stats["validated"] < stats["total"]:
        print("  WARNING: unvalidated requirement records in scope. Findings are")
        print("  indicative only and the report will withhold a score.")

    from .analyzers import compliance  # imported here so `coherence` never needs the SDK

    def progress(done, total):
        print("  assessed " + str(done) + "/" + str(total), file=sys.stderr)

    findings, usage = compliance.analyze(plan_text, reqs, progress=progress)
    store.save_findings(int(row["id"]), findings)
    store.log("agent", "compliance_run", "plan_version:" + str(row["id"]),
              requirements=len(reqs), findings=len(findings), tokens=usage)

    s = compliance.score(findings)
    print("Coverage: full " + str(s["full"]) + " | partial " + str(s["partial"])
          + " | gap " + str(s["gap"]) + " | unverifiable " + str(s["insufficient_evidence"]))
    print("Tokens - in " + str(usage["input"]) + ", out " + str(usage["output"])
          + ", cache read " + str(usage["cache_read"]) + ", cache write "
          + str(usage["cache_write"]))
    if usage["cache_read"] == 0 and len(reqs) > config.REQUIREMENTS_PER_CALL:
        print("  NOTE: zero cache reads across multiple calls - the plan prefix is not")
        print("  caching. Check that nothing volatile precedes it in the system blocks.")

    if args.open_gaps:
        created, dup = triage.persist(
            store, triage.gaps_from_findings(findings, reqs, plan.plan_id))
        print("Gap registry: " + str(created) + " opened, " + str(dup) + " already tracked.")
    store.close()
    return 0


def cmd_report(args) -> int:
    store = Store(args.db)
    row = store.latest_plan_version(args.plan)
    plan = _plan_from_row(row)
    reqs = _in_scope(args)

    from .models import CoverageFinding
    findings = [CoverageFinding(
        req_id=r["req_id"], coverage=r["coverage"], rationale=r["rationale"] or "",
        evidence_quote=r["evidence_quote"] or "", plan_section=r["plan_section"] or "",
        recommended_action=r["recommended_action"] or "",
        evidence_verified=bool(r["evidence_verified"]),
    ) for r in store.findings_for(int(row["id"]))]

    issues = coherence.run_all(plan, **_test_rules(args))
    text = report.render(
        plan_id=plan.plan_id, plan_version=plan.plan_version, findings=findings,
        requirements=reqs, issues=issues, gaps=store.open_gaps(plan.plan_id),
        examiner_facing=args.examiner,
    )
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("\nWritten to " + args.out, file=sys.stderr)
    store.log("operator", "report_generated", plan.plan_id, examiner_facing=bool(args.examiner))
    store.close()
    return 0


def cmd_gaps(args) -> int:
    store = Store(args.db)
    rows = store.open_gaps(args.plan)
    if not rows:
        print("No open gaps.")
    for g in rows:
        print(g["gap_id"] + "  " + g["severity"].upper().ljust(9) + g["title"])
        print("        owner: " + (g["assigned_to"] or "-") + " | due: "
              + (g["deadline"] or "-") + " | source: " + (g["source"] or "-"))
    if args.notifications:
        print("\nNotification plan (nothing is sent by this command):")
        for n in triage.notification_plan(rows):
            print("  " + n["gap_id"] + " -> " + ", ".join(n["recipients"])
                  + " [" + n["channel"] + "] due " + n["deadline"]
                  + (" escalate " + n["escalate_if_silent"] if n["escalate_if_silent"] else ""))
    store.close()
    return 0


def cmd_decide(args) -> int:
    store = Store(args.db)
    try:
        store.decide_gap(args.gap_id, actor=args.actor, decision=args.decision,
                         note=args.note, new_deadline=args.deadline or "",
                         reason_code=args.reason or "")
    except ValueError as e:
        print("REFUSED: " + str(e), file=sys.stderr)
        if args.decision == "reject":
            print("", file=sys.stderr)
            print("Reason codes:", file=sys.stderr)
            for code, desc in sorted(Store.REJECTION_REASONS.items()):
                print("  " + code.ljust(20) + desc, file=sys.stderr)
        return 2
    print(args.gap_id + " -> " + args.decision + " by " + args.actor
          + (" (" + args.reason + ")" if args.reason else ""))
    if args.decision == "reject":
        print("Recorded as a false positive. It will appear in `calibrate`.")
    store.close()
    return 0


def cmd_calibrate(args) -> int:
    store = Store(args.db)
    stats = calibrate.collect(store, args.plan)
    text = calibrate.render(stats)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("Written to " + args.out, file=sys.stderr)
    store.log("operator", "calibration_run", args.plan or "(all plans)",
              decisions=stats["total_decisions"], rejected=stats["rejected"])
    store.close()
    return 0


def cmd_audit_verify(args) -> int:
    store = Store(args.db)
    ok, broken = store.verify_chain()
    n = store.db.execute("SELECT COUNT(*) c FROM audit_events").fetchone()["c"]
    head = store.read_head()
    if ok:
        # "Intact" with no head checkpoint is a narrower claim than it sounds.
        # The chain proves the surviving rows link to each other; only the
        # checkpoint proves none were removed from the END. Reporting a clean
        # verification without it is the same overclaim finding 17 was about,
        # one level up in the CLI.
        if head is None:
            print("AUDIT CHECKPOINT MISSING - the " + str(n) + " events present are internally "
                  "consistent, but with no head checkpoint a DELETED TAIL cannot be detected. "
                  "This is NOT a clean verification. A checkpoint is written on the next "
                  "audited action; until then completeness is unproven.", file=sys.stderr)
            store.close()
            return 4
        print("Audit chain intact across " + str(n) + " events "
              "(head checkpoint at seq " + str(head.get("seq")) + ").")
        store.close()
        return 0
    print("AUDIT CHAIN BROKEN at seq " + str(broken) + " - the log has been altered "
          "or a row was deleted.", file=sys.stderr)
    store.close()
    return 3


def cmd_audit_log(args) -> int:
    store = Store(args.db)
    for r in store.db.execute("SELECT * FROM audit_events ORDER BY seq DESC LIMIT ?",
                              (args.limit,)):
        print(r["ts"] + "  " + r["actor"].ljust(12) + r["action"].ljust(24)
              + (r["subject"] or "") + "  " + r["detail_json"])
    store.close()
    return 0



def cmd_decompose(args) -> int:
    from . import decompose as dec

    text = Path(args.path).read_text(encoding="utf-8")
    print("Decomposing " + args.source + " (" + str(len(text)) + " chars)...")

    def progress(n, total, stats):
        print("  chunk " + str(n) + "/" + str(total) + " - "
              + str(stats["kept"]) + " kept, "
              + str(stats["quote_failed"]) + " discarded on quote", file=sys.stderr)

    records, stats = dec.decompose(
        text, source_name=args.source, as_of_version=args.version or "",
        req_id_prefix=args.prefix, progress=progress)

    out = Path(args.out)
    out.write_text(json.dumps(dec.to_index(records, args.source, stats),
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("")
    print("Candidates drafted: " + str(stats["candidates"]))
    print("  kept:                 " + str(stats["kept"]))
    print("  discarded (no quote): " + str(stats["quote_failed"]))
    print("  duplicates:           " + str(stats["duplicates"]))
    if stats["candidates"] and stats["quote_failed"] / stats["candidates"] > 0.2:
        print("")
        print("  WARNING: more than a fifth of candidates could not be quoted back to")
        print("  the source. The model is paraphrasing. Treat this output with")
        print("  suspicion and validate every record especially carefully.")
    print("")
    print("Wrote " + str(out) + " - ALL RECORDS UNVALIDATED.")
    print("Next: python -m openbcdr validate --file " + str(out) + " --validator YOUR_NAME")

    store = Store(args.db)
    store.log("operator", "index_decomposed", args.source,
              kept=stats["kept"], discarded=stats["quote_failed"], out=str(out))
    store.close()
    return 0


def cmd_validate(args) -> int:
    path = Path(args.file)
    store = Store(args.db)
    try:
        tally = validate_index.walk(
            path, validator=args.validator,
            ask=lambda prompt: input(prompt),
            say=print, limit=args.limit, audit=store)
    except (KeyboardInterrupt, EOFError):
        print("")
        print("Stopped. Everything confirmed so far is saved.")
        store.close()
        return 0
    print("")
    print("Validated this session: " + str(tally["validated"])
          + "   Skipped: " + str(tally["skipped"])
          + "   Still pending: " + str(tally["remaining"]))
    store.close()
    return 0


def cmd_standards_status(args) -> int:
    if args.file:
        _env, records = validate_index.load_file(Path(args.file))
    else:
        records = standards.load()
    print(validate_index.status(records))
    return 0



def cmd_register_plan(args) -> int:
    """Store a plan's raw text with no API call, so quotes can be verified."""
    from .models import PlanExtract

    text = Path(args.path).read_text(encoding="utf-8")
    try:
        from .boundary import enforce
        enforce(text, args.mode, args.attest_synthetic)
    except BoundaryViolation as e:
        print("BOUNDARY REFUSAL: " + str(e), file=sys.stderr)
        return 2

    store = Store(args.db)
    store.log("operator", "boundary_attestation", str(args.path),
              mode=args.mode, attested=bool(args.attest_synthetic))
    extract = PlanExtract(plan_id=args.plan, plan_version=args.plan_version or "",
                          scope_name=args.scope_name or "")
    pv = store.save_plan_version(plan_id=args.plan, version=args.plan_version or "",
                                 mode=args.mode, source_path=str(args.path),
                                 raw_text=text, extract=extract.model_dump(mode="json"))
    print("Registered " + args.plan + " as plan_version " + str(pv) + " (text only).")
    print("Structured fields are EMPTY - `coherence` needs `ingest`, which calls the API.")
    print("This is enough for `import-findings` to verify evidence quotes.")
    store.close()
    return 0


def cmd_import_findings(args) -> int:
    payload = import_findings.load_payload(Path(args.file))
    plan_id = args.plan or payload.get("plan_id")

    store = Store(args.db)
    try:
        row = store.latest_plan_version(plan_id)
    except KeyError:
        print("No plan text on file for " + str(plan_id) + ".", file=sys.stderr)
        print("Evidence quotes cannot be verified without it, and importing without", file=sys.stderr)
        print("verification would defeat the point. Register the plan text first:", file=sys.stderr)
        print("", file=sys.stderr)
        print("  python -m openbcdr register-plan <plan.md> --plan " + str(plan_id)
              + " --mode internal", file=sys.stderr)
        store.close()
        return 2

    reqs = _in_scope(args)
    known = {r.req_id for r in reqs}
    findings, problems = import_findings.to_findings(payload, row["raw_text"], known)

    store.save_findings(int(row["id"]), findings)
    created, duplicate = triage.persist(
        store, triage.gaps_from_findings(findings, reqs, plan_id))

    coherence_ignored = len(payload.get("coherence_findings") or [])
    store.log("operator", "findings_imported", plan_id,
              accepted=len(findings), gaps_opened=created, duplicates=duplicate,
              rejected={k: len(v) for k, v in problems.items() if v},
              source_file=str(args.file))

    print(import_findings.render_summary(payload, findings, problems, created,
                                         duplicate, coherence_ignored))
    store.close()
    return 0


def cmd_onboard(args) -> int:
    """Build or update an organisation profile from plain answers (no API calls)."""
    from . import onboard
    if args.update and args.starter:
        raise SystemExit("use --starter for a new profile or --update for an existing one, not both")
    if not args.update and not args.starter:
        raise SystemExit("choose a starting point: --starter bank|small-business, or --update <profile>")
    base = onboard.load_base(starter=args.starter, existing=Path(args.update) if args.update else None)
    sections = set(args.section) if args.section else None
    if sections and not sections <= set(onboard.SECTIONS):
        raise SystemExit("--section must be one of: " + ", ".join(onboard.SECTIONS))

    if args.export:
        exp = Path(args.export)
        if exp.exists() and not args.force:
            raise SystemExit(str(exp) + " already exists. Choose a new file name, or --force to replace it.")
        if args.update:
            # An export from an existing profile carries that organisation's answers.
            onboard.check_out_path(exp)
        exp.write_text(
            onboard.export_markdown(base, args.starter or args.update), encoding="utf-8")
        print("Questionnaire written to " + args.export + ". Fill in the Answer: lines, then run")
        print("  python -m openbcdr onboard " + ("--starter " + args.starter if args.starter
                                                  else "--update " + args.update)
              + " --import " + args.export + " --out org/<your-org>.local.json")
        return 0

    out = Path(args.out or args.update or "")
    if not str(out):
        raise SystemExit("--out is required (e.g. org/acme.local.json)")
    onboard.check_out_path(out)
    is_target = bool(args.update) and out.resolve() == Path(args.update).resolve()
    if out.exists() and not is_target and not args.force:
        raise SystemExit(str(out) + " already exists. Use --update to change it, or --force to replace it.")

    if args.import_path:
        answers = onboard.import_markdown(Path(args.import_path).read_text(encoding="utf-8-sig"))
    else:
        answers = onboard.interactive(base, sections, starter=bool(args.starter))
    profile, problems = onboard.apply_answers(base, answers, sections, starter=bool(args.starter))
    if problems:
        print("Nothing written. Fix these answers and try again:", file=sys.stderr)
        for pr in problems:
            print("  - " + pr, file=sys.stderr)
        return 2
    onboard.write(profile, out)
    print("Profile written to " + str(out) + " (its .local. name keeps it out of git).")
    if profile.defaults_used:
        print("Kept the starting value for: " + ", ".join(profile.defaults_used))
    print("Use it with: python -m openbcdr --org " + str(out) + " <command>")
    return 0


def cmd_instructions(args) -> int:
    """Fill AGENT-INSTRUCTIONS.md from the organisation profile (no API calls)."""
    from . import instructions, onboard
    if args.org_profile is None:
        raise SystemExit("instructions needs --org <profile> (before the command)")
    text = instructions.render(args.org_profile)
    if args.out:
        out = onboard.check_out_path(Path(args.out))
        if out.exists() and not args.force:
            raise SystemExit(str(out) + " already exists. Use --force to replace it.")
        out.write_text(text, encoding="utf-8")
        print("Instructions written to " + str(out) + ". Paste them into the agent's instructions field.")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="openbcdr", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(config.DB_PATH))
    p.add_argument("--org", help="organisation profile JSON (see org/ for the starters); "
                                 "omit it and the built-in defaults apply")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="extract a plan into the knowledge base")
    i.add_argument("path")
    i.add_argument("--mode", choices=["sandbox", "internal"], required=True)
    i.add_argument("--attest-synthetic", action="store_true",
                   help="required in sandbox mode: attest this document is synthetic or sanitized")
    i.add_argument("--plan-id", default="")
    i.set_defaults(func=cmd_ingest)

    c = sub.add_parser("coherence", help="deterministic checks - no API calls, no cost")
    c.add_argument("--plan", required=True)
    c.add_argument("--verbose", action="store_true")
    c.add_argument("--open-gaps", action="store_true")
    c.add_argument("--not-finra-member", action="store_true")
    c.set_defaults(func=cmd_coherence)

    a = sub.add_parser("analyze", help="compliance coverage against the standards index")
    a.add_argument("--plan", required=True)
    a.add_argument("--profile", help="JSON file of applicability tags")
    a.add_argument("--open-gaps", action="store_true")
    a.set_defaults(func=cmd_analyze)

    r = sub.add_parser("report", help="render the compliance report")
    r.add_argument("--plan", required=True)
    r.add_argument("--profile")
    r.add_argument("--out")
    r.add_argument("--examiner", action="store_true",
                   help="refuse to render unless every requirement in scope is validated")
    r.add_argument("--not-finra-member", action="store_true")
    r.set_defaults(func=cmd_report)

    g = sub.add_parser("gaps", help="list the open gap registry")
    g.add_argument("--plan")
    g.add_argument("--notifications", action="store_true")
    g.set_defaults(func=cmd_gaps)

    d = sub.add_parser("decide", help="record a risk-owner decision on a gap")
    d.add_argument("gap_id")
    d.add_argument("--actor", required=True)
    d.add_argument("--decision", required=True,
                   choices=sorted(Store.DECISIONS))
    d.add_argument("--note", default="",
                   help="required for reject: what the agent got wrong")
    d.add_argument("--deadline", default="")
    d.add_argument("--reason", default="", choices=sorted(Store.REJECTION_REASONS) + [""],
                   help="required for reject; invalid for any other decision")
    d.set_defaults(func=cmd_decide)

    cb = sub.add_parser("calibrate",
                        help="what the risk team's decisions say about agent precision")
    cb.add_argument("--plan")
    cb.add_argument("--out")
    cb.set_defaults(func=cmd_calibrate)


    dc = sub.add_parser("decompose",
                        help="draft candidate requirements from source material (calls the API)")
    dc.add_argument("path", help="plain-text file of the booklet, rule or standard")
    dc.add_argument("--source", required=True,
                    help='source name as it should be cited, e.g. "FFIEC BCM Booklet"')
    dc.add_argument("--version", default="", help="edition or revision, e.g. 2019")
    dc.add_argument("--prefix", default="REQ", help="req_id prefix")
    dc.add_argument("--out", required=True, help="output index JSON")
    dc.set_defaults(func=cmd_decompose)

    vd = sub.add_parser("validate",
                        help="walk unvalidated records one at a time (no API calls)")
    vd.add_argument("--file", required=True)
    vd.add_argument("--validator", required=True, help="who is confirming these records")
    vd.add_argument("--limit", type=int, help="stop after this many records")
    vd.set_defaults(func=cmd_validate)

    ss = sub.add_parser("standards-status", help="index coverage and staleness by source")
    ss.add_argument("--file", help="defaults to the configured standards directory")
    ss.set_defaults(func=cmd_standards_status)


    rp = sub.add_parser("register-plan",
                        help="store a plan's raw text with no API call (enables quote checks)")
    rp.add_argument("path")
    rp.add_argument("--plan", required=True, help="plan_id to file it under")
    rp.add_argument("--plan-version", default="")
    rp.add_argument("--scope-name", default="")
    rp.add_argument("--mode", choices=["sandbox", "internal"], required=True)
    rp.add_argument("--attest-synthetic", action="store_true")
    rp.set_defaults(func=cmd_register_plan)

    imp = sub.add_parser("import-findings",
                         help="import findings from a prompt-only agent, verifying evidence")
    imp.add_argument("file", help="JSON (or a fenced JSON block) emitted by the agent")
    imp.add_argument("--plan", help="override the plan_id in the payload")
    imp.add_argument("--profile")
    imp.set_defaults(func=cmd_import_findings)

    ob = sub.add_parser("onboard",
                        help="build or update your organisation profile from plain questions")
    ob.add_argument("--starter", choices=["bank", "small-business"],
                    help="start a new profile from this starter")
    ob.add_argument("--update", help="change an existing profile (keeps every unasked answer)")
    ob.add_argument("--section", action="append",
                    help="only ask this section (repeatable): who, rules, running, speed, people, "
                         "deadlines, upkeep, words, template")
    ob.add_argument("--out", help="where to write the profile; the name must contain .local.")
    ob.add_argument("--export", help="write a fill-in questionnaire document instead of asking")
    ob.add_argument("--import", dest="import_path", help="read answers from a filled-in document")
    ob.add_argument("--force", action="store_true", help="replace an existing --out file")
    ob.set_defaults(func=cmd_onboard)

    ins = sub.add_parser("instructions",
                         help="fill the agent instructions from your organisation profile")
    ins.add_argument("--out", help="write to a file; the name must contain .local. "
                                   "(it holds your organisation's details)")
    ins.add_argument("--force", action="store_true")
    ins.set_defaults(func=cmd_instructions)

    v = sub.add_parser("audit-verify", help="verify the hash chain of the audit trail")
    v.set_defaults(func=cmd_audit_verify)

    lg = sub.add_parser("audit-log", help="print recent audit events")
    lg.add_argument("--limit", type=int, default=25)
    lg.set_defaults(func=cmd_audit_log)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.org_profile = None
    if args.org:
        args.org_profile = org_profile.load(args.org)
        org_profile.apply(args.org_profile)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
