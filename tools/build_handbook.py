"""Generate the OpenBCDR handbook as a .docx.

Everything in the document is read from the live source tree, so regenerating
after an edit keeps the document and the code in step. Nothing is transcribed
by hand.

    python tools/build_handbook.py [-o OUTPUT.docx]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent

# Order matters: this is a reading order, not a directory listing.
CODE_FILES = [
    ("openbcdr/config.py", "Runtime configuration. Every tunable value lives here."),
    ("openbcdr/models.py", "Typed records. The Pydantic models double as the extraction schema."),
    ("openbcdr/boundary.py", "Data-boundary guard. Decides what may be ingested, and in which mode."),
    ("openbcdr/store.py", "Persistence and the hash-chained audit trail."),
    ("openbcdr/llm.py", "The only module that talks to the model API."),
    ("openbcdr/standards.py", "The standards and regulatory index, plus the validation gate."),
    ("openbcdr/ingest.py", "Ingestion: plan document in, structured record out."),
    ("openbcdr/analyzers/compliance.py", "Compliance Analyzer. Model-driven, evidence-verified."),
    ("openbcdr/analyzers/coherence.py", "Coherence Analyzer. Deterministic. No model calls."),
    ("openbcdr/analyzers/trend.py", "Trend Analyzer. Model classifies, arithmetic scores."),
    ("openbcdr/triage.py", "Severity triage, routing, SLA clocks, gap creation."),
    ("openbcdr/report.py", "Report rendering, including the refusal paths."),
    ("openbcdr/calibrate.py", "Calibration. Read-only; measures the agent against risk-team decisions."),
    ("openbcdr/import_findings.py", "Bridge for a prompt-only agent: re-imposes quote verification, dedup and audit on import."),
    ("openbcdr/decompose.py", "Booklet decomposer. Drafts candidate requirements from source material."),
    ("openbcdr/validate_index.py", "Validation walker. Turns index validation into a queue."),
    ("openbcdr/cli.py", "Command line entry point."),
    ("openbcdr/__main__.py", "Module entry point."),
    ("openbcdr/__init__.py", "Package marker."),
    ("openbcdr/analyzers/__init__.py", "Analyzers package marker."),
]

SUPPORT_FILES = [
    ("requirements.txt", "Dependencies."),
    ("config/boundary_patterns.txt", "Deny-list patterns. Tune per deployment."),
    ("standards/finra-4370.json", "Requirement index: FINRA Rule 4370. The only mandatory source. UNVALIDATED."),
    ("standards/ffiec-bcm-2019.json", "Requirement index: FFIEC BCM Booklet Nov 2019. UNVALIDATED."),
    ("standards/nist-800-34r1.json", "Requirement index: NIST SP 800-34 Rev. 1. UNVALIDATED."),
    ("standards/pending/README.md", "How to take a source out of scope so one source can be validated first."),
    ("standards/sources/ffiec-bcm-2019-extract.txt", "FFIEC BCM passages the records were drafted from. Quotes verify against this."),
    ("standards/retired/README.md", "Why the original paraphrased seed index was retired."),
    ("samples/sample_plan.md", "Synthetic plan with defects planted on purpose."),
    ("samples/load_sample.py", "Loads the synthetic plan with no API call."),
]

TEST_FILES = [
    ("tests/run_all.py", "Runs every suite that needs no API key."),
    ("tests/test_offline.py", "Deterministic pipeline."),
    ("tests/test_llm_paths.py", "API paths against a stub transport."),
    ("tests/test_index_build.py", "Decomposer and validation walker."),
    ("tests/test_import.py", "The import bridge."),
    ("tools/build_handbook.py", "Regenerates this document from the source tree."),
    ("tools/make_transfer.py", "Emits the plain-text transfer bundle. Contains the unpacker source verbatim - retype it from here if both attachments are blocked."),
]

DOC_FILES = [
    ("AGENT-INSTRUCTIONS.md",
     "The agent's system prompt. Paste into the instructions field, fill the placeholders."),
    ("PLAN-EVALUATION-PROMPT.md",
     "The per-run prompts. One per evaluation, plus re-check, delta and currency variants."),
    ("LESSONS-LEARNED.md",
     "What this build got wrong on the way, and the general form of each mistake."),
    ("BUILDING-THE-INDEX.md",
     "How to build a validated standards index - the gate on examiner-facing output."),
    ("README.md", "Repository overview, design decisions, and what is not verified."),
]

MONO = "Consolas"


# --------------------------------------------------------------- doc helpers

def shade(paragraph, hex_fill: str) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    pPr.append(shd)


def code_block(doc: Document, text: str, size: float = 7.5) -> None:
    """One paragraph per line so the block stays selectable and pasteable."""
    for line in text.replace("\t", "    ").split("\n"):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.0
        pf.left_indent = Inches(0.12)
        run = p.add_run(line if line.strip() else " ")
        run.font.name = MONO
        run.font.size = Pt(size)
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:ascii"), MONO)
        rFonts.set(qn("w:hAnsi"), MONO)
        shade(p, "F4F4F4")


def para(doc: Document, text: str, bold: bool = False, size: float = 10.5,
         space_after: float = 6) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)


def bullet(doc: Document, text: str, bold_prefix: str = "") -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    if bold_prefix:
        r = p.add_run(bold_prefix)
        r.bold = True
        r.font.size = Pt(10.5)
    r = p.add_run(text)
    r.font.size = Pt(10.5)


def callout(doc: Document, title: str, body: str, fill: str = "FFF4E5") -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.left_indent = Inches(0.1)
    r = p.add_run(title)
    r.bold = True
    r.font.size = Pt(10.5)
    shade(p, fill)
    p2 = doc.add_paragraph()
    p2.paragraph_format.space_before = Pt(0)
    p2.paragraph_format.space_after = Pt(8)
    p2.paragraph_format.left_indent = Inches(0.1)
    r2 = p2.add_run(body)
    r2.font.size = Pt(10.5)
    shade(p2, fill)


def table(doc: Document, headers: list[str], rows: list[list[str]],
          widths: list[float] | None = None) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        r = cell.paragraphs[0].add_run(h)
        r.bold = True
        r.font.size = Pt(9.5)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(val)
            r.font.size = Pt(9.5)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def extract_system_prompt(path: str) -> str:
    """Pull the SYSTEM = \"\"\"...\"\"\" literal out of a module."""
    src = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(r'^SYSTEM = """(.*?)"""', src, re.S | re.M)
    return m.group(1).strip() if m else "(no SYSTEM literal found in " + path + ")"


