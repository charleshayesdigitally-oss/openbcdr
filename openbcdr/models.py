"""Typed records. The Pydantic models double as the extraction schema for the API."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
Coverage = Literal["full", "partial", "gap", "insufficient_evidence"]

# Plans are written per business unit or per IT application - there is no single
# enterprise plan to evaluate. That shapes the whole model: `scope_name` says
# WHICH unit or application a plan covers, and `depends_on_scopes` names the
# other plans it relies on, because a per-application plan can be internally
# perfect and still promise a recovery time its upstream dependency cannot meet.
PlanScope = Literal["business_unit", "it_application", "shared_service", "vendor"]


# ---------------------------------------------------------------- plan extract

class RtoRpo(BaseModel):
    system: str
    rto_hours: Optional[float] = None
    rpo_hours: Optional[float] = None
    # Systems this one must be available before, i.e. this system feeds them.
    feeds: list[str] = Field(default_factory=list)


class Contact(BaseModel):
    name: str
    title: str = ""
    role: str = ""
    is_backup: bool = False
    phone: str = ""
    email: str = ""
    last_verified: Optional[date] = None


class Vendor(BaseModel):
    name: str
    service: str = ""
    criticality: str = ""
    contact_documented: bool = False
    rto_documented: bool = False
    escalation_documented: bool = False
    sla_on_file: bool = False


class Procedure(BaseModel):
    name: str
    scenario: str = ""
    has_trigger_condition: bool = False
    has_step_by_step: bool = False
    responsible_party: str = ""
    has_success_criteria: bool = False
    systems_referenced: list[str] = Field(default_factory=list)
    # Verbatim text so unresolved-marker checks run on source, not a summary.
    raw_text: str = ""


class TestRecord(BaseModel):
    test_date: Optional[date] = None
    scenario: str = ""
    result: str = ""
    plan_version_tested: str = ""
    lessons_documented: bool = False


class PlanSection(BaseModel):
    number: str
    title: str
    owner: str = ""
    text: str = ""


class PlanExtract(BaseModel):
    """Spec section 4.1 structured output."""
    plan_id: str
    plan_scope: PlanScope = "business_unit"
    # The business unit or IT application this plan covers. A plan that does not
    # name its own scope cannot be reconciled against the plans it depends on.
    scope_name: str = ""
    # Other business units, applications or shared services this scope relies on
    # to recover. Names should match the scope_name of their own plans.
    depends_on_scopes: list[str] = Field(default_factory=list)
    plan_version: str = ""
    last_approved: Optional[date] = None
    approver: str = ""
    next_review_due: Optional[date] = None
    rto_rpo: list[RtoRpo] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    critical_vendors: list[Vendor] = Field(default_factory=list)
    critical_systems: list[str] = Field(default_factory=list)
    system_inventory: list[str] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    scenarios_covered: list[str] = Field(default_factory=list)
    tests: list[TestRecord] = Field(default_factory=list)
    sections: list[PlanSection] = Field(default_factory=list)
    bia_last_updated: Optional[date] = None


# ------------------------------------------------------------- standards index

class Requirement(BaseModel):
    """Spec section 5.2. One discrete, searchable regulatory obligation."""
    req_id: str
    source: str
    section: str
    requirement: str
    mandatory: bool = True
    applicability: list[str] = Field(default_factory=lambda: ["all_banks"])
    related_standards: list[str] = Field(default_factory=list)
    related_regulations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    last_confirmed: Optional[date] = None
    as_of_version: str = ""
    # The passage this record was drafted from, copied verbatim. It travels with
    # the record so a validator can check the paraphrase against its source
    # instead of re-finding it in a 150-page booklet. Empty on hand-written
    # records; populated by `decompose` and verified against the source text.
    source_quote: str = ""
    validated_by: str = ""
    # Severity to assign when this requirement is unmet. Curated, not inferred:
    # the spec's severity matrix is a policy decision, not a model judgment.
    gap_severity: Severity = "high"
    # A compliance tool that invents a requirement is worse than no tool. Every
    # record ships false and must be confirmed against the primary source by a
    # human before any examiner-facing output is generated from it.
    validated_by_human: bool = False


# ------------------------------------------------------------------- findings

class CoverageFindingDraft(BaseModel):
    """What the model is allowed to say. This is the extraction schema.

    Deliberately has no `evidence_verified` field - the model does not get to
    assert that its own quote is real.
    """
    req_id: str
    coverage: Coverage
    rationale: str
    evidence_quote: str
    plan_section: str
    recommended_action: str


class CoverageBatch(BaseModel):
    findings: list[CoverageFindingDraft]


class CoverageFinding(CoverageFindingDraft):
    """A draft after quote verification. Set by the verifier, never the model."""
    evidence_verified: bool = False
    # Which revision of the plan this finding was assessed against. Set by the
    # pipeline, never by the model — same reason as evidence_verified. Empty on
    # findings that predate this field, which are treated as not-stale rather
    # than as failures. (Codex review 2026-09-08, finding 4.)
    plan_version: str = ""


class Gap(BaseModel):
    """Spec section 5.4 gap registry record."""
    gap_id: str = ""
    identified_date: Optional[date] = None
    identified_by: str = ""
    plan_id: str = ""
    plan_section: str = ""
    title: str = ""
    description: str = ""
    standard_ref: str = ""
    regulatory_ref: str = ""
    severity: Severity = "medium"
    status: str = "open"
    assigned_to: str = ""
    deadline: Optional[date] = None
    resolution_notes: str = ""
    closed_date: Optional[date] = None
    exception_approved: bool = False
    examiner_visible: bool = True
    source: str = "compliance"  # compliance | coherence | trend
    # What produced this gap: a req_id, or "coherence:<check>". Calibration
    # groups rejections by this, so without it nothing can be tuned.
    origin_key: str = ""


class FeedItem(BaseModel):
    """Spec section 4.2 regulatory feed item."""
    item_id: str
    source: str
    title: str
    published: Optional[date] = None
    summary: str = ""
    url: str = ""
    references_continuity: bool = False
    applies_to_banking: bool = False
    applies_to_midsize: bool = False
    references_primary_regulator: bool = False
    relevance_score: int = 0
    disposition: str = ""  # alert | digest | archive
