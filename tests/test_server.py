"""Smoke tests that don't require a Thingiverse token, Cura, or network."""

import asyncio

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
    SetTemperatureInput,
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


def _clear_cura_env(monkeypatch):
    for var in (
        "PRINTMCP_CURA_DIR",
        "PRINTMCP_CURAENGINE",
        "PRINTMCP_CURA_RESOURCES",
    ):
        monkeypatch.delenv(var, raising=False)


def _make_resources(base):
    """Create a minimal share/cura/resources tree under ``base`` and return it."""
    res = base / "share" / "cura" / "resources"
    (res / "definitions").mkdir(parents=True)
    (res / "extruders").mkdir(parents=True)
    return res


def test_cura_detect_windows_layout(monkeypatch, tmp_path):
    # <root>\CuraEngine.exe  +  <root>\share\cura\resources\
    _clear_cura_env(monkeypatch)
    monkeypatch.setattr(printmcp_config, "_IS_WINDOWS", True)
    monkeypatch.setattr(printmcp_config, "_IS_MACOS", False)
    monkeypatch.setattr(printmcp_config, "_ENGINE_EXE", "CuraEngine.exe")
    root = tmp_path / "UltiMaker Cura 5.11.0"
    root.mkdir()
    (root / "CuraEngine.exe").write_text("")
    _make_resources(root)
    monkeypatch.setenv("PRINTMCP_CURA_DIR", str(root))

    paths = printmcp_config.get_cura_paths()
    assert paths.engine == root / "CuraEngine.exe"
    assert paths.definitions == root / "share" / "cura" / "resources" / "definitions"


def test_cura_detect_macos_bundle(monkeypatch, tmp_path):
    # <app>/Contents/MacOS/CuraEngine + <app>/Contents/Resources/share/cura/resources/
    _clear_cura_env(monkeypatch)
    monkeypatch.setattr(printmcp_config, "_IS_WINDOWS", False)
    monkeypatch.setattr(printmcp_config, "_IS_MACOS", True)
    monkeypatch.setattr(printmcp_config, "_ENGINE_EXE", "CuraEngine")
    app = tmp_path / "UltiMaker Cura.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "CuraEngine").write_text("")
    _make_resources(app / "Contents" / "Resources")
    monkeypatch.setenv("PRINTMCP_CURA_DIR", str(app))

    paths = printmcp_config.get_cura_paths()
    assert paths.engine == app / "Contents" / "MacOS" / "CuraEngine"
    assert (paths.definitions).is_dir()
    assert "Resources" in str(paths.definitions)


def test_cura_detect_linux_prefix(monkeypatch, tmp_path):
    # <prefix>/bin/CuraEngine + <prefix>/share/cura/resources/
    _clear_cura_env(monkeypatch)
    monkeypatch.setattr(printmcp_config, "_IS_WINDOWS", False)
    monkeypatch.setattr(printmcp_config, "_IS_MACOS", False)
    monkeypatch.setattr(printmcp_config, "_ENGINE_EXE", "CuraEngine")
    prefix = tmp_path / "usr"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "CuraEngine").write_text("")
    _make_resources(prefix)
    monkeypatch.setenv("PRINTMCP_CURA_DIR", str(prefix))

    paths = printmcp_config.get_cura_paths()
    assert paths.engine == prefix / "bin" / "CuraEngine"
    assert paths.definitions == prefix / "share" / "cura" / "resources" / "definitions"


def test_cura_env_overrides_engine_and_resources(monkeypatch, tmp_path):
    # The two explicit env vars should pin both halves regardless of layout.
    _clear_cura_env(monkeypatch)
    monkeypatch.setattr(printmcp_config, "_ENGINE_EXE", "CuraEngine")
    engine = tmp_path / "weird" / "place" / "CuraEngine"
    engine.parent.mkdir(parents=True)
    engine.write_text("")
    res = _make_resources(tmp_path / "elsewhere")
    monkeypatch.setenv("PRINTMCP_CURAENGINE", str(engine))
    monkeypatch.setenv("PRINTMCP_CURA_RESOURCES", str(res))

    paths = printmcp_config.get_cura_paths()
    assert paths.engine == engine
    assert paths.definitions == res / "definitions"


def test_cura_missing_engine_raises(monkeypatch, tmp_path):
    _clear_cura_env(monkeypatch)
    monkeypatch.setattr(printmcp_config, "_IS_WINDOWS", False)
    monkeypatch.setattr(printmcp_config, "_IS_MACOS", False)
    # Point at an empty dir and ensure nothing on PATH is picked up.
    monkeypatch.setenv("PRINTMCP_CURA_DIR", str(tmp_path))
    monkeypatch.setattr(printmcp_config.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="CuraEngine"):
        printmcp_config.get_cura_paths()


