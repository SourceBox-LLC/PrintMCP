"""Runtime configuration for PrintMCP, sourced from environment variables.

A .env file in the working directory (or a parent) is loaded automatically.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv

load_dotenv()

# Base URL for the Thingiverse REST API.
THINGIVERSE_API_BASE = "https://api.thingiverse.com"


def get_token() -> str:
    """Return the configured Thingiverse API token (empty string if unset)."""
    return os.environ.get("THINGIVERSE_TOKEN", "").strip()


def _os_downloads_dir() -> Path | None:
    """Return the OS-standard Downloads directory, or None if it can't be found.

    Windows: %USERPROFILE%\\Downloads (USERPROFILE env, or via known-folders
    registry as a fallback). macOS / Linux: ~/Downloads (with XDG_DOWNLOAD_DIR
    honored on Linux when set). Falls back to None if nothing usable resolves.
    """
    if _IS_WINDOWS:
        profile = os.environ.get("USERPROFILE", "").strip()
        if profile:
            p = Path(profile) / "Downloads"
            if p.is_dir():
                return p
        # Fallback via the registry (no extra deps, stdlib only).
        try:
            import ctypes
            from ctypes import wintypes

            csidl_profile = 0x0028
            csidl_download = 0x0008 | 0x4000  # FLAG_CREATE
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH * 2)
            if (
                ctypes.windll.shell32.SHGetFolderPathW(0, csidl_download, 0, 0, buf)
                == 0
            ):
                p = Path(buf.value)
                if p.is_dir():
                    return p
            if ctypes.windll.shell32.SHGetFolderPathW(0, csidl_profile, 0, 0, buf) == 0:
                p = Path(buf.value) / "Downloads"
                if p.is_dir():
                    return p
        except Exception:
            pass
        return None

    # macOS / Linux / other Unix.
    xdg = os.environ.get("XDG_DOWNLOAD_DIR", "").strip()
    if xdg:
        p = Path(xdg).expanduser()
        if p.is_dir():
            return p
    home = Path.home()
    p = home / "Downloads"
    if p.is_dir():
        return p
    return None


def get_download_dir() -> Path:
    """Resolve (and create) the directory where downloaded models are stored.

    Honors PRINTMCP_DOWNLOAD_DIR; otherwise prefers the OS-standard Downloads
    folder (Windows: ``%USERPROFILE%\\Downloads``, macOS/Linux: ``~/Downloads``),
    falling back to ``~/PrintMCP/downloads`` if the Downloads folder can't be
    located.
    """
    raw = os.environ.get("PRINTMCP_DOWNLOAD_DIR", "").strip()
    if raw:
        base = Path(raw).expanduser()
    else:
        base = _os_downloads_dir() or (Path.home() / "PrintMCP" / "downloads")
    base.mkdir(parents=True, exist_ok=True)
    return base


# --------------------------------------------------------------------------- #
# Cura / CuraEngine (Level 2: slicing)
#
# CuraEngine and its resource files ship in different places per OS:
#   Windows  <root>\CuraEngine.exe        <root>\share\cura\resources\
#   macOS    <app>/Contents/MacOS/CuraEngine
#                                          <app>/Contents/Resources/share/cura/resources/
#   Linux    /usr/bin/CuraEngine          /usr/share/cura/resources/   (or AppImage-relative)
# So we discover the engine and the resources directory *independently*, try a
# list of platform candidates for each, and validate resources by checking that
# `definitions/` exists. Env vars override every step:
#   PRINTMCP_CURAENGINE       -> the CuraEngine executable
#   PRINTMCP_CURA_RESOURCES   -> the .../resources directory (with definitions/)
#   PRINTMCP_CURA_DIR         -> the install root / app bundle to search under
# --------------------------------------------------------------------------- #
_IS_WINDOWS = os.name == "nt"
_IS_MACOS = sys.platform == "darwin"
_ENGINE_EXE = "CuraEngine.exe" if _IS_WINDOWS else "CuraEngine"


class CuraPaths(NamedTuple):
    """Filesystem locations CuraEngine needs to slice a model."""

    engine: Path  # CuraEngine executable
    definitions: Path  # printer .def.json files (and the inherited base defs)
    extruders: Path  # extruder-train .def.json files


def _version_key(name: str) -> tuple[int, ...]:
    """Extract a comparable version tuple from a 'UltiMaker Cura X.Y.Z' folder."""
    m = re.search(r"(\d+(?:\.\d+)*)\s*$", name)
    if not m:
        return (0,)
    return tuple(int(part) for part in m.group(1).split("."))


def _install_roots() -> list[Path]:
    """Candidate Cura install roots / app bundles for the current OS, newest first.

    Honors PRINTMCP_CURA_DIR first; then derives from PRINTMCP_CURAENGINE; then
    globs the platform's standard install locations.
    """
    raw = os.environ.get("PRINTMCP_CURA_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return [p] if p.is_dir() else []

    engine_env = os.environ.get("PRINTMCP_CURAENGINE", "").strip()
    if engine_env:
        return [Path(engine_env).expanduser().parent]

    globbed: list[Path] = []
    if _IS_WINDOWS:
        for root in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
            if root.is_dir():
                globbed += root.glob("UltiMaker Cura *")
                globbed += root.glob("Ultimaker Cura *")
    elif _IS_MACOS:
        for root in (Path("/Applications"), Path.home() / "Applications"):
            if root.is_dir():
                globbed += root.glob("UltiMaker Cura*.app")
                globbed += root.glob("Ultimaker Cura*.app")
                globbed += root.glob("Cura*.app")
    else:  # Linux / other Unix
        for base in (
            Path("/usr"),
            Path("/usr/local"),
            Path("/opt"),
            Path("/opt/cura"),
            Path.home() / ".local",
        ):
            if (base / "share" / "cura").is_dir():
                globbed.append(base)
        # AppImages extracted with --appimage-extract land in a squashfs-root.
        for spot in (Path.cwd(), Path.home(), Path.home() / "Applications"):
            if spot.is_dir():
                globbed += spot.glob("squashfs-root")
                globbed += spot.glob("*ura*/squashfs-root")

    # De-dupe, keep dirs, newest version last -> reverse for newest first.
    uniq = sorted(
        {c for c in globbed if c.is_dir()}, key=lambda p: _version_key(p.name)
    )
    return list(reversed(uniq))


def _engine_candidates(roots: list[Path]) -> list[Path]:
    """Possible CuraEngine executable locations, in priority order."""
    out: list[Path] = []

    env = os.environ.get("PRINTMCP_CURAENGINE", "").strip()
    if env:
        out.append(Path(env).expanduser())

    for root in roots:
        out.append(root / _ENGINE_EXE)  # Windows; some Linux
        out.append(root / "Contents" / "MacOS" / _ENGINE_EXE)  # macOS .app
        out.append(root / "bin" / _ENGINE_EXE)  # Linux prefix
        out.append(root / "usr" / "bin" / _ENGINE_EXE)  # extracted AppImage

    # Last resort: a CuraEngine already on PATH.
    found = shutil.which(_ENGINE_EXE)
    if found:
        out.append(Path(found))

    return out


def _resources_candidates(engine: Path | None, roots: list[Path]) -> list[Path]:
    """Possible ``.../share/cura/resources`` directories, in priority order."""
    out: list[Path] = []

    env = os.environ.get("PRINTMCP_CURA_RESOURCES", "").strip()
    if env:
        out.append(Path(env).expanduser())

    rel = Path("share") / "cura" / "resources"
    # Relative to the engine (covers the per-OS engine/resources offsets).
    if engine is not None:
        ep = engine.parent
        out += [
            ep / rel,  # Windows: <root>/share/cura/resources
            ep.parent / rel,  # Linux: <root>/bin -> <root>/share
            ep.parent / "Resources" / rel,  # macOS: MacOS -> Resources
            ep.parent / "share" / "cura" / "resources",
        ]

    # Relative to each install root / app bundle.
    for root in roots:
        out += [
            root / rel,
            root / "Contents" / "Resources" / rel,  # macOS bundle
            root / "usr" / rel,  # extracted AppImage
        ]

    # Absolute system locations (Linux packages).
    out += [
        Path("/usr") / rel,
        Path("/usr/local") / rel,
        Path("/opt/cura") / rel,
    ]
    return out


def _first_engine(candidates: list[Path]) -> Path | None:
    for c in candidates:
        if c.is_file():
            return c
    return None


def _first_resources(candidates: list[Path]) -> Path | None:
    # A valid resources dir must contain the definitions folder.
    for c in candidates:
        if (c / "definitions").is_dir():
            return c
    return None


def get_cura_paths() -> CuraPaths:
    """Resolve the CuraEngine executable and its definition/extruder folders.

    Works across Windows, macOS, and Linux by trying a list of per-OS candidate
    locations and validating the resources directory by the presence of
    ``definitions/``. Environment overrides (PRINTMCP_CURAENGINE,
    PRINTMCP_CURA_RESOURCES, PRINTMCP_CURA_DIR) take precedence.

    Raises:
        FileNotFoundError: if the engine or its resources can't be located, with
            guidance on the env vars that pin them.
    """
    roots = _install_roots()

    engine = _first_engine(_engine_candidates(roots))
    if engine is None:
        raise FileNotFoundError(
            "Could not find the CuraEngine executable. Install Ultimaker Cura, or set "
            "PRINTMCP_CURAENGINE to the CuraEngine binary "
            "(Windows: '...\\CuraEngine.exe'; macOS: "
            "'/Applications/UltiMaker Cura.app/Contents/MacOS/CuraEngine'; "
            "Linux: e.g. '/usr/bin/CuraEngine'), or PRINTMCP_CURA_DIR to the install folder."
        )

    resources = _first_resources(_resources_candidates(engine, roots))
    if resources is None:
        raise FileNotFoundError(
            f"Found CuraEngine at {engine}, but could not locate its resource "
            "definitions. Set PRINTMCP_CURA_RESOURCES to Cura's "
            "'share/cura/resources' directory (the one containing 'definitions')."
        )

    return CuraPaths(
        engine=engine,
        definitions=resources / "definitions",
        extruders=resources / "extruders",
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


# --------------------------------------------------------------------------- #
# OrcaSlicer (Level 2 alternative: slicing)
#
# OrcaSlicer uses a 3-tier preset model: a MACHINE preset (the printer), a
# PROCESS preset (print-quality/layer-height profile), and one or more
# FILAMENT presets. Each is a JSON file; presets resolve into the slicer via
# --load-settings "machine;process" and --load-filaments "filament". CLI
# overrides (--load-settings values from the command line) win over the file
# presets, which win over any 3MF.
#
# Discovery has two halves, each overridable via env:
#   PRINTMCP_ORCA_COMMAND     -> full launch command, e.g. "orca-slicer" or
#                                "flatpak run com.orcaslicer.OrcaSlicer"
#                                (split with shlex; overrides detection)
#   PRINTMCP_ORCA_PROFILES    -> the bundled .../profiles dir that holds the
#                                per-vendor machine/process/filament presets
#
# The binary is found on PATH (native install: orca-slicer / OrcaSlicer) or via
# Flatpak (com.orcaslicer.OrcaSlicer). Bundled presets are host-readable even
# for a Flatpak install under .../flatpak/app/.../files/share/OrcaSlicer/profiles.
# User-saved presets live under the app's config dir (user/*/...). We search the
# bundled dir for the 3 preset tiers; that is enough to slice.
# --------------------------------------------------------------------------- #
ORCA_FLATPAK_ID = "com.orcaslicer.OrcaSlicer"
_ORCA_NATIVE_NAMES = ("orca-slicer", "OrcaSlicer", "orcaslicer")


class OrcaBinary(NamedTuple):
    """How to launch OrcaSlicer's CLI and where its presets live."""

    argv: tuple[str, ...]  # launch prefix, e.g. ("flatpak", "run", ORCA_FLATPAK_ID)
    profiles_dir: Path  # bundled <profiles> dir (vendors of machine/process/filament)
    via: str  # "env" | "flatpak" | "path" — how the binary was located (for --check)


