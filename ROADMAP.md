# ROADMAP — plan drafting + organisation profile

*Roadmap for the next features. Phase 0 is done; the rest is not yet built. Contributors: read CLAUDE.md, CONTRIBUTING.md and LESSONS-LEARNED.md first.*

> **Goal:** help write BC/DR plans as well as find gaps in them, and let each organisation that uses it customise it to itself.

## The wall (read before anything else)

- This repo is a general framework on **public** standards. Nothing from any employer goes in: no policy, standard, template, tier model, term, test result or **revision feedback**. Ideas come from public frameworks, public talks and contributors' own designs, never from any organisation's internal work.
- Org-specific values live only in gitignored `*.local.*` files. The sample org profile shipped in git is fictional.
- Existing rules still hold: never assert a requirement that is not in the index; every coverage claim quotes the plan verbatim; every model call returns a schema-validated object.

## Who it's for — BOTH

Two starter profiles ship in git, both fictional:
- **`org/starter-bank.json`** — today's behaviour (FFIEC, FINRA, NIST; regulated-institution defaults). Public standards only.
- **`org/starter-small-business.json`** — no regulator; plain-language terms; tiers sized for a small team; checks against a small-business index source set.

**Small-business index sources** must be public, free and license-checked before any record is written (the ISO ban stands). Candidates to verify, not yet confirmed: NIST SP 800-34 (already in), FEMA/Ready.gov business continuity planning materials, NIST small-business cybersecurity guidance. Same `decompose` → human `validate` gate as today.


## Phase 0 — Open-source ready (do this first)

Before the repository goes public, every item below is done and a full-history scan is clean.

1. **License: Apache-2.0** (`LICENSE` + `NOTICE`). Chosen over MIT for the explicit patent grant, which matters once others contribute.
2. **DCO sign-off:** every commit carries `Signed-off-by:`; a CI check refuses unsigned commits. Keeps provenance clear.
3. **Contributor boundary = the existing guard, extended to outsiders:**
   - PR template with a required checkbox: "Public, free sources only. No employer plans, policies, data, names or test results."
   - CI (GitHub Actions) runs `python tests/run_all.py` on every PR, plus `tools/ci_checks.py` (no tracked `*.local.*` path, file or directory; DCO on every commit, merges included). **CI cannot scan for organisation names**: that pattern list is private by design and never leaves the maintainer's machine, so the maintainer runs the local leak scan before every merge.
   - `CONTRIBUTING.md` states the standards rule in plain words: public, free, license-checked sources; no ISO text; every index record goes through `decompose` → human `validate`.
4. **Basics:** `CONTRIBUTING.md`, `SECURITY.md` (private reporting), `CODE_OF_CONDUCT.md` (Contributor Covenant), issue templates, branch protection on `main` (PR + passing CI + owner review required).
5. **README disclaimer, near the top:** a decision-support tool; it does not certify compliance or replace a qualified professional; every finding is for a human to review.
6. **README reframe:** lead with "a general BC/DR framework on public standards"; drop the "regional bank system design" framing (bank becomes one starter in Phase 1).
7. **No company branding** in the repository (product, offer or company names).
8. **Pre-flip checks:** re-run the full-history scan; open `OpenBCDR-Handbook.docx` and the `dist/` bundle and confirm they hold nothing org-specific (or remove them); confirm no secrets in history (`git log -p` for key patterns).

**Done when:** all eight items land, CI is green on a test PR (and goes red on a test PR that adds a fake `*.local.*` file and one with an unsigned commit), and the maintainer flips visibility.

## Phase 1 — Organization profile + questionnaire (drafting depends on it)

