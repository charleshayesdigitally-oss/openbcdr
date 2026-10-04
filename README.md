# OpenBCDR

A general, open-source framework for business-continuity and disaster-recovery
(BC/DR) plans, built on **public standards** (FFIEC BCM Booklet 2019, FINRA Rule
4370, NIST SP 800-34 Rev. 1). It ingests a plan, checks it for internal
coherence and against a standards index, emits explainable findings with
verified evidence quotes, routes them by severity, and writes a hash-chained
audit trail with a separate head checkpoint. That gives limited tamper evidence,
not immutability against someone who can change both files.

> **Disclaimer.** This is decision-support software. It does not certify
> compliance with any regulation or standard, it does not replace a qualified
> BC/DR professional, and every finding is for a person to review before anyone
> acts on it. Licensed under Apache-2.0 and provided "as is", without warranty
> (see `LICENSE`).

It started from a private system design for a risk-owned, human-in-the-loop
BC/DR agent (sections cited below refer to that design). It is **not** the
full platform: no Airflow, no Postgres, no Pinecone, no Retool, no GRC
connector. Those are integrations around this core, and every one of them is
cheaper to build once the analysis has proven itself.

Contributions welcome: see `CONTRIBUTING.md`. Public sources only.

---

## The two design decisions worth knowing before you read the code

**1. The model judges; Python decides facts.**

The original design runs all four analyzers through the LLM. Here, only two use it:

| Layer | Engine | Why |
|---|---|---|
| Plan extraction | Claude | Input is prose by a dozen authors over ten years |
| Compliance coverage | Claude | "Does this language satisfy this obligation" is a judgment |
| **Coherence** | **Pure Python** | RTO(A) ≤ RTO(B), date arithmetic, marker strings, set differences |
| Feed classification | Claude | Judgment over prose |
| **Feed scoring** | **Pure Python** | The design's own weighted sum |

Coherence is where an examiner will say *show me why*. An inequality between
two numbers has one right answer; routing it through a model buys nothing and
costs reproducibility. It also means the coherence pass is free and instant, so
it can run on every save while compliance runs nightly.

**2. Evidence is verified, not trusted.**

Every `full` or `partial` coverage finding must carry a verbatim quote from the
plan. `compliance.verify_quote` checks that quote against the source text
before the finding stands. Whitespace and smart quotes are normalized — a PDF
conversion shouldn't trip the alarm — but reworded, merged, or invented text
fails. A failed quote isn't a near-miss; it's the model manufacturing coverage,
which in a compliance tool is the worst available failure mode. Those findings
are forced to `insufficient_evidence`, excluded from the score, and listed under
their own heading in the report.

`insufficient_evidence` never counts as coverage anywhere.

---

## Install and run

```bash
pip install -r requirements.txt          # anthropic, pydantic
```

The whole deterministic half runs with **no API key and no spend**:

```bash
python samples/load_sample.py --db test.sqlite3
python -m openbcdr --db test.sqlite3 coherence --plan APP_PAYPROC_v4 --open-gaps
python -m openbcdr --db test.sqlite3 gaps --notifications
python -m openbcdr --db test.sqlite3 report --plan APP_PAYPROC_v4
python -m openbcdr --db test.sqlite3 audit-verify
```

The sample is a fully invented bank with defects planted on purpose — an RTO
dependency conflict, a critical system with no objectives, a stale contact, a
`TBD` responsible party, a vendor missing its recovery expectation, a procedure
referencing a decommissioned system, an untested current version.

The two commands that call the API:

```bash
python -m openbcdr ingest plans/payment-processing.md --mode internal
python -m openbcdr analyze --plan APP_PAYPROC_v4 --open-gaps
```

Model defaults to `claude-opus-5`; override with `BCDR_MODEL` / `BCDR_EFFORT`.

### Draft a fix for a gap

```bash
python -m openbcdr draft-fix GAP-2026-0004 --out fix.local.md   # calls the API
```

The model proposes plan text for one open gap and labels where each statement
comes from. The proposed text may not state any specific value: every number,
date, time, amount, count, frequency or contact is an `[ORG: ...]` placeholder
for the owner to fill. Facts it builds on are shown separately as quotes, each
a whole line or sentence copied word for word from the plan, your profile or the
requirement, and checked. Code refuses the whole draft if any rule breaks.

The value check is a strong net, not a proof: no pattern catches every way to
write a value, and names of people, vendors and systems aren't checked by code.
So a draft is always a proposal. The gap stays open and nothing counts as
coverage until a person reviews it, fills the placeholders and the plan is
re-ingested.

### Customise it to your organisation

Pass an organisation profile with `--org` (before the command). Two fictional
starters ship in `org/`; copy one to `org/<your-org>.local.json` (gitignored, never
committed) and edit it:

