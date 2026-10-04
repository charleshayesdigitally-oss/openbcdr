# Building a validated standards index

The index is the only place the agent may assert what a regulator requires.
Until a record is confirmed by a human against the primary source, the report
withholds its score and `report --examiner` refuses to render. That refusal is
the gate this document exists to get you through.

---

## Start much smaller than 300

The design document sizes the real index at 200–300 records. Do not build that
first. A half-validated 300-record index and a fully-validated 30-record one
both produce a report with the score withheld — one took four months.

Start with the mandatory core your plans actually get examined against: the
FINRA 4370 written-plan, annual-review and annual-test obligations, the FFIEC
BCM booklet's BIA and recovery-objective requirements, third-party expectations,
and testing documentation. Roughly 25–40 records. Run real evaluations against
it, see where findings come back thin, and expand into the gap. The index then
grows from evidence rather than from a table of contents.

---

## What counts as one requirement

**One testable obligation** — something a specific plan either satisfies or does
not.

The test: *can you picture the sentence in a plan that would satisfy this?* If
not, it is too abstract. "Maintain a business continuity program" is a heading.
"Identify critical business functions and determine the impact of disruption on
those functions" is a requirement, because you can see the paragraph that
satisfies it.

**Split a clause containing two independently-failable obligations.** FINRA
4370(b) is really two — review annually, *and* update on material change. A firm
can do the first and skip the second, and you want that to surface as one
precise finding rather than a vague partial.

Because plans here are written per business unit and per IT application, prefer
requirement text that reads correctly at that scope. "The plan documents
recovery objectives for the systems in its scope" works for an application plan;
"the institution documents recovery objectives for all critical systems" does
not, and will produce false gaps on every individual plan.

---

## Sources: free only

Free, directly citable, and reproducible: **FFIEC** booklets and InfoBase,
**FINRA** rules and notices, **Federal Reserve** SR letters, **FDIC** Financial
Institution Letters, **OCC** bulletins, **NIST** SP 800-34 / CSF / SP 800-184,
**CIS** Controls.

⛔ **ISO is out of scope — decided 2026-09-04.** ISO 22301 and ISO 27001 cost
roughly $150-200 each per edition, and their requirement text may not be
reproduced in an index that circulates. The program builds on free sources only.
Do not add an ISO record, and do not cite an ISO clause as authority for a
finding.

What that costs, honestly: very little on the regulatory side. No examiner at a
US regional bank cites ISO 22301 — they cite FFIEC, FINRA and the Fed, all free.
NIST SP 800-34 covers most of the same structural ground and is also free. What
you lose is a mapping layer, not coverage.

What it did cost, concretely: ISO 22301 was the seed index's **only** testing
requirement. Removing it would have left the index unable to assess testing at
all, while the coherence checker went on flagging test staleness — findings on
one side, nothing to measure against on the other. Testing coverage moved to
**NIST SP 800-34 Rev. 1 §3.5**, which is free and states the same obligation.

⚠️ **One gap is still open.** Crisis communication had one record, sourced from
ISO 22301, and it was dropped with the rest. Add an FFIEC-sourced replacement
during the build rather than leaving it uncovered — the seed index README carries
the same warning.

**The lesson worth keeping:** before removing a source, check what only that
source covers. Deleting records is a two-minute job; discovering six weeks later
that nothing assesses testing is not.

---

## The workflow

### 1. Decompose (drafts candidates — calls the API)

```bash
python -m openbcdr decompose ffiec-bcm-booklet.txt \
    --source "FFIEC BCM Booklet" --version 2019 \
    --prefix FFIEC --out standards/ffiec-bcm.json
```

Feed it plain text. Every candidate must quote the passage it came from, and the
quote is checked against the source before the candidate survives — a model that
paraphrases produces nothing. The command reports how many were discarded, and
warns loudly if more than a fifth failed, because that means the output should
not be trusted wholesale.

Everything comes back `validated_by_human: false`. `gap_severity` is a
provisional guess; severity is a policy decision belonging to the risk function.

### 2. Validate (confirms records — no API calls)

```bash
python -m openbcdr validate --file standards/ffiec-bcm.json --validator "J. Rivera"
```

Walks the pending records one at a time, showing the draft. For each: open the
primary source, confirm the section and the obligation, correct the text, set
the edition and the severity. Work is saved after **every** record, so an
interrupted session keeps what it did. `skip` leaves a record unvalidated —
use it freely; a record you cannot confirm today should stay pending rather than
be waved through to clear a queue.

The walker refuses to validate a record with no validator name, no section
citation, or no edition. A requirement confirmed against an unnamed edition
cannot be re-checked when the source changes, and a finding without a citation
cannot be explained — so it must not be raisable.

Every validation is written to the audit trail with the validator's name.

### 3. Check status

```bash
python -m openbcdr standards-status --file standards/ffiec-bcm.json
```

Coverage and staleness by source. Records confirmed more than a quarter ago are
flagged for re-confirmation.

---

## Two people, two passes

**Draft and confirm should not be the same pass, ideally not the same person.**
The failure mode is someone writing a record from memory of the booklet and
validating their own paraphrase an hour later. For a compliance artifact that is
the whole ballgame — the index looks validated and is not.

A machine-drafted requirement that survives validation completely unchanged is
more often a sign the validator skimmed than that the draft was perfect. Expect
to rewrite most of them.

---

## What the agent does with an unvalidated index

- Findings still generate, so you can exercise the pipeline.
- The report prints a NOT EXAMINER-READY banner naming the unvalidated records.
- The coverage score is withheld entirely.
- `report --examiner` refuses to render at all.
- `standards.assert_validated()` raises.

None of this is configurable, and that is deliberate. A compliance percentage
built on requirement text nobody checked is a number that looks like evidence.
