#!/usr/bin/env python3
"""OrcaSlicer integration for PrintMCP (Level 2: slice models into G-code).

Drives the **OrcaSlicer CLI** headlessly. OrcaSlicer is structured around a
3-tier preset model:

- a **machine** preset   (the printer, e.g. "Creality Ender-3 Pro 0.4 nozzle")
- a **process** preset    (the print/layer-height profile, e.g. "0.20mm Standard @Creality Ender3 Pro 0.4")
- one or more **filament** presets (e.g. "Creality Generic PLA")

Each preset is a JSON file that may ``inherits`` from a base preset; OrcaSlicer
resolves the chain itself. We therefore resolve a preset *by name* to its JSON
file on disk (searching every vendor's machine/process/filament dir plus the
global OrcaFilamentLibrary), copy the chosen files into a per-slice working
directory, apply any scalar overrides into those copies, and hand the copies to
the CLI::

    orca-slicer --load-settings "machine.json;process.json" \
                --load-filaments "filament.json" \
                --slice 0 --outputdir <dir> model.stl

For Flatpak installs the binary runs inside a sandbox that cannot see arbitrary
host paths, so we additionally pass ``--filesystem=`` grants for the work dir,
the output dir, and the model's directory.

Exposes two tools:
- ``orca_slice_model``    - slice a local model file to printer-ready G-code.
- ``orca_list_profiles``  - list available machine/process/filament presets.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .app import mcp
from .config import OrcaBinary, get_orca_paths

# Slicing a large/detailed model can take a while; cap it so a hung engine
# can't block the tool forever.
SLICE_TIMEOUT = 900.0

# Mesh formats OrcaSlicer can load.
SLICEABLE_EXTENSIONS = {".stl", ".obj", ".3mf", ".amf", ".ply", ".step", ".stp"}

# Secrets OrcaSlicer has no need for; scrubbed from the child environment so
# they are not readable via the subprocess environment.
_SECRET_ENV_VARS = ("OCTOPRINT_API_KEY", "THINGIVERSE_TOKEN")

# The three preset tiers and the per-vendor subfolder that holds each.
_TIER_SUBDIR = {"machine": "machine", "process": "process", "filament": "filament"}


# --------------------------------------------------------------------------- #
# Preset discovery / resolution
# --------------------------------------------------------------------------- #
def _load_preset_name(path: Path) -> str | None:
    """Read a preset file's ``name`` field (returns None on any failure)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data.get("name") if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - a malformed preset just isn't indexed
        return None


def _iter_preset_files(profiles_dir: Path, tier: str) -> list[Path]:
    """All JSON files under every vendor's <tier>/ subdir (plus the library)."""
    sub = _TIER_SUBDIR[tier]
    out: list[Path] = []
    try:
        for vendor in sorted(profiles_dir.iterdir()):
            if not vendor.is_dir():
                continue
            d = vendor / sub
            if d.is_dir():
                out.extend(sorted(d.rglob("*.json")))
    except OSError:
        pass
    return out


def list_presets(profiles_dir: Path, tier: str) -> list[dict[str, str]]:
    """List presets of one tier as {"name", "file"} dicts (deduped by name)."""
    seen: dict[str, str] = {}
    for f in _iter_preset_files(profiles_dir, tier):
        name = _load_preset_name(f)
        if name and name not in seen:
            seen[name] = f.name
    return [{"name": n, "file": seen[n]} for n in sorted(seen)]


def resolve_preset(profiles_dir: Path, tier: str, name: str) -> Path:
    """Resolve a preset by its ``name`` to the JSON file that defines it.

    Searches every vendor's <tier>/ dir (and the OrcaFilamentLibrary) for a
    preset whose ``name`` matches. Falls back to a filename match (with or
    without the .json suffix). Raises ToolError listing close matches if the
    preset cannot be found.
    """
    files = _iter_preset_files(profiles_dir, tier)
    by_name: dict[str, Path] = {}
    by_file: dict[str, Path] = {}
    for f in files:
        by_file.setdefault(f.name, f)
        by_file.setdefault(
            f.name[: -len(".json")] if f.name.endswith(".json") else f.name, f
        )
        n = _load_preset_name(f)
        if n and n not in by_name:
            by_name[n] = f

    if name in by_name:
        return by_name[name]
    if name in by_file:
        return by_file[name]

    # Case-insensitive exact match as a fallback.
    low = name.lower()
    for n, p in by_name.items():
        if n.lower() == low:
            return p

    suggestions = _closest(name, list(by_name) + list(by_file))
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise ToolError(
        f"Unknown {tier} preset '{name}'.{hint} "
        f'Call orca_list_profiles(tier="{tier}") to see available presets.'
    )


