"""Emit a plain-text transfer bundle for environments that only accept text.

Writes the whole working tree into one readable text file with a SHA-256
manifest, plus a short unpacker. Use it only where your organisation's rules
allow bringing code in this way; it is meant to make code reviewable, never to
get around a security control.

Nothing is encoded or compressed, deliberately. A base64 blob pasted into a
script is exactly the shape a security team should refuse, and refusing it would
be correct. Everything here stays readable so it can be reviewed on the way in.

    python tools/make_transfer.py [-o DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = "#>>>>>> FILE: "
END = "#>>>>>> END OF BUNDLE"
SKIP_DIRS = {"__pycache__", ".netlify", "dist", ".git", ".venv", "venv", ".claude"}
# Local-only files (config/boundary_patterns.local.txt, CLAUDE.local.md, ...)
# hold machine- or organisation-specific values and must never leave this
# machine. Matched case-insensitively: Windows resolves .LOCAL.txt to the same file.
LOCAL_MARK = ".local."
# This tool's own outputs are never inputs, wherever --out pointed last time.
BUNDLE_OUTPUTS = {"openbcdr-source.txt", "unpack.py"}
KEEP_EXT = (".py", ".json", ".txt", ".md")

# The emitted unpacker. Note the markers are matched ANCHORED TO LINE START:
# this generator's own source ships inside the bundle and contains the marker
# strings as constants, so an unanchored match parses them as real markers and
# corrupts the extraction. That is not hypothetical - it happened on the first run.
UNPACK = '''"""Rebuild the openbcdr tree from the plain-text bundle.

    python unpack.py openbcdr-source.txt

