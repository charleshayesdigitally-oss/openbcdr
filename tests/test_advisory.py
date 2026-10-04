"""Practice advisories (Phase 4). Offline.

Proves each advisory fires on the case it's for and stays quiet otherwise, and
that advisories never touch anything that counts: no gap, no score, no
severity, always under their own label.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

from openbcdr import report, standards  # noqa: E402
from openbcdr.analyzers import advisory, coherence  # noqa: E402
from openbcdr.models import Contact, PlanExtract, Procedure, TestRecord, Vendor  # noqa: E402
from openbcdr.store import Store  # noqa: E402
from load_sample import SAMPLE  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global failures
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else "  -- " + str(detail)[:300]))
    if not ok:
        failures += 1


def checks(plan, name):
    return [a for a in advisory.run_all(plan) if a.check == name]


print("\n[A1] people as dependencies")
p = PlanExtract(plan_id="P", contacts=[
    Contact(name="J. Smith", role="Facilities Lead"), Contact(name="j. smith ", role="Technology Lead"),
    Contact(name="A. Bhatt", role="Facilities Lead", is_backup=True),
    Contact(name="A. Bhatt", role="Technology Lead", is_backup=True)])
kp = checks(p, "key_person")
check("one person as primary for two roles is flagged (names compared loosely)",
      any("several recovery roles: J. Smith" in a.title for a in kp), [a.title for a in kp])
p2 = PlanExtract(plan_id="P", contacts=[Contact(name="M. Okonkwo", role="Application Owner"),
                                         Contact(name="M. Okonkwo", role="Application Owner", is_backup=True)])
check("the same person as primary and backup is flagged",
      any("same person as the primary" in a.title for a in checks(p2, "key_person")))
clean = PlanExtract(plan_id="P", contacts=[Contact(name="A", role="R1"), Contact(name="B", role="R1", is_backup=True),
                                            Contact(name="C", role="R2"), Contact(name="D", role="R2", is_backup=True)])
check("distinct people in every role: nothing flagged", not checks(clean, "key_person"))
hon = PlanExtract(plan_id="P", contacts=[Contact(name="Dr. Smith", role="Facilities Lead"),
                                          Contact(name="Smith", role="Technology Lead")])
check("'Dr. Smith' and 'Smith' are recognised as one person", len(checks(hon, "key_person")) == 1)
case = PlanExtract(plan_id="P", contacts=[Contact(name="Smith", role="Recovery Lead"),
                                           Contact(name="Smith", role="recovery  lead")])
check("roles differing only in case or spacing are one role (no false several-roles)",
      not checks(case, "key_person"))
case2 = PlanExtract(plan_id="P", contacts=[Contact(name="Smith", role="Recovery Lead"),
                                            Contact(name="Smith", role="recovery lead", is_backup=True)])
check("...and the same-person backup is still caught across that difference",
      any("same person" in a.title for a in checks(case2, "key_person")))
extra = PlanExtract(plan_id="P", contacts=[Contact(name="Smith", role="R"), Contact(name="Smith", role="R", is_backup=True),
                                            Contact(name="Jones", role="R", is_backup=True)])
check("with another real backup, the wording doesn't claim there is none",
      any("the other backup does" in a.detail for a in checks(extra, "key_person")))
check("several-roles wording doesn't claim the roles go uncovered",
      all("uncovered" not in a.detail for a in checks(p, "key_person")))

print("\n[A2] tested, but fixed or only documented?")
t_no_lessons = PlanExtract(plan_id="P", tests=[TestRecord(test_date=date(2026, 3, 1), scenario="cyber",
                                                         result="Passed", lessons_documented=False)])
check("a latest test with no lessons is flagged",
      any("no documented lessons" in a.title for a in checks(t_no_lessons, "test_follow_through")))
t_fail = PlanExtract(plan_id="P", tests=[
    TestRecord(test_date=date(2025, 6, 1), scenario="facility loss", result="Partially met: RTO exceeded",
               lessons_documented=True),
    TestRecord(test_date=date(2026, 3, 1), scenario="cyber", result="Passed", lessons_documented=True)])
check("a test that fell short with no later test of that scenario is flagged",
      any("fell short" in a.title and "facility loss" in a.title for a in checks(t_fail, "test_follow_through")))
t_fixed = PlanExtract(plan_id="P", tests=[
    TestRecord(test_date=date(2025, 6, 1), scenario="facility loss", result="Failed", lessons_documented=True),
    TestRecord(test_date=date(2026, 3, 1), scenario="Facility Loss", result="Passed", lessons_documented=True)])
check("a shortfall followed by a later test of the same scenario is not flagged",
      not checks(t_fixed, "test_follow_through"))
for ok_result in ("No issues found", "Passed with no problems", "Exceeded recovery objectives",
                  "Successful, all objectives met", "Failed over to the DR site successfully"):
    one = PlanExtract(plan_id="P", tests=[TestRecord(test_date=date(2026, 3, 1), scenario="cyber",
                                                     result=ok_result, lessons_documented=True)])
    check("not a shortfall: '" + ok_result + "'", not checks(one, "test_follow_through"))
for bad_result in ("Failed", "Partially met", "RTO exceeded by two hours", "Objectives not met"):
    one = PlanExtract(plan_id="P", tests=[TestRecord(test_date=date(2026, 3, 1), scenario="cyber",
                                                     result=bad_result, lessons_documented=True)])
    check("a shortfall: '" + bad_result + "'", bool(checks(one, "test_follow_through")))
spaced = PlanExtract(plan_id="P", tests=[
    TestRecord(test_date=date(2025, 6, 1), scenario="facility  loss", result="Failed", lessons_documented=True),
    TestRecord(test_date=date(2026, 3, 1), scenario=" Facility Loss ", result="Passed", lessons_documented=True)])
check("a retest matches despite spacing and case", not checks(spaced, "test_follow_through"))
blank = PlanExtract(plan_id="P", tests=[
    TestRecord(test_date=date(2025, 6, 1), scenario="", result="Failed", lessons_documented=True),
    TestRecord(test_date=date(2026, 3, 1), scenario="", result="Passed", lessons_documented=True)])
check("an unnamed later test doesn't count as a retest of an unnamed failure",
      bool(checks(blank, "test_follow_through")))
same_day = PlanExtract(plan_id="P", tests=[
    TestRecord(test_date=date(2026, 3, 1), scenario="cyber", result="Passed", lessons_documented=True),
    TestRecord(test_date=date(2026, 3, 1), scenario="cyber", result="Failed", lessons_documented=True)])
check("a same-day record isn't a retest, whatever the order", bool(checks(same_day, "test_follow_through")))
check("no dated tests: nothing to say here (coherence already flags missing tests)",
      not checks(PlanExtract(plan_id="P"), "test_follow_through"))

print("\n[A3] AI in a critical path")
ai = PlanExtract(plan_id="P", critical_systems=["Customer chatbot", "Payments"],
                 critical_vendors=[Vendor(name="HelpDesk Co", service="LLM ticket triage")])
titles = [a.title for a in checks(ai, "ai_dependency")]
check("an AI-looking critical system with no fallback is flagged", any("Customer chatbot" in t for t in titles), titles)
check("an AI-looking vendor service is flagged", any("HelpDesk Co" in t for t in titles), titles)
check("ordinary systems are not flagged", not any("Payments" in t for t in titles))
ai_ok = ai.model_copy(update={"procedures": [Procedure(
    name="Chatbot outage", raw_text="If the customer chatbot is down, switch to manual email replies.")]})
check("a procedure covering work without it clears the chatbot",
      not any("Customer chatbot" in a.title for a in checks(ai_ok, "ai_dependency")))
check("'mail', 'air', 'paid' and 'Dubai' don't count as AI",
      not checks(PlanExtract(plan_id="P", critical_systems=["Mail server", "Air handling", "Paid invoices", "Dubai office"]),
                 "ai_dependency"))
check("the advisory says it is a keyword match", all("keyword match" in a.detail for a in checks(ai, "ai_dependency")))
check("...and so does the one-line form people actually read",
      all("(keyword match)" in ln for ln in advisory.lines(checks(ai, "ai_dependency"))[1:]))
for label, text in (("a procedure saying there is NO fallback", "Customer chatbot has no manual fallback."),
                    ("a mention of the chatbot's operating manual", "Consult the Customer chatbot operating manual."),
                    ("a negated workaround", "Customer chatbot: no workaround exists; staff cannot work without it.")):
    neg = ai.model_copy(update={"procedures": [Procedure(name="Chatbot", raw_text=text)]})
    check("doesn't clear it: " + label, any("Customer chatbot" in a.title for a in checks(neg, "ai_dependency")))
gpt = PlanExtract(plan_id="P", critical_systems=["ChatGPT team plan", "GPT4 summariser"])
check("ChatGPT and GPT4 are recognised", len(checks(gpt, "ai_dependency")) == 2,
      [a.title for a in checks(gpt, "ai_dependency")])

print("\n[A4] advisories never count")
check("the sample plan raises no advisories (baseline)", advisory.run_all(SAMPLE) == [])
every = PlanExtract(plan_id="ADV", plan_version="1.0", contacts=p.contacts, tests=t_fail.tests,
                    critical_systems=["Customer chatbot"])
advs = advisory.run_all(every)
check("fixture raises all three kinds", {a.check for a in advs} == {"key_person", "test_follow_through", "ai_dependency"})
check("an advisory carries no severity or requirement id",
      all(not hasattr(a, "severity") and not hasattr(a, "req_id") for a in advs))
reqs = standards.load()[:3]
base = report.render("ADV", "1.0", [], reqs, issues=coherence.run_all(every, date(2026, 10, 4)),
                     today=date(2026, 10, 4))
with_adv = report.render("ADV", "1.0", [], reqs, issues=coherence.run_all(every, date(2026, 10, 4)),
                         today=date(2026, 10, 4), advisories=advs)
check("the report labels them as practice, not requirements", advisory.LABEL in with_adv)
check("the report is otherwise identical: score and findings untouched",
      with_adv.replace("\n".join(advisory.lines(advs)) + "\n\n", "") == base)
# With a real, released score: validated requirements and a full assessment.
from openbcdr.models import CoverageFinding  # noqa: E402
vreqs = [r.model_copy(update={"validated_by_human": True, "validated_by": "test"}) for r in standards.load()[:3]]
fnd = [CoverageFinding(req_id=r.req_id, coverage="full", rationale="ok", evidence_quote="x", plan_section="1",
                       recommended_action="", evidence_verified=True) for r in vreqs]
scored = report.render("ADV", "1.0", fnd, vreqs, today=date(2026, 10, 4))
scored_adv = report.render("ADV", "1.0", fnd, vreqs, today=date(2026, 10, 4), advisories=advs)
score_line = lambda txt: [ln for ln in txt.splitlines() if "Score" in ln or "Coverage" in ln]  # noqa: E731
check("with a released score, advisories change no score or coverage line",
      score_line(scored) == score_line(scored_adv) and score_line(scored) != [], score_line(scored))
exam = report.render("ADV", "1.0", fnd, vreqs, today=date(2026, 10, 4), advisories=advs, examiner_facing=True)
check("examiner-facing output carries no advisories", advisory.LABEL not in exam and "ADVISORY" not in exam)
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "t.sqlite3"
    st = Store(db)
    st.save_plan_version(plan_id="ADV", version="1.0", mode="sandbox", source_path="x",
                         raw_text="synthetic", extract=every.model_dump(mode="json"))
    st.close()
    r = subprocess.run([sys.executable, "-m", "openbcdr", "--db", str(db), "coherence", "--plan", "ADV",
                        "--open-gaps"], cwd=ROOT, capture_output=True, text=True)
    check("the coherence command prints the advisories under their label",
          r.returncode == 0 and "Practice advisories (" in r.stdout and advisory.LABEL in r.stdout, r.stderr)
    st = Store(db)
    origins = {row["origin_key"] for row in st.db.execute("SELECT origin_key FROM gaps")}
    n_gaps = st.db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]
    st.close()
    n_issues = len(coherence.run_all(every))
    check("--open-gaps opens exactly the coherence gaps and nothing for advisories",
          n_gaps <= n_issues and all(o and o.startswith("coherence:") for o in origins), (n_gaps, n_issues, origins))

if failures:
    print("\n" + str(failures) + " advisory check(s) FAILED")
    raise SystemExit(1)
print("\nAll advisory checks passed.")