# ------------------------------------------------------------------ sections

def cover(doc: Document) -> None:
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("OpenBCDR")
    r.bold = True
    r.font.size = Pt(30)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Agent Instructions and Source Code")
    r.font.size = Pt(15)
    r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Risk-owned  |  Human-in-the-loop  |  Augments an existing program")
    r.font.size = Pt(11)
    r.italic = True
    for _ in range(9):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Generated " + date.today().isoformat() + " from the source tree.\n"
                  "Regenerate with: python tools/build_handbook.py")
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(0x77, 0x77, 0x77)
    doc.add_page_break()


def section_1_overview(doc: Document) -> None:
    doc.add_heading("1. What this is", level=1)
    para(doc, "An implementation of the agent core described in the OpenBCDR "
              "system design: ingest a continuity plan, analyze it against a standards "
              "index, produce explainable findings, route them by severity, and write an "
              "audit trail that cannot be quietly edited. It covers sections 4 through 7 "
              "of that design.")
    callout(doc, "What it is not",
            "This is not the full platform. There is no workflow orchestrator, no "
            "Postgres, no vector store, no dashboard, and no GRC connector. Those are "
            "integrations around this core, and each is cheaper to build once the "
            "analysis has proven itself on real plans. The storage layer is SQLite with "
            "the schema the design asks for, so moving to Postgres is a driver change.")

    doc.add_heading("1.1 The engine split", level=2)
    para(doc, "The source design routes all four analyzers through the language model. "
              "This implementation does not, and the difference is deliberate.")
    table(doc,
          ["Layer", "Engine", "Why"],
          [["Plan extraction", "Model", "Input is prose by many authors over many years"],
           ["Compliance coverage", "Model", "\"Does this language satisfy this obligation\" is a judgment"],
           ["Coherence checks", "Python", "Inequalities, date math, marker strings, set differences"],
           ["Feed classification", "Model", "Judgment over prose"],
           ["Feed relevance scoring", "Python", "A fixed weighted sum defined by the design"]],
          widths=[1.6, 0.9, 4.0])
    para(doc, "Coherence is the layer an examiner will ask you to reproduce. Whether one "
              "system's recovery time exceeds another's has exactly one correct answer, "
              "derivable from two numbers. Routing that through a model produces a "
              "non-reproducible answer to a reproducible question, and buys nothing. "
              "Being deterministic also makes it free and instant, so it can run on every "
              "save while the compliance pass runs overnight.")

    doc.add_heading("1.2 The two guarantees", level=2)
    callout(doc, "Evidence is verified, not trusted",
            "Every finding of full or partial coverage must carry a quote copied "
            "verbatim from the plan. That quote is checked against the source text in "
            "code before the finding is allowed to stand. Whitespace and typographic "
            "quotes are normalised so a PDF conversion does not raise a false alarm, but "
            "reworded, merged or invented text fails the check. A failed quote is not a "
            "near miss - it is the model manufacturing coverage, which in a compliance "
            "tool is the worst available failure. Those findings are forced to "
            "insufficient_evidence, excluded from the score, and listed under their own "
            "heading in the report.", "FFF4E5")
    callout(doc, "The audit trail is hash-chained",
            "Each audit row's hash covers the previous row's hash plus its own "
            "canonicalised payload. Editing a row or deleting one both break "
            "verification, and audit-verify names the first broken sequence number. "
            "Both attack shapes are covered by the test suite.", "E8F1FB")
    para(doc, "Nothing in this code closes a gap, approves an exception, edits a plan, or "
              "sends a notification. The agent proposes; a named human disposes. The "
              "command gaps --notifications prints what would be sent, to whom, with "
              "which deadline, without sending anything.")
    doc.add_page_break()


