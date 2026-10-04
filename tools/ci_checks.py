"""Contributor guards that CI runs on every pull request.

    python tools/ci_checks.py no-local-files
    python tools/ci_checks.py dco <base>..<head>

no-local-files  Fails if git tracks any path with a component containing
                ".local." (any case), file or directory. Organisation-specific
                values live only there; .gitignore keeps them out, but
                `git add -f` would not.
dco             Fails if any commit in the range, merge commits included, lacks
                a well-formed `Signed-off-by: Name <email>` trailer (Developer
                Certificate of Origin, see CONTRIBUTING.md). This checks the
                form of the sign-off only; it cannot prove identity.

Both exit 0 when clean and 1 with a list of offenders. Plain git, no network.

What CI cannot check: organisation-specific CONTENT in an ordinary file name.
The pattern list that would catch it is private by design and never in CI, so
the maintainer runs the local leak scan (`python tests/run_all.py` with
config/boundary_patterns.local.txt present) before merging.
"""
from __future__ import annotations

import re
import subprocess
import sys

LOCAL_MARK = ".local."
SIGNER = re.compile(r"^\S(?:.*\S)? <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$")


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8",
    ).stdout


def tracked_local_files(cwd: str | None = None) -> list[str]:
    names = _git("ls-files", "-z", cwd=cwd).split("\0")
    return [n for n in names
            if n and any(LOCAL_MARK in part.lower() for part in n.split("/"))]


def signers_of(sha: str, cwd: str | None = None) -> list[str]:
    """Signed-off-by values from the commit's trailer block (git's own parser),
    unfolded so a continuation line is validated as part of its value."""
    out = _git("log", "-1", "--format=%(trailers:key=Signed-off-by,valueonly,unfold)", sha, cwd=cwd)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def unsigned_commits(rev_range: str, cwd: str | None = None) -> list[str]:
    """Commits in rev_range (merges included) without a well-formed sign-off."""
    shas = _git("rev-list", rev_range, cwd=cwd).split()
    bad = []
    for sha in shas:
        if not any(SIGNER.match(s) for s in signers_of(sha, cwd)):
            subject = _git("log", "-1", "--format=%s", sha, cwd=cwd).strip()
            bad.append(f"{sha[:12]} {subject}")
    return bad


def main(argv: list[str], cwd: str | None = None) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "no-local-files" and len(argv) == 1:
        bad = tracked_local_files(cwd)
        if bad:
            print("Tracked *.local.* paths (organisation-specific; must never be committed):")
            for n in bad:
                print("  " + n)
            print("Remove with: git rm --cached <path>")
            return 1
        print("OK: no *.local.* path is tracked.")
        return 0
    if cmd == "dco" and len(argv) == 2:
        bad = unsigned_commits(argv[1], cwd)
        if bad:
            print("Commits without a well-formed 'Signed-off-by: Name <email>' line"
                  " (see CONTRIBUTING.md, 'Sign your commits'):")
            for b in bad:
                print("  " + b)
            print("Fix: git rebase --signoff <base>, then force-push the branch.")
            return 1
        print(f"OK: every commit in {argv[1]} is signed off.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
