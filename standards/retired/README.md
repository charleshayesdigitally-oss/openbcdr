# Retired

`seed-index.json` — the original starter index, retired 2026-09-04.

Its records were paraphrases whose section numbers were copied from the citations
in the source design document, never checked against a primary source. It was
written to exercise the pipeline, and it did that job.

It is superseded by `../drafted-index-2026-09-04.json`, whose records were
drafted from source text actually retrieved from FINRA and NIST, with every
quote mechanically verified.

Files in this directory are NOT loaded — `standards.load()` globs only the top
level of `standards/`. Kept for reference, not for use.

The seed carried 7 paraphrased FFIEC records whose section numbers came from the
design document, never checked. The replacement carries **45**, drafted from the
booklet itself after the operator supplied it on 2026-09-04, every quote verified.

⚠️ The seed also cited "FFIEC **BCP** Booklet" - the superseded February 2015
name. The current booklet is "Business Continuity **Management**", November 2019.
Anything still citing the BCP booklet is quoting a withdrawn document.
