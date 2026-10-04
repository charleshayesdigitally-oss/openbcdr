"""The onboarding questionnaire (Phase 1b). Offline, no API key.

Proves: one question list drives export, import and interactive answering; every
starter value shown to a user can be read back; bad answers write nothing; the
answers are validated as a whole profile; --update changes only what it asks;
and a profile can only be written under a .local. name.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openbcdr import onboard, standards  # noqa: E402
from openbcdr.profile import OrgProfile  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else "  -- " + str(detail)))
    if not ok:
        failures += 1


def fill(md: str, answers: dict[str, str]) -> str:
    out, cur = [], None
    for line in md.splitlines():
        m = re.match(r"^###\s+(\S+)", line)
        if m:
            cur = m.group(1)
        if line.startswith("Answer:") and cur in answers:
            line = "Answer: " + answers[cur]
        out.append(line)
    return "\n".join(out)


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "openbcdr", *args], cwd=ROOT,
                          capture_output=True, text=True)


def new(base, answers, sections=None):
    return onboard.apply_answers(base, answers, sections, starter=True)


def update(base, answers, sections=None):
    return onboard.apply_answers(base, answers, sections, starter=False)


bank = onboard.load_base("bank")
small = onboard.load_base("small-business")

print("\n[O1] one question list, every value round-trips")
ids = [q.id for q in onboard.QUESTIONS]
check("question ids are unique", len(ids) == len(set(ids)))
check("every question belongs to a known section",
      all(q.section in onboard.SECTIONS for q in onboard.QUESTIONS))
for label, base in (("bank", bank), ("small-business", small)):
    bad = []
    for q in onboard.QUESTIONS:
        shown = onboard._fmt(q.get(base))
        if not shown:
            continue
        d = json.loads(json.dumps(base))
        d.setdefault("critical_services", [])
        try:
            q.put(d, q.parse(shown))
        except onboard.AnswerError as e:
            bad.append(q.id + " (" + str(e) + ")")
            continue
        if q.get(d) != q.get(base) and q.id != "plan_sections":
            bad.append(q.id)
    check(label + ": every 'Current:' value parses back to the same value", not bad, bad)

# Every setter must really write: give each question a value different from
# the starter and read it back.
DIFFERENT = {
    "name": "Other (synthetic)", "sector": "other", "size": "9", "regulators": "Reg A; Reg B",
    "is_bank": "yes", "finra": "yes", "fed": "yes", "sifi": "yes",
    "critical_services": "Svc 1; Svc 2", "tiers": "T1 | 2 | 1 | d1; T2 | 9",
    "critical_days": "7", "critical_escalate": "3", "high_days": "11", "medium_days": "13",
    "contact_review_days": "17", "contact_overdue_days": "19", "plan_review_months": "23",
    "annual_test": "no", "terminology": "aa = plan; bb = finding",
    "plan_sections": "S1; S2", **{"role_" + r: "Title " + r for r in onboard.FRAMEWORK_ROLES}}
check("every question has a test value", set(DIFFERENT) == set(ids), set(ids) ^ set(DIFFERENT))
dead = []
for q in onboard.QUESTIONS:
    d = json.loads(json.dumps(small))
    want = q.parse(DIFFERENT[q.id])
    q.put(d, want)
    if q.get(d) != want or q.get(d) == q.get(small):
        dead.append(q.id)
check("every question's answer actually lands in the profile", not dead, dead)
md = onboard.export_markdown(small, "small-business")
check("export lists every question exactly once",
      all(md.count("### " + i + "\n") == 1 for i in ids))
check("export carries a no-confidential-data warning", "never paste anything confidential" in md)

print("\n[O2] answering only the name gives the starter back")
p, problems = new(small, onboard.import_markdown(fill(md, {"name": "Acme (synthetic)"})))
check("valid profile, no problems", p is not None and not problems, problems)
check("name taken from the answer", p and p.name == "Acme (synthetic)")
if p:
    got, want = p.model_dump(), OrgProfile.model_validate(small).model_dump()
    for k in ("name", "defaults_used"):
        got.pop(k)
        want.pop(k)
    check("everything else equals the starter, field for field", got == want,
          {k for k in got if got[k] != want.get(k)})
check("every unanswered question is recorded in defaults_used",
      p and set(p.defaults_used) == set(ids) - {"name"}, p and p.defaults_used)
_, problems = new(small, onboard.import_markdown(md))
check("the placeholder name can't be kept", any(x.startswith("name:") for x in problems), problems)

print("\n[O3] answers change the profile")
answers = {"name": "Acme (synthetic)", "critical_services": "Orders; Payments",
           "tiers": "Must run | 4 | 1 | orders; Later | 96",
           "role_risk_owner": "Owner", "terminology": "playbook = plan; drill = exercise",
           "critical_days": "3", "critical_escalate": "1", "contact_review_days": "45",
           "contact_overdue_days": "100", "annual_test": "no",
           "plan_sections": "What this plan covers; Brand new section"}
p, problems = new(small, answers)
check("valid", p is not None, problems)
if p:
    check("critical services in order", p.critical_services == ["Orders", "Payments"])
    check("tiers parsed, RPO optional", [(t.name, t.rto_hours, t.rpo_hours) for t in p.tiers]
          == [("Must run", 4.0, 1.0), ("Later", 96.0, None)])
    check("terminology pairs", p.terminology == {"playbook": "plan", "drill": "exercise"})
    check("deadline and escalation", tuple(p.sla["critical"]) == (3, 1))
    check("thresholds", (p.thresholds.contact_stale_medium_days, p.thresholds.contact_stale_high_days,
                         p.thresholds.annual_test_required) == (45, 100, False))
    kept = {s.title: s.covers_tags for s in p.plan_template}
    check("a kept section keeps its covered tags, a new one starts empty",
          kept.get("What this plan covers") == [s["covers_tags"] for s in small["plan_template"] if s["title"] == "What this plan covers"][0] and kept.get("Brand new section") == [])

print("\n[O4] the rules section decides which standards apply")
p, _ = new(small, {"name": "X (synthetic)", "is_bank": "yes"})
check("small business that answers 'bank: yes' gets bank scope, not any_organization",
      p and p.applicability == {"all_banks": True}, p and p.applicability)
p, _ = new(bank, {"name": "X (synthetic)", "is_bank": "no", "finra": "no",
                                    "fed": "no", "sifi": "no"})
check("bank starter answered 'no' to every rule falls back to any_organization",
      p and p.applicability == {"any_organization": True}, p and p.applicability)
check("...and that scope is the general (NIST) records",
      p and len(standards.applicable(standards.load(), p.applicability)) == 6)

print("\n[O5] bad answers write nothing and say why")
for label, ans, expect in (
        ("yes/no answer", {"annual_test": "maybe"}, "annual_test"),
        ("whole number", {"high_days": "ten"}, "high_days"),
        ("zero", {"medium_days": "0"}, "medium_days"),
        ("tier format", {"tiers": "Must run"}, "tiers"),
        ("tier hours as text", {"tiers": "Must run | soon"}, "tiers"),
        ("terminology format", {"terminology": "playbook"}, "terminology"),
        ("escalation after the deadline", {"critical_days": "3", "critical_escalate": "5"}, "fit together"),
        ("overdue not after review", {"contact_review_days": "90", "contact_overdue_days": "60"},
         "fit together"),
        ("infinite recovery time", {"tiers": "Critical | inf | inf"}, "tiers"),
        ("a term mapped twice", {"terminology": "playbook = plan; playbook = exercise"}, "terminology"),
        ("Fed member alone (no standard applies)", {"fed": "yes"}, "no standard"),
        ("SIFI alone (no standard applies)", {"sifi": "yes"}, "no standard")):
    p, problems = new(small, dict(ans, name="X (synthetic)"))
    check("refused: " + label, p is None and any(expect in x for x in problems), problems)
p, problems = new(bank, {"name": "X (synthetic)", "regulators": "none"})
check("'none' clears a list (bank regulators)", p and p.regulators == [], problems)
p, problems = new(small, {"name": "X (synthetic)", "terminology": "playbook = plan"})
p2, _ = update(p.model_dump(mode="json"), {"terminology": "none"}, sections={"words"})
check("'none' clears word pairs", p2 and p2.terminology == {}, p2 and p2.terminology)
check("a ';' inside a tier description stays in the description",
      onboard._tiers("Must run | 4 | 1 | orders; payments")[0]["description"] == "orders; payments")

print("\n[O6] --update changes only the section it asks")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    first, _ = new(small, answers)
    prof = td / "acme.local.json"
    onboard.write(first, prof)
    base = onboard.load_base(existing=prof)
    upd, problems = update(base, {"role_plan_owner": "Office manager",
                                                 "tiers": "IGNORED | 1"}, sections={"people"})
    check("update is valid", upd is not None, problems)
    if upd:
        check("asked section changed", upd.role_titles["plan_owner"] == "Office manager")
        check("an answer for an unasked section is ignored", upd.tiers == first.tiers)
        same = upd.model_dump()
        before = first.model_dump()
        for k in ("role_titles", "defaults_used"):
            same.pop(k)
            before.pop(k)
        check("everything outside the section is identical", same == before)
        check("an answer given earlier stays an answer when left blank on update",
              "role_risk_owner" not in upd.defaults_used, upd.defaults_used)
        check("a starter default stays a default when left blank on update",
              "role_bcdr_pm" in upd.defaults_used, upd.defaults_used)
    mixed = json.loads(json.dumps(first.model_dump(mode="json")))
    mixed["applicability"] = {"all_banks": True, "any_organization": True}
    upd, problems = update(mixed, {"role_plan_owner": "X"}, sections={"people"})
    check("a people-only update leaves applicability exactly as it was",
          upd and upd.applicability == {"all_banks": True, "any_organization": True},
          upd and upd.applicability)
    trimmed = td / "trimmed.local.json"
    trimmed.write_text(json.dumps({"name": "Trim (synthetic)",
                                   "applicability": {"any_organization": True}}), encoding="utf-8")
    try:
        tb = onboard.load_base(existing=trimmed)
        upd, problems = update(tb, {"role_plan_owner": "X"}, sections={"people"})
        ok = upd is not None
    except Exception as e:  # noqa: BLE001
        ok, problems = False, [repr(e)]
    check("updating a hand-trimmed profile works (no missing-field crash)", ok, problems)

    # Starter status comes from the run, never from the editable name.
    p, problems = new(small, {"role_plan_owner": "X"}, sections={"people"})
    check("a new profile asked one section still requires the real name",
          p is None and any(x.startswith("name:") for x in problems), problems)
    p, problems = new(small, {"name": "N (synthetic)", "role_plan_owner": "X"}, sections={"people"})
    check("...and records every unasked starter value as a default",
          p is not None and {"tiers", "sector", "annual_test"} <= set(p.defaults_used)
          and "role_plan_owner" not in p.defaults_used and "name" not in p.defaults_used,
          p and p.defaults_used)
    tricky, _ = new(small, dict(answers, name="Acme (fictional starter)"))
    upd, problems = update(tricky.model_dump(mode="json"), {}, sections={"people"})
    check("a user whose name ends '(fictional starter)' isn't treated as a starter on update",
          upd is not None and "role_risk_owner" not in upd.defaults_used, problems)
    from openbcdr.profile import Tier
    try:
        Tier.model_validate({"name": "x", "rto_hours": float("inf")})
        ok = False
    except Exception:  # noqa: BLE001 - pydantic ValidationError
        ok = True
    check("Tier model rejects an infinite RTO", ok)

    print("\n[O7] the CLI: export, import, guard, refusal")
    q = td / "q.md"
    r = cli("onboard", "--starter", "bank", "--export", str(q))
    check("export exits 0", r.returncode == 0 and q.exists(), r.stderr)
    q.write_text(fill(q.read_text(encoding="utf-8"), {"name": "Riverside Bank (synthetic)"}),
                 encoding="utf-8")
    out = td / "riverside.local.json"
    r = cli("onboard", "--starter", "bank", "--import", str(q), "--out", str(out))
    check("import writes a profile", r.returncode == 0 and out.exists(), r.stderr)
    r = cli("--org", str(out), "--db", str(td / "t.sqlite3"), "gaps")
    check("the written profile loads in a real run", r.returncode == 0, r.stderr)
    before_bytes = out.read_bytes()
    r = cli("onboard", "--starter", "bank", "--import", str(q), "--out", str(out))
    check("an existing profile isn't overwritten without --force", r.returncode != 0
          and "already exists" in r.stderr and out.read_bytes() == before_bytes, r.stderr)
    r = cli("onboard", "--starter", "bank", "--import", str(q), "--out", str(td / "r.LOCAL.json"))
    check("an uppercase .LOCAL. name is refused (git ignores only lowercase everywhere)",
          r.returncode != 0 and not (td / "r.LOCAL.json").exists(), r.stderr)
    victim = td / "victim.md"
    victim.write_text("keep me", encoding="utf-8")
    r = cli("onboard", "--starter", "bank", "--export", str(victim))
    check("export won't overwrite an existing file", r.returncode != 0
          and victim.read_text(encoding="utf-8") == "keep me", r.stderr)
    r = cli("onboard", "--starter", "bank", "--export", str(victim), "--force")
    check("--force lets export replace a file", r.returncode == 0
          and victim.read_text(encoding="utf-8") != "keep me", r.stderr)
    r = cli("onboard", "--update", str(out), "--export", str(td / "leak.md"))
    check("exporting an existing profile needs a .local. name (it carries real answers)",
          r.returncode != 0 and not (td / "leak.md").exists(), r.stderr)
    r = cli("onboard", "--update", str(out), "--export", str(td / "mine.local.md"))
    check("...and works with one", r.returncode == 0 and (td / "mine.local.md").exists(), r.stderr)
    other = td / "other.local.json"
    other.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    other_bytes = other.read_bytes()
    pq = td / "people.md"
    pq.write_text("### role_plan_owner\nAnswer: Office manager\n", encoding="utf-8")
    r = cli("onboard", "--update", str(out), "--section", "people", "--import", str(pq),
            "--out", str(other))
    check("--update won't overwrite a different existing profile without --force",
          r.returncode != 0 and other.read_bytes() == other_bytes, r.stderr)
    r = cli("onboard", "--update", str(out), "--section", "people", "--import", str(pq))
    check("--update rewrites its own file without --force",
          r.returncode == 0 and json.loads(out.read_text(encoding="utf-8"))["role_titles"]
          ["plan_owner"] == "Office manager", r.stderr)
    r = cli("onboard", "--update", str(ROOT / "org" / "starter-bank.json"), "--section", "people",
            "--import", str(q))
    check("--update refuses a tracked (non-.local.) profile", r.returncode != 0, r.stderr)
    r = cli("onboard", "--starter", "bank", "--import", str(q), "--out", str(td / "riverside.json"))
    check("a name without .local. is refused", r.returncode != 0 and ".local." in r.stderr
          and not (td / "riverside.json").exists(), r.stderr)
    bad = td / "bad.md"
    bad.write_text(fill(q.read_text(encoding="utf-8"), {"high_days": "ten"}), encoding="utf-8")
    out2 = td / "bad.local.json"
    r = cli("onboard", "--starter", "bank", "--import", str(bad), "--out", str(out2))
    check("a bad answer writes nothing and exits 2", r.returncode == 2 and not out2.exists()
          and "high_days" in r.stderr, r.stderr)
    dup = td / "dup.md"
    dup.write_text("### name\nAnswer: A\n### name\nAnswer: B\n", encoding="utf-8")
    try:
        onboard.import_markdown(dup.read_text(encoding="utf-8"))
        ok = False
    except SystemExit as e:
        ok = "twice" in str(e)
    check("a question answered twice is refused", ok)
    try:
        onboard.import_markdown("### nmae\nAnswer: A\n")
        ok = False
    except SystemExit as e:
        ok = "Unknown question" in str(e)
    check("an unknown question id is refused", ok)
    for label, doc, expect in (
            ("an answer spilling onto a second line", "### critical_services\nAnswer: Orders;\nPayments\n",
             "text after the answer"),
            ("an orphan Answer: line", "Answer: stray\n### name\nAnswer: A\n", "no question above"),
            ("a heading-shaped second line", "### critical_services\nAnswer: Orders;\n## Payments\n",
             "text after the answer"),
            ("a second Answer: under one question", "### name\nAnswer: A\nAnswer: B\n", "no question above")):
        try:
            onboard.import_markdown(doc)
            ok = False
        except SystemExit as e:
            ok = expect in str(e)
        check("refused: " + label, ok)
    check("CRLF documents import", onboard.import_markdown("### name\r\nAnswer: A\r\n") == {"name": "A"})

print("\n[O8] interactive: retries a bad answer, Enter keeps the value")
script = iter(["Acme (synthetic)"] + [""] * 200)
replies = {"annual_test": iter(["maybe", "no"])}
said: list[str] = []
current_q = {"id": None}


def ask(prompt: str) -> str:
    qid = current_q["id"]
    if qid in replies:
        return next(replies[qid])
    return next(script)


def say(msg: str) -> None:
    said.append(msg)
    for q in onboard.QUESTIONS:
        if msg.startswith(q.prompt):
            current_q["id"] = q.id


ans = onboard.interactive(small, None, ask=ask, say=say, starter=True)
p, problems = new(small, ans)
check("interactive run produces a valid profile", p is not None, problems)
check("the bad answer was rejected with a reason", any("Not quite" in m for m in said))
check("the retried answer was used", p and p.thresholds.annual_test_required is False)

if failures:
    print("\n" + str(failures) + " onboarding check(s) FAILED")
    raise SystemExit(1)
print("\nAll onboarding checks passed.")
