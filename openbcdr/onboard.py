"""The onboarding questionnaire: build an organisation profile from plain answers.

One list of questions drives every way of answering, so they can't drift:

    python -m openbcdr onboard --starter small-business --out org/acme.local.json
        asks each question at the command line; Enter keeps the starter's value
    python -m openbcdr onboard --starter bank --export questionnaire.md
        writes a fill-in document for someone who won't use a command line
    python -m openbcdr onboard --starter bank --import questionnaire.md --out org/acme.local.json
        reads the filled-in document back
    python -m openbcdr onboard --update org/acme.local.json --section people
        re-asks one section of an existing profile and keeps the rest

Every answer set is validated as a whole profile before anything is written, and
the output name must contain ".local." so it is gitignored and can't be
committed by accident. A question left blank keeps the starting value and is
recorded in the profile's `defaults_used`, so nobody mistakes a default for a
decision.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import config
from .profile import FRAMEWORK_ROLES, OrgProfile

STARTERS = {
    "bank": config.ROOT / "org" / "starter-bank.json",
    "small-business": config.ROOT / "org" / "starter-small-business.json",
}

SECTIONS = {
    "who": "Who you are",
    "rules": "Rules you answer to",
    "running": "What must keep running",
    "speed": "How fast it has to come back",
    "people": "Who's who",
    "deadlines": "How fast findings get handled",
    "upkeep": "Keeping the plan current",
    "words": "Your words",
    "template": "Your plan's shape",
}

ROLE_PROMPTS = {
    "risk_owner": "Who owns the risk and decides on the most serious findings? (a title, not a name)",
    "compliance_officer": "Who handles regulatory or compliance questions? (a title; can be the same person)",
    "plan_owner": "Who owns and maintains the recovery plans day to day?",
    "bcdr_pm": "Who runs the continuity program overall and keeps plans formatted and current?",
    "weekly_digest": "Where do low-priority findings go for a weekly look? (e.g. a weekly check-in)",
}


class AnswerError(ValueError):
    """An answer that can't be read. The message says what was expected."""


# ------------------------------------------------------------------ parsers

def _text(s: str) -> str:
    return s.strip()


CLEAR = "none"  # answering "none" empties a list or word-pair answer


def _list(s: str) -> list[str]:
    if s.strip().lower() == CLEAR:
        return []
    return [x.strip() for x in s.split(";") if x.strip()]


def _bool(s: str) -> bool:
    v = s.strip().lower()
    if v in ("y", "yes", "true", "1"):
        return True
    if v in ("n", "no", "false", "0"):
        return False
    raise AnswerError("answer yes or no")


def _pos_int(s: str) -> int:
    try:
        n = int(s.strip())
    except ValueError:
        raise AnswerError("answer a whole number")
    if n <= 0:
        raise AnswerError("answer a number above zero")
    return n


def _tiers(s: str) -> list[dict]:
    """`Name | RTO hours | RPO hours | description; Name | ...` (RPO and
    description optional)."""
    # A ';' inside a description is text, not a new tier: a chunk with no '|'
    # continues the previous tier's description.
    chunks: list[str] = []
    for chunk in _list(s):
        if "|" not in chunk and chunks:
            chunks[-1] += "; " + chunk
        else:
            chunks.append(chunk)
    out = []
    for chunk in chunks:
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) < 2 or not parts[0]:
            raise AnswerError("each tier is 'Name | RTO hours | RPO hours | description', "
                              "tiers separated by ';'")
        try:
            tier = {"name": parts[0], "rto_hours": float(parts[1]), "rpo_hours": None,
                    "description": ""}
            if len(parts) > 2 and parts[2]:
                tier["rpo_hours"] = float(parts[2])
        except ValueError:
            raise AnswerError("RTO and RPO are hours, as numbers (e.g. 4 or 0.25)")
        if not all(math.isfinite(tier[k]) for k in ("rto_hours", "rpo_hours") if tier[k] is not None):
            raise AnswerError("RTO and RPO must be real numbers of hours")
        if len(parts) > 3:
            tier["description"] = " | ".join(parts[3:])
        out.append(tier)
    return out


def _pairs(s: str) -> dict[str, str]:
    """`your term = framework term; ...`"""
    out = {}
    for chunk in _list(s):
        if "=" not in chunk:
            raise AnswerError("write each as 'your term = our term', separated by ';'")
        k, v = (x.strip() for x in chunk.split("=", 1))
        if not k or not v:
            raise AnswerError("both sides of '=' need a word")
        if k in out:
            raise AnswerError("'" + k + "' is mapped twice")
        out[k] = v
    return out


# ---------------------------------------------------------------- questions

