# Payment Processing — Application Recovery Plan

**SYNTHETIC TEST DOCUMENT — NOT A REAL PLAN.** Invented institution, invented
systems, invented people. Written to exercise the analyzers, with defects
planted on purpose. Do not reuse any of it as guidance.

Institution: Fictional Regional Bank, N.A. (illustrative, ~$9B assets)
Plan ID: APP_PAYPROC_v4
Scope: IT application — Payment Processing
Version: 4.0
Last approved: 2026-01-15 by Application Owner, Payments Technology
Next review due: 2027-01-15
BIA last updated: 2024-06-01

Depends on the recovery of: Core Banking, Enterprise Network, Identity Services

---

## Section 1 — Purpose and Scope

This plan covers recovery of the Payment Processing application and the
processes that depend on it. It does not cover recovery of upstream platforms,
which are addressed in their own plans.

## Section 2 — Recovery Objectives

| System | RTO | RPO | Depends on |
|---|---|---|---|
| Payment Processing | 2 hours | 30 minutes | Core Banking |
| Payment Gateway | 2 hours | 30 minutes | Payment Processing |
| Settlement Batch | 6 hours | 1 hour | Payment Processing |

Core Banking is recovered under its own plan at an RTO of 4 hours.

Treasury Interface is in scope for this application. Recovery objectives for
Treasury Interface are being established.

## Section 3 — Emergency Contact Tree

| Role | Name | Title | Phone | Email | Last verified |
|---|---|---|---|---|---|
| Application Owner | J. Rivera | VP Payments Technology | 555-0101 | j.rivera@example.invalid | 2026-07-02 |
| Application Owner (backup) | M. Okonkwo | Director, Payments | 555-0102 | m.okonkwo@example.invalid | 2026-07-02 |
| Facilities Lead | J. Smith | Facilities Manager | 555-0140 | j.smith@example.invalid | 2024-11-15 |
| Technology Lead | A. Bhatt | VP Infrastructure | 555-0170 | a.bhatt@example.invalid | 2026-06-20 |

## Section 4 — Scenarios Addressed

Cyber incident, facility loss, data center outage, pandemic.

## Section 5 — Recovery Procedures

### 5.1 Data Center Outage
Trigger: loss of primary data center confirmed by Technology Lead.
Steps: (1) declare, (2) fail Payment Processing over to the secondary site once
Core Banking is confirmed available, (3) validate transaction posting,
(4) notify regulators.
Responsible party: Technology Lead.
Success criteria: payment transactions posting at the secondary site.

### 5.2 Facility Loss
Trigger: banking center unavailable for more than four hours.
Steps: (1) redirect staff to the alternate site, (2) reroute customer calls,
(3) confirm application reachable from the alternate site.
Responsible party: TBD.
Success criteria: not yet defined.

### 5.3 Cyber Incident
Trigger: security operations declares a confirmed compromise.
Steps: (1) isolate affected segments, (2) engage incident response retainer,
(3) restore from the last clean backup on OldCore, (4) validate integrity.
Responsible party: Chief Information Security Officer.
Success criteria: clean restore validated against the prior business day.

## Section 6 — Critical Third Parties

- **FIS** — core processing. Contact on file. Recovery time expectation not
  documented. No escalation procedure documented. No SLA referenced.
- **Regional Telco** — network transport. Contact on file, escalation procedure
  documented, recovery expectation documented, SLA on file.

## Section 7 — Testing

Last exercise: 2025-10-20, data center outage scenario, result pass, conducted
against plan version 3.0. Lessons learned were documented.

## Section 8 — Communication

The Application Owner is authorised to communicate with internal stakeholders
during a declared event. External and regulatory communication is handled under
the institution's crisis communication procedure.