**Status:** 1a (profile format, both starters, `--org` wired into applicability, routing, deadlines, staleness and test rules) and 1b (the `onboard` questionnaire: interactive, export/import document, `--update --section`) are built. 1c (organisation-aware prompts: the org's words and good-practice framing in the plan-reading prompts, and `openbcdr instructions` to fill AGENT-INSTRUCTIONS.md) is built. Still bank-specific and left for later: the regulatory-notice classifier (`trend.py`) and index building (`decompose.py`), which only make sense for regulated sources. Known limit: the rendered instructions for an unregulated organisation still carry the "Standards currency" section (which regulators to watch for index updates); it governs index upkeep, not findings, and gets an organisation-aware version later. The "own policies" upload is deferred to 1c, since org-policy records need their own provenance rules. Profiles are JSON rather than YAML: no new dependency, and most people fill them in through the questionnaire.

One file per org: `org/<slug>.local.json` (gitignored) plus the two tracked, fictional starters (`org/starter-bank.json`, `org/starter-small-business.json`). Loaded by `--org <path>`; extends today's `--profile` (`cli.py:30-33`), which keeps working.

Profile holds:
- **Identity:** org name, sector, size, regulators, applicability tags (replaces the hardcoded `DEFAULT_PROFILE`).
- **Recovery tiers:** named tiers with RTO/RPO targets (e.g. Tier 1 = 4h/15m). Coherence checks validate plans against these.
- **Roles + routing:** the org's titles mapped to the existing roles (`risk_owner`, `compliance_officer`…); SLA per severity. Moves `SLA`, `ROUTING` and thresholds out of `config.py` constants, with today's values as the default.
- **Terminology:** org term → framework term (e.g. "Recovery Playbook" → plan), used in prompts and reports.
- **Plan template:** the org's required section list, in order, with which index tags each section must cover.
- **Org policies (optional):** extra index records under `source: org-policy`, with their own provenance field and the same human-validation gate. They never mix with public records in a report without being labelled as the org's own.

**The onboarding questionnaire (how a company customizes it):**
- `openbcdr onboard --starter <bank|small-business>` walks the company through questions in plain language and writes `org/<slug>.local.json`. Every answer is validated against the profile schema before the file is written; nothing is guessed, and a skipped question keeps the starter's default (marked as a default in the file).
- The same questions also export as a fill-in document (`openbcdr onboard --export questionnaire.md`) for someone who won't run a command line, and import back with `onboard --import`. One question source drives both, so they can't drift.
- Questions cover, in order: who you are (name, sector, size, regulators if any) · what must keep running (critical functions/services, in priority order) · how fast (recovery tiers and target times) · who's who (owner per function, who gets findings, escalation) · your words (terms you use for plans, tiers, roles) · your plan shape (required sections, or "use the starter's") · your own policies (optional upload, labelled as the org's).
- Re-running `onboard --update` changes one section without redoing the rest, so the profile can be updated as the company changes.

Also: replace the hardcoded "mid-size regional bank" wording in prompts and `AGENT-INSTRUCTIONS.md` with profile values, and generate the prompt-only form from the profile (some deployments run prompt-only).

**Done when:** `onboard` produces a valid profile from both starters (tested with scripted answers, and with an exported-then-imported questionnaire); each starter changes tiers, routing, terminology and template in a run, verified by tests; with no `--org` given, output is byte-identical to today's; the leak scan [6c] also scans for profile values if a `*.local.json` is present.

## Phase 2 — Gap remediation drafts (smaller, safer first step into writing)

For each open gap, `openbcdr draft-fix <gap_id>` proposes plan text that would close it.
- Output is a structured `RemediationDraft`: target section, proposed text, the `req_id`s it addresses, and `[ORG: ...]` placeholders for every fact the agent cannot know (names, numbers, vendors, contacts, RTOs not in the profile). **It never invents a fact.**
- It is a proposal for the plan owner, never an edit to the plan (the existing "no authority to alter a plan" rule stands).

## Phase 3 — Full plan drafting from the template

`openbcdr draft-plan --org <profile> --scope <business_unit|it_application|...> --intake <answers.md>`
- **Intake first:** a structured questionnaire built from the template + index (critical functions, dependencies, vendors, contacts, tiers). The draft is built from the answers; anything unanswered stays an `[ORG: ...]` placeholder.
- Output: a structured `PlanDraft` per template section, each section listing the `req_id`s it is meant to satisfy.
- **Self-check, labelled honestly:** run coherence (fine, deterministic) and compliance on the draft, but report coverage as `drafted-to-cover`, never `full`. The model wrote both the text and the quote, so that's circular, not evidence. Real coverage is only claimed after a human fills the placeholders and the plan is re-ingested as a normal plan.

## Standards for every phase

- Tests in `tests/run_all.py` style, including a check that goes red when each new guard is broken (mutation-test it).
- Codex (a different model family) reviews real logic before it's committed; guard/boundary changes are reviewed before they run.
- Update README "Not built", CLAUDE.md and LESSONS-LEARNED.md as each phase lands.

## Design rule from industry practice — the `practice-advisory` finding class

Most ideas below are good practice raised at industry conferences, not regulatory requirements. They get their own finding class: `practice-advisory`. No index `req_id`, no curated severity, never counted in the compliance score, and the report labels them plainly ("industry practice, not a regulatory requirement"). Any regulation a speaker mentioned is a **candidate index source to verify against primary text**, never a requirement on a speaker's say-so.

## Improvements from DRJ Fall 2026 public sessions (added 2026-10-04)

*Ideas only, paraphrased from what speakers said publicly (session codes in brackets). No vendor products, no statistics, nothing from any employer. Ranked by value. DET = deterministic code, MODEL = model judgment with a verbatim quote.*

1. **Claims must be checkable** (existing analysis, Phases 2-3; MODEL + DET) [AI-enabled resilience session]. Flag vague recovery claims ("we can recover critical systems quickly"). Every claim needs a condition, basis, named owner, rehearsal date and review-by date that expires. `draft-fix` rewrites into that shape with `[ORG: ...]` slots; Phase 3 drafts every claim that way.
2. **Open findings from past tests: fixed or just documented?** (existing analysis; DET) [MC-6, GS-5]. Store test findings with owner/due/status; flag any open past due or repeated across consecutive tests.
3. **Measured recovery vs RTO** (Phase 1 tiers, Phase 3, coherence; DET) [MC-6]. Optional `measured_recovery_minutes` + `clock_end_definition` per test ("business can transact", not "server pings"). Flag measured > tier RTO, RTOs with no clock endpoint, and component RTOs that can't reach the service RTO.
4. **Hidden shared dependencies, inherited exposure, key people** (coherence; DET graph) [GS-7, GS-4, BT3, MC-6, GS-5]. `shared_upstream` edges and person nodes: "redundant" alternates must not share an upstream; a critical node with one named person is flagged; effective exposure = worst of own and inherited (advisory, never a severity change).
5. **AI as a mapped dependency** (Phase 1 questionnaire, Phase 3 template, advisory; DET + MODEL) [GS-2, BT1, BT5, MC-6, AI-enabled resilience session — five sessions independently]. Questionnaire asks whether any critical process relies on an AI tool; captures the tool, a tested fallback (can be non-AI automation), recovery targets for AI assets (prompts, configs, vector stores), and which recovery actions need a person, decided before deployment. Flag an AI dependency with no fallback or no test date.
6. **Escalation thresholds + declaration authority, after hours** (Phase 1 roles, Phase 3, analysis; DET + MODEL) [GS-4, BT5, GS-5, MC-6]. Profile gets `declaration_authority` and `escalation_thresholds` (tier, duration, visibility). Check each authority has a named backup and off-hours coverage; Phase 3 leaves `[ORG: ...]` until answered.
7. **Restore the house, not the furniture** (Phase 3 procedure, analysis; MODEL + DET) [BT1, BT4, MC-6]. Recovery steps ordered backup → infrastructure → network → identity → application → transact. Model check (quoted) that procedures cover more than data; code checks a last full-restore-test date separate from backup-job success; cyber plans require a "confirm clean before restore" step.
8. **Third-party timing and evidence** (coherence, Phase 1, Phase 3; DET) [GS-4, GS-5, BT5, BT3]. Vendor fields: `notice_window_hours`, `after_hours_contact`, `test_observation_right`, `evidence_definition`, `fourth_parties`. Flag notice window + internal SLA > service RTO and critical vendors with no after-hours contact; criticality uses sole-source and impact, not spend alone. Candidate sources to verify: FFIEC outsourced-technology material; EU DORA (EU entities only).
9. **Questionnaire: one service first** (Phase 1) [GS-7, BT4, GS-4, AI-enabled resilience session, GS-5]. `onboard` offers a "start with one service" mode tracing technology, third-party, facility and people dependencies; a required question sets ONE definition of "critical" and the test for it.
10. **Exercise objectives, follow-up cadence, findings table** (Phase 3, test-recency check; DET) [SWS-4 deck]. Objective / How satisfied / Limitations tied to an RTO or procedure; follow-up dates derived in code (critique +1 day, draft +7, final +14, plans revised +30); findings table Ref / Observation / Recommendation / Owner / Due / Status feeds item 2.
11. **Honest drafts** (Phase 2-3 schemas) [AI-enabled resilience session, GS-2, BT3]. Every drafted statement carries `basis` (`intake_answer | inference | assumption`) and `limitations`. The goal is an 80% draft people finish, never a finished plan.
12. **Staleness and change triggers** (coherence, Phase 1 thresholds; DET) [MC-6, BT4, GS-5]. Sections carry `last_reviewed` / `review_by`; thresholds per tier in the profile; a change on a listed dependency flags the sections it touches.
13. **Name normalization** (Phase 1 terminology, coherence; DET) [AI-enabled resilience session, BT3]. Alias map for systems and vendors resolved before other checks; flag one entity under several names or with conflicting RTOs across BIA, plan and inventory.
14. **What to test next** (advisory ranking) [GS-7]. Rank next test slots by impact × threat fit × vulnerability; separate "needs evidence" gaps from "just fix it" gaps; a workaround never lowers the rank. Never changes curated severity.
15. **`draft-exercise` (optional — the only new command)** [GS-4, GS-5, BT5, SWS-4, GS-6]. Structured exercise outline from the profile and the plan's gaps: compound events, timed injects, roles-only tabletops, AI-loss / vendor-failure / proactive-outage scenarios; executive version aimed at the strategic role.

**Small-business starter:** items 1, 2, 3, 6, 9, 11 and 12 carry over in plain wording; vendor, cyber and AI items get softer defaults.
