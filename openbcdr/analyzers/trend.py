"""Trend Analyzer (spec section 4.2 / 6.3).

Split in two on purpose:

- Classification ("does this notice reference operational resilience? does it
  apply to non-SIFI institutions?") is a judgment over prose -> model.
- Scoring is the spec's own weighted sum, and the thresholds decide whether a
  human gets paged -> arithmetic, in code, reproducible.

Fetching the feeds themselves is deliberately not here. RSS/API plumbing for
FINRA, the Fed, FFIEC and FS-ISAC is integration work with no shared logic; it
belongs behind `classify_and_score`, which takes an already-fetched item.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from .. import config, llm
from ..models import FeedItem

SYSTEM = """You classify regulatory and industry notices for a mid-size US regional bank's BC/DR program.

Answer four independent yes/no questions about the item:
- references_continuity: does it concern business continuity, disaster recovery, incident recovery, or operational resilience?
- applies_to_banking: does it apply to banks or financial institutions specifically (as opposed to general guidance for any industry)?
- applies_to_midsize: does it apply at a mid-size, non-SIFI asset tier? Answer no if the obligation is scoped to systemically important institutions, large bank holding companies, or an asset threshold this institution would not meet.
- references_primary_regulator: is it issued by, or does it directly interpret, FINRA, the Federal Reserve, or the FFIEC?

Judge only from the text provided. If the text is too thin to tell, answer no - a false negative sends the item to a digest, a false positive pages a risk officer."""


class Classification(BaseModel):
    references_continuity: bool
    applies_to_banking: bool
    applies_to_midsize: bool
    references_primary_regulator: bool
    reasoning: str


def score(item: FeedItem) -> int:
    """The spec's weighted sum. Deterministic, so a score is reproducible."""
    w = config.RELEVANCE_WEIGHTS
    total = 0
    if item.references_continuity:
        total += w["references_continuity"]
    if item.applies_to_banking:
        total += w["applies_to_banking"]
    if item.applies_to_midsize:
        total += w["applies_to_midsize"]
    if item.references_primary_regulator:
        total += w["references_primary_regulator"]
    return total


def disposition(score_value: int) -> str:
    if score_value >= config.ALERT_THRESHOLD:
        return "alert"
    if score_value >= config.DIGEST_THRESHOLD:
        return "digest"
    return "archive"


def classify_and_score(item: FeedItem) -> tuple[FeedItem, Classification]:
    """Classify an already-fetched item, then score it deterministically."""
    system = [llm.cache_block(SYSTEM)]
    user = (
        "Classify this item.\n\nSource: " + item.source
        + "\nTitle: " + item.title
        + "\nPublished: " + (item.published.isoformat() if item.published else "unknown")
        + "\n\n" + item.summary
    )
    cls, _usage = llm.structured(Classification, system, user, max_tokens=2000, effort="low")

    item.references_continuity = cls.references_continuity
    item.applies_to_banking = cls.applies_to_banking
    item.applies_to_midsize = cls.applies_to_midsize
    item.references_primary_regulator = cls.references_primary_regulator
    item.relevance_score = score(item)
    item.disposition = disposition(item.relevance_score)
    return item, cls


def record(store, item: FeedItem) -> None:
    store.db.execute(
        "INSERT OR REPLACE INTO feed_items (item_id, source, title, published, summary, url,"
        " relevance_score, disposition, scored_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (item.item_id, item.source, item.title,
         item.published.isoformat() if item.published else "", item.summary, item.url,
         item.relevance_score, item.disposition,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    store.db.commit()
    store.log("agent", "feed_item_scored", item.item_id,
              score=item.relevance_score, disposition=item.disposition, source=item.source)