Verifies every file against the SHA-256 manifest in the bundle and refuses to
finish quietly if anything does not match - which is what catches a truncated
copy-paste, or a mail gateway that rewrote the text in transit.
"""
import hashlib
import sys
from pathlib import Path

MARK = "#>>>>>> FILE: "
END = "#>>>>>> END OF BUNDLE"
NL = chr(10)


def main(bundle_path):
    text = Path(bundle_path).read_text(encoding="utf-8")
    if (NL + END) not in text:
        raise SystemExit("bundle is truncated: end marker missing")

    manifest = {}
    body_start = text.index(NL + MARK) + 1
    for line in text[:body_start].splitlines():
        if line.startswith("#  "):
            parts = line[3:].split(None, 1)
            if len(parts) == 2:
                manifest[parts[1].strip()] = parts[0].strip()

    # Markers count only at the start of a line - some bundled files contain the
    # marker strings as ordinary text.
    chunks = (NL + text[body_start:]).split(NL + MARK)
    written, bad, unlisted = 0, [], []
    for chunk in chunks:
        if not chunk.strip():
            continue
        header, _, content = chunk.partition(NL)
        rel = header.strip()
        if not rel:
            continue
        content = content.split(NL + END)[0]
        # No trailing-newline strip here. The bundle separator consumes exactly
        # the newline the writer inserted, so what remains is the file's own
        # content. Stripping one more removed every file's final newline and made
        # all 40 checksums fail at once - which is how this was found.
        out = Path(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8", newline=NL)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if rel not in manifest:
            unlisted.append(rel)
        elif manifest[rel] != digest:
            bad.append(rel)
        written += 1

    print("wrote " + str(written) + " files")
    missing = [r for r in manifest if not Path(r).exists()]
    if bad or unlisted or missing:
        print("")
        if bad:
            print("CHECKSUM MISMATCH on " + str(len(bad)) + " file(s):")
            for b in bad:
                print("   " + b)
        if missing:
            print("MISSING - in the manifest but not in the bundle: " + str(len(missing)))
            for m in missing:
                print("   " + m)
        if unlisted:
            print("UNLISTED - in the bundle but not in the manifest: " + str(len(unlisted)))
            for u in unlisted:
                print("   " + u)
        print("")
        print("The transfer was corrupted. Do not run this code - get a clean copy.")
        raise SystemExit(2)

    print("all " + str(len(manifest)) + " files match the manifest")
    print("")
    print("next:  cd openbcdr")
    print("       pip install -r requirements.txt")
    print("       python tests/run_all.py")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python unpack.py openbcdr-source.txt")
    main(sys.argv[1])
'''


def collect(exclude: Path | None = None) -> list[tuple[str, str]]:
    """`exclude` is the output directory, so a custom --out inside the tree does
    not pack the previous bundle on the next build."""
    files = []
    excluded = exclude.resolve() if exclude is not None else None
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS
                       and LOCAL_MARK not in d.lower()
                       and (excluded is None or (Path(dirpath) / d).resolve() != excluded)]
        for f in sorted(filenames):
            if (not f.endswith(KEEP_EXT) or "Handbook" in f or LOCAL_MARK in f.lower()
                    or f.lower() in BUNDLE_OUTPUTS):
                continue
            p = Path(dirpath) / f
            rel = "openbcdr/" + str(p.relative_to(ROOT)).replace("\\", "/")
            content = p.read_text(encoding="utf-8")
            # Normalise to exactly one trailing newline BEFORE hashing, so the
            # manifest describes what the bundle actually carries. Hashing the
            # raw file and shipping a normalised copy is what put the two ends
            # out of step.
            if not content.endswith("\n"):
                content += "\n"
            files.append((rel, content))
    files.sort()
    return files


def build(out_dir: Path) -> tuple[Path, Path]:
    if out_dir.resolve() == ROOT.resolve():
        raise SystemExit("refusing --out at the project root: use dist/ or a folder outside the tree")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = collect(exclude=out_dir)

    # The unpacker source is carried INSIDE this file. A .py attachment gets
    # blocked where a .txt does not, so a bundle that needs a separate unpacker
    # to arrive is a bundle that does not arrive. One file, self-sufficient.
    head = [
        "OPENBCDR - PLAIN-TEXT TRANSFER BUNDLE",
        "",
        "The complete source tree as readable text. Nothing is encoded or compressed;",
        "every line can be reviewed before it is used.",
        "",
        "This file is self-sufficient. The unpacker is included below - you do not need",
        "any other attachment.",
        "",
        "TO REBUILD, IN THREE STEPS:",
        "",
        "  1. Copy the block between the two UNPACKER lines below - the whole block,",
        "     starting at the line after BEGIN and ending at the line before END.",
        "  2. Paste it into Notepad and save it beside this file as:  unpack.py",
        "  3. Run:  python unpack.py openbcdr-source.txt",
        "",
        "It writes " + str(len(files)) + " files and checks each against the manifest at the end of this",
        "header. It stops loudly on any mismatch, missing file, or unlisted file -",
        "which is how a truncated attachment or a gateway that rewrote the text gets",
        "caught, instead of silently producing code that looks fine and is not.",
        "",
        "=" * 78,
        "UNPACKER - BEGIN (copy from the next line)",
        "=" * 78,
    ]
    head += UNPACK.rstrip("\n").split("\n")
    head += [
        "=" * 78,
        "UNPACKER - END (copy up to the previous line)",
        "=" * 78,
        "",
        "MANIFEST (sha256, path) - " + str(len(files)) + " files",
        "",
    ]
    for rel, content in files:
        head.append("#  " + hashlib.sha256(content.encode("utf-8")).hexdigest() + "  " + rel)
    head += ["", "=" * 78, ""]

    body = []
    for rel, content in files:
        body.append(MARK + rel)
        body.append(content if content.endswith("\n") else content + "\n")
    body.append(END + "\n")

    bundle = out_dir / "openbcdr-source.txt"
    bundle.write_text("\n".join(head) + "\n".join(body), encoding="utf-8", newline="\n")

    unpack = out_dir / "unpack.py"
    unpack.write_text(UNPACK, encoding="utf-8", newline="\n")

    print("bundle: " + bundle.name + "  " + str(round(bundle.stat().st_size / 1024))
          + " KB, " + str(len(files)) + " files")
    print("unpack: " + unpack.name + "  " + str(len(UNPACK.splitlines())) + " lines")
    return bundle, unpack


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=str(ROOT / "dist"))
    build(Path(ap.parse_args().out))
