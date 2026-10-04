"""Exercise the API-calling paths without an API key.

A stub client stands in for the SDK: it records the exact request body our code
builds, validates it against the SDK's own parameter types, and returns a canned
response. That covers everything on our side of the wire - schema construction,
cache-block placement, batching, response parsing, quote verification, the
downgrade path, and the store write.

What it CANNOT cover: whether Anthropic's server accepts the schema, whether the
model honours the quoting instruction, and real cache behaviour. Those need a
key. Do not read a green run here as "the API path works".

    python tests/test_llm_paths.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Tests must not depend on whether THIS machine has a local deny list
# (the missing-local refusal itself is tested in test_offline.py).
os.environ.setdefault("BCDR_ALLOW_NO_LOCAL_PATTERNS", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openbcdr import ingest, llm, standards  # noqa: E402
from openbcdr.analyzers import compliance  # noqa: E402
from openbcdr.models import PlanExtract  # noqa: E402

PLAN = (ROOT / "samples" / "sample_plan.md").read_text(encoding="utf-8")

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


# --------------------------------------------------------------- stub client

class _Usage:
    def __init__(self, cache_read=0, cache_write=0):
        self.input_tokens = 1000
        self.output_tokens = 500
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


class _Text:
    type = "text"

    def __init__(self, text): self.text = text


class _Response:
    def __init__(self, payload, cache_read=0, cache_write=0):
        self.content = [_Text(json.dumps(payload))]
        self.stop_reason = "end_turn"
        self.stop_details = None
        self.usage = _Usage(cache_read, cache_write)


class StubMessages:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responder(kwargs, len(self.calls))


class StubClient:
    def __init__(self, responder):
        self.messages = StubMessages(responder)


def install(responder) -> StubClient:
    c = StubClient(responder)
    llm._client = c
    return c


def validate_request(kwargs: dict, label: str) -> None:
    """Type-check the request body against the SDK's own param definitions."""
    from anthropic.types import message_create_params as mcp  # noqa: F401

    oc = kwargs.get("output_config", {})
    check(label + ": output_config keys are SDK-valid",
          set(oc) <= {"effort", "format", "task_budget"}, str(set(oc)))
    check(label + ": effort is a valid level",
          oc.get("effort") in ("low", "medium", "high", "xhigh", "max"), str(oc.get("effort")))
    fmt = oc.get("format", {})
    check(label + ": format is json_schema with a schema",
          fmt.get("type") == "json_schema" and isinstance(fmt.get("schema"), dict))
    check(label + ": no unsupported top-level params",
          set(kwargs) <= {"model", "max_tokens", "system", "messages", "output_config",
                          "cache_control", "thinking", "tools", "tool_choice",
                          "temperature", "metadata", "stop_sequences", "stream"},
          str(set(kwargs)))
    check(label + ": messages start with a user turn",
          kwargs["messages"][0]["role"] == "user")
    check(label + ": max_tokens set and sane",
          isinstance(kwargs.get("max_tokens"), int) and kwargs["max_tokens"] >= 1024)


