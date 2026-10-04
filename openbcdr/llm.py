"""The single place this codebase talks to Claude.

Two rules hold everywhere downstream:

1. Every model call returns a schema-validated object, never free text. The
   agent's output is routed to a risk officer with a deadline attached, so
   "mostly parseable prose" is not an acceptable interface.
2. The plan text is cached. A compliance run asks the same long document about
   a few hundred short requirements; the document is the stable prefix and the
   requirement batch is the volatile suffix. Ordering it the other way round
   re-bills the whole plan on every call.
"""
from __future__ import annotations

import json
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)

_client = None


def client():
    """Lazily construct the SDK client.

    Zero-arg construction resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or
    an `ant auth login` profile - do not hardcode a key here.
    """
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema acceptable to json_schema structured output.

    Structured outputs require, at every object level, `additionalProperties:
    false` and a `required` list naming every property. Pydantic omits both for
    fields that have defaults, so its schema is rejected as-is. Optional fields
    survive because Pydantic already emits them as `anyOf [..., null]` - the
    model returns an explicit null instead of dropping the key.
    """
    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        # `default` contradicts an all-required schema; drop it rather than ship
        # a schema that says a field is both mandatory and defaulted.
        out = {k: walk(v) for k, v in node.items() if k != "default"}
        if out.get("type") == "object" or "properties" in out:
            props = out.get("properties", {})
            out["additionalProperties"] = False
            out["required"] = list(props.keys())
        return out

    return walk(json.loads(json.dumps(schema)))


def structured(
    model_cls: Type[T],
    system_blocks: list[dict[str, Any]],
    user_content: str,
    max_tokens: int = 16000,
    effort: str | None = None,
) -> tuple[T, Any]:
    """One structured call. Returns (validated instance, usage).

    `system_blocks` is passed through verbatim so callers control cache
    breakpoints - put the stable material first and mark it with
    cache_control, put anything that varies per call in `user_content`.
    """
    resp = client().messages.create(
        model=config.MODEL,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": effort or config.EFFORT,
            "format": {"type": "json_schema", "schema": strictify(model_cls.model_json_schema())},
        },
    )
    if resp.stop_reason == "refusal":
        detail = getattr(resp, "stop_details", None)
        raise RuntimeError(
            "model declined the request"
            + (" (" + str(getattr(detail, "category", "")) + ")" if detail else "")
        )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            "response hit max_tokens and the JSON is truncated - lower "
            "REQUIREMENTS_PER_CALL or raise max_tokens"
        )
    text = next(b.text for b in resp.content if b.type == "text")
    return model_cls.model_validate_json(text), resp.usage


def cache_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
