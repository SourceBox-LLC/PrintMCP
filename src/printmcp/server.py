"""PrintMCP server entry point."""

from __future__ import annotations

from .app import mcp


def main() -> None:
    """Register all tools and run the MCP server over stdio.

    Tool modules register their handlers as an import side effect, so they must
    be imported before ``mcp.run()``.
    """
    from . import thingiverse  # noqa: F401  (registers Level 1 tools)
    from . import cura  # noqa: F401  (registers Level 2 tools)
    from . import octoprint  # noqa: F401  (registers Level 3 tools)

    mcp.run()


if __name__ == "__main__":
    main()
