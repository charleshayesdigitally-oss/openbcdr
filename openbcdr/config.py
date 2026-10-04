"""Runtime configuration. Everything tunable lives here, nothing is hardcoded downstream."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODEL = os.environ.get("BCDR_MODEL", "claude-opus-5")
EFFORT = os.environ.get("BCDR_EFFORT", "high")  # low|medium|high|xhigh|max
DB_PATH = Path(os.environ.get("BCDR_DB", ROOT / "bcdr.sqlite3"))

STANDARDS_DIR = ROOT / "standards"
BOUNDARY_PATTERNS = ROOT / "config" / "boundary_patterns.txt"
BOUNDARY_PATTERNS_LOCAL = ROOT / "config" / "boundary_patterns.local.txt"

# How many requirements go into one compliance call. The plan text is cached
# across calls, so this only trades per-call output size against call count.
REQUIREMENTS_PER_CALL = 12

# Added to the plan-reading prompts by an organisation profile (--org): its own
# words, and the good-practice framing for an unregulated organisation. Empty
# without --org, so the prompts are unchanged.
ORG_CONTEXT = ""

# Severity -> (default deadline in BUSINESS days, escalate-if-no-response days)
# Spec section 7.2. Medium/Low deadlines in the spec are calendar days; kept as
# business days here for one consistent clock. Change deliberately, not by drift.
SLA = {
    "critical": (15, 5),
    "high": (30, None),
    "medium": (60, None),
    "low": (None, None),
}

ROUTING = {
    "critical": ["risk_owner", "compliance_officer"],
    "high": ["risk_owner"],
    "medium": ["plan_owner", "bcdr_pm"],
    "low": ["weekly_digest"],
    "format": ["bcdr_pm"],
}

# Trend Analyzer relevance weights (spec section 4.2). Must sum to 100.
RELEVANCE_WEIGHTS = {
    "references_continuity": 40,
    "applies_to_banking": 25,
    "applies_to_midsize": 15,
    "references_primary_regulator": 20,
}
ALERT_THRESHOLD = 70   # >= 70 -> active alert
DIGEST_THRESHOLD = 40  # 40-69 -> weekly digest; < 40 archived

# Coherence thresholds (spec section 6.2 B)
CONTACT_STALE_MEDIUM_DAYS = 90
CONTACT_STALE_HIGH_DAYS = 180
PLAN_STALE_MONTHS = 18