def _closest(name: str, options: list[str], limit: int = 5) -> list[str]:
    """Cheap subsequence/prefix-based suggestions for an unknown preset name."""
    low = name.lower()
    scored = []
    for opt in options:
        o = opt.lower()
        if low in o or o in low or o.startswith(low[:4]) or low.startswith(o[:4]):
            scored.append(opt)
    return sorted(scored)[:limit]


# --------------------------------------------------------------------------- #
# Preset patching (apply scalar overrides into the copied JSON)
# --------------------------------------------------------------------------- #
def _apply_overrides(copy_path: Path, overrides: dict[str, Any]) -> None:
    """Write ``overrides`` into the preset JSON at ``copy_path`` (in place).

    OrcaSlicer's CLI does not accept bare key=value overrides, and a second
    machine-type file in --load-settings errors out. Editing the *copy* we pass
    is deterministic and sandbox-safe. Values are coerced to strings (Orca
    presets store numbers as strings).
    """
    try:
        data = json.loads(copy_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Could not parse preset JSON at {copy_path.name}: {e}") from e
    for k, v in overrides.items():
        data[k] = (
            v if isinstance(v, bool) else (v if isinstance(v, str) else _to_str(v))
        )
    copy_path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _to_str(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        # Orca stores float-ish settings as strings; keep them clean.
        return f"{v:g}"
    return str(v)


# --------------------------------------------------------------------------- #
# Engine invocation
# --------------------------------------------------------------------------- #
def _run_orca(
    argv: tuple[str, ...], args: list[str], grants: list[Path]
) -> subprocess.CompletedProcess:
    """Run the OrcaSlicer CLI with a scrubbed env and flatpak filesystem grants.

    ``grants`` are host paths the sandbox must be able to read/write (work dir,
    output dir, model directory). They are passed as ``--filesystem=`` only when
    the binary is launched via flatpak.
    """
    env = {k: v for k, v in __import__("os").environ.items()}
    for secret in _SECRET_ENV_VARS:
        env.pop(secret, None)

    cmd = list(argv)
    if len(argv) >= 2 and Path(argv[0]).name == "flatpak" and argv[1] == "run":
        # flatpak options must come BEFORE the app id:  flatpak run [opts] APP ...
        cmd = [argv[0], "run"] + [f"--filesystem={g}" for g in grants] + list(argv[2:])
    cmd += args

    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SLICE_TIMEOUT,
    )


def _parse_orca_stats(gcode_path: Path | None) -> dict[str, Any]:
    """Pull print-time / filament estimates from the produced G-code footer.

    OrcaSlicer writes its estimates into the G-code CONFIG_BLOCK footer, e.g.::

        ; estimated printing time (normal mode) = 24m 21s
        ; filament used [mm] = 1330.76
        ; filament used [cm3] = 3.20

    Filament is returned in metres to match the Cura tool's ``filament_m``.
    Stats are best-effort: any parse failure yields an empty dict.
    """
    stats: dict[str, Any] = {}
    if gcode_path is None or not gcode_path.is_file():
        return stats
    try:
        # Only scan the tail: the CONFIG_BLOCK is at the end of the file.
        with gcode_path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return stats

    m = re.search(r"estimated printing time \(normal mode\)\s*=\s*(.+)", tail)
    if m:
        stats["print_time"] = m.group(1).strip()
    m = re.search(r"filament used \[mm\]\s*=\s*([0-9.]+)", tail)
    if m:
        stats["filament_m"] = round(float(m.group(1)) / 1000.0, 3)  # mm -> m
    return stats


# --------------------------------------------------------------------------- #
# Structured output models (MCP 2025-06-18 spec)
# --------------------------------------------------------------------------- #
class OrcaSliceSettings(BaseModel):
    """The slicing inputs that were applied."""

    model_config = ConfigDict(extra="ignore")

    machine: str
    process: str
    filament: str


class OrcaSliceStats(BaseModel):
    """Print-time / filament estimates reported by OrcaSlicer."""

    model_config = ConfigDict(extra="ignore")

    print_time: str | None = None
    print_time_s: int | None = None
    filament_m: float | None = None


class OrcaSliceResult(BaseModel):
    """Structured result of ``orca_slice_model``."""

    model_config = ConfigDict(extra="ignore")

    slicer: str = "orcaslicer"
    model: str
    gcode_path: str
    gcode_size_bytes: int
    settings: OrcaSliceSettings
    stats: OrcaSliceStats


class OrcaPreset(BaseModel):
    """One available preset in a tier."""

    model_config = ConfigDict(extra="ignore")

    name: str
    file: str


class OrcaProfileList(BaseModel):
    """Structured result of ``orca_list_profiles``."""

    model_config = ConfigDict(extra="ignore")

    tier: str
    count: int
    presets: list[OrcaPreset] = []


def _markdown(text: str, structured: dict[str, Any]) -> CallToolResult:
    """Build a CallToolResult carrying markdown text + matching structuredContent."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured,
    )


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #
class Tier(str, Enum):
    """Which OrcaSlicer preset tier to list."""

    MACHINE = "machine"
    PROCESS = "process"
    FILAMENT = "filament"


class SliceModelInput(BaseModel):
    """Input for ``orca_slice_model``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    model_path: str = Field(
        ...,
        description="Absolute path to a local model file (.stl/.obj/.3mf/.amf/.ply/.step), e.g. one saved by thingiverse_download_model.",
        min_length=1,
    )
    machine: str = Field(
        ...,
        description="OrcaSlicer machine preset NAME, e.g. 'Creality Ender-3 Pro 0.4 nozzle'. See orca_list_profiles(tier='machine').",
        min_length=1,
        max_length=200,
    )
    process: str = Field(
        ...,
        description="OrcaSlicer process (print profile) preset NAME, e.g. '0.20mm Standard @Creality Ender3 Pro 0.4'. See orca_list_profiles(tier='process').",
        min_length=1,
        max_length=200,
    )
    filament: str = Field(
        ...,
        description="OrcaSlicer filament preset NAME, e.g. 'Creality Generic PLA'. See orca_list_profiles(tier='filament').",
        min_length=1,
        max_length=200,
    )
    output_path: str | None = Field(
        default=None,
        description="Where to write the .gcode. Defaults to the model file path with a .gcode extension.",
    )
    overrides: dict[str, Any] | None = Field(
        default=None,
        description='Optional map of OrcaSlicer setting overrides applied to the presets (e.g. {"layer_height": 0.16, "sparse_infill_density": "15%"}). Applied to the preset copies only.',
    )

    @field_validator("model_path")
    @classmethod
    def _model_path_is_sane(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model_path cannot be blank")
        return v


class ListProfilesInput(BaseModel):
    """Input for ``orca_list_profiles``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    tier: Tier = Field(
        default=Tier.MACHINE,
        description="Which preset tier to list: 'machine', 'process', or 'filament'.",
    )
    filter: str | None = Field(
        default=None,
        description="Optional case-insensitive substring filter (e.g. 'Ender' or 'PLA').",
        max_length=100,
    )


# --------------------------------------------------------------------------- #
# Tool: list profiles
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="orca_list_profiles",
    annotations={
        "title": "List OrcaSlicer Presets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def orca_list_profiles(
    tier: Tier = Tier.MACHINE,
    filter: str | None = None,
) -> OrcaProfileList:
    """List available OrcaSlicer presets (machine / process / filament).

    Call this to discover the exact preset *names* to pass to
    ``orca_slice_model``. Machine presets describe printers, process presets
    are print/quality profiles (usually machine-specific), and filament presets
    are materials.

    Args:
        tier: 'machine', 'process', or 'filament' (default 'machine').
        filter: optional case-insensitive substring filter, e.g. "Ender".

    Returns:
        OrcaProfileList with tier, count, and presets=[{name, file}].

    Example:
        orca_list_profiles(tier="machine", filter="Ender")
        -> presets include "Creality Ender-3 Pro 0.4 nozzle".
    """
    params = ListProfilesInput(tier=tier, filter=filter)
    try:
        orca = get_orca_paths()
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e

    presets = list_presets(orca.profiles_dir, params.tier.value)
    if params.filter:
        f = params.filter.lower()
        presets = [p for p in presets if f in p["name"].lower()]

    result = OrcaProfileList(
        tier=params.tier.value,
        count=len(presets),
        presets=[OrcaPreset(**p) for p in presets],
    )
    return result


# --------------------------------------------------------------------------- #
# Tool: slice
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="orca_slice_model",
    annotations={
        "title": "Slice a 3D Model to G-code (OrcaSlicer)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def orca_slice_model(
    model_path: str,
    machine: str,
    process: str,
    filament: str,
    output_path: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> OrcaSliceResult:
    """Slice a local 3D model into printer-ready G-code using OrcaSlicer.

    This is Level 2 (OrcaSlicer) of the pipeline: it takes a downloaded model
    file (e.g. from ``thingiverse_download_model``) and produces a .gcode file
    using OrcaSlicer's 3-tier presets. Requires OrcaSlicer installed (native or
    Flatpak), auto-detected — set PRINTMCP_ORCA_COMMAND / PRINTMCP_ORCA_PROFILES
    to pin it.

    Use ``orca_list_profiles`` first to find exact preset names for each tier.

    Args:
        model_path: path to the .stl/.obj/.3mf/.amf/.ply/.step file.
        machine: machine preset name, e.g. "Creality Ender-3 Pro 0.4 nozzle".
        process: process preset name, e.g. "0.20mm Standard @Creality Ender3 Pro 0.4".
        filament: filament preset name, e.g. "Creality Generic PLA".
        output_path: .gcode destination (default: alongside model).
        overrides: optional OrcaSlicer setting overrides, e.g. {"layer_height": 0.12}.

    Returns:
        OrcaSliceResult with slicer, model, gcode_path, gcode_size_bytes,
        settings {machine, process, filament}, and stats (print time/filament).

    Example:
        orca_slice_model(
            model_path="/home/me/Downloads/benchy.stl",
            machine="Creality Ender-3 Pro 0.4 nozzle",
            process="0.20mm Standard @Creality Ender3 Pro 0.4",
            filament="Creality Generic PLA",
        )
    """
    params = SliceModelInput(
        model_path=model_path,
        machine=machine,
        process=process,
        filament=filament,
        output_path=output_path,
        overrides=overrides,
    )

    model = Path(params.model_path).expanduser()
    if not model.is_file():
        raise ToolError(f"Model file not found: {params.model_path}")
    if model.suffix.lower() not in SLICEABLE_EXTENSIONS:
        raise ToolError(
            f"Unsupported model type '{model.suffix}'. OrcaSlicer can slice: "
            + ", ".join(sorted(SLICEABLE_EXTENSIONS))
        )

    try:
        orca: OrcaBinary = get_orca_paths()
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e

    # Resolve each preset by NAME to its file (ToolError with suggestions if not).
    machine_file = resolve_preset(orca.profiles_dir, "machine", params.machine)
    process_file = resolve_preset(orca.profiles_dir, "process", params.process)
    filament_file = resolve_preset(orca.profiles_dir, "filament", params.filament)

    output = (
        Path(params.output_path).expanduser()
        if params.output_path
        else model.with_suffix(".gcode")
    )

    workdir = Path(tempfile.mkdtemp(prefix="printmcp-orca-"))
    try:
        # Stage preset copies into a user/default/<tier>/ tree. Some Orca builds
        # resolve inherits relative to a datadir; passing absolute file paths to
        # --load-settings works either way, and editing our own copies is safe.
        stage = workdir / "user" / "default"
        for tier in _TIER_SUBDIR:
            (stage / tier).mkdir(parents=True, exist_ok=True)
        m_copy = stage / "machine" / machine_file.name
        p_copy = stage / "process" / process_file.name
        f_copy = stage / "filament" / filament_file.name
        shutil.copyfile(machine_file, m_copy)
        shutil.copyfile(process_file, p_copy)
        shutil.copyfile(filament_file, f_copy)

        # Apply scalar overrides into the machine copy (deterministic, sandbox-safe).
        if params.overrides:
            _apply_overrides(m_copy, params.overrides)

        out_dir = workdir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        args = [
            "--load-settings",
            f"{m_copy};{p_copy}",
            "--load-filaments",
            str(f_copy),
            "--slice",
            "0",
            "--outputdir",
            str(out_dir),
            str(model),
        ]
        grants = [workdir, out_dir, model.parent]

        try:
            proc = _run_orca(orca.argv, args, grants)
        except subprocess.TimeoutExpired as e:
            raise ToolError(
                f"OrcaSlicer timed out after {int(SLICE_TIMEOUT)}s while slicing {model.name}."
            ) from e
        except FileNotFoundError as e:
            raise ToolError(f"Could not launch OrcaSlicer: {e}") from e

        # Orca reports failures in result.json and/or non-zero exit text.
        result_err = ""
        rj = out_dir / "result.json"
        if rj.is_file():
            try:
                result_err = (
                    json.loads(rj.read_text(encoding="utf-8", errors="replace")).get(
                        "error_string", ""
                    )
                    or ""
                )
            except Exception:  # noqa: BLE001
                pass

        gcode_files = sorted(out_dir.glob("*.gcode"))
        if proc.returncode != 0 or not gcode_files:
            detail = result_err or (proc.stderr.strip() or proc.stdout.strip())[:400]
            raise ToolError(
                f"OrcaSlicer failed to slice {model.name} "
                f"(exit {proc.returncode}). {detail}"
            )

        # Move the produced gcode to the requested output path.
        produced = max(gcode_files, key=lambda p: p.stat().st_size)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, output)

        stats = _parse_orca_stats(output)

        result = OrcaSliceResult(
            model=str(model),
            gcode_path=str(output),
            gcode_size_bytes=output.stat().st_size,
            settings=OrcaSliceSettings(
                machine=params.machine,
                process=params.process,
                filament=params.filament,
            ),
            stats=OrcaSliceStats(
                print_time=stats.get("print_time"),
                print_time_s=stats.get("print_time_s"),
                filament_m=stats.get("filament_m"),
            ),
        )
        return result
    finally:
        # Clean up the staging area; the produced .gcode has been copied out.
        shutil.rmtree(workdir, ignore_errors=True)
