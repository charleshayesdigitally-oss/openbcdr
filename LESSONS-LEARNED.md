# Lessons learned building this

Written during the build, 2026-09-04. Each of these cost time or nearly shipped a
defect. They are recorded because the same mistakes are cheap to repeat and
expensive to find twice.

---

## 1. Don't ask for honesty. Require the artifact honesty produces.

This is the load-bearing idea in the whole design, and it showed up three times.

**Findings.** "Be accurate about coverage" is unenforceable. "Supply the verbatim
span that supports it" is, because the span can be checked against the plan. A
model that fabricates coverage now has to fabricate a quote that survives a
string match, which it cannot.

**Standards currency.** "Confirm the standards are current each time" produced a
confident *declaration* — the agent restated the version from the index and
called it confirmed. The index is the claim under test; it cannot be its own
evidence. The fix was to make `CONFIRMED CURRENT` unwriteable without a URL
retrieved and the edition read at the source. No URL, only `UNCONFIRMED` is
permitted.

**Index validation.** "Validate these records" would have been waved through. The
walker refuses to accept a record without a validator name, a section citation
and an edition — three artifacts that only exist if someone actually opened the
source.

The pattern: find the thing that can only exist if the work happened, and make
the claim structurally depend on it.

## 2. Before removing a source, check what only that source covers.

ISO was dropped on cost grounds. ISO 22301 turned out to be the index's **only**
testing requirement. Deleting it would have left the compliance analyzer unable
to assess testing at all while the coherence checker went on flagging test
staleness — findings on one side, nothing to measure against on the other, and
nothing in the system would have said so.

Caught by listing tag coverage before deleting, not after. Testing moved to NIST
SP 800-34 §3.5.

## 3. A gap left visible gets fixed. A gap papered over does not.

Removing ISO also killed the only crisis-communication record. The tempting move
was to write an FFIEC-sourced replacement from memory — a plausible section
number, and nobody would have questioned it. Instead it was left as an explicit
`KNOWN COVERAGE GAP` in the index README.

It was filled from the real booklet a few hours later, precisely because it was
sitting there unresolved. A fabricated citation would have looked finished and
stayed wrong indefinitely.

## 4. Instructions about dull results get skipped.

The standards-currency block was written as advice: "declare currency… if
everything is current, say so in one line." It appeared intermittently, because
reporting *nothing changed* feels like noise worth trimming when there is an
interesting document to analyse.

Three changes made it reliable: framed as a hard output gate ("output that does
not begin with this block is incomplete"), given a fixed one-line-per-source
format instead of prose, and moved all explanation *beneath* the table so the
block itself stays short.

A status grid is far more reliably produced than a paragraph, and far harder to
half-do.

## 5. Prompt-only deployment loses the guarantees, not just the conveniences.

When the agent runs without this codebase bound to it, the obvious losses are
memory-shaped: no gap registry, no deduplication, no SLA clocks. The one that
actually matters is quieter — **nothing verifies evidence quotes.** The main
anti-fabrication mechanism is simply absent, and nothing announces its absence.

`import-findings` exists for that reason. It re-imposes quote verification,
severity from the curated index, dedup by fingerprint, and the audit trail. The
convenience features are a bonus; restoring verification is the point.

## 6. A tool that includes itself in its own output needs line-anchored markers.

`make_transfer.py` ships its own source inside the bundle it writes. Its marker
constants — `MARK = "#>>>>>> FILE: "` — were then parsed as real markers, and
extraction produced a file named `"`.

Anchor delimiters to line start when the payload can contain them as ordinary
text. Obvious afterwards, invisible beforehand.

## 7. Normalise before hashing, not after.

The transfer manifest failed all 40 files at once. Not corruption — the generator
hashed each file as read from disk while the bundle carried a copy normalised to
end with a newline, and the unpacker then stripped one newline too many.

Two symptoms worth separating: a *few* checksums failing means corruption; *all*
of them failing means the two ends disagree about what they are hashing.

Without the manifest this ships clean and surfaces weeks later as a mystery diff
where every file has lost its final newline.

## 8. The verifier earns its place on the way in, not in theory.

Both transfer bugs above were caught by the SHA-256 manifest, during
construction, before anything left the machine. It would have been reasonable to
skip the manifest as over-engineering for a file transfer.

The general form: when you build a checking mechanism, the first thing it catches
is usually your own work.

## 9. Test data that conflates two failure modes measures ordering, not rules.

The import tests reused one `req_id` for both the duplicate case and the
bad-enum case. The dedup check fires first, so the bad-enum assertion failed —
the test was measuring evaluation order while claiming to measure a validation
rule.

Give every failure mode its own fixture. A test that fails for the wrong reason
is worse than no test, because it gets "fixed" by loosening the assertion.

## 10. Deterministic checks belong in code, and the reason is reproducibility.

The source design routed all four analyzers through the model. Coherence checks —
RTO inequalities, date arithmetic, marker strings, set differences — have exactly
one correct answer, and they are the layer an examiner asks you to reproduce.

Being deterministic also makes them free and instant, so they can run on every
save while the compliance pass runs overnight. That was a side benefit, not the
reason.

`import-findings` deliberately **ignores** coherence findings submitted by the
agent. Taking a model's word for arithmetic is the specific thing this split
exists to prevent.

## 11. Read the source's own words about its authority.

Every FFIEC record is `mandatory: false`, because page 2 of the booklet says:
*"This booklet does not impose requirements on entities. Instead, this booklet
describes practices that examiners may use to assess an entity's BCM function."*

It would have been easy, and wrong, to mark 45 records mandatory because they
*feel* mandatory in examination. Severity is tracked separately and still carries
examination consequence — consequence and legal force are different questions and
deserve different fields.

The same reading fixed a contradiction in the source design: §6.2A states the RTO
dependency rule one way and its worked example reads the opposite way. The
booklet settles it (§III.A.3), and the code now cites the booklet rather than an
argument.