def section_2_operating(doc: Document) -> None:
    doc.add_heading("2. Operating instructions", level=1)

    doc.add_heading("2.1 Install", level=2)
    code_block(doc, "pip install -r requirements.txt", 9)
    para(doc, "Two runtime dependencies: the Anthropic SDK and Pydantic. Python 3.10 or "
              "newer. No database server, no message broker, no scheduler.", space_after=10)

    doc.add_heading("2.2 Run the whole deterministic half with no key and no spend", level=2)
    code_block(doc,
               "python samples/load_sample.py --db test.sqlite3\n"
               "python -m openbcdr --db test.sqlite3 coherence --plan APP_PAYPROC_v4 --open-gaps\n"
               "python -m openbcdr --db test.sqlite3 gaps --notifications\n"
               "python -m openbcdr --db test.sqlite3 report --plan APP_PAYPROC_v4\n"
               "python -m openbcdr --db test.sqlite3 audit-verify\n"
               "python tests/run_all.py", 9)
    para(doc, "The sample is a fully invented institution with defects planted on purpose: "
              "a recovery-time dependency conflict, a critical system with no objectives, "
              "a contact stale by nearly two years, a TBD responsible party, a critical "
              "vendor with no documented recovery expectation, a procedure referencing a "
              "decommissioned system, and a current plan version that has never been "
              "tested.", space_after=10)

    doc.add_heading("2.3 The two commands that call the API", level=2)
    code_block(doc,
               "python -m openbcdr ingest plans/payment-processing.md --mode internal\n"
               "python -m openbcdr analyze --plan APP_PAYPROC_v4 --open-gaps", 9)
    para(doc, "Credentials resolve from the environment; the client is constructed with no "
              "arguments, so pointing the agent at an approved endpoint is a change to one "
              "function (client() in llm.py) and nothing else. Model and effort are "
              "overridable with the BCDR_MODEL and BCDR_EFFORT environment variables.",
         space_after=10)

    doc.add_heading("2.4 Command reference", level=2)
    table(doc, ["Command", "Calls the API", "What it does"],
          [["ingest", "Yes", "Boundary-check a plan, extract it, store a version"],
           ["coherence", "No", "Deterministic checks; --open-gaps writes them to the registry"],
           ["analyze", "Yes", "Coverage against the standards index"],
           ["report", "No", "Render the compliance report; --examiner enforces validation"],
           ["gaps", "No", "List open gaps; --notifications previews routing"],
           ["decide", "No", "Record a risk-owner decision against a gap"],
           ["audit-verify", "No", "Walk the hash chain and report the first break"],
           ["audit-log", "No", "Print recent audit events"]],
          widths=[1.15, 1.0, 4.35])

    para(doc, "The prompts and the index-building method are reproduced in full in "
              "section 8. Nothing in this handbook depends on a file you do not have.",
              space_after=10)

    doc.add_heading("2.5 Modes", level=2)
    para(doc, "Two modes, and choosing the wrong one is the entire point of the boundary "
              "module.")
    bullet(doc, "development, demonstrations, and any work outside the institution's own "
                "environment. Public frameworks and synthetic or sanitized plans only. "
                "Fail-closed: a document is refused unless it passes the deny-list scan "
                "AND the caller explicitly attests it is synthetic. The attestation is "
                "written to the audit trail against a named actor.", "sandbox - ")
    bullet(doc, "the agent deployed inside the institution, reading that institution's own "
                "plans. This is the production mode. The deny-list still runs, but as a "
                "stray-document check - a third party's confidential material landing in "
                "the ingestion folder - rather than a blanket block.", "internal - ")
    para(doc, "Tune config/boundary_patterns.txt to the deployment. Patterns naming a "
              "specific organisation belong in config/boundary_patterns.local.txt, which "
              "is excluded from version control. For an in-house install, empty the "
              "shipped file and list third-party markers instead.")
    doc.add_page_break()


