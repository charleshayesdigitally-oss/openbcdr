# Contributing

Thanks for helping. This project evaluates business-continuity and disaster-recovery
plans against **public** standards, and it can help draft plans. It stays useful to
everyone only if it never carries anyone's private material. Most of the rules below
exist for that reason.

## The one rule that matters most

**Public, free sources only. Never anything from an employer or a client.**

Don't contribute (in code, tests, fixtures, examples, issues or comments):

- a real organisation's BC/DR plan, policy, standard, template, tier model or test result;
- internal system, vendor or people names from any organisation;
- text from paid or licensed standards. **No ISO text**, paraphrased or quoted.

Test fixtures and examples use synthetic organisations and generic markers
(for example `INTERNAL USE ONLY`). If you aren't sure whether something is public,
leave it out and ask in an issue.

## Sign your commits (DCO)

Every commit needs a `Signed-off-by:` line. It certifies the
[Developer Certificate of Origin](https://developercertificate.org/): that you wrote
the change, or have the right to submit it under this project's license.

```
git commit -s -m "Your message"
```

Use your real name. A pull request with an unsigned commit fails the DCO check;
fix it with `git commit --amend -s` (one commit) or `git rebase --signoff main`.

## Adding to the standards index

The index (`standards/*.json`) is the only thing the agent may cite as a requirement.

1. The source must be public, free to read, and its license must allow quoting short passages.
   Name the source and its license in your pull request.
2. One record = one testable obligation, with `source`, `section`, `source_quote` and
   `as_of_version`. See `BUILDING-THE-INDEX.md`.
3. Records start unvalidated. A human checks each one against the source with
   `python -m openbcdr validate` before it counts toward a score.
4. Good practice that isn't a requirement (conference advice, a team's habit) is not an
   index record. It belongs in an advisory check, labelled as practice.

## Before you open a pull request

```
pip install -r requirements.txt
python tests/run_all.py
```

All suites must pass. CI runs the same suites on every pull request, plus a check that
no `*.local.*` file or directory is tracked and the DCO check on every commit (merge
commits included).

CI can't tell a real organisation's name from a made-up one. The maintainer runs a
private leak scan before merging, but the first line of defence is you: keep real
names out.

If you change a guard (the boundary guard, the quote verifier, the audit chain or the
report's score gate), break it on purpose and show a test going red before the fix
goes green. Say so in the pull request.

## How changes get merged

Every change goes through a pull request, passes CI and gets a review from the
maintainer before it merges into `main`. Be patient; this is maintained alongside a day job.

## Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
