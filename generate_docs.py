#!/usr/bin/env python3
"""
Generate the Docsify documentation mirror for PrintMCP.

Copies the in-repo documentation (docs/*.md, docs/tutorials/, docs/tools/,
docs/img/) and root-level project docs (README, CHANGELOG) into
docs/documentation/ and auto-generates _sidebar.md + a landing README.

Run by the Sync Docs workflow on every push to doc paths, and hourly.

Usage:
    python3 generate_docs.py    # from the repo root
"""

import shutil
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
MIRROR = ROOT / "docs" / "documentation"

# ── Source ────────────────────────────────────────────────────────────
DOCS_SRC = ROOT / "docs"

# ── Root-level project docs to mirror ─────────────────────────────────
PROJECT_FILES = ["README.md", "CHANGELOG.md"]

# ── Top-level doc pages (in docs/) to include in the sidebar ──────────
# Excluded: README.md (used as the docs landing page), .nojekyll, CNAME,
# index.html, og-image.png, img/ (copied as assets)
TOP_LEVEL_DOCS = [
    "getting-started.md",
    "configuration.md",
    "architecture.md",
    "safety.md",
    "troubleshooting.md",
    "RELEASING.md",
]


def make_title(filename: str) -> str:
    """Convert a filename stem into a human-readable sidebar title."""
    name = filename.replace(".md", "").replace("_", " ").replace("-", " ")
    replacements = {
        "readme": "Overview",
        "getting started": "Getting Started",
        "configuration": "Configuration",
        "architecture": "Architecture",
        "safety": "Safety Model",
        "troubleshooting": "Troubleshooting",
        "releasing": "Releasing to PyPI",
        "changelog": "Changelog",
        "agentic": "Agentic",
        # Tutorials
        "01 find and download": "1 · Find Something to Print",
        "02 slice for your printer": "2 · Get It Print-Ready",
        "03 print with octoprint": "3 · Send It to Your Printer",
        "04 end to end": "4 · From Idea to Object",
        # Tools — README inside tools/ should be "Tool Reference", not "Overview"
        "tools/readme": "Tool Reference",
        "cura": "Cura (Slicing)",
        "octoprint": "OctoPrint (Printing)",
        "thingiverse": "Thingiverse (Models)",
    }
    lower = name.lower()
    # Check the full path first (e.g. "tools/readme" → "Tool Reference")
    if lower in replacements:
        return replacements[lower]
    # Then check just the last segment (e.g. "tools/cura" → "cura" → "Cura (Slicing)")
    last_segment = lower.split("/")[-1]
    if last_segment in replacements:
        return replacements[last_segment]
    # Title-case fallback, preserving acronyms
    acronyms = {"MCP", "API", "PyPI", "GCode", "STL"}
    lowercase_words = {"with", "and", "the", "a", "an", "to", "for", "of", "in", "on", "at", "by"}
    words = name.split()
    result = []
    for i, w in enumerate(words):
        if w.upper() in acronyms:
            result.append(w.upper())
        elif i > 0 and w.lower() in lowercase_words:
            result.append(w.lower())
        else:
            result.append(w.capitalize())
    return " ".join(result)


LANDING_PAGE = """# PrintMCP Documentation

Documentation for **PrintMCP** — an MCP server that automates the entire 3D-printing pipeline. Find a model, slice it, and print it — all driven by your AI assistant.

## Start Here

New to PrintMCP? Follow this path in order:

1. **[Getting Started](getting-started)** — a one-time setup: install PrintMCP and connect it to your assistant. ~10 minutes.
2. **[Tutorial 1 · Find Something to Print](tutorials/01-find-and-download)** — ask your assistant to find and download a model.
3. **[Tutorial 2 · Get It Print-Ready](tutorials/02-slice-for-your-printer)** — describe how you want it printed, in plain words.
4. **[Tutorial 3 · Send It to Your Printer](tutorials/03-print-with-octoprint)** — start and watch a real print, safely.
5. **[Tutorial 4 · From Idea to Object](tutorials/04-end-to-end)** — the whole thing in one conversation.

## Reference

- [Configuration](configuration) — All environment variables and what they do.
- [Architecture](architecture) — How PrintMCP is put together and why.
- [Safety Model](safety) — How PrintMCP prevents accidental physical actions.
- [Troubleshooting](troubleshooting) — Fixes for common issues.

## Tool Reference (for Developers)

- [Tool Reference](tools/README) — The 14 MCP tools, their schemas, and shared contract.
- [Thingiverse (Models)](tools/thingiverse) — Level 1: search, get, and download models.
- [Cura (Slicing)](tools/cura) — Level 2: slice STL files into G-code.
- [OctoPrint (Printing)](tools/octoprint) — Level 3: upload, control, and monitor prints.

## Project

- [Overview](project/README) — Project README with features and install instructions.
- [Changelog](project/CHANGELOG) — Release history and changes.
- [Releasing to PyPI](RELEASING) — How releases are published (Trusted Publishing).

---

*This documentation is auto-generated from the [PrintMCP](https://github.com/SourceBox-LLC/PrintMCP) repository by the **Sync Docs** workflow.*
"""


