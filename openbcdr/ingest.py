"""Ingestion layer (spec section 4.1): plan document in, structured record out.

Extraction is the one job where the model is unambiguously the right tool - the
input is prose written by a dozen different authors over ten years. Everything
downstream reasons over the structured record plus the retained raw text; the
raw text is kept precisely so evidence quotes can be checked against source
rather than against a summary of it.
"""
from __future__ import annotations

from pathlib import Path

from . import boundary, config, llm
from .models import PlanExtract

SYSTEM = """You extract structured data from business continuity and disaster recovery plans.

Rules:
- Extract only what the document states. If a value is absent, use null or an empty list. Never infer, complete, or normalise a value the document does not contain.
- rto_hours and rpo_hours are numbers of hours. Convert stated units (minutes, days) to hours. If a plan states a range, use the longer value.
- `feeds` on an RTO entry lists systems that DEPEND ON this system - that is, systems that cannot operate until this one is recovered. Populate it only from explicit dependency statements in the document.
- `raw_text` on a procedure must be the verbatim procedure text from the document, not a paraphrase. Downstream checks run on that text.
- `system_inventory` is every system the document names anywhere. `critical_systems` is only those the document designates as critical.
- Dates use YYYY-MM-DD. If a date is stated ambiguously, use null rather than guessing a format."""


def read_document(path: Path) -> str:
    """Read a plan. PDF/DOCX are converted upstream; this core takes text."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        raise NotImplementedError(
            "PDF ingestion needs an OCR/extraction step (spec section 9 names AWS Textract "
            "or Azure Document Intelligence). Convert to text first, or wire that here."
        )
    if suffix == ".docx":
        raise NotImplementedError("Convert .docx to text first, or add python-docx here.")
    raise ValueError("unsupported document type: " + suffix)


def extract(
    raw_text: str,
    mode: str,
    attested: bool = False,
    plan_id_hint: str = "",
) -> tuple[PlanExtract, object]:
    """Boundary-check, then extract. Returns (PlanExtract, usage)."""
    boundary.enforce(raw_text, mode, attested)

    system = [llm.cache_block(SYSTEM + config.ORG_CONTEXT)]
    user = (
        "Extract the structured record from this plan document.\n"
        + ("Use plan_id " + plan_id_hint + " if the document does not state one.\n" if plan_id_hint else "")
        + "\n<plan_document>\n"
        + raw_text
        + "\n</plan_document>"
    )
    return llm.structured(PlanExtract, system, user, max_tokens=16000)