@dataclass(frozen=True)
class Question:
    id: str
    section: str
    prompt: str
    parse: Callable[[str], object]
    get: Callable[[dict], object]           # current value from a profile dict
    put: Callable[[dict, object], None]     # write a parsed answer into it
    help: str = ""
    required: bool = False                  # no usable default (e.g. the real name)


def _fmt(v) -> str:
    """Render a current value in the same syntax the parser reads."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list) and v and isinstance(v[0], dict) and "rto_hours" in v[0]:
        return "; ".join(" | ".join(str(x) for x in (t["name"], _num(t["rto_hours"]),
                                                     _num(t.get("rpo_hours")) if t.get("rpo_hours") is not None else "",
                                                     t.get("description", ""))).rstrip(" |")
                         for t in v)
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(k + " = " + str(val) for k, val in v.items())
    return "" if v is None else str(v)


def _num(x) -> str:
    return str(int(x)) if isinstance(x, (int, float)) and float(x).is_integer() else str(x)


def _set_tag(tag: str) -> Callable[[dict, object], None]:
    def put(d: dict, v) -> None:
        d["applicability"][tag] = bool(v)
    return put


def _set_sla(sev: str, idx: int) -> Callable[[dict, object], None]:
    def put(d: dict, v) -> None:
        pair = list(d["sla"][sev])
        pair[idx] = v
        d["sla"][sev] = pair
    return put


def _set_role(role: str) -> Callable[[dict, object], None]:
    def put(d: dict, v) -> None:
        d["role_titles"][role] = v
    return put


def _set_template(d: dict, titles) -> None:
    by_title = {s["title"]: s for s in d.get("plan_template", [])}
    d["plan_template"] = [by_title.get(t, {"title": t, "covers_tags": []}) for t in titles]


QUESTIONS: list[Question] = [
    Question("name", "who", "What is your organisation called?", _text,
             lambda d: d["name"], lambda d, v: d.__setitem__("name", v), required=True),
    Question("sector", "who", "What kind of organisation is it? (e.g. community bank, dental practice, online store)",
             _text, lambda d: d.get("sector", ""), lambda d, v: d.__setitem__("sector", v)),
    Question("size", "who", "Roughly how big? (people, locations)", _text,
             lambda d: d.get("size", ""), lambda d, v: d.__setitem__("size", v)),
    Question("regulators", "who", "Which regulators or examiners oversee you, if any?",
             _list, lambda d: d.get("regulators", []), lambda d, v: d.__setitem__("regulators", v),
             help="Separate several with ';'. Write 'none' if there are none."),

    Question("is_bank", "rules", "Are you a bank, credit union or other depository institution?",
             _bool, lambda d: d["applicability"].get("all_banks", False), _set_tag("all_banks"),
             help="Yes brings in the FFIEC continuity requirements."),
    Question("finra", "rules", "Are you a FINRA member firm (a broker-dealer)?",
             _bool, lambda d: d["applicability"].get("finra_member", False), _set_tag("finra_member"),
             help="Yes brings in FINRA Rule 4370, including annual testing."),
    Question("fed", "rules", "Are you a Federal Reserve member bank?",
             _bool, lambda d: d["applicability"].get("fed_member", False), _set_tag("fed_member")),
    Question("sifi", "rules", "Are you designated systemically important (SIFI)?",
             _bool, lambda d: d["applicability"].get("sifi", False), _set_tag("sifi"),
             help="Almost always no."),

    Question("critical_services", "running",
             "What must keep running? List your critical services, most important first.",
             _list, lambda d: d.get("critical_services", []),
             lambda d, v: d.__setitem__("critical_services", v),
             help="Think about what stops revenue or customers first. Separate with ';'."),

    Question("tiers", "speed", "What are your recovery tiers, and how fast must each come back?",
             _tiers, lambda d: d.get("tiers", []), lambda d, v: d.__setitem__("tiers", v),
             help="Format: 'Name | RTO hours | RPO hours | what belongs here', tiers separated "
                  "by ';'. RTO = how long it can be down. RPO = how much recent data you can lose. "
                  "Every tier needs at least 'Name | RTO hours'; text after a ';' with no '|' is "
                  "read as part of the previous tier's description."),

    *[Question("role_" + r, "people", ROLE_PROMPTS[r], _text,
               (lambda role: lambda d: d.get("role_titles", {}).get(role, ""))(r), _set_role(r))
      for r in FRAMEWORK_ROLES],

    Question("critical_days", "deadlines",
             "How many business days should a critical finding get before it's overdue?",
             _pos_int, lambda d: d["sla"]["critical"][0], _set_sla("critical", 0)),
    Question("critical_escalate", "deadlines",
             "After how many business days should an unanswered critical finding be escalated?",
             _pos_int, lambda d: d["sla"]["critical"][1], _set_sla("critical", 1),
             help="Must be sooner than the deadline above."),
    Question("high_days", "deadlines", "How many business days for a high finding?",
             _pos_int, lambda d: d["sla"]["high"][0], _set_sla("high", 0)),
    Question("medium_days", "deadlines", "How many business days for a medium finding?",
             _pos_int, lambda d: d["sla"]["medium"][0], _set_sla("medium", 0)),

    Question("contact_review_days", "upkeep",
             "After how many days should an unverified contact be flagged?",
             _pos_int, lambda d: d["thresholds"]["contact_stale_medium_days"],
             lambda d, v: d["thresholds"].__setitem__("contact_stale_medium_days", v)),
    Question("contact_overdue_days", "upkeep",
             "After how many days is an unverified contact a serious problem?",
             _pos_int, lambda d: d["thresholds"]["contact_stale_high_days"],
             lambda d, v: d["thresholds"].__setitem__("contact_stale_high_days", v),
             help="Must be more than the answer above."),
    Question("plan_review_months", "upkeep", "After how many months without approval is a plan stale?",
             _pos_int, lambda d: d["thresholds"]["plan_stale_months"],
             lambda d, v: d["thresholds"].__setitem__("plan_stale_months", v)),
    Question("annual_test", "upkeep",
             "Should every plan be tested at least once a year, even where no rule requires it?",
             _bool, lambda d: d["thresholds"]["annual_test_required"],
             lambda d, v: d["thresholds"].__setitem__("annual_test_required", v),
             help="FINRA members are held to annual testing either way."),

    Question("terminology", "words", "Do you use your own words for any of these? "
             "(plan, tier, risk owner, finding, exercise)",
             _pairs, lambda d: d.get("terminology", {}), lambda d, v: d.__setitem__("terminology", v),
             help="Write 'your term = our term', separated by ';', e.g. 'playbook = plan'. "
                  "Write 'none' to clear."),

    Question("plan_sections", "template", "What sections must every plan have, in order?",
             _list, lambda d: [s["title"] for s in d.get("plan_template", [])], _set_template,
             help="Separate with ';'. Leave blank to keep the starter's sections."),
]

BY_ID = {q.id: q for q in QUESTIONS}


# ------------------------------------------------------------------- engine

def load_base(starter: Optional[str] = None, existing: Optional[Path] = None) -> dict:
    """The starting answers, normalised through the model so every optional
    field is present (a hand-trimmed profile must not crash an update)."""
    if existing is not None:
        check_out_path(Path(existing))
        raw = json.loads(Path(existing).read_text(encoding="utf-8-sig"))
    elif starter in STARTERS:
        raw = json.loads(STARTERS[starter].read_text(encoding="utf-8"))
    else:
        raise SystemExit("--starter must be one of: " + ", ".join(STARTERS))
    return OrgProfile.model_validate(raw).model_dump(mode="json")


def asked_questions(sections: Optional[set[str]], starter: bool) -> list[Question]:
    """The questions a run asks. A new profile always asks the required ones
    (the real name), whatever sections were chosen."""
    return [q for q in QUESTIONS
            if sections is None or q.section in sections or (starter and q.required)]


def apply_answers(base: dict, answers: dict[str, str],
                  sections: Optional[set[str]] = None, *,
                  starter: bool) -> tuple[OrgProfile, list[str]]:
    """Apply raw answers (question id -> text) to a copy of `base` and validate.

    `starter` says where `base` came from (a starter file, or an existing
    profile); it is never guessed from editable content. From a starter, every
    value not answered is a default and the placeholder name must be replaced,
    even when only some sections are asked. On an update, a blank keeps both the
    value and its history: an earlier answer stays an answer.

    Returns (profile, problems); a non-empty problems list means nothing should
    be written.
    """
    d = json.loads(json.dumps(base))
    d.setdefault("critical_services", [])
    problems: list[str] = []
    asked = asked_questions(sections, starter)
    asked_ids = {q.id for q in asked}
    prev_defaults = set(base.get("defaults_used", []))
    if starter:
        defaults = {q.id for q in QUESTIONS if q.id not in asked_ids}
    else:
        defaults = {x for x in prev_defaults if x not in asked_ids}
    for q in asked:
        raw = (answers.get(q.id) or "").strip()
        if not raw:
            if q.required and starter:
                problems.append(q.id + ": required (the starter's value is a placeholder)")
            if starter or q.id in prev_defaults:
                defaults.add(q.id)
            continue
        try:
            q.put(d, q.parse(raw))
        except AnswerError as e:
            problems.append(q.id + ": " + str(e))
    if any(q.section == "rules" for q in asked):
        # Only the rules section decides applicability. An organisation in no
        # regulated category is held to the general (any_organization) records.
        regulated = any(d["applicability"].get(t, False)
                        for t in ("all_banks", "finra_member", "fed_member", "sifi"))
        d["applicability"]["any_organization"] = not regulated
        d["applicability"] = {k: v for k, v in d["applicability"].items() if v}
    d["defaults_used"] = sorted(defaults)
    if problems:
        return None, problems  # type: ignore[return-value]
    try:
        profile = OrgProfile.model_validate(d)
    except Exception as e:
        return None, ["the answers don't fit together:\n" + str(e)]  # type: ignore[return-value]
    from . import standards
    if not standards.applicable(standards.load(), profile.applicability):
        return None, ["rules: these answers select no standard in the index (Federal Reserve "  # type: ignore[return-value]
                      "membership or SIFI status apply only alongside 'bank: yes')"]
    return profile, []


def check_out_path(out: Path) -> Path:
    # Exactly lowercase: .gitignore's *.local.* only matches lowercase on a
    # case-sensitive checkout.
    if ".local." not in out.name:
        raise SystemExit("Refusing to write " + str(out) + ": a real profile's file name must "
                         "contain '.local.' (e.g. org/acme.local.json) so it is never committed.")
    return out


def write(profile: OrgProfile, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")


# ----------------------------------------------------------- the document form

def export_markdown(base: dict, starter_label: str) -> str:
    lines = ["# OpenBCDR onboarding questionnaire", "",
             "Starting point: **" + starter_label + "**.",
             "",
             "Write each answer on the `Answer:` line, after the colon. Leave it blank to keep the "
             "starting value shown under `Current:`. Separate several items with `;`.",
             "Use made-up or public examples if you are trying it out; never paste anything confidential "
             "into a shared copy of this file.", ""]
    for key, title in SECTIONS.items():
        qs = [q for q in QUESTIONS if q.section == key]
        lines += ["## " + title, ""]
        for q in qs:
            lines.append("### " + q.id)
            lines.append(q.prompt)
            if q.help:
                lines.append("> " + q.help)
            cur = _fmt(q.get(base))
            lines.append("Current: " + (cur if cur else "(none)")
                         + ("  (placeholder: please answer)" if q.required else ""))
            lines.append("Answer: ")
            lines.append("")
    return "\n".join(lines)


_Q_HEADER = re.compile(r"^###\s+(\S+)\s*$")
# Only the section headings the exporter writes may follow an answer.
_SECTION_HEADERS = {"## " + t for t in SECTIONS.values()}


def import_markdown(text: str, known: Optional[set] = None) -> dict[str, str]:
    """Read `Answer:` lines back, keyed by the `### <id>` above them.
    Unknown ids and a question answered twice are errors, not guesses."""
    answers: dict[str, str] = {}
    current: Optional[str] = None
    last_answered: Optional[str] = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        m = _Q_HEADER.match(line)
        if m:
            current, last_answered = m.group(1), None
            if current not in (BY_ID if known is None else known):
                raise SystemExit("Unknown question id in the document: " + current)
            if current in answers:
                raise SystemExit("Question answered twice: " + current)
            continue
        if line.startswith("Answer:"):
            if current is None:
                raise SystemExit("Line " + str(n) + ": an 'Answer:' with no question above it "
                                 "(each question takes one Answer: line)")
            answers[current] = line[len("Answer:"):].strip()
            current, last_answered = None, current
            continue
        if last_answered and line.strip() and line not in _SECTION_HEADERS:
            raise SystemExit("Line " + str(n) + ": text after the answer to '" + last_answered
                             + "'. Keep each answer on its one Answer: line.")
    return answers


# ------------------------------------------------------------- interactive

def interactive(base: dict, sections: Optional[set[str]], ask: Callable[[str], str] = input,
                say: Callable[[str], None] = print, *, starter: bool = False) -> dict[str, str]:
    answers: dict[str, str] = {}
    asked = asked_questions(sections, starter)
    for key, title in SECTIONS.items():
        qs = [q for q in asked if q.section == key]
        if not qs:
            continue
        say("\n== " + title + " ==")
        for q in qs:
            cur = _fmt(q.get(base))
            while True:
                say(q.prompt + (("\n  " + q.help) if q.help else ""))
                raw = ask("  [" + (cur or "none") + "] > ").strip()
                if not raw:
                    break
                try:
                    q.parse(raw)
                    break
                except AnswerError as e:
                    say("  Not quite: " + str(e) + ". Try again, or press Enter to keep the current value.")
            answers[q.id] = raw
    return answers
