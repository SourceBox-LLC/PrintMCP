"""Offline tests for the OrcaSlicer (Level 2) plumbing.

These run without OrcaSlicer installed, a network, or a printer:

* input validation (model path, preset names),
* preset name resolution + suggestions against a synthetic profiles tree,
* scalar override application into preset copies,
* binary/profile discovery (env override, flatpak, PATH) via monkeypatching,
* flatpak launch-argv construction (--filesystem grants precede the app id),
* stats parsing from an OrcaSlicer G-code footer.

The real end-to-end slice is exercised separately (and only when a machine has
OrcaSlicer) — these unit tests pin the offline behavior.
"""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from printmcp import config as orca_config
from printmcp.orca import (
    SliceModelInput,
    _apply_overrides,
    _closest,
    _load_preset_name,
    _parse_orca_stats,
    _run_orca,
    list_presets,
    resolve_preset,
)


# --------------------------------------------------------------------------- #
# Synthetic profiles tree
# --------------------------------------------------------------------------- #
def _make_profiles(tmp_path):
    """Build a minimal profiles tree: Creality vendor + global library."""
    profiles = tmp_path / "profiles"
    crea = profiles / "Creality"
    (crea / "machine").mkdir(parents=True)
    (crea / "process").mkdir(parents=True)
    (crea / "filament").mkdir(parents=True)
    lib = profiles / "OrcaFilamentLibrary"
    (lib / "filament").mkdir(parents=True)

    def w(path, name, extra=None):
        d = {"name": name, "type": "x"}
        if extra:
            d.update(extra)
        (path).write_text(json.dumps(d), encoding="utf-8")

    w(crea / "machine" / "Creality Ender-3 Pro 0.4 nozzle.json", "Creality Ender-3 Pro 0.4 nozzle")
    w(crea / "process" / "0.20mm Standard @Creality Ender3 Pro 0.4.json", "0.20mm Standard @Creality Ender3 Pro 0.4")
    w(crea / "filament" / "Creality Generic PLA.json", "Creality Generic PLA")
    w(lib / "filament" / "Generic PLA @System.json", "Generic PLA @System")
    return profiles


def test_load_preset_name_reads_name(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"name": "Hello"}), encoding="utf-8")
    assert _load_preset_name(p) == "Hello"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _load_preset_name(bad) is None


def test_list_presets_indexes_all_vendors_and_library(tmp_path):
    profiles = _make_profiles(tmp_path)
    machines = {p["name"] for p in list_presets(profiles, "machine")}
    filaments = {p["name"] for p in list_presets(profiles, "filament")}
    assert "Creality Ender-3 Pro 0.4 nozzle" in machines
    # Filament from the vendor AND the global library are both indexed.
    assert {"Creality Generic PLA", "Generic PLA @System"} <= filaments


def test_resolve_preset_by_name(tmp_path):
    profiles = _make_profiles(tmp_path)
    p = resolve_preset(profiles, "machine", "Creality Ender-3 Pro 0.4 nozzle")
    assert p.name == "Creality Ender-3 Pro 0.4 nozzle.json"


def test_resolve_preset_by_filename_without_extension(tmp_path):
    profiles = _make_profiles(tmp_path)
    p = resolve_preset(profiles, "filament", "Generic PLA @System")
    assert "OrcaFilamentLibrary" in str(p)


def test_resolve_preset_case_insensitive(tmp_path):
    profiles = _make_profiles(tmp_path)
    p = resolve_preset(profiles, "machine", "creality ender-3 pro 0.4 nozzle")
    assert "machine" in str(p)


def test_resolve_preset_unknown_raises_with_suggestion(tmp_path):
    profiles = _make_profiles(tmp_path)
    with pytest.raises(ToolError) as exc:
        resolve_preset(profiles, "machine", "Ender")
    assert "Unknown machine preset" in str(exc.value)
    # Suggests the close match.
    assert "Ender-3 Pro" in str(exc.value)


def test_closest_returns_matches_only():
    matches = _closest("Ender", ["Creality Ender-3 Pro", "Bambu A1", "Ender-5"])
    assert set(matches) == {"Creality Ender-3 Pro", "Ender-5"}
    # No match -> empty.
    assert _closest("zzz", ["a", "b"]) == []


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
def test_slice_input_requires_model_path():
    with pytest.raises(ValidationError):
        SliceModelInput(machine="m", process="p", filament="f")


def test_slice_input_minimal_ok():
    s = SliceModelInput(
        model_path="/x/model.stl", machine="m", process="p", filament="f"
    )
    assert s.machine == "m" and s.overrides is None


def test_slice_input_forbids_extra_keys():
    with pytest.raises(ValidationError):
        SliceModelInput(
            model_path="/x.stl", machine="m", process="p", filament="f", bogus=1
        )