def section_3_prompts(doc: Document) -> None:
    doc.add_heading("3. Agent instructions (the prompts)", level=1)
    para(doc, "Three prompts, reproduced verbatim from source. They are collected here so "
              "they can be reviewed as text, without reading Python. Each one is the "
              "complete instruction set the model receives for that task; there is no "
              "hidden preamble.")

    for title, path, note in [
        ("3.1 Plan extraction", "openbcdr/ingest.py",
         "Sent with the plan document in the user turn. The prompt forbids inference: an "
         "absent value must come back null rather than completed. Procedure text is "
         "demanded verbatim because downstream marker checks run on it."),
        ("3.2 Compliance coverage", "openbcdr/analyzers/compliance.py",
         "Sent with the plan cached in the system prefix and a batch of requirements in "
         "the user turn. The verbatim-quote rule is the load-bearing instruction, and it "
         "is enforced in code afterwards rather than trusted. Note the final clause: "
         "absence of evidence is a gap, not a pass."),
        ("3.3 Regulatory feed classification", "openbcdr/analyzers/trend.py",
         "Four independent yes/no judgments. The weighted score and the alert threshold "
         "are computed in code, so the model never decides whether a human is paged. The "
         "prompt biases toward answering no, because a false negative reaches a digest "
         "while a false positive pages a risk officer."),
    ]:
        doc.add_heading(title, level=2)
        para(doc, note, size=10)
        code_block(doc, extract_system_prompt(path), 8.5)
        doc.add_paragraph()
    doc.add_page_break()


def section_4_gate(doc: Document) -> None:
    doc.add_heading("4. The standards index, and the gate on it", level=1)
    callout(doc, "Read this before anyone acts on a report",
            "standards/seed-index.json ships 14 PARAPHRASED requirement records. Every one "
            "is flagged validated_by_human: false. Section references mirror the citations "
            "used in the source design document and have NOT been re-read against the "
            "primary sources. They exist to exercise the pipeline, not to state the law.",
            "FDE7E9")
    para(doc, "While any requirement in scope is still unvalidated, the report prints a "
              "NOT EXAMINER-READY banner and withholds the coverage score, and "
              "report --examiner refuses to render at all. That refusal is the feature. A "
              "compliance percentage computed from requirement text nobody has checked is "
              "a number that looks like evidence and is not one.")
    para(doc, "To validate a record: open the primary source, confirm the section number "
              "and the obligation, replace the requirement text with accurate language, "
              "set last_confirmed and as_of_version, then set validated_by_human to true. "
              "The design sizes a real index at 200 to 300 records.")
    para(doc, "One field is deliberately curated rather than inferred: gap_severity. What "
              "severity an unmet requirement carries is a policy decision belonging to the "
              "risk function, not a judgment for a model.")

    doc.add_heading("4.1 Applicability, so out-of-scope obligations stay out", level=2)
    para(doc, "Each record carries applicability tags, and a requirement applies when any "
              "of its tags is true for the institution. This is what keeps obligations "
              "scoped to systemically important institutions from firing at a mid-size "
              "bank. The default profile assumes a FINRA member, a Federal Reserve member, "
              "and non-SIFI status; override it with --profile pointing at a JSON file.")
    code_block(doc, '{"all_banks": true, "finra_member": true, "fed_member": true, "sifi": false}', 9)
    doc.add_page_break()


