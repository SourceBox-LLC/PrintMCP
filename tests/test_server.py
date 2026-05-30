"""Smoke tests that don't require a Thingiverse token, Cura, or network."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from printmcp.app import mcp
import printmcp.thingiverse  # noqa: F401 - registers the Level 1 tools
import printmcp.cura  # noqa: F401 - registers the Level 2 tools
import printmcp.octoprint  # noqa: F401 - registers the Level 3 tools
from printmcp.thingiverse import _safe_filename
from printmcp.cura import SliceModelInput, _parse_stats
from printmcp.octoprint import (
    HomeInput,
    MoveInput,
    ResponseFormat,
    SetTemperatureInput,
    _confirm_required,
    _flatten_files,
    _fmt_duration,
    _handle_error,
    _MissingConfigError,
)


def test_tools_registered():
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "thingiverse_search_models",
        "thingiverse_get_model",
        "thingiverse_download_model",
        "cura_slice_model",
        "octoprint_get_status",
        "octoprint_list_files",
        "octoprint_get_job",
        "octoprint_connect",
        "octoprint_upload_file",
        "octoprint_start_print",
        "octoprint_control_job",
        "octoprint_set_temperature",
        "octoprint_home",
        "octoprint_move",
    } <= names


def test_safe_filename_strips_traversal():
    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename("a/b/c.stl") == "c.stl"
    assert _safe_filename('bad:name?.stl') == "bad_name_.stl"
    assert _safe_filename("") == "file"


def test_slice_input_normalizes_printer_id():
    # A definition filename is reduced to its bare id.
    assert SliceModelInput(model_path="x.stl", printer="creality_ender3pro.def.json").printer == "creality_ender3pro"


def test_slice_input_rejects_printer_path():
    # Guards against -j pointing outside the definitions folder.
    with pytest.raises(ValidationError):
        SliceModelInput(model_path="x.stl", printer="../../evil")


def test_slice_input_rejects_out_of_range():
    with pytest.raises(ValidationError):
        SliceModelInput(model_path="x.stl", layer_height=5.0)


def test_parse_stats_reads_engine_log():
    sample = (
        ";Filament used: 25.476m\n"
        "[2026-05-28 16:06:58.249] [info] Print time (s): 23491\n"
        "[2026-05-28 16:06:58.249] [info] Print time (hr|min|s): 6h 31m 31s\n"
        "[2026-05-28 16:06:58.249] [info] Filament (mm^3): 61277\n"
    )
    stats = _parse_stats(sample)
    assert stats["filament_m"] == 25.476
    assert stats["print_time_s"] == 23491
    assert stats["print_time"] == "6h 31m 31s"
    assert stats["filament_mm3"] == 61277


# --------------------------------------------------------------------------- #
# Level 3: OctoPrint (offline — input validation + pure helpers, no network)
# --------------------------------------------------------------------------- #
def test_move_requires_at_least_one_axis():
    with pytest.raises(ValidationError):
        MoveInput()  # no x/y/z given


def test_move_accepts_single_axis():
    assert MoveInput(z=5.0).z == 5.0


def test_set_temperature_caps_bed():
    # Bed has a lower ceiling than the tool; 200°C bed is a likely typo.
    with pytest.raises(ValidationError):
        SetTemperatureInput(heater="bed", target=200)
    # Same value is fine for the tool.
    assert SetTemperatureInput(heater="tool", target=200).target == 200


def test_home_normalizes_and_rejects_bad_axes():
    assert HomeInput(axes=["X", "x", "y"]).axes == ["x", "y"]
    with pytest.raises(ValidationError):
        HomeInput(axes=["w"])


def test_confirm_gate_sends_nothing_and_prompts():
    md = _confirm_required("start the print", "begin printing cup.gcode", ResponseFormat.MARKDOWN)
    assert "confirm=true" in md
    assert "nothing was sent" in md.lower()

    payload = json.loads(
        _confirm_required("start the print", "begin printing cup.gcode", ResponseFormat.JSON)
    )
    assert payload["dry_run"] is True
    assert payload["action"] == "start the print"


def test_handle_error_reports_missing_config():
    msg = _handle_error(_MissingConfigError("OCTOPRINT_URL not set"))
    assert msg.startswith("Error:")
    assert "OCTOPRINT_URL" in msg


def test_flatten_files_walks_folders():
    tree = [
        {"type": "machinecode", "name": "a.gcode", "path": "a.gcode"},
        {
            "type": "folder",
            "name": "sub",
            "children": [{"type": "machinecode", "name": "b.gcode", "path": "sub/b.gcode"}],
        },
    ]
    flat = _flatten_files(tree)
    assert {f["path"] for f in flat} == {"a.gcode", "sub/b.gcode"}


def test_fmt_duration():
    assert _fmt_duration(None) is None
    assert _fmt_duration(45) == "45s"
    assert _fmt_duration(90) == "1m 30s"
    assert _fmt_duration(23491) == "6h 31m 31s"