```bash
python -m openbcdr --org org/starter-small-business.json --db test.sqlite3 coherence --plan APP_PAYPROC_v4
```

A profile sets which standards apply, who gets each finding and how fast, when
contacts and plans count as stale, and whether an annual test is required. With
no `--org`, the built-in defaults apply (the same as `org/starter-bank.json`).

Build a profile by answering plain questions instead of editing JSON:

```bash
python -m openbcdr onboard --starter small-business --out org/acme.local.json      # asks at the prompt
python -m openbcdr onboard --starter bank --export questionnaire.md                # fill-in document
python -m openbcdr onboard --starter bank --import questionnaire.md --out org/acme.local.json
python -m openbcdr onboard --update org/acme.local.json --section people           # change one part
```

Answers are checked as a whole before anything is written, a blank answer keeps
the starting value (and is listed in the profile's `defaults_used`), and the
file name must contain `.local.` so a real profile is never committed.

With a profile, the plan-reading prompts also learn the organisation's own words
(a "playbook" is a plan), and an organisation no regulator oversees gets its
findings framed as good practice, never as violations. To run the agent without
the codebase, generate its instructions already filled in:

```bash
python -m openbcdr --org org/acme.local.json instructions --out acme-instructions.local.md
```

---

## Running the agent without this codebase bound to it

A chat agent, a Project, anything with no code execution can still do the
judgment work - it has the plan and the index in its context. What it cannot do
is remember anything between runs, and, more seriously, **nothing verifies its
evidence quotes**. Prompt-only mode has no anti-fabrication check at all.

The bridge closes that. The agent emits a JSON block at the end of its output;
one command imports it and re-imposes everything the code half provides:

```bash
# once per plan - stores the text so quotes can be checked. No API call.
python -m openbcdr register-plan plans/payment-processing.md     --plan APP_PAYPROC_v4 --plan-version 4.0 --mode internal

# after each evaluation - paste the agent's JSON block into a file first
python -m openbcdr import-findings findings.json
```

Import re-applies quote verification (a claim whose quote is not in the plan is
downgraded exactly as on a local run), assigns severity, routing and deadlines
from the curated index rather than the agent's opinion, deduplicates against
previous evaluations by fingerprint, and writes the audit trail.

It refuses, loudly and to a human: a `req_id` that is not in the index, the same
requirement reported twice, an unrecognised coverage value. Coherence findings in
the payload are ignored - those checks are deterministic and free, so run
`coherence` rather than taking a model's word for arithmetic.

Feed the current registry back in via the run prompt's `KNOWN OPEN GAPS` field
and the loop closes: `gaps` out, evaluate, `import-findings` in.

---

## Modes

- `sandbox` — development, demos, anything outside the institution's own
  environment. Public frameworks and synthetic or sanitized plans only.
  Fail-closed: refuses unless the document passes the deny-list scan **and** the
  caller passes `--attest-synthetic`. The attestation is logged with an actor.
- `internal` — deployed inside the institution, reading its own plans. The
  deny-list still runs, as a stray-document check (someone *else's* confidential
  material in the ingestion folder), not as a block.

Tune `config/boundary_patterns.txt` for the deployment. Anything naming a
specific institution goes in `config/boundary_patterns.local.txt`, which is not
tracked. For an in-house install, empty the shipped file and list third-party
markers instead.

Sandbox mode refuses every document while that local file is missing (a fresh
clone, a new machine), so protection never drops quietly to the generic list.
Create the file, or set `BCDR_ALLOW_NO_LOCAL_PATTERNS=1` to accept generic-only
protection on purpose. Internal mode is unaffected.

---

## The standards index is the part that needs a human

`standards/drafted-index-2026-09-04.json` holds 73 records drafted from primary
source text - FFIEC BCM Booklet Nov 2019 (45), FINRA Rule 4370 (22) and NIST SP
800-34 Rev. 1 (6). Each carries a `source_quote` that was mechanically verified
against the source before the record was written. They are GROUNDED but not
VALIDATED: every one is `validated_by_human: false`, and while any requirement in scope is still
false the report prints a NOT-EXAMINER-READY banner and **withholds the score**;
`report --examiner` refuses to render at all.

That refusal is the feature. A compliance percentage computed from requirement
text nobody checked is a number that looks like evidence and isn't. To validate
a record: open the primary source, confirm the section and the obligation,
replace `requirement` with accurate language, set `last_confirmed` and
`as_of_version`, then flip the flag. Section 5.2 sizes the real index at 200–300
records.

⚠️ **FFIEC records are `mandatory: false` deliberately.** The booklet says of
itself (p.2): *"This booklet does not impose requirements on entities. Instead,
this booklet describes practices that examiners may use to assess an entity's BCM
function."* `gap_severity` is a separate field and still carries examination
consequence. FINRA 4370 is phrased in *must* and is the only mandatory source in
the index.

⚠️ **FFIEC quotes verify against an EXTRACT**, not the whole booklet -
`standards/sources/ffiec-bcm-2019-extract.txt` holds the sections the records were
drafted from. Validate against the booklet itself; the extract only catches
transcription drift between it and the records.

`gap_severity` is curated per requirement, not inferred — the design's severity
matrix is a policy decision, not a model judgment.

---

## Audit trail

`audit_events` is append-only and hash-chained: each row's hash covers the
previous row's hash plus its own canonicalized payload. `audit-verify` walks the
chain and compares its last event to the `.audit-head` sidecar, detecting edits
and deletions while that checkpoint remains trustworthy. A missing checkpoint
produces an explicit warning and exit code 4: internal chain consistency alone
cannot prove that the tail is complete. Preserve the sidecar with the database
when backing up or restoring. Protection against someone who can rewrite both
requires a checkpoint under a separate trust boundary.

Nothing in this code closes a gap, approves an exception, edits a plan, or sends
a notification. `gaps --notifications` prints what *would* be sent, to whom,
with what deadline. Wiring a transport is deliberately a separate, explicit
step.

---

## Tests

```bash
python tests/run_all.py          # 273 checks, no API key, no network, no spend
python tests/test_offline.py     # deterministic pipeline
python tests/test_llm_paths.py   # API paths against a stub transport
```

`test_offline.py` asserts every planted defect is found **and that the compliant
vendor, the complete procedure and the current contacts are not flagged** — a
checker that flags everything is as useless as one that flags nothing. It also
pins `today` to a fixed date, so staleness thresholds can't flap as real time
passes.

`test_llm_paths.py` swaps in a stub client that records the exact request body
and returns canned responses. It verifies the emitted schema is strict at every
nesting level, that the plan sits in the **cached** system prefix while only the
requirement batch varies, that batching maths out, that a fabricated quote is
downgraded, that a requirement the model silently drops surfaces instead of
reading as compliant, and that refusals and `max_tokens` truncation raise rather
than parse as results. Request bodies are checked against the SDK's own
`OutputConfigParam` type (`anthropic` 1.3.0), which confirms `effort` and
`format` legally coexist.

## What is still NOT verified

**No live API call has ever been made.** `ingest.extract`, `compliance.analyze`
and `trend.classify_and_score` are proven up to the wire and no further. Three
things only a real key can settle:

1. Whether the server accepts the generated schema (nested `$defs` is the risky
   part).
2. Whether the model actually honours the verbatim-quoting instruction. If it
   paraphrases habitually, the downgrade path will fire constantly and the
   normalizer in `verify_quote` needs loosening — carefully, because every
   loosening buys back some of the fabrication risk it exists to catch.
3. Real cache behaviour. Run `analyze` on the sample first and watch the
   cache-read counter; the CLI warns on zero across multiple calls, which means
   the plan prefix isn't caching and every batch is re-billing the whole
   document.

---

## Not built (deliberately, from the original design)

- Feed **fetching**. FINRA/Fed/FFIEC/FS-ISAC plumbing is integration work with
  no shared logic. `trend.classify_and_score` takes an already-fetched item.
- **OCR / PDF / DOCX**. `ingest.read_document` raises with a pointer rather than
  half-doing it. Section 9 names Textract or Azure Document Intelligence.
- **Format Corrector** (6.4), the **dashboard** (8), **GRC/SharePoint/DocuSign**
  (9), the **examiner package export** (11.1). All downstream of a working
  analysis.
- **Holiday calendars.** `triage.add_business_days` skips weekends only. Wire a
  real calendar before these deadlines drive an escalation clock in anger.
- The original design's **section 13 open questions** are still open — GRC platform, plan
  repository, notification channel, exception approver, examiner access, FINRA
  member status, charter. Only the last two change behavior here, via the
  applicability profile (`--profile`, default assumes FINRA member, Fed member,
  non-SIFI).

One error in the source spec, corrected in `coherence.check_rto_dependencies`:
section 6.2A states the rule as "if A feeds B, RTO(A) ≤ RTO(B)" and then gives a
worked example that reads the other way round. The implemented rule is that a
dependency cannot be restored before the thing it depends on.

---

## Deployment note

This is written to run wherever the plans already live. Model access, data
retention, and whose API endpoint it calls are governance questions for the
institution's own AI review, not decisions this code makes — it constructs a
zero-arg SDK client and reads credentials from the environment, so pointing it
at an approved endpoint is a client-construction change in `llm.py:client()`
and nothing else.
