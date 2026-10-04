"""Load the synthetic plan into the store WITHOUT calling the API.

`ingest` costs money and needs a key. This writes the same structured record by
hand so the deterministic half of the pipeline - coherence, triage, the gap
registry, the audit chain, the report - can be exercised and tested for free.

    python samples/load_sample.py [--db bcdr.sqlite3]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openbcdr import config  # noqa: E402
from openbcdr.models import (  # noqa: E402
    Contact, PlanExtract, Procedure, RtoRpo, TestRecord, Vendor,
)
from openbcdr.store import Store  # noqa: E402

SAMPLE = PlanExtract(
    plan_id="APP_PAYPROC_v4",
    plan_scope="it_application",
    scope_name="Payment Processing",
    depends_on_scopes=["Core Banking", "Enterprise Network", "Identity Services"],
    plan_version="4.0",
    last_approved=date(2026, 1, 15),
    approver="Chief Risk Officer",
    next_review_due=date(2027, 1, 15),
    bia_last_updated=date(2024, 6, 1),
    critical_systems=["Payment Processing", "Payment Gateway", "Settlement Batch",
                      "Treasury Interface"],
    system_inventory=["Payment Processing", "Payment Gateway", "Settlement Batch",
                      "Treasury Interface", "Core Banking"],
    rto_rpo=[
        # Core Banking recovers in 4h but Payment Processing, which depends on
        # it, promises 2h. The conflict is visible here only because this plan
        # restates its upstream's objective; when it does not, the same defect
        # is invisible to a single-plan analyzer. That is the cross-plan blind
        # spot the README calls out.
        RtoRpo(system="Core Banking", rto_hours=4, rpo_hours=1,
               feeds=["Payment Processing"]),
        RtoRpo(system="Payment Processing", rto_hours=2, rpo_hours=0.5,
               feeds=["Payment Gateway", "Settlement Batch"]),
        RtoRpo(system="Payment Gateway", rto_hours=2, rpo_hours=0.5),
        RtoRpo(system="Settlement Batch", rto_hours=6, rpo_hours=1),
        # Treasury Interface is critical with no objectives - planted.
    ],
    contacts=[
        Contact(name="J. Rivera", title="VP Payments Technology", role="Application Owner",
                phone="555-0101", email="j.rivera@example.invalid",
                last_verified=date(2026, 7, 2)),
        Contact(name="M. Okonkwo", title="Director, Payments", role="Application Owner",
                is_backup=True, phone="555-0102", email="m.okonkwo@example.invalid",
                last_verified=date(2026, 7, 2)),
        # Stale contact, and the only person in this role - planted twice over.
        Contact(name="J. Smith", title="Facilities Manager", role="Facilities Lead",
                phone="555-0140", email="j.smith@example.invalid",
                last_verified=date(2024, 11, 15)),
        Contact(name="A. Bhatt", title="VP Infrastructure", role="Technology Lead",
                phone="555-0170", email="a.bhatt@example.invalid",
                last_verified=date(2026, 6, 20)),
    ],
    critical_vendors=[
        Vendor(name="FIS", service="core processing", criticality="critical",
               contact_documented=True, rto_documented=False,
               escalation_documented=False, sla_on_file=False),
        Vendor(name="Regional Telco", service="network transport", criticality="critical",
               contact_documented=True, rto_documented=True,
               escalation_documented=True, sla_on_file=True),
    ],
    scenarios_covered=["cyber incident", "facility loss", "data center outage", "pandemic"],
    procedures=[
        Procedure(name="5.1 Data Center Outage", scenario="data center outage",
                  has_trigger_condition=True, has_step_by_step=True,
                  responsible_party="Technology Lead", has_success_criteria=True,
                  systems_referenced=["Payment Processing", "Core Banking"],
                  raw_text="Trigger: loss of primary data center confirmed by Technology Lead. "
                           "Steps: declare, fail over Core Banking to the secondary site, "
                           "validate transaction posting, notify regulators."),
        Procedure(name="5.2 Facility Loss", scenario="facility loss",
                  has_trigger_condition=True, has_step_by_step=True,
                  responsible_party="", has_success_criteria=False,
                  systems_referenced=[],
                  raw_text="Trigger: banking center unavailable for more than four hours. "
                           "Responsible party: TBD. Success criteria: not yet defined."),
        Procedure(name="5.3 Cyber Incident", scenario="cyber incident",
                  has_trigger_condition=True, has_step_by_step=True,
                  responsible_party="Chief Information Security Officer",
                  has_success_criteria=True,
                  # OldCore is not in the system inventory - planted.
                  systems_referenced=["OldCore"],
                  raw_text="Trigger: security operations declares a confirmed compromise. "
                           "Steps: isolate affected segments, engage incident response "
                           "retainer, restore from the last clean backup on OldCore, "
                           "validate integrity."),
        # No pandemic procedure - planted.
    ],
    tests=[
        TestRecord(test_date=date(2025, 10, 20), scenario="data center outage",
                   result="pass", plan_version_tested="3.0", lessons_documented=True),
    ],
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()

    raw = (Path(__file__).resolve().parent / "sample_plan.md").read_text(encoding="utf-8")
    store = Store(args.db)
    store.log("operator", "boundary_attestation", "samples/sample_plan.md",
              mode="sandbox", attested=True, note="synthetic fixture, no API call")
    pv = store.save_plan_version(
        plan_id=SAMPLE.plan_id, version=SAMPLE.plan_version, mode="sandbox",
        source_path="samples/sample_plan.md", raw_text=raw,
        extract=SAMPLE.model_dump(mode="json"))
    print("Loaded synthetic plan as plan_version " + str(pv) + " in " + args.db)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