def section_5_deviations(doc: Document) -> None:
    doc.add_heading("5. Deviations from the source design", level=1)

    doc.add_heading("5.1 A contradiction in the recovery-time rule", level=2)
    para(doc, "Section 6.2A of the design states the rule as \"if System A feeds System B, "
              "System A's RTO must be less than or equal to System B's RTO\", and the "
              "worked example immediately beneath it reads the other way round. The "
              "implemented rule is the one that holds operationally: a system cannot be "
              "restored before the thing it depends on. Both directions are covered by a "
              "test, so the behaviour is pinned rather than assumed.")

    doc.add_heading("5.2 Deadlines are business days throughout", level=2)
    para(doc, "The design specifies business days for critical findings and calendar days "
              "for medium and low. This implementation uses business days for all of them, "
              "so there is one clock rather than two. Change it deliberately in config.py "
              "if the program wants the original mix.")
    callout(doc, "Holidays are not modelled",
            "add_business_days skips weekends only. Wire an actual holiday calendar before "
            "these deadlines drive a real escalation clock, or a finding raised before a "
            "long weekend will show a deadline a day or two earlier than the program "
            "intends.", "FFF4E5")

    doc.add_heading("5.3 Built deliberately shallow, or not at all", level=2)
    bullet(doc, "the plumbing for regulatory sources is integration work with no shared "
                "logic. classify_and_score takes an already-fetched item, so the fetcher "
                "is a separate concern.", "Feed fetching - ")
    bullet(doc, "read_document raises with a pointer rather than half-doing it. The design "
                "names a document-intelligence service for this.", "OCR, PDF and DOCX - ")
    bullet(doc, "the format corrector, the dashboard, the GRC and document-management "
                "integrations, and the one-click examiner package are all downstream of a "
                "working analysis.", "Sections 6.4 and 8 through 11 - ")
    bullet(doc, "of the seven questions in section 13, only FINRA member status and "
                "charter change behaviour here, and both are handled by the applicability "
                "profile.", "The open design decisions - ")
    doc.add_page_break()


def section_6_verification(doc: Document) -> None:
    doc.add_heading("6. Verification record", level=1)
    para(doc, "112 automated checks, none of which require an API key, a network "
              "connection, or any spend. Run them with python tests/run_all.py.")

    doc.add_heading("6.1 What the tests actually assert", level=2)
    bullet(doc, "every planted defect class is found, AND the compliant vendor, the "
                "complete procedure and the current contacts are not flagged. A checker "
                "that flags everything is as useless as one that flags nothing, so both "
                "directions are asserted.", "Coherence - ")
    bullet(doc, "the suite pins today to a fixed date. A moving clock makes staleness "
                "thresholds flap and the suite start failing months later for no reason.",
           "Time - ")
    bullet(doc, "verbatim text across a source line break is accepted, collapsed "
                "whitespace is accepted, typographic quotes are accepted; invented text, "
                "real words reordered into a false claim, and empty quotes are all "
                "rejected.", "Evidence verification - ")
    bullet(doc, "an edited row and a deleted row both break the chain.", "Audit trail - ")
    bullet(doc, "a stub client records the exact request body. The emitted schema is "
                "strict at every nesting level; the plan sits in the cached system prefix "
                "while only the requirement batch varies; batching maths out; a fabricated "
                "quote is downgraded; a requirement the model silently drops surfaces "
                "rather than reading as compliant; refusals and token-limit truncation "
                "raise rather than parse as results.", "API paths - ")
    bullet(doc, "request bodies are checked against the SDK's own parameter types, "
                "confirming the output configuration is legal as constructed.",
           "SDK conformance - ")

    doc.add_heading("6.2 What has NOT been verified", level=2)
    callout(doc, "No live API call has ever been made",
            "The three model-calling paths are proven up to the wire and no further. "
            "Three things only a real key settles. First, whether the server accepts the "
            "generated schema - the nested definitions are the risky part. Second, whether "
            "the model honours the verbatim-quoting instruction in practice; if it "
            "paraphrases by habit, the downgrade path will fire constantly and the "
            "normaliser needs loosening, carefully, because every loosening buys back some "
            "of the fabrication risk it exists to catch. Third, real caching behaviour - "
            "run analyze against the synthetic sample first and watch the cache-read "
            "counter, which the command warns about when it reads zero across multiple "
            "calls, meaning every batch is re-billing the whole document.", "FDE7E9")

    doc.add_heading("6.3 Deployment questions this code does not answer", level=2)
    para(doc, "Which endpoint the agent calls, what retention applies to prompts and "
              "responses, and who may run it are governance decisions for the "
              "institution's own review. The code takes no position: it constructs a "
              "zero-argument client and reads credentials from the environment.")
    doc.add_page_break()


