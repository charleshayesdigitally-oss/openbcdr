# Plan Evaluation Prompt

The per-run prompt. Paste the block below and fill the braces each time a plan needs evaluating. The agent instructions are the system prompt; this is the task.

## Full evaluation

```
Evaluate the attached business continuity plan.

PLAN: {plan name}
VERSION: {version}  |  SCOPE: {business unit / IT application / shared service / vendor}  |  COVERS: {which unit or application}
DOCUMENT: {attached / pasted below}
DEPENDS ON: {other business units, applications or shared services this scope needs to recover, or: none stated}
STANDARDS SCOPE: {all applicable requirements, or a named subset}
LAST EVALUATED: {date and version evaluated, or: first evaluation}
STANDARDS LAST CONFIRMED: {date the index was last checked against primary sources, or: unknown}
KNOWN OPEN GAPS: {paste the open gap list, or: none on file}
TRIGGER: {annual review / post-test / new system / M&A / vendor change / regulatory change / ad hoc}

Before you evaluate anything, confirm what you are evaluating against.

Run: compliance coverage, internal coherence, and structural defects.

Return, in this order:

0. STANDARDS CURRENCY — mandatory, always, even when nothing changed. One line per source in scope, in exactly this form and nothing more:

   `<source> | <version in the index> | CONFIRMED CURRENT | CHANGED | UNCONFIRMED | <URL retrieved> | <edition/date found at the source> | <today's date>`

   Verify by retrieving each source and reading the edition stated there, then comparing it to the index. CONFIRMED CURRENT requires a URL you actually retrieved and the edition you actually read; with no URL the only permitted status is UNCONFIRMED. If you have no web access, mark every source UNCONFIRMED with `no retrieval capability` and say so once. No prose in this block. Every source gets a line even when the answer is dull. Use CHANGED when the edition at the source differs from the index, then note beneath the table what changed and which findings it affects. Do not omit this block; output that does not begin with it is incomplete.

1. HEADER — plan, version, evaluation date, and exactly which standards and versions you assessed against.

2. COVERAGE SUMMARY — counts for full, partial, gap, and insufficient evidence, with the total assessed. If any requirement in scope has not been confirmed against its primary source, say so here and do not report an overall score.

3. FINDINGS, most severe first. For each: severity, title, the requirement and its citation, the affected plan section, the verbatim evidence quote or an explicit statement that no supporting text exists, one concrete recommended action, and the proposed owner and deadline. Reference an existing gap identifier rather than raising a duplicate.

4. COHERENCE FINDINGS — internal contradictions, stale contacts, incomplete procedures, vendor coverage, test currency. Show the numbers or dates the conclusion rests on.

5. COULD NOT DETERMINE — anything you were unable to assess, and what would resolve it.

6. NOT CHECKED — anything in scope you did not actually examine, and why.

Do not propose changes to the plan's structure or format. Do not close, waive, or soften anything. Every quote must be copied character for character from the document, and absence of evidence is a gap, not a pass.
```

## Re-check after remediation

```
Re-evaluate {plan name} version {version} against these previously identified gaps: {paste gap IDs and titles}.

For each: is it now closed, partially addressed, or unchanged? Quote the text that closes it, or state that no such text exists. Do not close anything yourself — state what you found and leave the disposition to the risk owner. Flag any NEW gap the remediation introduced.
```

## Delta between versions

```
Compare {plan name} version {new} against version {old}, both attached.

Report what materially changed: recovery objectives, critical systems, vendors, contacts, procedures, scenarios, test records. For each change, say whether it closes an existing gap, opens a new one, or is neutral. Ignore formatting and wording changes that do not alter meaning.
```

## Standards currency, run on its own

Use this for the quarterly refresh, separately from any plan.

```
Run a standards currency check. Do not evaluate any plan.

CURRENT INDEX VERSIONS: {paste the standard, version and last-confirmed date for each record, or: see attached index}

For every source in scope — FFIEC, Federal Reserve, FINRA, OCC, FDIC, FS-ISAC, NIST, CIS (ISO is deliberately out of scope; do not check or cite it) — establish the current edition or revision, its publication or last-amendment date, and whether anything material changed since the version in the index.

Report per source: confirmed unchanged, or changed. For each change, give the requirement text as the primary source states it, its section reference, what changed, and which plan sections would be affected if accepted. Cite the primary source with a link and the date you retrieved it; a summary or secondary source may guide you but may never be the citation.

Produce proposed index changes for a human to confirm. Do not raise findings against any plan and do not update the index yourself. Flag anything urgent enough not to wait for the next quarterly refresh at the top.
```

---

## Why these fields matter

**`KNOWN OPEN GAPS`** is load-bearing. The agent is instructed to deduplicate against the registry and cannot do it if you do not hand it the registry. Skip the field and you get the same finding raised fresh every quarter under a new identifier.

**`STANDARDS LAST CONFIRMED`** is what makes section 0 real. Without it the agent has nothing to compare against and will default to assuming its index is current, which is exactly the failure the section exists to prevent. If the answer is genuinely unknown, write `unknown` — that is a useful input, not a blank.

**Section 6, `NOT CHECKED`,** is the one people delete because it looks like padding. It is the only thing standing between a skipped check and a clean-looking report; without an explicit slot, an agent that skipped something simply does not mention it, and silence reads identical to a pass.