def test_cura_engine_found_but_resources_missing_raises(monkeypatch, tmp_path):
    _clear_cura_env(monkeypatch)
    monkeypatch.setattr(printmcp_config, "_ENGINE_EXE", "CuraEngine")
    engine = tmp_path / "CuraEngine"
    engine.write_text("")  # engine exists, but no resources anywhere
    monkeypatch.setenv("PRINTMCP_CURAENGINE", str(engine))
    monkeypatch.setattr(printmcp_config.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="resource"):
        printmcp_config.get_cura_paths()


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


# --------------------------------------------------------------------------- #
# Structured output (MCP 2025-06-18 spec): every tool must declare an
# outputSchema so smolagents structured_output=True clients see the shape.
#
# The refactored contract is FLAT:
#   * inputSchema lists each parameter as a top-level property (no "params"
#     wrapper) — tools take flat keyword args, not a single InputModel.
#   * outputSchema lists each result field as a top-level property (no
#     "result" wrapper) — tools return a pure Pydantic model, not a union of
#     str | model that would have produced an anyOf "result" property.
# --------------------------------------------------------------------------- #
def test_all_tools_have_output_schema():
    """Every registered tool must emit an outputSchema (MCP structured output)."""
    tools = asyncio.run(mcp.list_tools())
    missing = [t.name for t in tools if t.outputSchema is None]
    assert missing == [], f"Tools missing outputSchema: {missing}"


def test_no_input_schema_wraps_params():
    """Flat signatures: no tool's inputSchema wraps args in a 'params' property."""
    tools = asyncio.run(mcp.list_tools())
    offenders = [
        t.name
        for t in tools
        if t.inputSchema and "params" in (t.inputSchema.get("properties") or {})
    ]
    assert offenders == [], f"Tools with a 'params' input wrapper: {offenders}"


def test_no_output_schema_wraps_result():
    """Pure-model returns: no tool's outputSchema wraps fields in a 'result' property.

    The old contract returned ``str | Model`` which generated an anyOf
    'result' property. The refactored tools return a pure Pydantic model, so
    the outputSchema must describe the model's fields directly at the top level.
    """
    tools = asyncio.run(mcp.list_tools())
    offenders = [
        t.name
        for t in tools
        if t.outputSchema and "result" in (t.outputSchema.get("properties") or {})
    ]
    assert offenders == [], f"Tools with a 'result' output wrapper: {offenders}"


def test_thingiverse_search_has_structured_result_schema():
    """The search tool's outputSchema describes the SearchResult model directly."""
    from printmcp.thingiverse import SearchResult

    tools = asyncio.run(mcp.list_tools())
    search = next(t for t in tools if t.name == "thingiverse_search_models")
    schema = search.outputSchema
    assert schema is not None
    # Flat contract: the model's own fields appear as top-level output properties
    # (no union 'result' wrapper property anymore).
    props = schema.get("properties", {})
    assert "result" not in props, "outputSchema should not wrap fields in 'result'"
    assert "query" in props
    assert "results" in props
    # Verify the SearchResult model itself generates a valid JSON schema.
    sr_schema = SearchResult.model_json_schema()
    assert "query" in sr_schema.get("properties", {})
    assert "results" in sr_schema.get("properties", {})


def test_cura_slice_has_structured_result_schema():
    """The slice tool's outputSchema describes the SliceResult model directly."""
    from printmcp.cura import SliceResult

    tools = asyncio.run(mcp.list_tools())
    slice_tool = next(t for t in tools if t.name == "cura_slice_model")
    assert slice_tool.outputSchema is not None
    # Flat contract: no 'result' wrapper.
    props = slice_tool.outputSchema.get("properties", {})
    assert "result" not in props
    sr_schema = SliceResult.model_json_schema()
    assert "model" in sr_schema.get("properties", {})
    assert "gcode_path" in sr_schema.get("properties", {})
    assert "settings" in sr_schema.get("properties", {})


def test_octoprint_status_has_structured_result_schema():
    """The status tool's outputSchema describes the StatusResult model directly."""
    from printmcp.octoprint import StatusResult

    tools = asyncio.run(mcp.list_tools())
    status = next(t for t in tools if t.name == "octoprint_get_status")
    assert status.outputSchema is not None
    # Flat contract: no 'result' wrapper; model fields appear at top level.
    props = status.outputSchema.get("properties", {})
    assert "result" not in props
    assert "ready" in props
    assert "temperatures" in props
    sr_schema = StatusResult.model_json_schema()
    assert "ready" in sr_schema.get("properties", {})
    assert "temperatures" in sr_schema.get("properties", {})


def test_octoprint_status_input_schema_is_flat():
    """The status tool takes flat keyword args (no InputModel 'params' wrapper).

    ``octoprint_get_status(response_format=...)`` should expose ``response_format``
    as a top-level inputSchema property, not nested under a 'params' object.
    """
    tools = asyncio.run(mcp.list_tools())
    status = next(t for t in tools if t.name == "octoprint_get_status")
    assert status.inputSchema is not None
    props = status.inputSchema.get("properties", {})
    assert "response_format" in props
    assert "params" not in props
