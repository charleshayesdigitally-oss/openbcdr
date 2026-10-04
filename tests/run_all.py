"""Run every suite that does not need an API key.

    python tests/run_all.py

Exit 0 means the deterministic pipeline and the request-building side of the
API paths are sound. It does NOT mean a live call has ever succeeded.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ["test_offline.py", "test_llm_paths.py", "test_index_build.py", "test_import.py", "test_ci_checks.py"]

failed = []
for suite in SUITES:
    print("\n" + "#" * 60)
    print("# " + suite)
    print("#" * 60)
    rc = subprocess.call([sys.executable, str(HERE / suite)])
    if rc != 0:
        failed.append(suite)

print("\n" + "=" * 60)
if failed:
    print("SUITES FAILED: " + ", ".join(failed))
    raise SystemExit(1)
print("All suites passed.")
print("Still unproven: a live API call. Run `analyze` against samples/ with a key.")
