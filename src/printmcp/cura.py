#!/usr/bin/env python3
"""Cura integration for PrintMCP (Level 2: slice models into G-code).

Drives the headless **CuraEngine** bundled with Ultimaker Cura (not the GUI).

Exposes one tool:
- ``cura_slice_model`` - slice a local model file to printer-ready G-code.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import anyio
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .app import mcp
from .config import get_cura_paths

# Slicing a large/detailed model can take a while; cap it so a hung engine
# can't block the tool forever.
SLICE_TIMEOUT = 900.0

# Mesh formats CuraEngine can load.
SLICEABLE_EXTENSIONS = {".stl", ".obj", ".3mf", ".amf", ".ply"}

# Cura 5.11 ships these per-extruder-limited settings without a value that
# CuraEngine's CLI can resolve, so it aborts with "no value given" unless we
# pass them explicitly. Their Cura defaults are 0 (no top/bottom surface skin).
_CLI_DEFAULT_FIXUPS = {"roofing_layer_count": "0", "flooring_layer_count": "0"}

_PATH_SEP = re.compile(r"[\\/]")


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


class AdhesionType(str, Enum):
    """Build-plate adhesion strategies supported by CuraEngine."""

    SKIRT = "skirt"
    BRIM = "brim"
    RAFT = "raft"
    NONE = "none"


# --------------------------------------------------------------------------- #
# Engine invocation
# --------------------------------------------------------------------------- #
def _run_engine(args: list[str], extruders_dir: Path) -> subprocess.CompletedProcess:
    """Run CuraEngine with the search path set so extruder defs resolve.

    CuraEngine resolves a definition's ``inherits`` chain from the directory of
    the ``-j`` file, but finds the extruder train via CURA_ENGINE_SEARCH_PATH.
    Only a single path is honored there, so we point it at the extruders folder.
    """
    env = os.environ.copy()
    # CuraEngine has no need for our API credentials; don't expose them to the
    # child process (where they'd be readable via its environment).
    for secret in ("OCTOPRINT_API_KEY", "THINGIVERSE_TOKEN"):
        env.pop(secret, None)
    env["CURA_ENGINE_SEARCH_PATH"] = str(extruders_dir)
    return subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SLICE_TIMEOUT,
    )


def _parse_stats(output: str) -> dict[str, Any]:
    """Pull print-time / filament estimates from CuraEngine's log output.

    The G-code header's own ``;TIME``/``;Filament`` fields are placeholders when
    the engine runs standalone (Cura's GUI normally back-fills them), so we read
    the values the engine logs instead.
    """
    stats: dict[str, Any] = {}
    m = re.search(r";Filament used:\s*([\d.]+)\s*m", output)
    if m:
        stats["filament_m"] = float(m.group(1))
    m = re.search(r"Print time \(s\):\s*(\d+)", output)
    if m:
        stats["print_time_s"] = int(m.group(1))
    m = re.search(r"Print time \(hr\|min\|s\):\s*(.+)", output)
    if m:
        stats["print_time"] = m.group(1).strip()
    m = re.search(r"Filament \(mm\^3\):\s*(\d+)", output)
    if m:
        stats["filament_mm3"] = int(m.group(1))
    return stats


# --------------------------------------------------------------------------- #
# Tool: slice
# --------------------------------------------------------------------------- #
class SliceModelInput(BaseModel):
    """Input for ``cura_slice_model``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    model_path: str = Field(
        ...,
        description="Absolute path to a local model file (.stl/.obj/.3mf/.amf/.ply), e.g. one saved by thingiverse_download_model.",
        min_length=1,
    )
    printer: str = Field(
        default="creality_ender3pro",
        description="Cura printer definition id (the .def.json filename without extension), e.g. 'creality_ender3pro' (default: Creality Ender-3 Pro).",
        min_length=1,
        max_length=128,
    )
    layer_height: float = Field(
        default=0.2,
        description="Layer height in mm (0.05-0.6). Lower is finer but slower.",
        ge=0.05,
        le=0.6,
    )
    infill_density: int = Field(
        default=20, description="Infill density as a percentage (0-100).", ge=0, le=100
    )
    supports: bool = Field(
        default=False,
        description="Generate support structures for overhangs.",
    )
    adhesion_type: AdhesionType = Field(
        default=AdhesionType.SKIRT,
        description="Build-plate adhesion: 'skirt', 'brim', 'raft', or 'none'.",
    )
    material_print_temperature: int = Field(
        default=200,
        description="Nozzle temperature in degrees C (150-300). ~200 for PLA.",
        ge=150,
        le=300,
    )
    material_bed_temperature: int = Field(
        default=60,
        description="Bed temperature in degrees C (0-120). ~60 for PLA.",
        ge=0,
        le=120,
    )
    output_path: Optional[str] = Field(
        default=None,
        description="Where to write the .gcode. Defaults to the model file path with a .gcode extension.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )

    @field_validator("printer")
    @classmethod
    def _printer_is_an_id(cls, v: str) -> str:
        v = v.strip()
        if v.lower().endswith(".def.json"):
            v = v[: -len(".def.json")]
        if _PATH_SEP.search(v):
            raise ValueError("printer must be a definition id, not a path")
        return v


@mcp.tool(
    name="cura_slice_model",
    annotations={
        "title": "Slice a 3D Model to G-code (Cura)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cura_slice_model(params: SliceModelInput) -> str:
    """Slice a local 3D model into printer-ready G-code using CuraEngine.

    This is Level 2 of the pipeline: it takes a downloaded model file (e.g. from
    ``thingiverse_download_model``) and produces a .gcode file for a specific
    printer. Requires Ultimaker Cura to be installed (auto-detected, or set
    PRINTMCP_CURA_DIR / PRINTMCP_CURAENGINE).

    Args:
        params (SliceModelInput): Validated input containing:
            - model_path (str): path to the .stl/.obj/.3mf/.amf/.ply file.
            - printer (str): Cura definition id (default 'creality_ender3pro').
            - layer_height (float): mm, 0.05-0.6 (default 0.2).
            - infill_density (int): percent, 0-100 (default 20).
            - supports (bool): generate supports (default false).
            - adhesion_type (str): skirt|brim|raft|none (default skirt).
            - material_print_temperature (int): nozzle degrees C (default 200).
            - material_bed_temperature (int): bed degrees C (default 60).
            - output_path (str|None): .gcode destination (default: alongside model).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Markdown summary, or JSON of the form:
        {
          "model": str, "printer": str, "gcode_path": str,
          "gcode_size_bytes": int,
          "settings": {"layer_height": float, "infill_density": int,
                       "supports": bool, "adhesion_type": str,
                       "material_print_temperature": int,
                       "material_bed_temperature": int},
          "stats": {"print_time": str|null, "print_time_s": int|null,
                    "filament_m": float|null, "filament_mm3": int|null}
        }
        On failure: "Error: <reason>".

    Examples:
        - "Slice that cup for my Ender 3" -> model_path=<downloaded .stl>.
        - "Slice it at 0.12mm with 40% infill and supports" -> set those fields.
    """
    try:
        model = Path(params.model_path).expanduser()
        if not model.is_file():
            return f"Error: model file not found: {params.model_path}"
        if model.suffix.lower() not in SLICEABLE_EXTENSIONS:
            return (
                f"Error: '{model.suffix or 'no extension'}' is not a sliceable model. "
                f"Supported: {', '.join(sorted(SLICEABLE_EXTENSIONS))}."
            )

        try:
            paths = get_cura_paths()
        except FileNotFoundError as e:
            return f"Error: {e}"

        printer_def = paths.definitions / f"{params.printer}.def.json"
        if not printer_def.is_file():
            return (
                f"Error: printer definition '{params.printer}' not found in "
                f"{paths.definitions}."
            )

        out = (
            Path(params.output_path).expanduser()
            if params.output_path
            else model.with_suffix(".gcode")
        )
        out.parent.mkdir(parents=True, exist_ok=True)

        # Global settings MUST precede -l <model>; placed after, CuraEngine
        # treats them as per-mesh and silently ignores global-only ones like
        # layer_height and adhesion_type.
        settings: dict[str, Any] = {
            "layer_height": params.layer_height,
            "infill_sparse_density": params.infill_density,
            "support_enable": "true" if params.supports else "false",
            "adhesion_type": params.adhesion_type.value,
            "material_print_temperature": params.material_print_temperature,
            "material_bed_temperature": params.material_bed_temperature,
            **_CLI_DEFAULT_FIXUPS,
        }
        args = [str(paths.engine), "slice", "-j", str(printer_def)]
        for key, value in settings.items():
            args += ["-s", f"{key}={value}"]
        args += ["-o", str(out), "-l", str(model)]

        try:
            proc = await anyio.to_thread.run_sync(_run_engine, args, paths.extruders)
        except subprocess.TimeoutExpired:
            return f"Error: CuraEngine timed out after {int(SLICE_TIMEOUT)}s slicing {model.name}."

        output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        produced = out.is_file() and out.stat().st_size > 0

        if proc.returncode != 0 or not produced:
            missing = re.findall(r"no value given:\s*(\S+)", output)
            if missing:
                detail = "CuraEngine could not resolve setting(s): " + ", ".join(
                    sorted(set(missing))
                )
            else:
                errs = re.findall(r"\[error\]\s*(.+)", output)
                detail = errs[-1].strip() if errs else "no G-code was produced"
            return f"Error: slicing failed (CuraEngine exit {proc.returncode}): {detail}"

        stats = _parse_stats(output)
        settings_out = {
            "layer_height": params.layer_height,
            "infill_density": params.infill_density,
            "supports": params.supports,
            "adhesion_type": params.adhesion_type.value,
            "material_print_temperature": params.material_print_temperature,
            "material_bed_temperature": params.material_bed_temperature,
        }
        result = {
            "model": model.name,
            "printer": params.printer,
            "gcode_path": str(out),
            "gcode_size_bytes": out.stat().st_size,
            "settings": settings_out,
            "stats": stats,
        }

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(result, indent=2)

        lines = [
            f"# Sliced {model.name}",
            "",
            f"- Printer: {params.printer}",
            f"- G-code: {out}",
            f"- Size: {out.stat().st_size} bytes",
        ]
        if stats.get("print_time"):
            lines.append(f"- Estimated print time: {stats['print_time']}")
        if stats.get("filament_m") is not None:
            vol = (
                f" ({stats['filament_mm3']} mm3)" if stats.get("filament_mm3") else ""
            )
            lines.append(f"- Filament: {stats['filament_m']} m{vol}")
        lines.append(
            f"- Settings: {params.layer_height}mm layers, {params.infill_density}% infill, "
            f"adhesion {params.adhesion_type.value}, supports "
            f"{'on' if params.supports else 'off'}"
        )
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 - surfaced as an actionable string
        return f"Error: Unexpected {type(e).__name__}: {e}"
