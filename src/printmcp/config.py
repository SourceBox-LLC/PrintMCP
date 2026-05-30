"""Runtime configuration for PrintMCP, sourced from environment variables.

A .env file in the working directory (or a parent) is loaded automatically.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple, Optional

from dotenv import load_dotenv

load_dotenv()

# Base URL for the Thingiverse REST API.
THINGIVERSE_API_BASE = "https://api.thingiverse.com"


def get_token() -> str:
    """Return the configured Thingiverse API token (empty string if unset)."""
    return os.environ.get("THINGIVERSE_TOKEN", "").strip()


def get_download_dir() -> Path:
    """Resolve (and create) the directory where downloaded models are stored.

    Honors PRINTMCP_DOWNLOAD_DIR; otherwise defaults to ``~/PrintMCP/downloads``.
    """
    raw = os.environ.get("PRINTMCP_DOWNLOAD_DIR", "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / "PrintMCP" / "downloads"
    base.mkdir(parents=True, exist_ok=True)
    return base


# --------------------------------------------------------------------------- #
# Cura / CuraEngine (Level 2: slicing)
# --------------------------------------------------------------------------- #
_ENGINE_EXE = "CuraEngine.exe" if os.name == "nt" else "CuraEngine"


class CuraPaths(NamedTuple):
    """Filesystem locations CuraEngine needs to slice a model."""

    engine: Path  # CuraEngine executable
    definitions: Path  # printer .def.json files (and the inherited base defs)
    extruders: Path  # extruder-train .def.json files


def _cura_root() -> Optional[Path]:
    """Locate the Ultimaker Cura install directory.

    Honors PRINTMCP_CURA_DIR, then derives it from PRINTMCP_CURAENGINE, then
    falls back to discovering the newest "UltiMaker Cura X.Y.Z" under the
    standard Windows install roots.
    """
    raw = os.environ.get("PRINTMCP_CURA_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_dir() else None

    engine_env = os.environ.get("PRINTMCP_CURAENGINE", "").strip()
    if engine_env:
        return Path(engine_env).expanduser().parent

    candidates: list[Path] = []
    for root in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if root.is_dir():
            candidates += root.glob("UltiMaker Cura *")
            candidates += root.glob("Ultimaker Cura *")
    # Sort by name so the highest version (e.g. "... 5.11.0") sorts last.
    candidates = sorted({c for c in candidates if c.is_dir()}, key=lambda p: p.name)
    return candidates[-1] if candidates else None


def get_cura_paths() -> CuraPaths:
    """Resolve the CuraEngine executable and its definition/extruder folders.

    Raises:
        FileNotFoundError: if Cura cannot be located. The message explains how
            to point PrintMCP at an install via PRINTMCP_CURA_DIR.
    """
    engine_env = os.environ.get("PRINTMCP_CURAENGINE", "").strip()
    engine = Path(engine_env).expanduser() if engine_env else None

    root = _cura_root()
    if engine is None:
        if root is None:
            raise FileNotFoundError(
                "Could not find Ultimaker Cura. Install it, or set PRINTMCP_CURA_DIR "
                "to the install folder (e.g. 'C:\\Program Files\\UltiMaker Cura 5.11.0') "
                "or PRINTMCP_CURAENGINE to the CuraEngine executable."
            )
        engine = root / _ENGINE_EXE

    if not engine.is_file():
        raise FileNotFoundError(f"CuraEngine executable not found at: {engine}")

    base = (root or engine.parent) / "share" / "cura" / "resources"
    return CuraPaths(
        engine=engine,
        definitions=base / "definitions",
        extruders=base / "extruders",
    )


# --------------------------------------------------------------------------- #
# OctoPrint (Level 3: print management)
#
# The base URL and API key are read from the environment so the credential is
# never hard-coded. Level 3 talks to one print host (OctoPrint); a future
# Moonraker/Klipper backend would read its own env and expose moonraker_* tools.
# --------------------------------------------------------------------------- #
def get_octoprint_url() -> str:
    """Return the configured OctoPrint base URL, trailing slash trimmed.

    Empty string if unset (the octoprint tools surface a helpful error then).
    """
    return os.environ.get("OCTOPRINT_URL", "").strip().rstrip("/")


def get_octoprint_api_key() -> str:
    """Return the configured OctoPrint API key (empty string if unset)."""
    return os.environ.get("OCTOPRINT_API_KEY", "").strip()
