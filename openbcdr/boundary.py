"""Data-boundary guard.

Two modes, and picking the wrong one is the whole point of this module:

- `sandbox` - development, demos, and any work outside the institution's own
  environment. Public frameworks and synthetic or sanitized plans ONLY. Refuses
  a document unless it passes the pattern scan AND the caller explicitly
  attests it is synthetic. Fail-closed: silence is a refusal.
- `internal` - the agent deployed inside the institution, reading that
  institution's own plans. This is the production mode. The deny-list still
  runs, but as a stray-document check (someone else's confidential material
  landing in the ingestion folder), not as a blanket block.

The attestation is written to the audit trail, so "I said it was sanitized" is
a logged claim by a named actor rather than an invisible assumption.

Tune `config/boundary_patterns.txt` to the deployment. Patterns naming an
institution belong in `boundary_patterns.local.txt`, which stays out of git.

Because those patterns live only in the local file, sandbox mode REFUSES when
the local file is missing (a fresh clone, a new machine). Protection does not
quietly drop to the generic list. To accept generic-only protection on purpose,
set BCDR_ALLOW_NO_LOCAL_PATTERNS=1. Internal mode is unaffected.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import config


class BoundaryViolation(RuntimeError):
    """Raised when a document may not be ingested in the current mode."""


@dataclass
class ScanResult:
    allowed: bool
    mode: str
    matches: list[str]
    attested: bool
    missing_local: bool = False

    @property
    def reason(self) -> str:
        if self.allowed:
            return "clear"
        if self.missing_local:
            return ("no usable local deny list at config/boundary_patterns.local.txt "
                    "(missing, empty, or comments only) - create it "
                    "with this machine's organisation-specific patterns, or set "
                    "BCDR_ALLOW_NO_LOCAL_PATTERNS=1 to accept generic-only protection")
        if self.matches:
            return "matched deny-list: " + ", ".join(sorted(set(self.matches)))
        return "sandbox mode requires an explicit synthetic/sanitized attestation"


def _read_patterns(path: Path) -> list[re.Pattern[str]]:
    p = Path(path)
    if not p.exists():
        return []
    pats: list[re.Pattern[str]] = []
    # utf-8-sig: a BOM (PowerShell 5.1 writes one) would otherwise glue itself
    # to the first pattern and silently stop it matching.
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pats.append(re.compile(line, re.IGNORECASE))
    return pats


def _load_patterns() -> list[re.Pattern[str]]:
    return _read_patterns(config.BOUNDARY_PATTERNS) + _read_patterns(config.BOUNDARY_PATTERNS_LOCAL)


MODES = ("sandbox", "internal")


def scan(text: str, mode: str, attested: bool = False) -> ScanResult:
    """Scan document text. `mode` is 'sandbox' or 'internal'."""
    if mode not in MODES:
        raise ValueError("mode must be one of " + str(MODES) + ", got " + repr(mode))

    # Missing, empty and comments-only all count as "no local deny list".
    if (mode == "sandbox"
            and not _read_patterns(config.BOUNDARY_PATTERNS_LOCAL)
            and os.environ.get("BCDR_ALLOW_NO_LOCAL_PATTERNS") != "1"):
        return ScanResult(False, mode, [], attested, missing_local=True)

    matches = [p.pattern for p in _load_patterns() if p.search(text)]

    if mode == "internal":
        # The institution's own documents. The deny-list is still applied so a
        # third party's confidential material does not get ingested by accident.
        return ScanResult(not matches, mode, matches, attested)

    return ScanResult((not matches) and attested, mode, matches, attested)


def enforce(text: str, mode: str, attested: bool = False) -> ScanResult:
    res = scan(text, mode, attested)
    if not res.allowed:
        raise BoundaryViolation("[" + mode + " mode] refused: " + res.reason)
    return res
