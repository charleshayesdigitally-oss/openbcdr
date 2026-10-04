"""Organisation profiles (Phase 1a). Offline, no API key.

Proves: both starters load; with no profile nothing changes; the bank starter
reproduces today's defaults exactly; a profile really changes routing,
deadlines, staleness and test rules; and bad profiles are refused with a reason.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

from openbcdr import cli, config, standards, triage  # noqa: E402
from openbcdr import profile as org_profile  # noqa: E402
from openbcdr.analyzers import coherence  # noqa: E402
from openbcdr.models import Contact, PlanExtract, TestRecord  # noqa: E402
from load_sample import SAMPLE  # noqa: E402

BANK = ROOT / "org" / "starter-bank.json"
SMALL = ROOT / "org" / "starter-small-business.json"
TODAY = date(2026, 10, 4)
failures = 0

# Snapshot the defaults so every test can restore them (apply() mutates config).
DEFAULTS = {k: copy.deepcopy(getattr(config, k)) for k in (
    "ROUTING", "SLA", "CONTACT_STALE_MEDIUM_DAYS", "CONTACT_STALE_HIGH_DAYS", "PLAN_STALE_MONTHS")}


def restore() -> None:
    for k, v in DEFAULTS.items():
        setattr(config, k, copy.deepcopy(v))


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else "  -- " + detail))
    if not ok:
        failures += 1


def refused(data: dict) -> bool:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        try:
            org_profile.load(p)
        except SystemExit:
            return True
    return False


def ns(**kw) -> argparse.Namespace:
    base = {"not_finra_member": False, "org_profile": None}
    base.update(kw)
    return argparse.Namespace(**base)


print("\n[P1] starters load and validate")
bank = org_profile.load(BANK)
small = org_profile.load(SMALL)
check("bank starter loads", bank.name.startswith("Example"))
check("small-business starter loads", small.name.startswith("Example"))
check("starters say they are fictional", "fictional" in bank.name and "fictional" in small.name)

print("\n[P2] no profile = no change")
check("default applicability unchanged", cli._load_profile(None) == cli.DEFAULT_PROFILE)
check("coherence rules unchanged without --org", cli._test_rules(ns()) == {"finra_member": True})
check("--not-finra-member still works without --org",
      cli._test_rules(ns(not_finra_member=True)) == {"finra_member": False})

print("\n[P3] the bank starter reproduces today's defaults exactly")
check("bank applicability == built-in default", bank.applicability == cli.DEFAULT_PROFILE)
check("bank routing == built-in routing", bank.routing == DEFAULTS["ROUTING"])
check("bank sla == built-in sla", {k: tuple(v) for k, v in bank.sla.items()} == DEFAULTS["SLA"])
check("bank staleness thresholds == built-in",
      (bank.thresholds.contact_stale_medium_days, bank.thresholds.contact_stale_high_days,
       bank.thresholds.plan_stale_months)
      == (DEFAULTS["CONTACT_STALE_MEDIUM_DAYS"], DEFAULTS["CONTACT_STALE_HIGH_DAYS"],
          DEFAULTS["PLAN_STALE_MONTHS"]))
def fields(issues):
    return [tuple(getattr(i, f) for f in i.__slots__) for i in issues]


old_test = PlanExtract(plan_id="T", tests=[TestRecord(test_date=date(2025, 1, 10))])
for label, plan in (("sample plan", SAMPLE), ("plan with a stale test", old_test)):
    for flag in (False, True):
        before = fields(coherence.run_all(plan, TODAY, **cli._test_rules(ns(not_finra_member=flag))))
        org_profile.apply(bank)
        after = fields(coherence.run_all(
            plan, TODAY, **cli._test_rules(ns(org_profile=bank, not_finra_member=flag))))
        restore()
        check(label + ", --not-finra-member=" + str(flag)
              + ": bank starter gives identical findings, every field", before == after,
              str(set(before) ^ set(after)))
check("baseline: built-in scope is still the 73 public bank-scope records",
      len(standards.applicable(standards.load(), cli.DEFAULT_PROFILE)) == 73)

print("\n[P4] a profile really changes the run")
org_profile.apply(small)
check("routing follows the profile", triage.route("critical") == ["risk_owner"])
check("deadline follows the profile (5 business days)",
      triage.deadline_for("critical", date(2026, 10, 5)) == date(2026, 10, 12))
check("plan staleness follows the profile", config.PLAN_STALE_MONTHS == 12)
restore()
check("restore really restores", triage.route("critical") == ["risk_owner", "compliance_officer"])
sb_reqs = standards.applicable(standards.load(), cli._load_profile(None, small))
check("small business gets only any_organization records (NIST)",
      len(sb_reqs) > 0 and all("any_organization" in r.applicability for r in sb_reqs),
      str(len(sb_reqs)))
check("baseline: small business scope is the 6 NIST records", len(sb_reqs) == 6, str(len(sb_reqs)))
check("small business gets no FFIEC or FINRA records",
      not any(r.source.upper().startswith(("FFIEC", "FINRA")) for r in sb_reqs))
bank_reqs = standards.applicable(standards.load(), cli._load_profile(None, bank))
check("bank scope unchanged by the new tag",
      len(bank_reqs) == len(standards.applicable(standards.load(), cli.DEFAULT_PROFILE)))
sb_rules = cli._test_rules(ns(org_profile=small))
sb_issues = [i for i in coherence.run_all(old_test, TODAY, **sb_rules) if i.check == "test_annual"]
check("small business: stale test flagged by the profile rule",
      len(sb_issues) == 1 and sb_issues[0].severity == "high")
check("small business: the finding doesn't cite FINRA",
      sb_issues and "FINRA" not in sb_issues[0].detail)
bank_issues = [i for i in coherence.run_all(old_test, TODAY, **cli._test_rules(ns(org_profile=bank)))
               if i.check == "test_annual"]
check("bank: stale test still cites FINRA 4370 as critical",
      len(bank_issues) == 1 and bank_issues[0].severity == "critical"
      and "FINRA" in bank_issues[0].detail)
no_annual = small.model_copy(deep=True)
no_annual.thresholds.annual_test_required = False
check("profile with annual_test_required=false raises nothing",
      not [i for i in coherence.run_all(old_test, TODAY, **cli._test_rules(ns(org_profile=no_annual)))
           if i.check == "test_annual"])

# Behaviour, not stored values: the thresholds must reach the checks themselves.
aging = PlanExtract(plan_id="A", last_approved=date(2025, 8, 1),
                    contacts=[Contact(name="X", last_verified=date(2026, 8, 20)),
                              Contact(name="Y", last_verified=date(2026, 7, 26))])


def fresh(plan):
    return sorted((i.check, i.severity) for i in coherence.run_all(plan, TODAY)
                  if i.check in ("plan_freshness", "contact_currency"))


base = fresh(aging)
tight = small.model_copy(deep=True)
tight.thresholds.contact_stale_medium_days = 30
tight.thresholds.contact_stale_high_days = 60
org_profile.apply(tight)
tightened = fresh(aging)
restore()
check("a 14-month-old plan is only 'past annual review' by default",
      ("plan_freshness", "medium") in base and ("plan_freshness", "high") not in base, str(base))
check("the profile's 12-month staleness makes the same plan 'stale' (high)",
      ("plan_freshness", "high") in tightened, str(tightened))
check("by default, 45- and 70-day-old contacts are not stale",
      not [c for c in base if c[0] == "contact_currency"], str(base))
check("the profile's medium contact threshold reaches the check (45 days > 30)",
      ("contact_currency", "medium") in tightened, str(tightened))
check("the profile's high contact threshold reaches the check (70 days > 60)",
      ("contact_currency", "high") in tightened, str(tightened))

print("\n[P5] bad profiles are refused, with a reason")
good = json.loads(SMALL.read_text(encoding="utf-8"))
check("control: the unmodified starter is accepted", not refused(good))


def bad(mutate) -> dict:
    d = copy.deepcopy(good)
    mutate(d)
    return d


check("unknown applicability tag", refused(bad(lambda d: d["applicability"].update({"al_banks": True}))))
check("no applicability tag true", refused(bad(lambda d: d.update(applicability={"any_organization": False}))))
check("routing to a role the code can't reach",
      refused(bad(lambda d: d["routing"].update(critical=["ceo"]))))
check("routing missing a severity", refused(bad(lambda d: d["routing"].pop("high"))))
check("empty routing list", refused(bad(lambda d: d["routing"].update(low=[]))))
check("sla escalation not before deadline", refused(bad(lambda d: d["sla"].update(critical=[5, 5]))))
check("sla escalation with no deadline", refused(bad(lambda d: d["sla"].update(low=[None, 3]))))
check("sla missing a severity", refused(bad(lambda d: d["sla"].pop("medium"))))
check("duplicate tier names", refused(bad(lambda d: d["tiers"].append(dict(d["tiers"][0])))))
check("non-positive RTO", refused(bad(lambda d: d["tiers"][0].update(rto_hours=0))))
check("high staleness not above medium",
      refused(bad(lambda d: d["thresholds"].update(contact_stale_high_days=90))))
check("unknown role in role_titles", refused(bad(lambda d: d["role_titles"].update(ceo="Boss"))))
check("missing name", refused(bad(lambda d: d.pop("name"))))
check("wrong schema version", refused(bad(lambda d: d.update(schema_version=2))))
check("misspelled nested key", refused(bad(lambda d: d["thresholds"].update(
    {"annual_test_requred": False}))))
check("misspelled top-level key", refused(bad(lambda d: d.update(regulator=["x"]))))
check("misspelled tier key", refused(bad(lambda d: d["tiers"][0].update(rto_hour=4))))
check("misspelled template key", refused(bad(lambda d: d["plan_template"][0].update(covers=["x"]))))
check("negative escalation", refused(bad(lambda d: d["sla"].update(critical=[5, -1]))))
check("zero escalation", refused(bad(lambda d: d["sla"].update(critical=[5, 0]))))
sifi_only = small.model_copy(deep=True)
sifi_only.applicability = {"sifi": True}
try:
    cli._in_scope(ns(profile=None, org_profile=sifi_only))
    ok = False
except SystemExit as e:
    ok = "empty scope" in str(e)
check("a profile that selects zero standards is refused before any report", ok)
with tempfile.TemporaryDirectory() as td:
    junk = Path(td) / "junk.json"
    junk.write_text("{not json", encoding="utf-8")
    try:
        org_profile.load(junk)
        ok = False
    except SystemExit as e:
        ok = "not valid JSON" in str(e)
    check("invalid JSON is refused with a readable message", ok)

print("\n[P6] the real CLI applies --org")
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "t.sqlite3"
    subprocess.run([sys.executable, str(ROOT / "samples" / "load_sample.py"), "--db", str(db)],
                   check=True, capture_output=True)
    strict = json.loads(SMALL.read_text(encoding="utf-8"))
    strict["thresholds"].update(contact_stale_medium_days=1, contact_stale_high_days=2,
                                plan_stale_months=1)
    sp = Path(td) / "strict.local.json"
    sp.write_text(json.dumps(strict), encoding="utf-8")

    def cli_run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "openbcdr", *args],
                              cwd=ROOT, capture_output=True, text=True)

    def run(*extra: str) -> tuple[int, str]:
        r = cli_run(*extra, "--db", str(db), "coherence", "--plan", "APP_PAYPROC_v4", "--verbose")
        return r.returncode, r.stdout

    plain, banked, strict_out = run(), run("--org", str(BANK)), run("--org", str(sp))
    check("CLI: every coherence run exits 0", plain[0] == banked[0] == strict_out[0] == 0,
          str((plain[0], banked[0], strict_out[0])))
    check("CLI: bank starter output == no-org output, in full", plain[1] == banked[1])
    check("CLI: a strict profile changes the findings", strict_out[1] != plain[1])

    empty = json.loads(SMALL.read_text(encoding="utf-8"))
    empty["applicability"] = {"sifi": True}
    ep = Path(td) / "empty.local.json"
    ep.write_text(json.dumps(empty), encoding="utf-8")
    payload = Path(td) / "findings.json"
    payload.write_text(json.dumps({"plan_id": "APP_PAYPROC_v4", "findings": []}), encoding="utf-8")
    for cmd in (["report", "--plan", "APP_PAYPROC_v4"],
                ["analyze", "--plan", "APP_PAYPROC_v4"],
                ["import-findings", str(payload)]):
        r = cli_run("--org", str(ep), "--db", str(db), *cmd)
        check("CLI " + cmd[0] + ": an empty-scope profile is refused, nothing rendered",
              r.returncode != 0 and "empty scope" in r.stderr and "Score" not in r.stdout,
              "rc=" + str(r.returncode) + " " + r.stderr[-200:])

restore()
if failures:
    print("\n" + str(failures) + " profile check(s) FAILED")
    raise SystemExit(1)
print("\nAll profile checks passed.")
