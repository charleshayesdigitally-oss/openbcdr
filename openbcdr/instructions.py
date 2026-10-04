"""Organisation-aware wording for the agent (Phase 1c).

Two jobs, both driven by the organisation profile:

1. `render(profile)` fills `AGENT-INSTRUCTIONS.md` (the prompt-only form of the
   agent) with this organisation's name, regulators, role titles, routing,
   deadlines, tiers, critical services and words. For an organisation no
   regulator oversees, the scope, regulatory-currency and severity paragraphs
   are replaced with good-practice versions. Every paragraph it replaces is
   matched against a fingerprint of the exact original text, so an edited
   template stops the render instead of leaving bank wording behind.
2. `context_note(profile)` is a short block added to the plan-reading prompts
   (ingest, compliance) when `--org` is given: the organisation's own words,
   and, if unregulated, a rule to frame findings as good practice. With no
   `--org`, those prompts are byte-identical to before.

Profile text is data, never instructions: it is flattened to one line, and
angle brackets are removed, before it goes anywhere near a prompt. Placeholders
are filled in one pass over the template, so braces in profile text are kept
literally and never re-filled.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Optional

from . import config
from .profile import OrgProfile

TEMPLATE = config.ROOT / "AGENT-INSTRUCTIONS.md"
PLACEHOLDER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
REGULATED_TAGS = ("all_banks", "finra_member", "fed_member", "sifi")

# Paragraphs this module replaces: prefix -> sha256 of the exact original line.
FINGERPRINTS = {
    "This is a {INSTITUTION_TYPE} supervised by":
        "fcdceb8a84966f08deb77d9e07ffd05e3156b37dd1387ed29fe3b032be651159",
    "**Regulatory currency.**":
        "2d30123626b076cc888896f1f482641d14c209487c156f055c7a802e4337721c",
    "**Critical**": "a76391aa07a5f4edb4ff73b3a061ad7215e2f516a0ebda168c1236e3473bb26e",
    "**High**": "c152ad77e08bbd41d895c0333367a3ab844894f567fa81c5050441a8b3336ae4",
    "**Medium**": "3db4af320c521b16c179a86344d13037b81bc7bbb1768ce1e810777845fe7775",
    "Route critical findings to":
        "be30887690825e64b62eee190b3b4c674e79b3f4365a0c03e94b86c7b3d96240",
}

SIFI_SCOPE = (
    "This is a {INSTITUTION_TYPE} supervised by {REGULATORS}, designated systemically "
    "important. Obligations scoped to systemically important institutions apply to it. "
    "Apply every requirement in your index that is tagged for this institution, and if you "
    "are unsure whether a requirement applies, say so explicitly rather than raising it as "
    "a finding.")

UNREGULATED = {
    "This is a {INSTITUTION_TYPE} supervised by": (
        "This is a {INSTITUTION_TYPE} with no regulator overseeing its continuity planning. "
        "The requirements in your index are general good-practice standards, not legal "
        "obligations for this organisation. Describe every finding as a gap against good "
        "practice. Never call it a regulatory violation, an examination failure or a "
        "compliance breach, and never imply a penalty. If you are unsure whether a practice "
        "is proportionate for an organisation of this size, say so explicitly rather than "
        "raising it as a finding."),
    "**Regulatory currency.**": (
        "**Regulatory currency.** This organisation has no continuity regulator. If you are "
        "given a regulatory or industry item, judge only whether it concerns continuity or "
        "operational resilience and whether it is relevant to an organisation of this kind "
        "and size. When the text is too thin to tell, answer no."),
    "**Critical**": (
        "**Critical** — a gap that directly impairs the organisation's ability to "
        "recover or to keep serving its customers. Examples: no list of what must keep "
        "running, no recovery objectives defined, the plan never tested."),
    "**High**": (
        "**High** — an important good practice is missing and recovery would likely be "
        "slow or confused. Examples: vendor recovery expectations undocumented, recovery "
        "scenarios materially incomplete."),
    "**Medium**": (
        "**Medium** — a documentation weakness or a smaller gap against good practice. "
        "Examples: contact list more than six months stale, plan refers to an outdated "
        "system name."),
}

_ROLE_WORDS = {"risk_owner": "the risk owner", "compliance_officer": "the compliance officer",
               "plan_owner": "the plan owner", "bcdr_pm": "the BC/DR program manager",
               "weekly_digest": "the weekly digest"}


_DELIMITER = re.compile(r"organisation_context", re.IGNORECASE)


def clean(text: object) -> str:
    """Profile text as inert data: one line, no control or format characters
    (C0, C1, zero-width, line/paragraph separators). Meaning is kept: '<30 staff'
    stays '<30 staff'. The only rewrite is the context delimiter's own name, so
    profile text can never open or close the <organisation_context> block."""
    out = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch
                  for ch in str(text))
    out = _DELIMITER.sub("organisation-context", out)
    return re.sub(r"\s+", " ", out).strip()


def is_regulated(profile: OrgProfile) -> bool:
    return any(profile.applicability.get(t, False) for t in REGULATED_TAGS)


def _title(profile: OrgProfile, role: str) -> str:
    return clean(profile.role_titles.get(role) or _ROLE_WORDS.get(role, role.replace("_", " ")))


def context_note(profile: OrgProfile) -> str:
    lines = []
    if profile.terminology:
        lines.append("This organisation's plans use their own words. Read them as follows: "
                     + "; ".join("'" + clean(k) + "' means " + clean(v)
                                 for k, v in profile.terminology.items()) + ".")
    if not is_regulated(profile):
        lines.append("This organisation is not regulated for continuity. The requirements are "
                     "good-practice guidance: describe gaps as gaps against good practice, never "
                     "as regulatory violations or examination failures.")
    if not lines:
        return ""
    return "\n\n<organisation_context>\n" + "\n".join(lines) + "\n</organisation_context>"


def _agent_block(text: str) -> str:
    """The pasteable agent text: everything after the first '---' rule."""
    parts = re.split(r"^---\s*$", text, maxsplit=1, flags=re.M)
    if len(parts) != 2:
        raise SystemExit("AGENT-INSTRUCTIONS.md has no '---' line before the agent text")
    return parts[1].strip() + "\n"


def _replace_line(lines: list[str], prefix: str, new: Optional[str]) -> None:
    """Find the one line starting with prefix, check its fingerprint, and
    replace it (or just verify it, when new is None)."""
    hits = [i for i, ln in enumerate(lines) if ln.startswith(prefix)]
    if len(hits) != 1 or hashlib.sha256(lines[hits[0]].encode("utf-8")).hexdigest() != FINGERPRINTS[prefix]:
        raise SystemExit("AGENT-INSTRUCTIONS.md changed: the paragraph starting '" + prefix
                         + "' is not the one this renderer knows, so it can't be adapted safely. "
                         "Update FINGERPRINTS (and the replacement text) in openbcdr/instructions.py.")
    if new is not None:
        lines[hits[0]] = new


def _routing(profile: OrgProfile) -> str:
    """One sentence per severity, neutral about whether the destination is a
    person or a digest, so any valid routing reads correctly."""
    def who(sev: str) -> str:
        names = [_title(profile, r) for r in profile.routing.get(sev, [])]
        return " and ".join(dict.fromkeys(names)) or _ROLE_WORDS["bcdr_pm"]

    def when(sev: str) -> str:
        days, esc = profile.sla.get(sev, (None, None))
        if days is None:
            return "no fixed deadline"
        out = "due within " + str(days) + " business days"
        if esc is not None:
            out += ", escalated if there is no response within " + str(esc) + " business days"
        return out

    parts = []
    for sev in ("critical", "high", "medium", "low"):
        parts.append(sev.capitalize() + " findings go to " + who(sev)
                     + (", immediately" if sev == "critical" else "") + " (" + when(sev) + ").")
    return (" ".join(parts) + " State the route and the proposed deadline on every finding; "
            "never send anything yourself.")


def render(profile: OrgProfile, template: Path = TEMPLATE) -> str:
    body = _agent_block(template.read_text(encoding="utf-8"))
    lines = body.split("\n")
    regulated = is_regulated(profile)
    scope_prefix = "This is a {INSTITUTION_TYPE} supervised by"
    for prefix in FINGERPRINTS:
        if prefix == "Route critical findings to":
            _replace_line(lines, prefix, None)  # verified now, filled after the placeholders
        elif not regulated:
            _replace_line(lines, prefix, UNREGULATED[prefix])
        elif prefix == scope_prefix and profile.applicability.get("sifi", False):
            _replace_line(lines, prefix, SIFI_SCOPE)
        else:
            _replace_line(lines, prefix, None)
    routing_at = next(i for i, ln in enumerate(lines) if ln.startswith("Route critical findings to"))
    body = "\n".join(lines)

    org = clean(profile.name) + (" (" + clean(profile.sector) + ")" if profile.sector else "")
    values = {
        "{ORG_DESCRIPTION}": org,
        "{INSTITUTION_TYPE}": clean(profile.sector) or "organisation",
        "{REGULATORS}": ", ".join(clean(r) for r in profile.regulators) or "no external regulator",
        "{RISK_OWNER}": _title(profile, "risk_owner"),
        "{COMPLIANCE_OFFICER}": _title(profile, "compliance_officer"),
        "{BCDR_PM}": _title(profile, "bcdr_pm"),
    }
    unknown = sorted(set(PLACEHOLDER.findall(body)) - set(values))
    if unknown:
        raise SystemExit("Unfilled placeholder(s) in AGENT-INSTRUCTIONS.md: " + ", ".join(unknown))
    # One pass over the template's own tokens: text inserted from the profile
    # is never scanned again, so braces in a name stay exactly as typed.
    body = PLACEHOLDER.sub(lambda m: values[m.group(0)], body)
    # Profile-derived routing goes in only now, so its text is never scanned
    # for placeholders. Filled values are single-line, so line numbers held.
    lines = body.split("\n")
    lines[routing_at] = _routing(profile)
    body = "\n".join(lines)

    extra = ["", "## This organisation", ""]
    if profile.size:
        extra.append("Size: " + clean(profile.size) + ". Judge proportionality against it.")
    if profile.critical_services:
        extra.append("Critical services, most important first: "
                     + "; ".join(clean(s) for s in profile.critical_services) + ".")
    if profile.tiers:
        extra.append("Recovery tiers: " + "; ".join(
            clean(t.name) + " (back within " + _hours(t.rto_hours) + " hours"
            + (", losing at most " + _hours(t.rpo_hours) + " hours of data"
               if t.rpo_hours is not None else "")
            + ")" for t in profile.tiers) + ".")
    if profile.terminology:
        extra.append("Their words: " + "; ".join(
            "'" + clean(k) + "' means " + clean(v) for k, v in profile.terminology.items()) + ".")
    return body.rstrip() + "\n" + "\n".join(extra) + "\n"


def _hours(x: float | None) -> str:
    return "" if x is None else (str(int(x)) if float(x).is_integer() else str(x))
