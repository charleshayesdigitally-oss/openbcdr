"""Tests for tools/ci_checks.py, run in throwaway git repos. Offline.

Each guard is shown failing on a planted offender before it is trusted to pass,
through both the helper functions and the CLI exit code CI actually reads.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ci_checks  # noqa: E402

failures = 0
SIG = "Signed-off-by: Test Person <t@example.com>"


def check(label: str, ok: bool) -> None:
    global failures
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        failures += 1


def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="Test Person", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="Test Person", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def commit(cwd: Path, name: str, msg: str, force: bool = False) -> str:
    """msg is the full commit message; sign-offs are written into it explicitly."""
    (cwd / name).parent.mkdir(parents=True, exist_ok=True)
    (cwd / name).write_text(name, encoding="utf-8")
    git(cwd, "add", *(["-f"] if force else []), name)
    msgfile = cwd.parent / (cwd.name + "-msg.txt")
    msgfile.write_bytes(msg.encode("utf-8"))
    git(cwd, "commit", "-q", "-F", str(msgfile))
    return git(cwd, "rev-parse", "HEAD")


def cli(argv: list[str], cwd: Path) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return ci_checks.main(argv, cwd=str(cwd))


def n_unsigned(rng: str, repo: Path) -> int:
    return len(ci_checks.unsigned_commits(rng, str(repo)))


with tempfile.TemporaryDirectory() as td:
    repo = Path(td) / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("*.local.*\n", encoding="utf-8")
    base = commit(repo, "README.md", f"base\n\n{SIG}\n")

    # --- no-local-files
    check("clean repo: no tracked local paths", ci_checks.tracked_local_files(str(repo)) == [])
    check("CLI exits 0 on a clean repo", cli(["no-local-files"], repo) == 0)
    commit(repo, "docs/localization.md", f"not local\n\n{SIG}\n")
    commit(repo, "local.d/notes.md", f"dir local.d\n\n{SIG}\n")
    check("names merely containing 'local' are not flagged",
          ci_checks.tracked_local_files(str(repo)) == [])
    commit(repo, "config/patterns.LOCAL.txt", f"oops\n\n{SIG}\n", force=True)
    check("force-added *.LOCAL.* file (any case) is caught",
          ci_checks.tracked_local_files(str(repo)) == ["config/patterns.LOCAL.txt"])
    check("CLI exits 1 when a local file is tracked", cli(["no-local-files"], repo) == 1)
    git(repo, "rm", "-q", "--cached", "config/patterns.LOCAL.txt")
    git(repo, "commit", "-q", "-m", f"untrack\n\n{SIG}")
    commit(repo, "org/acme.Local.d/plan.md", f"dir oops\n\n{SIG}\n", force=True)
    check("a file inside a *.local.* DIRECTORY is caught",
          ci_checks.tracked_local_files(str(repo)) == ["org/acme.Local.d/plan.md"])
    git(repo, "rm", "-q", "--cached", "-r", "org")
    git(repo, "commit", "-q", "-m", f"untrack dir\n\n{SIG}")

    # --- dco
    start = git(repo, "rev-parse", "HEAD")
    check("all signed: no offenders", n_unsigned(f"{base}..{start}", repo) == 0)
    check("CLI exits 0 when all signed", cli(["dco", f"{base}..{start}"], repo) == 0)
    commit(repo, "a.txt", "unsigned change\n")
    check("unsigned commit is caught", n_unsigned(f"{start}..HEAD", repo) == 1)
    check("CLI exits 1 on an unsigned commit", cli(["dco", f"{start}..HEAD"], repo) == 1)
    # A message that is only a sign-off line counts as empty to git, so the
    # subject carries it mid-line.
    commit(repo, "b.txt", "note Signed-off-by: Test Person <t@example.com> in the subject only\n")
    check("sign-off in the subject is not a trailer", n_unsigned(f"{start}..HEAD", repo) == 2)
    commit(repo, "c.txt", f"sign-off then prose\n\n{SIG}\n\nMore prose after it.\n")
    check("sign-off followed by a prose paragraph is not a trailer",
          n_unsigned(f"{start}..HEAD", repo) == 3)
    commit(repo, "d.txt", "junk signer\n\nSigned-off-by: x\n")
    check("'Signed-off-by: x' (no name/email) is rejected", n_unsigned(f"{start}..HEAD", repo) == 4)
    commit(repo, "e.txt", "sep\x1fsubject\x1fTest Person <t@example.com>\n")
    check("field separators in the subject don't fake a signer",
          n_unsigned(f"{start}..HEAD", repo) == 5)
    commit(repo, "f.txt", f"crlf signed\r\n\r\n{SIG}\r\n")
    check("CRLF message with a proper sign-off passes", n_unsigned(f"{start}..HEAD", repo) == 5)
    commit(repo, "g.txt", f"folded junk\n\n{SIG}\n invalid continuation\n")
    check("a sign-off with a junk continuation line is rejected",
          n_unsigned(f"{start}..HEAD", repo) == 6)
    commit(repo, "h.txt", "one-char name\n\nSigned-off-by: X <x@example.com>\n")
    check("a one-character name is accepted", n_unsigned(f"{start}..HEAD", repo) == 6)
    commit(repo, "i.txt", "unicode name\n\nSigned-off-by: 李 <li@example.com>\n")
    check("a one-character unicode name is accepted", n_unsigned(f"{start}..HEAD", repo) == 6)
    mid = git(repo, "rev-parse", "HEAD")

    # Unsigned merge of two signed branches, with extra content smuggled in.
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "side.txt", f"side work\n\n{SIG}\n")
    git(repo, "checkout", "-q", "main")
    commit(repo, "main2.txt", f"main work\n\n{SIG}\n")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    (repo / "smuggled.txt").write_text("extra", encoding="utf-8")
    git(repo, "add", "smuggled.txt")
    git(repo, "commit", "-q", "-m", "unsigned merge")
    check("unsigned merge commit is caught (merges are not skipped)",
          n_unsigned(f"{mid}..HEAD", repo) == 1)
    check("empty range has no offenders", n_unsigned("HEAD..HEAD", repo) == 0)
    check("CLI with wrong arguments exits 2", cli(["dco"], repo) == 2)

    # The real process exit code, which is what CI reads.
    def proc(*args: str) -> int:
        return subprocess.run([sys.executable, str(ROOT / "tools" / "ci_checks.py"), *args],
                              cwd=repo, capture_output=True).returncode
    check("process exit 1: unsigned commits in range", proc("dco", f"{start}..HEAD") == 1)
    check("process exit 0: signed-only range", proc("dco", f"{base}..{start}") == 0)
    check("process exit 0: no local paths tracked", proc("no-local-files") == 0)
    commit(repo, "x.LOCAL.txt", f"oops\n\n{SIG}\n", force=True)
    check("process exit 1: a local file is tracked", proc("no-local-files") == 1)

if failures:
    print(f"\n{failures} check(s) FAILED")
    raise SystemExit(1)
print("\nAll ci_checks tests passed.")