def section_7_code(doc: Document) -> None:
    doc.add_heading("7. Source code", level=1)
    para(doc, "Complete and current as of generation. Code is set one line per paragraph "
              "so it can be selected and pasted without reflowing. Recreate the directory "
              "layout exactly - the package uses relative imports.")
    code_block(doc,
               "openbcdr/\n"
               "  openbcdr/\n"
               "    __init__.py  __main__.py  config.py  models.py  boundary.py\n"
               "    store.py  llm.py  standards.py  ingest.py  triage.py  report.py  cli.py\n"
               "    analyzers/\n"
               "      __init__.py  compliance.py  coherence.py  trend.py\n"
               "  config/boundary_patterns.txt\n"
               "  standards/seed-index.json\n"
               "  samples/sample_plan.md  samples/load_sample.py\n"
               "  tests/run_all.py  tests/test_offline.py  tests/test_llm_paths.py\n"
               "  requirements.txt", 8.5)
    doc.add_paragraph()

    for group_title, files in [
        ("7.1 Package modules", CODE_FILES),
        ("7.2 Configuration, standards and sample data", SUPPORT_FILES),
        ("7.3 Tests", TEST_FILES),
    ]:
        doc.add_heading(group_title, level=2)
        for rel, note in files:
            path = ROOT / rel
            if not path.exists():
                continue
            h = doc.add_heading(rel, level=3)
            for r in h.runs:
                r.font.name = MONO
            para(doc, note, size=9.5, space_after=4)
            code_block(doc, path.read_text(encoding="utf-8").rstrip("\n"))
            doc.add_paragraph()


def section_8_documents(doc: Document) -> None:
    doc.add_heading("8. Operating documents", level=1)
    para(doc, "The four markdown documents that run alongside the code, reproduced "
              "in full so this handbook is sufficient on its own. They are set as "
              "code blocks rather than formatted prose because they are meant to be "
              "copied back out unchanged - the first two are pasted directly into an "
              "agent.")
    callout(doc, "Which goes where",
            "AGENT-INSTRUCTIONS.md is the system prompt: paste it once into the "
            "agent's instructions field and fill the five placeholders. "
            "PLAN-EVALUATION-PROMPT.md is the task: paste one of its blocks each "
            "time a plan is evaluated. The instructions say what the agent must do; "
            "the run prompt is what makes it actually happen on a given plan. Both "
            "are needed - an instruction alone gets followed inconsistently across a "
            "long context.", "E8F1FB")

    for rel, note in DOC_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        h = doc.add_heading(rel, level=2)
        for r in h.runs:
            r.font.name = MONO
        para(doc, note, size=10, space_after=4)
        code_block(doc, path.read_text(encoding="utf-8").rstrip("\n"), 8.0)
        doc.add_paragraph()


def add_footer(doc: Document) -> None:
    for section in doc.sections:
        p = section.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run("OpenBCDR - Agent Instructions and Source Code    |    "
                      "Page ")
        r.font.size = Pt(8)
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), "PAGE")
        p._p.append(fld)


def build(out_path: Path) -> Path:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.85)
        s.top_margin = s.bottom_margin = Inches(0.8)

    cover(doc)
    section_1_overview(doc)
    section_2_operating(doc)
    section_3_prompts(doc)
    section_4_gate(doc)
    section_5_deviations(doc)
    section_6_verification(doc)
    section_7_code(doc)
    section_8_documents(doc)
    add_footer(doc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=str(ROOT / "OpenBCDR-Handbook.docx"))
    args = ap.parse_args()
    p = build(Path(args.out))
    print("Wrote " + str(p) + " (" + str(round(p.stat().st_size / 1024)) + " KB)")
