"""Smoke tests that don't require a Thingiverse token, Cura, or network."""

import asyncio
import json

import pytest
from pydantic import ValidationError

import printmcp
import printmcp.cura  # noqa: F401 - registers the Level 2 tools
import printmcp.octoprint  # noqa: F401 - registers the Level 3 tools
import printmcp.thingiverse  # noqa: F401 - registers the Level 1 tools
from printmcp import config as printmcp_config
from printmcp import server as printmcp_server
from printmcp.app import mcp
from printmcp.cura import SliceModelInput, _parse_stats, _run_engine
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
from printmcp.thingiverse import _safe_filename


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
    assert _safe_filename("bad:name?.stl") == "bad_name_.stl"
    assert _safe_filename("") == "file"


def test_slice_input_normalizes_printer_id():
    # A definition filename is reduced to its bare id.
    assert (
        SliceModelInput(
            model_path="x.stl", printer="creality_ender3pro.def.json"
        ).printer
        == "creality_ender3pro"
    )


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
    md = _confirm_required(
        "start the print", "begin printing cup.gcode", ResponseFormat.MARKDOWN
    )
    assert "confirm=true" in md
    assert "nothing was sent" in md.lower()

    payload = json.loads(
        _confirm_required(
            "start the print", "begin printing cup.gcode", ResponseFormat.JSON
        )
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
            "children": [
                {"type": "machinecode", "name": "b.gcode", "path": "sub/b.gcode"}
            ],
        },
    ]
    flat = _flatten_files(tree)
    assert {f["path"] for f in flat} == {"a.gcode", "sub/b.gcode"}


def test_fmt_duration():
    assert _fmt_duration(None) is None
    assert _fmt_duration(45) == "45s"
    assert _fmt_duration(90) == "1m 30s"
    assert _fmt_duration(23491) == "6h 31m 31s"


# --------------------------------------------------------------------------- #
# Level 2: Cura config / subprocess hardening
# --------------------------------------------------------------------------- #
def test_cura_version_key_orders_numerically():
    # 5.11.0 must rank above 5.9.0 (string sort would get this backwards).
    key = printmcp_config._version_key
    assert key("UltiMaker Cura 5.11.0") > key("UltiMaker Cura 5.9.0")
    assert key("UltiMaker Cura 5.9.0") > key("UltiMaker Cura 4.13.1")
    assert key("no version here") == (0,)


def test_run_engine_scrubs_secrets_from_subprocess_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OCTOPRINT_API_KEY", "super-secret-key")
    monkeypatch.setenv("THINGIVERSE_TOKEN", "super-secret-token")

    captured = {}

    def fake_run(args, **kwargs):
        captured["env"] = kwargs.get("env", {})

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    monkeypatch.setattr("printmcp.cura.subprocess.run", fake_run)
    _run_engine(["CuraEngine", "slice"], tmp_path)

    env = captured["env"]
    assert "OCTOPRINT_API_KEY" not in env
    assert "THINGIVERSE_TOKEN" not in env
    assert env["CURA_ENGINE_SEARCH_PATH"] == str(tmp_path)


# --------------------------------------------------------------------------- #
# CLI (server.py entry point)
# --------------------------------------------------------------------------- #
def test_version_is_a_real_string():
    # Derived from package metadata; never the source-tree fallback in CI/install.
    assert isinstance(printmcp.__version__, str)
    assert printmcp.__version__


def test_cli_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["printmcp", "--version"])
    printmcp_server.main()
    err = capsys.readouterr().err
    assert printmcp.__version__ in err


def test_cli_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["printmcp", "--help"])
    printmcp_server.main()
    err = capsys.readouterr().err
    assert "Usage:" in err and "--check" in err


def test_cli_unknown_flag_exits_2(monkeypatch):
    monkeypatch.setattr("sys.argv", ["printmcp", "--nope"])
    with pytest.raises(SystemExit) as exc:
        printmcp_server.main()
    assert exc.value.code == 2


def test_cli_check_reports_missing_and_exits_1(monkeypatch, capsys, tmp_path):
    # No config of any kind -> all three levels missing -> exit 1.
    monkeypatch.delenv("THINGIVERSE_TOKEN", raising=False)
    monkeypatch.delenv("OCTOPRINT_URL", raising=False)
    monkeypatch.delenv("OCTOPRINT_API_KEY", raising=False)
    # Point Cura discovery at an empty dir so no engine is found (Level 2 missing).
    monkeypatch.setenv("PRINTMCP_CURA_DIR", str(tmp_path))
    monkeypatch.delenv("PRINTMCP_CURAENGINE", raising=False)
    monkeypatch.setattr("sys.argv", ["printmcp", "--check"])
    with pytest.raises(SystemExit) as exc:
        printmcp_server.main()
    assert exc.value.code == 1
    out = capsys.readouterr()
    # Diagnostic goes to stderr (stdout is reserved for the MCP protocol).
    assert out.out == ""
    assert "Level 1" in out.err and "Level 3" in out.err


def test_cli_check_does_not_leak_secrets(monkeypatch, capsys, tmp_path):
    secret_token = "tok-SECRET-should-not-print"
    secret_key = "key-SECRET-should-not-print"
    monkeypatch.setenv("THINGIVERSE_TOKEN", secret_token)
    monkeypatch.setenv("OCTOPRINT_URL", "http://printer.local")
    monkeypatch.setenv("OCTOPRINT_API_KEY", secret_key)
    monkeypatch.setenv(
        "PRINTMCP_CURA_DIR", str(tmp_path)
    )  # no engine -> Level 2 "missing"
    monkeypatch.setattr("sys.argv", ["printmcp", "--check"])
    with pytest.raises(SystemExit):
        printmcp_server.main()
    err = capsys.readouterr().err
    assert secret_token not in err
    assert secret_key not in err
    # It DOES confirm they're set, and may show the (non-secret) URL.
    assert "THINGIVERSE_TOKEN is set" in err