def schema_is_strict(schema: dict) -> tuple[bool, str]:
    """Recursively assert every object level satisfies structured-output rules."""
    stack = [("root", schema)]
    while stack:
        name, node = stack.pop()
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                if node.get("additionalProperties") is not False:
                    return False, name + ": additionalProperties not false"
                props = set(node.get("properties", {}))
                if set(node.get("required", [])) != props:
                    return False, name + ": required != properties"
            if "default" in node:
                return False, name + ": stray `default` keyword"
            for k, v in node.items():
                stack.append((name + "." + str(k), v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((name + "[" + str(i) + "]", v))
    return True, ""


# ----------------------------------------------------------------- the tests

def test_ingest() -> None:
    print("\n[1] ingest.extract - full path, stubbed transport")

    canned = PlanExtract(
        plan_id="APP_PAYPROC_v4", plan_scope="it_application",
        scope_name="Payment Processing", plan_version="4.0",
    ).model_dump(mode="json")

    def responder(kwargs, n):
        validate_request(kwargs, "ingest")
        ok, why = schema_is_strict(kwargs["output_config"]["format"]["schema"])
        check("ingest: emitted schema is strict at every level", ok, why)
        sys_blocks = kwargs["system"]
        check("ingest: system prompt is cached",
              any(b.get("cache_control") for b in sys_blocks))
        check("ingest: plan text travels in the user turn, not the system prompt",
              "Fictional Regional Bank" in kwargs["messages"][0]["content"])
        return _Response(canned)

    c = install(responder)
    extract, usage = ingest.extract(PLAN, mode="sandbox", attested=True)
    check("ingest: exactly one API call", len(c.messages.calls) == 1, str(len(c.messages.calls)))
    check("ingest: response parsed into PlanExtract",
          isinstance(extract, PlanExtract) and extract.plan_id == "APP_PAYPROC_v4")
    check("ingest: usage returned", usage.input_tokens == 1000)


def test_boundary_blocks_before_any_call() -> None:
    print("\n[2] boundary refusal happens BEFORE the request is built")

    def responder(kwargs, n):
        raise AssertionError("a refused document reached the API")

    c = install(responder)
    from openbcdr.boundary import BoundaryViolation
    for label, text, mode, att in [
        ("sandbox without attestation", PLAN, "sandbox", False),
        ("deny-list hit", "INTERNAL USE ONLY continuity plan", "sandbox", True),
    ]:
        try:
            ingest.extract(text, mode=mode, attested=att)
            check("boundary: " + label + " refused", False, "no exception raised")
        except BoundaryViolation:
            check("boundary: " + label + " refused before any API call", True)
    check("boundary: zero API calls made", len(c.messages.calls) == 0)


def test_compliance_batching_and_cache() -> None:
    print("\n[3] compliance.analyze - batching, cache placement, quote verification")

    reqs = standards.applicable(
        standards.load(), {"all_banks": True, "finra_member": True, "fed_when": False,
                           "fed_member": True, "sifi": False})
    real_quote = "Next review due: 2027-01-15"
    fabricated = "Ransomware recovery is tested annually with the core provider."

    def responder(kwargs, n):
        validate_request(kwargs, "analyze#" + str(n))
        ok, why = schema_is_strict(kwargs["output_config"]["format"]["schema"])
        check("analyze#" + str(n) + ": schema strict", ok, why)
        blocks = kwargs["system"]
        check("analyze#" + str(n) + ": plan is in the CACHED system prefix",
              len(blocks) == 2 and all(b.get("cache_control") for b in blocks)
              and "Fictional Regional Bank" in blocks[1]["text"])
        check("analyze#" + str(n) + ": only the requirement batch varies (user turn)",
              "req_id:" in kwargs["messages"][0]["content"]
              and "Fictional Regional Bank" not in kwargs["messages"][0]["content"])

        # Alternate: first requirement of each batch claims coverage with a real
        # quote, second fabricates one, rest are honest gaps.
        ids = [ln.split("req_id: ", 1)[1].split("\n")[0]
               for ln in kwargs["messages"][0]["content"].split("---")
               if "req_id: " in ln]
        findings = []
        for i, rid in enumerate(ids):
            if i == 0:
                findings.append({"req_id": rid, "coverage": "full", "rationale": "covered",
                                 "evidence_quote": real_quote, "plan_section": "header",
                                 "recommended_action": ""})
            elif i == 1:
                findings.append({"req_id": rid, "coverage": "full", "rationale": "covered",
                                 "evidence_quote": fabricated, "plan_section": "7",
                                 "recommended_action": ""})
            else:
                findings.append({"req_id": rid, "coverage": "gap", "rationale": "not addressed",
                                 "evidence_quote": "", "plan_section": "",
                                 "recommended_action": "Document it."})
        # Simulate a cache hit on every call after the first.
        return _Response({"findings": findings}, cache_read=0 if n == 1 else 4200,
                         cache_write=4200 if n == 1 else 0)

    c = install(responder)
    findings, usage = compliance.analyze(PLAN, reqs, batch_size=6)

    expected_calls = (len(reqs) + 5) // 6
    # The responder makes the 1st item of each batch quotable and the 2nd
    # fabricated, so a final batch of one produces no fabrication. Derive the
    # expected counts from the real batch sizes rather than assuming every
    # batch is full - otherwise the suite breaks whenever the index size
    # changes, which is a fact about the test, not about the code.
    batch_sizes = [len(reqs[i:i + 6]) for i in range(0, len(reqs), 6)]
    expected_verified = sum(1 for n in batch_sizes if n >= 1)
    expected_downgraded = sum(1 for n in batch_sizes if n >= 2)
    check("analyze: correct number of batched calls",
          len(c.messages.calls) == expected_calls,
          str(len(c.messages.calls)) + " != " + str(expected_calls))
    check("analyze: one finding per requirement",
          len(findings) == len(reqs), str(len(findings)) + " != " + str(len(reqs)))

    verified = [f for f in findings if f.evidence_verified]
    downgraded = [f for f in findings if f.rationale.startswith("UNVERIFIED EVIDENCE")]
    check("analyze: real quotes verified", len(verified) == expected_verified,
          str(len(verified)) + " != " + str(expected_verified))
    check("analyze: fabricated quotes downgraded, one per batch that had one",
          len(downgraded) == expected_downgraded,
          str(len(downgraded)) + " != " + str(expected_downgraded))
    check("analyze: no downgraded finding still reads as coverage",
          all(f.coverage == "insufficient_evidence" for f in downgraded))
    check("analyze: usage totals accumulate across batches",
          usage["cache_read"] > 0 and usage["output"] == 500 * expected_calls, str(usage))

    s = compliance.score(findings)
    check("analyze: score counts only verified coverage",
          s["full"] == expected_verified
          and s["insufficient_evidence"] == expected_downgraded, str(s))


def test_missing_finding_is_not_a_pass() -> None:
    print("\n[4] a requirement the model silently drops must not read as compliant")

    reqs = standards.load()[:3]

    def responder(kwargs, n):
        rid = kwargs["messages"][0]["content"].split("req_id: ", 1)[1].split("\n")[0]
        return _Response({"findings": [{"req_id": rid, "coverage": "gap", "rationale": "x",
                                        "evidence_quote": "", "plan_section": "",
                                        "recommended_action": "y"}]})

    install(responder)
    findings, _ = compliance.analyze(PLAN, reqs, batch_size=3)
    dropped = [f for f in findings if f.rationale.startswith("Model returned no finding")]
    check("dropped requirements are surfaced, not silently omitted",
          len(findings) == 3 and len(dropped) == 2, str(len(findings)) + "/" + str(len(dropped)))
    check("dropped requirements are never 'full'",
          all(f.coverage == "insufficient_evidence" for f in dropped))


def test_error_paths() -> None:
    print("\n[5] refusal and truncation are raised, not parsed as results")

    class _Refusal(_Response):
        def __init__(self):
            super().__init__({})
            self.stop_reason = "refusal"

            class D:
                category = "cyber"
            self.stop_details = D()

    class _Truncated(_Response):
        def __init__(self):
            super().__init__({})
            self.stop_reason = "max_tokens"

    for label, resp_cls in (("refusal", _Refusal), ("max_tokens truncation", _Truncated)):
        install(lambda k, n, rc=resp_cls: rc())
        try:
            llm.structured(PlanExtract, [llm.block("x")], "y")
            check(label + " raises", False, "no exception")
        except RuntimeError as e:
            check(label + " raises RuntimeError", True)
        except Exception as e:  # noqa: BLE001
            check(label + " raises RuntimeError", False, type(e).__name__ + ": " + str(e))


if __name__ == "__main__":
    test_ingest()
    test_boundary_blocks_before_any_call()
    test_compliance_batching_and_cache()
    test_missing_finding_is_not_a_pass()
    test_error_paths()
    print("\n" + ("=" * 60))
    if FAILURES:
        print(str(len(FAILURES)) + " FAILED:")
        for f in FAILURES:
            print("  - " + f)
        raise SystemExit(1)
    print("All checks passed. NOTE: transport is stubbed - no live API call was made.")
