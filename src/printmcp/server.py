"""PrintMCP server entry point and command-line interface."""

from __future__ import annotations

import sys

from . import __version__
from .app import mcp

# All human-facing CLI output goes to stderr. stdout is reserved for the MCP
# stdio protocol, so writing anything there in server mode would corrupt it.

_USAGE = """\
PrintMCP - an MCP server for the 3D-printing pipeline (find -> slice -> print).

Usage:
  printmcp [options]

Options:
  (no args)        Run the MCP server over stdio (the normal mode; it blocks
                   waiting for an MCP client, which is expected).
  --check          Check your configuration for each level and exit. Reports
                   what's set up without revealing any secrets.
  -v, --version    Print the version and exit.
  -h, --help       Show this help and exit.

PrintMCP is normally launched by an MCP client, not by hand. See
https://github.com/SourceBox-LLC/PrintMCP for setup.
"""


def _register_tools() -> None:
    """Import tool modules so their @mcp.tool handlers register (side effect)."""
    from . import (
        cura,  # noqa: F401  (registers Level 2 tools)
        octoprint,  # noqa: F401  (registers Level 3 tools)
        thingiverse,  # noqa: F401  (registers Level 1 tools)
    )


def _check() -> int:
    """Print a configuration self-diagnostic. Returns a process exit code.

    Never prints secret values - only whether each is set, and where tools are
    found. Exit code is 0 if every level is ready, else 1.
    """
    from .config import (
        get_octoprint_api_key,
        get_octoprint_url,
        get_token,
    )

    out = sys.stderr
    print(f"PrintMCP {__version__} - configuration check\n", file=out)

    ok = True

    # Level 1: Thingiverse
    token = get_token()
    if token:
        print("[ OK ] Level 1 (Thingiverse): THINGIVERSE_TOKEN is set.", file=out)
    else:
        ok = False
        print(
            "[MISSING] Level 1 (Thingiverse): THINGIVERSE_TOKEN is not set. "
            "Search/download will not work.",
            file=out,
        )

    # Level 2: Cura / CuraEngine
    try:
        from .config import get_cura_paths

        paths = get_cura_paths()
        print(f"[ OK ] Level 2 (Cura): CuraEngine found at {paths.engine}", file=out)
    except FileNotFoundError as e:
        ok = False
        print(f"[MISSING] Level 2 (Cura): {e}", file=out)

    # Level 3: OctoPrint
    url = get_octoprint_url()
    key = get_octoprint_api_key()
    if url and key:
        print(
            f"[ OK ] Level 3 (OctoPrint): OCTOPRINT_URL ({url}) and API key are set.",
            file=out,
        )
    else:
        ok = False
        missing = " and ".join(
            n for n, v in (("OCTOPRINT_URL", url), ("OCTOPRINT_API_KEY", key)) if not v
        )
        print(
            f"[MISSING] Level 3 (OctoPrint): {missing} not set. Printing will not work.",
            file=out,
        )

    print(
        "\n"
        + (
            "All levels are configured."
            if ok
            else "Some levels are not configured (that's fine if you don't need them - "
            "the levels are independent)."
        ),
        file=out,
    )
    return 0 if ok else 1


def main() -> None:
    """CLI entry point: parse args, then run the server or a one-shot command."""
    args = sys.argv[1:]

    if args:
        flag = args[0]
        if flag in ("-h", "--help"):
            print(_USAGE, file=sys.stderr)
            return
        if flag in ("-v", "--version"):
            print(__version__, file=sys.stderr)
            return
        if flag == "--check":
            _register_tools()
            sys.exit(_check())
        print(f"printmcp: unknown option '{flag}'\n", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        sys.exit(2)

    # No args: run the MCP server over stdio. Tool modules must be imported
    # (registering their handlers) before mcp.run().
    _register_tools()
    mcp.run()


if __name__ == "__main__":
    main()
