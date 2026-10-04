# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's private
reporting instead: the **Security** tab → **Report a vulnerability**. You'll get a
reply as soon as the maintainer can look at it.

Worth reporting privately:

- anything that lets organisation-specific data get past the boundary guard
  (`openbcdr/boundary.py`) or into a commit, the transfer bundle or a report;
- a way to make the audit chain verify after it has been altered;
- a way to make an evidence quote pass verification when the plan doesn't contain it;
- a secret (API key, token) found anywhere in the repository or its history.

## If you find someone's real data in this repository

Report it privately the same way. Don't copy it into an issue, a comment or a fork.
It will be removed, and if needed the history will be rewritten.

## Scope

This is decision-support software. It does not certify compliance. Findings are
for a qualified person to review; see the disclaimer in `README.md`.
