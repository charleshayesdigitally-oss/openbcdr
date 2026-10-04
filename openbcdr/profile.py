"""Organisation profile: how one organisation customises OpenBCDR.

A profile is a JSON file. Real ones are named `org/<slug>.local.json` and are
gitignored (any `*.local.*` path is; CI refuses a tracked one). The two starters
in `org/` are fictional and tracked:

    org/starter-bank.json            today's behaviour, regulated-bank defaults
    org/starter-small-business.json  no regulator, plain terms, small-team tiers

Load one with `--org <path>`. With no `--org`, nothing changes: the built-in
defaults in `config.py` apply, exactly as before profiles existed.

What a profile changes today: which standards apply (applicability tags),
gap routing and response deadlines, staleness thresholds, and whether an annual
test is required. Recovery tiers, role titles, terminology and the plan
template are stored and validated now; the questionnaire, prompts and plan
drafting use them in later phases (see ROADMAP.md).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import config

Severity = Literal["critical", "high", "medium", "low"]
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")

# The framework's routing roles. An organisation maps its own titles onto these
# (role_titles) rather than inventing new routing targets the code can't reach.
FRAMEWORK_ROLES: tuple[str, ...] = (
    "risk_owner", "compliance_officer", "plan_owner", "bcdr_pm", "weekly_digest",
)

# Applicability tags the standards index understands. A tag outside this set
# would silently match nothing, so it is refused.
KNOWN_TAGS: tuple[str, ...] = (
    "all_banks", "finra_member", "fed_member", "sifi", "any_organization",
)


class _Strict(BaseModel):
    # A misspelled key must be an error, never a silently restored default.
    model_config = ConfigDict(extra="forbid")


class Tier(_Strict):
    name: str
    rto_hours: float = Field(gt=0, allow_inf_nan=False)
    rpo_hours: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    description: str = ""


class Thresholds(_Strict):
    contact_stale_medium_days: int = Field(default=config.CONTACT_STALE_MEDIUM_DAYS, gt=0)
    contact_stale_high_days: int = Field(default=config.CONTACT_STALE_HIGH_DAYS, gt=0)
    plan_stale_months: int = Field(default=config.PLAN_STALE_MONTHS, gt=0)
    # The organisation's own policy, beyond any regulation: flag a plan with no
    # test in 12 months. FINRA members are held to annual testing regardless
    # (applicability tag finra_member), so the bank starter leaves this off.
    annual_test_required: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> "Thresholds":
        if self.contact_stale_high_days <= self.contact_stale_medium_days:
            raise ValueError("contact_stale_high_days must be greater than contact_stale_medium_days")
        return self


class TemplateSection(_Strict):
    title: str
    # Index tags this section is meant to cover (used by plan drafting, Phase 3).
    covers_tags: list[str] = Field(default_factory=list)


class OrgProfile(_Strict):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1)
    sector: str = ""
    size: str = ""
    regulators: list[str] = Field(default_factory=list)
    applicability: dict[str, bool]
    # What must keep running, most important first (plan drafting uses this).
    critical_services: list[str] = Field(default_factory=list)
    tiers: list[Tier] = Field(default_factory=list)
    role_titles: dict[str, str] = Field(default_factory=dict)
    routing: dict[str, list[str]] = Field(default_factory=lambda: dict(config.ROUTING))
    sla: dict[str, tuple[Optional[int], Optional[int]]] = Field(
        default_factory=lambda: {k: tuple(v) for k, v in config.SLA.items()})
    terminology: dict[str, str] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    plan_template: list[TemplateSection] = Field(default_factory=list)
    # Fields the organisation did not answer and kept the starter's value for.
    defaults_used: list[str] = Field(default_factory=list)

    @field_validator("applicability")
    @classmethod
    def _known_tags(cls, v: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(set(v) - set(KNOWN_TAGS))
        if unknown:
            raise ValueError("unknown applicability tag(s): " + ", ".join(unknown)
                             + ". Known: " + ", ".join(KNOWN_TAGS))
        if not any(v.values()):
            raise ValueError("at least one applicability tag must be true, or no standard applies")
        return v

    @field_validator("role_titles")
    @classmethod
    def _known_roles(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v) - set(FRAMEWORK_ROLES))
        if unknown:
            raise ValueError("role_titles keys must be framework roles; unknown: " + ", ".join(unknown))
        return v

    @field_validator("routing")
    @classmethod
    def _routing_ok(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        missing = [s for s in SEVERITIES if s not in v]
        if missing:
            raise ValueError("routing must cover every severity; missing: " + ", ".join(missing))
        for sev, roles in v.items():
            if sev not in SEVERITIES and sev != "format":
                raise ValueError("routing has an unknown severity: " + sev)
            bad = [r for r in roles if r not in FRAMEWORK_ROLES]
            if bad or not roles:
                raise ValueError("routing[" + sev + "] must list framework roles; got " + repr(roles))
        return v

    @field_validator("sla")
    @classmethod
    def _sla_ok(cls, v):
        if set(v) != set(SEVERITIES):
            raise ValueError("sla must have exactly these severities: " + ", ".join(SEVERITIES))
        for sev, (days, escalate) in v.items():
            if days is not None and days <= 0:
                raise ValueError("sla[" + sev + "] deadline must be positive or null")
            if escalate is not None and escalate <= 0:
                raise ValueError("sla[" + sev + "] escalation must be a positive number of days or null")
            if escalate is not None and (days is None or escalate >= days):
                raise ValueError("sla[" + sev + "] escalation must come before the deadline")
        return v

    @field_validator("tiers")
    @classmethod
    def _tiers_ok(cls, v: list[Tier]) -> list[Tier]:
        names = [t.name for t in v]
        if len(names) != len(set(names)):
            raise ValueError("tier names must be unique")
        return v


def load(path: str | Path) -> OrgProfile:
    """Read and validate a profile. Any error names the file and the field."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit("org profile not found: " + str(p))
    except json.JSONDecodeError as e:
        raise SystemExit("org profile " + str(p) + " is not valid JSON: " + str(e))
    try:
        return OrgProfile.model_validate(data)
    except Exception as e:  # pydantic.ValidationError, kept readable for operators
        raise SystemExit("org profile " + str(p) + " is invalid:\n" + str(e))


def apply(profile: OrgProfile) -> None:
    """Make this profile the active configuration for the rest of the run.

    Downstream modules read config.* at call time, so overriding the module
    values here reaches triage (routing, deadlines) and coherence (staleness).
    Called once, at startup, by the CLI.
    """
    config.ROUTING = {k: list(v) for k, v in profile.routing.items()}
    config.SLA = {k: tuple(v) for k, v in profile.sla.items()}
    config.CONTACT_STALE_MEDIUM_DAYS = profile.thresholds.contact_stale_medium_days
    config.CONTACT_STALE_HIGH_DAYS = profile.thresholds.contact_stale_high_days
    config.PLAN_STALE_MONTHS = profile.thresholds.plan_stale_months