def write_landing_page():
    """Write the docsify landing page (README.md) at the mirror root."""
    (MIRROR / "README.md").write_text(LANDING_PAGE)


# ── Main ──────────────────────────────────────────────────────────────
print(f"PrintMCP docs generation — {datetime.now(timezone.utc).isoformat()}")

# Preserve index.html, wipe everything else in the mirror dir
if MIRROR.exists():
    for item in MIRROR.iterdir():
        if item.name != "index.html":
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
else:
    MIRROR.mkdir(parents=True)

# Write the docsify landing page *after* the wipe so it always exists.
write_landing_page()

total_files = 0
sidebar_lines = [
    "<!-- Auto-generated by generate_docs.py — do not edit manually -->",
    "",
    "- [Home](README)",
    "",
]

# ── Top-level docs (copied flat into the mirror root) ─────────────────
sidebar_lines.append("- **Getting Started**")
for fname in TOP_LEVEL_DOCS:
    src = DOCS_SRC / fname
    if src.exists():
        dst = MIRROR / fname
        shutil.copy2(src, dst)
        title = make_title(fname)
        link = fname.replace(".md", "")
        sidebar_lines.append(f"  - [{title}]({link})")
        total_files += 1
        print(f"  + {fname}")
sidebar_lines.append("")

# ── Tutorials ─────────────────────────────────────────────────────────
tut_src = DOCS_SRC / "tutorials"
if tut_src.exists():
    tut_dst = MIRROR / "tutorials"
    shutil.copytree(tut_src, tut_dst)
    sidebar_lines.append("- **Tutorials**")
    for md in sorted(tut_dst.glob("*.md")):
        title = make_title(md.stem)
        link = f"tutorials/{md.stem}"
        sidebar_lines.append(f"  - [{title}]({link})")
        total_files += 1
        print(f"  + tutorials/{md.name}")
    sidebar_lines.append("")

# ── Tool Reference ────────────────────────────────────────────────────
tools_src = DOCS_SRC / "tools"
if tools_src.exists():
    tools_dst = MIRROR / "tools"
    shutil.copytree(tools_src, tools_dst)
    sidebar_lines.append("- **Tool Reference**")
    for md in sorted(tools_dst.glob("*.md")):
        rel = f"tools/{md.stem}"
        title = make_title(rel)
        sidebar_lines.append(f"  - [{title}]({rel})")
        total_files += 1
        print(f"  + tools/{md.name}")
    sidebar_lines.append("")

# ── Copy the logo image so the docs README can reference it ───────────
img_src = DOCS_SRC / "img"
if img_src.exists():
    img_dst = MIRROR / "img"
    shutil.copytree(img_src, img_dst)

# ── Root-level Project Docs ───────────────────────────────────────────
project_dir = MIRROR / "project"
project_dir.mkdir(parents=True, exist_ok=True)
sidebar_lines.append("- **Project**")
for f in PROJECT_FILES:
    src = ROOT / f
    if src.exists():
        dst = project_dir / f
        shutil.copy2(src, dst)
        title = make_title(f)
        link = f"project/{f.replace('.md', '')}"
        sidebar_lines.append(f"  - [{title}]({link})")
        total_files += 1
        print(f"  + {f}")
sidebar_lines.append("")

# ── External links ────────────────────────────────────────────────────
sidebar_lines.append("- [Report a Bug](https://github.com/SourceBox-LLC/PrintMCP/issues)")
sidebar_lines.append("- [PyPI](https://pypi.org/project/printmcp/)")
sidebar_lines.append("- [Edit on GitHub](https://github.com/SourceBox-LLC/PrintMCP)")
sidebar_lines.append("")

(MIRROR / "_sidebar.md").write_text("\n".join(sidebar_lines))
print(f"\nGenerated _sidebar.md ({len(sidebar_lines)} lines)")
print(f"Total docs mirrored: {total_files} markdown files")
print("Generation complete!")