def _orca_command_argv() -> tuple[list[str], str] | None:
    """Resolve the OrcaSlicer launch argv prefix. Returns (argv, via) or None.

    Honors PRINTMCP_ORCA_COMMAND first; then a native binary on PATH; then
    Flatpak. Returns None if no launchable OrcaSlicer is found.
    """
    override = os.environ.get("PRINTMCP_ORCA_COMMAND", "").strip()
    if override:
        import shlex

        parts = shlex.split(override)
        if parts:
            return parts, "env"
        return None

    for name in _ORCA_NATIVE_NAMES:
        found = shutil.which(name)
        if found:
            return [found], "path"

    if shutil.which("flatpak"):
        # Flatpak only if the app id is actually installed.
        try:
            out = subprocess.run(
                ["flatpak", "info", ORCA_FLATPAK_ID],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if out.returncode == 0:
                return ["flatpak", "run", ORCA_FLATPAK_ID], "flatpak"
        except Exception:  # noqa: BLE001 - flatpak probing must never crash discovery
            pass
    return None


def _orca_bundled_profiles_candidates() -> list[Path]:
    """Possible host-readable locations of OrcaSlicer's bundled <profiles> dir."""
    rel = Path("share") / "OrcaSlicer" / "profiles"
    out: list[Path] = []

    env = os.environ.get("PRINTMCP_ORCA_PROFILES", "").strip()
    if env:
        out.append(Path(env).expanduser())

    home = Path.home()
    fid = ORCA_FLATPAK_ID
    out += [
        # Flatpak (user- and system-wide installs), host-readable app files.
        home
        / ".local"
        / "share"
        / "flatpak"
        / "app"
        / fid
        / "current"
        / "active"
        / "files"
        / rel,
        Path("/var/lib/flatpak/app") / fid / "current" / "active" / "files" / rel,
        # Native Linux installs / AppImage (extracted squashfs-root variants).
        Path("/usr") / rel,
        Path("/usr/local") / rel,
        Path("/opt") / "OrcaSlicer" / "resources" / "profiles",
        home / ".local" / rel,
        home / "Applications" / "OrcaSlicer" / "resources" / "profiles",
        # Windows install dir (user may run PrintMCP on Windows).
        Path(r"C:\Program Files\OrcaSlicer\resources\profiles"),
        # macOS app bundle.
        Path("/Applications/OrcaSlicer.app/Contents/Resources/profiles"),
    ]
    return out


def _is_orca_profiles_dir(p: Path) -> bool:
    """True if ``p`` looks like OrcaSlicer's bundled profiles dir.

    A valid dir contains at least one vendor subdirectory that itself holds a
    ``machine/`` folder of presets.
    """
    if not p.is_dir():
        return False
    try:
        for vendor in p.iterdir():
            if vendor.is_dir() and (vendor / "machine").is_dir():
                return True
    except OSError:
        return False
    return False


def _first_orca_profiles(candidates: list[Path]) -> Path | None:
    for c in candidates:
        if _is_orca_profiles_dir(c):
            return c
    return None


def get_orca_paths() -> OrcaBinary:
    """Resolve how to launch OrcaSlicer and where its bundled presets are.

    Raises:
        FileNotFoundError: if no OrcaSlicer binary or its bundled profiles dir
            can be located, with guidance on the env vars that pin them
            (PRINTMCP_ORCA_COMMAND / PRINTMCP_ORCA_PROFILES).
    """
    resolved = _orca_command_argv()
    if resolved is None:
        raise FileNotFoundError(
            "Could not find the OrcaSlicer CLI. Install OrcaSlicer (native or "
            "Flatpak, com.orcaslicer.OrcaSlicer), or set PRINTMCP_ORCA_COMMAND to "
            "the launch command (e.g. 'orca-slicer' or "
            "'flatpak run com.orcaslicer.OrcaSlicer')."
        )
    argv, via = resolved

    profiles = _first_orca_profiles(_orca_bundled_profiles_candidates())
    if profiles is None:
        raise FileNotFoundError(
            "Found the OrcaSlicer CLI, but could not locate its bundled 'profiles' "
            "presets directory (machine/process/filament). Set PRINTMCP_ORCA_PROFILES "
            "to that directory (it contains one vendor folder per printer brand, each "
            "with machine/, process/ and filament/ subfolders)."
        )

    return OrcaBinary(argv=tuple(argv), profiles_dir=profiles, via=via)
