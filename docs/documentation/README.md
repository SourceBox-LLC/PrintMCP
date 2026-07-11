# PrintMCP Documentation

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