# --------------------------------------------------------------------------- #
# Override application
# --------------------------------------------------------------------------- #
def test_apply_overrides_writes_string_scalars(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"name": "m", "use_relative_e_distances": "1"}), encoding="utf-8")
    _apply_overrides(p, {"use_relative_e_distances": 0, "sparse_infill_density": "15%"})
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["use_relative_e_distances"] == "0"
    assert d["sparse_infill_density"] == "15%"
    assert d["name"] == "m"  # untouched


def test_apply_overrides_coerces_bool(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"name": "m"}), encoding="utf-8")
    _apply_overrides(p, {"enable_support": True})
    assert json.loads(p.read_text())["enable_support"] is True


# --------------------------------------------------------------------------- #
# Stats parsing from an Orca G-code footer
# --------------------------------------------------------------------------- #
def test_parse_orca_stats_reads_footer(tmp_path):
    g = tmp_path / "out.gcode"
    g.write_text(
        "G1 X0\n; estimated printing time (normal mode) = 24m 21s\n"
        "; filament used [mm] = 1330.76\n; CONFIG_BLOCK_END\n",
        encoding="utf-8",
    )
    stats = _parse_orca_stats(g)
    assert stats["print_time"] == "24m 21s"
    assert stats["filament_m"] == pytest.approx(1.331, abs=1e-3)


def test_parse_orca_stats_missing_file(tmp_path):
    assert _parse_orca_stats(tmp_path / "nope.gcode") == {}
    assert _parse_orca_stats(None) == {}


# --------------------------------------------------------------------------- #
# Discovery (monkeypatched; never touches the real filesystem/PATH)
# --------------------------------------------------------------------------- #
def test_orca_command_env_override(monkeypatch):
    monkeypatch.setenv("PRINTMCP_ORCA_COMMAND", "orca-slicer --foo")
    monkeypatch.setattr(orca_config.shutil, "which", lambda _n: None)
    argv, via = orca_config._orca_command_argv()
    assert argv == ["orca-slicer", "--foo"]
    assert via == "env"


def test_orca_command_flatpak(monkeypatch):
    monkeypatch.delenv("PRINTMCP_ORCA_COMMAND", raising=False)

    def which(name):
        return "/usr/bin/flatpak" if name == "flatpak" else None

    monkeypatch.setattr(orca_config.shutil, "which", which)

    class _OK:
        returncode = 0

    monkeypatch.setattr(
        orca_config.subprocess, "run", lambda *a, **k: _OK()
    )
    argv, via = orca_config._orca_command_argv()
    assert argv == ["flatpak", "run", orca_config.ORCA_FLATPAK_ID]
    assert via == "flatpak"


def test_orca_command_none_found(monkeypatch):
    monkeypatch.delenv("PRINTMCP_ORCA_COMMAND", raising=False)
    monkeypatch.setattr(orca_config.shutil, "which", lambda _n: None)
    assert orca_config._orca_command_argv() is None


def test_is_orca_profiles_dir_requires_vendor_with_machine(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert orca_config._is_orca_profiles_dir(empty) is False
    good = tmp_path / "good"
    (good / "Creality" / "machine").mkdir(parents=True)
    assert orca_config._is_orca_profiles_dir(good) is True


def test_get_orca_paths_missing_raises(monkeypatch):
    monkeypatch.delenv("PRINTMCP_ORCA_COMMAND", raising=False)
    monkeypatch.delenv("PRINTMCP_ORCA_PROFILES", raising=False)
    monkeypatch.setattr(orca_config.shutil, "which", lambda _n: None)
    with pytest.raises(FileNotFoundError, match="OrcaSlicer CLI"):
        orca_config.get_orca_paths()


# --------------------------------------------------------------------------- #
# flatpak argv construction (grants must precede the app id)
# --------------------------------------------------------------------------- #
def test_run_orca_flatpak_grants_before_app_id(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    monkeypatch.setattr("printmcp.orca.subprocess.run", fake_run)
    monkeypatch.setenv("OCTOPRINT_API_KEY", "secret")
    argv = ("flatpak", "run", "com.orcaslicer.OrcaSlicer")
    _run_orca(argv, ["--slice", "0", "model.stl"], [tmp_path])

    cmd = captured["cmd"]
    # Grants appear after "run" and before the app id.
    assert cmd[:2] == ["flatpak", "run"]
    assert f"--filesystem={tmp_path}" in cmd
    assert cmd.index(f"--filesystem={tmp_path}") < cmd.index("com.orcaslicer.OrcaSlicer")
    assert cmd[-1] == "model.stl"
    # Secrets are scrubbed from the child env.
    assert "OCTOPRINT_API_KEY" not in captured["env"]


def test_run_orca_native_no_grants(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    monkeypatch.setattr("printmcp.orca.subprocess.run", fake_run)
    _run_orca(("orca-slicer",), ["--slice", "0"], [])
    # Native binary: no flatpak grants injected.
    assert captured["cmd"][0] == "orca-slicer"
    assert not any(str(c).startswith("--filesystem=") for c in captured["cmd"])
