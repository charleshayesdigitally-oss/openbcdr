# OpenBCDR

Evaluates business-continuity / disaster-recovery plans against public standards (FINRA Rule 4370, FFIEC BCM Booklet 2019, NIST SP 800-34 Rev. 1): internal coherence checks, requirement coverage with verified evidence quotes, a gap registry with human decisions, and a tamper-evident audit trail.

It is a generic framework built on public standards. It is not tooling for any one employer, and it has to stay that way to be usable (or sellable) anywhere.

Full detail: `README.md` · agent prompt: `AGENT-INSTRUCTIONS.md` · plan-evaluation prompt: `PLAN-EVALUATION-PROMPT.md` · building the index: `BUILDING-THE-INDEX.md` · what went wrong before: `LESSONS-LEARNED.md` (read it before changing anything important).

## Hard rules

1. **Never ingest real employer data during development.** Sandbox work uses public frameworks and synthetic or sanitized plans only. `samples/sample_plan.md` is synthetic, with defects planted on purpose.
2. **Nothing organisation-specific is committed.** Employer names and internal system names live only in `config/boundary_patterns.local.txt`, which is gitignored and never packed into the transfer bundle (any `*.local.*` file is skipped, any case). `config/boundary_patterns.txt` stays generic. Test fixtures use generic markers such as `INTERNAL USE ONLY`, never a real institution. Check `[6c]` in `tests/test_offline.py` scans every tracked text file against the local patterns and fails on any match, so run the tests before committing. Never write review diffs or scratch files containing the old patterns inside this folder.
3. **A fresh clone has no local file, and sandbox mode then refuses everything** (an empty or comments-only local file counts as missing). Recreate `config/boundary_patterns.local.txt` for sandbox work (plain UTF-8; a BOM is handled). `BCDR_ALLOW_NO_LOCAL_PATTERNS=1` accepts generic-only protection on purpose. The tests set it for themselves. **Do not carry the local file into an internal deployment:** in `--mode internal` a pattern naming the institution would refuse that institution's own plans.
4. **The report withholds its coverage score until requirements are human-validated.** Do not remove that gate, and never describe output as a compliance score or the audit trail as immutable. It is tamper-evident against accident and casual edits, not tamper-proof.

## Running it

```
pip install -r requirements.txt        # python-docx is also needed for tools/build_handbook.py
python tests/run_all.py                # 296 offline checks, no API key, no network, no spend
python -m openbcdr --help
```

- `ingest`, `analyze` and `decompose` call the Claude API: set `ANTHROPIC_API_KEY` (see `.env.example`). Optional: `BCDR_MODEL`, `BCDR_EFFORT`, `BCDR_DB`.
- Everything else runs offline: `coherence`, `report`, `gaps`, `decide`, `calibrate`, `validate`, `standards-status`, `register-plan`, `import-findings`, `audit-verify`, `audit-log`.
- Still unproven: a live API call end to end. Run `analyze` against `samples/` with a key before trusting it.

## Standards index (as of 2026-09-11)

- **73 records across 3 sources:** FFIEC BCM Booklet 45 (all `mandatory: false`, per the booklet's own disclaimer), FINRA 4370 22, NIST 800-34r1 6. **0 of 73 are human-validated.**
- `standards.load()` reads only the top level of `standards/`. `standards/retired/` and `standards/pending/` are invisible to it on purpose, so count with the loader, never with a file glob.
- Next step on the index: validate FINRA 4370 first. `standards/pending/README.md` shows how to scope a run to one source.

## Prompt-only deployments

Some environments run the agent as a prompt with no codebase attached (`AGENT-INSTRUCTIONS.md`), and that silently removes evidence-quote verification, dedup, and the audit trail. The bridge back: the agent emits a JSON findings block, then `python -m openbcdr import-findings` re-applies quote checks, severity from the index, fingerprint dedup and the audit trail.

## Moving the code into a restricted environment

`python tools/make_transfer.py` writes `dist/openbcdr-source.txt`: one plain-text bundle with its own unpacker and a SHA-256 manifest, reviewable line by line. `python tools/build_handbook.py` rebuilds `OpenBCDR-Handbook.docx`. `dist/` is gitignored. Follow your organisation's rules for bringing code in; the bundle exists to be reviewable, never to get around a control.

## Changing guards

The boundary guard, the quote verifier, the audit chain and the report's score gate are the parts where being wrong is silent. When changing one:

- Break it on purpose and confirm a test goes red before trusting the green run. A suite can pass over a broken guard.
- A check that loops over an empty list passes without testing anything. Make sure the fixture contains the case being tested.
- Get a second review from a different model family (e.g. Codex) on guard changes, not just a same-model read.
