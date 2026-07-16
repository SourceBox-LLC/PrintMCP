"""Offline tests for the OctoPrint (Level 3) HTTP plumbing.

These use ``httpx.MockTransport`` to exercise the *real* request-building and
response-parsing code paths (``_config`` -> ``_headers`` -> ``_request`` ->
parse) WITHOUT touching a network or a physical printer. They verify, for
every tool:

* the exact HTTP method, path, and JSON body sent to OctoPrint;
* that the ``X-Api-Key`` header is attached (and no ``Authorization`` leaks);
* that responses are parsed into the documented shape;
* that dry-run (``confirm=false``) calls send ZERO requests;
* that error statuses map to friendly strings without leaking the key.

The MCP ``@tool`` decorator returns the wrapped function unchanged, so the tool
coroutines are called directly here.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from printmcp import octoprint as op
from printmcp.octoprint import (
    ConnectInput,
    ControlJobInput,
    DryRunPreview,
    FileListResult,
    HomeInput,
    JobStatusInput,
    ListFilesInput,
    MoveInput,
    SetTemperatureInput,
    StartPrintInput,
    StatusInput,
    StatusResult,
    UploadInput,
    UploadResult,
    octoprint_connect,
    octoprint_control_job,
    octoprint_get_job,
    octoprint_get_status,
    octoprint_home,
    octoprint_list_files,
    octoprint_move,
    octoprint_set_temperature,
    octoprint_start_print,
    octoprint_upload_file,
)

TEST_URL = "http://printer.test"
TEST_KEY = "test-key-do-not-leak"


def run(coro):
    return asyncio.run(coro)


class Router:
    """Routes mock requests by (method, path) and records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self._routes: dict[tuple[str, str], object] = {}
        self.default = (200, {})

    def add(self, method, path, status=200, body=None, raises=None):
        self._routes[(method, path)] = raises or (
            status,
            body if body is not None else {},
        )
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self._routes.get((request.method, request.url.path), self.default)
        if isinstance(route, Exception):
            raise route
        status, body = route
        if status == 204:
            return httpx.Response(204)
        return httpx.Response(status, json=body)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def last_json(self):
        return json.loads(self.requests[-1].content)


def install(monkeypatch, router: Router, url: str = TEST_URL, key: str = TEST_KEY):
    """Point config at a fake printer and route httpx through the mock transport."""
    if url is None:
        monkeypatch.delenv("OCTOPRINT_URL", raising=False)
    else:
        monkeypatch.setenv("OCTOPRINT_URL", url)
    if key is None:
        monkeypatch.delenv("OCTOPRINT_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OCTOPRINT_API_KEY", key)

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(router.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(op.httpx, "AsyncClient", factory)
    return router


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
def test_status_parses_state_and_temps(monkeypatch):
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add(
        "GET",
        "/api/connection",
        body={"current": {"state": "Operational", "port": "COM3", "baudrate": 115200}},
    )
    r.add(
        "GET",
        "/api/printer",
        body={
            "state": {
                "text": "Operational",
                "flags": {"operational": True, "printing": False},
            },
            "temperature": {
                "tool0": {"actual": 23.1, "target": 0.0},
                "bed": {"actual": 24.0, "target": 0.0},
            },
        },
    )
    install(monkeypatch, r)

    out = run(octoprint_get_status(StatusInput()))
    assert "Operational" in out
    assert "Ready to print: yes" in out
    assert "tool0" in out and "Bed" in out
    # Three GETs, all carrying the key, none leaking Authorization.
    paths = [req.url.path for req in r.requests]
    assert paths == ["/api/version", "/api/connection", "/api/printer"]
    assert all(req.headers.get("x-api-key") == TEST_KEY for req in r.requests)
    assert all("authorization" not in req.headers for req in r.requests)


def test_status_handles_409_when_disconnected(monkeypatch):
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add(
        "GET",
        "/api/connection",
        body={"current": {"state": "Closed", "port": None, "baudrate": None}},
    )
    r.add(
        "GET", "/api/printer", status=409, body={"error": "Printer is not operational"}
    )
    install(monkeypatch, r)

    out = run(octoprint_get_status(StatusInput()))
    assert "Ready to print: no" in out
    assert "Error" not in out  # a 409 here is expected, not an error


@pytest.mark.parametrize(
    "flags,expected_ready",
    [
        ({"operational": True, "printing": False}, True),
        ({"operational": True, "printing": True}, False),
        ({"operational": True, "paused": True}, False),
        ({"operational": True, "pausing": True}, False),
        ({"operational": True, "cancelling": True}, False),
        ({"operational": True, "error": True}, False),
        ({"operational": True, "closedOrError": True}, False),
        ({"operational": False}, False),
        ({}, False),
    ],
)
def test_status_ready_flag_excludes_busy_and_error_states(
    monkeypatch, flags, expected_ready
):
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add("GET", "/api/connection", body={"current": {"state": "Operational"}})
    r.add(
        "GET",
        "/api/printer",
        body={
            "state": {"text": "X", "flags": flags},
            "temperature": {},
        },
    )
    install(monkeypatch, r)
    out = run(octoprint_get_status(StatusInput(response_format="json")))
    # JSON-format tools now return Pydantic model instances (MCP structured output).
    assert isinstance(out, StatusResult)
    assert out.ready is expected_ready


def test_status_json_format(monkeypatch):
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add(
        "GET",
        "/api/connection",
        body={"current": {"state": "Operational", "port": "COM3", "baudrate": 115200}},
    )
    r.add(
        "GET",
        "/api/printer",
        body={
            "state": {
                "text": "Operational",
                "flags": {"operational": True, "printing": False},
            },
            "temperature": {"tool0": {"actual": 23.1, "target": 0.0}},
        },
    )
    install(monkeypatch, r)

    out = run(octoprint_get_status(StatusInput(response_format="json")))
    assert isinstance(out, StatusResult)
    assert out.ready is True
    assert out.connection.state == "Operational"
    assert out.temperatures["tool0"].actual == 23.1


# --------------------------------------------------------------------------- #
# List files
# --------------------------------------------------------------------------- #
def test_list_files_flattens_folders_and_sorts(monkeypatch):
    r = Router()
    r.add(
        "GET",
        "/api/files/local",
        body={
            "files": [
                {
                    "type": "machinecode",
                    "name": "old.gcode",
                    "path": "old.gcode",
                    "size": 100,
                    "date": 1000,
                },
                {
                    "type": "folder",
                    "name": "sub",
                    "children": [
                        {
                            "type": "machinecode",
                            "name": "new.gcode",
                            "path": "sub/new.gcode",
                            "size": 200,
                            "date": 2000,
                            "gcodeAnalysis": {"estimatedPrintTime": 3600},
                        },
                    ],
                },
            ]
        },
    )
    install(monkeypatch, r)

    out = run(octoprint_list_files(ListFilesInput(response_format="json")))
    assert isinstance(out, FileListResult)
    assert out.count == 2
    # Newest first.
    assert out.files[0].path == "sub/new.gcode"
    assert out.files[0].estimated_print_time_s == 3600
    assert out.files[1].path == "old.gcode"
    # recursive=true query was sent.
    assert "recursive=true" in str(r.last.url)


def test_list_files_renders_zero_estimate(monkeypatch):
    r = Router()
    r.add(
        "GET",
        "/api/files/local",
        body={
            "files": [
                {
                    "type": "machinecode",
                    "name": "a.gcode",
                    "path": "a.gcode",
                    "size": 5,
                    "date": 1,
                    "gcodeAnalysis": {"estimatedPrintTime": 0},
                },
            ]
        },
    )
    install(monkeypatch, r)
    out = run(octoprint_list_files(ListFilesInput()))
    assert "est. print time: 0 s" in out


def test_list_files_empty(monkeypatch):
    r = Router()
    r.add("GET", "/api/files/local", body={"files": []})
    install(monkeypatch, r)
    out = run(octoprint_list_files(ListFilesInput()))
    assert "No G-code files" in out


# --------------------------------------------------------------------------- #
# Job
# --------------------------------------------------------------------------- #
def test_job_progress_markdown(monkeypatch):
    r = Router()
    r.add(
        "GET",
        "/api/job",
        body={
            "state": "Printing",
            "job": {"file": {"name": "cup.gcode"}},
            "progress": {
                "completion": 42.456,
                "printTime": 1800,
                "printTimeLeft": 3661,
            },
        },
    )
    install(monkeypatch, r)

    out = run(octoprint_get_job(JobStatusInput()))
    assert "Printing" in out
    assert "cup.gcode" in out
    assert "42.5%" in out  # rounded to 1 dp
    assert "30m 0s" in out  # 1800s elapsed
    assert "1h 1m 1s" in out  # 3661s remaining


def test_job_idle(monkeypatch):
    r = Router()
    r.add(
        "GET",
        "/api/job",
        body={"state": "Operational", "job": {"file": {"name": None}}, "progress": {}},
    )
    install(monkeypatch, r)
    out = run(octoprint_get_job(JobStatusInput()))
    assert "none selected" in out


# --------------------------------------------------------------------------- #
# Connect
# --------------------------------------------------------------------------- #
def test_connect_dry_run_sends_nothing(monkeypatch):
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_connect(ConnectInput()))  # confirm defaults False
    assert "confirm=true" in out
    assert r.requests == []


def test_connect_confirm_posts_body(monkeypatch):
    r = Router()
    r.add("POST", "/api/connection", status=204)
    install(monkeypatch, r)
    out = run(
        octoprint_connect(
            ConnectInput(action="connect", port="COM3", baudrate=115200, confirm=True)
        )
    )
    assert "Sent 'connect'" in out
    assert r.last.method == "POST"
    assert r.last.url.path == "/api/connection"
    assert r.last_json() == {"command": "connect", "port": "COM3", "baudrate": 115200}


def test_disconnect_body(monkeypatch):
    r = Router()
    r.add("POST", "/api/connection", status=204)
    install(monkeypatch, r)
    run(octoprint_connect(ConnectInput(action="disconnect", confirm=True)))
    assert r.last_json() == {"command": "disconnect"}


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
def test_upload_posts_multipart_and_parses_path(monkeypatch, tmp_path):
    gco = tmp_path / "cup.gcode"
    gco.write_text("G28\nG1 X0 Y0\n")
    r = Router()
    r.add(
        "POST",
        "/api/files/local",
        status=201,
        body={"files": {"local": {"name": "cup.gcode", "path": "cup.gcode"}}},
    )
    install(monkeypatch, r)

    out = run(
        octoprint_upload_file(UploadInput(gcode_path=str(gco), response_format="json"))
    )
    assert isinstance(out, UploadResult)
    assert out.server_path == "cup.gcode"
    assert out.printing is False
    # multipart contains the file.
    assert b'filename="cup.gcode"' in r.last.content
    assert "multipart/form-data" in r.last.headers.get("content-type", "")


def test_upload_print_after_upload_requires_confirm(monkeypatch, tmp_path):
    gco = tmp_path / "cup.gcode"
    gco.write_text("G28\n")
    r = Router()
    install(monkeypatch, r)
    out = run(
        octoprint_upload_file(UploadInput(gcode_path=str(gco), print_after_upload=True))
    )  # confirm False
    assert "confirm=true" in out
    assert r.requests == []  # dry run: nothing uploaded, nothing printed


def test_upload_print_after_upload_sets_form_flags(monkeypatch, tmp_path):
    gco = tmp_path / "cup.gcode"
    gco.write_text("G28\n")
    r = Router()
    r.add(
        "POST",
        "/api/files/local",
        status=201,
        body={"files": {"local": {"name": "cup.gcode", "path": "cup.gcode"}}},
    )
    install(monkeypatch, r)
    out = run(
        octoprint_upload_file(
            UploadInput(gcode_path=str(gco), print_after_upload=True, confirm=True)
        )
    )
    assert "started" in out.lower()
    body = r.last.content
    assert b'name="select"' in body and b'name="print"' in body


def test_upload_rejects_non_gcode(monkeypatch, tmp_path):
    bad = tmp_path / "model.stl"
    bad.write_text("solid\n")
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_upload_file(UploadInput(gcode_path=str(bad))))
    assert out.startswith("Error:")
    assert r.requests == []


def test_upload_missing_file(monkeypatch, tmp_path):
    r = Router()
    install(monkeypatch, r)
    out = run(
        octoprint_upload_file(UploadInput(gcode_path=str(tmp_path / "nope.gcode")))
    )
    assert out.startswith("Error:")
    assert "not found" in out
    assert r.requests == []


# --------------------------------------------------------------------------- #
# Start print
# --------------------------------------------------------------------------- #
def test_start_print_dry_run_sends_nothing(monkeypatch):
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_start_print(StartPrintInput(path="cup.gcode")))
    assert "confirm=true" in out
    assert r.requests == []


def test_start_print_confirm_posts_select_and_print(monkeypatch):
    r = Router()
    r.add("POST", "/api/files/local/cup.gcode", status=204)
    install(monkeypatch, r)
    out = run(octoprint_start_print(StartPrintInput(path="cup.gcode", confirm=True)))
    assert "Started printing" in out
    assert r.last.url.path == "/api/files/local/cup.gcode"
    assert r.last_json() == {"command": "select", "print": True}


def test_start_print_quotes_subfolder_path(monkeypatch):
    r = Router()
    r.add("POST", "/api/files/local/sub/my cup.gcode", status=204)
    install(monkeypatch, r)
    out = run(
        octoprint_start_print(StartPrintInput(path="sub/my cup.gcode", confirm=True))
    )
    # Space is percent-encoded but the slash is preserved.
    assert "%20" in str(r.last.url)
    assert "/api/files/local/sub/my cup.gcode" == r.last.url.path
    assert not out.startswith("Error:")


# --------------------------------------------------------------------------- #
# Control job
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "action,expected",
    [
        ("pause", {"command": "pause", "action": "pause"}),
        ("resume", {"command": "pause", "action": "resume"}),
        ("cancel", {"command": "cancel"}),
    ],
)
def test_control_job_bodies(monkeypatch, action, expected):
    r = Router()
    r.add("POST", "/api/job", status=204)
    install(monkeypatch, r)
    out = run(octoprint_control_job(ControlJobInput(action=action, confirm=True)))
    assert not out.startswith("Error:")
    assert r.last.url.path == "/api/job"
    assert r.last_json() == expected


def test_control_job_dry_run(monkeypatch):
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_control_job(ControlJobInput(action="cancel")))
    assert "confirm=true" in out
    assert r.requests == []


# --------------------------------------------------------------------------- #
# Set temperature
# --------------------------------------------------------------------------- #
def test_set_tool_temperature_body(monkeypatch):
    r = Router()
    r.add("POST", "/api/printer/tool", status=204)
    install(monkeypatch, r)
    out = run(
        octoprint_set_temperature(
            SetTemperatureInput(heater="tool", target=200, confirm=True)
        )
    )
    assert "tool0" in out and "200" in out
    assert r.last.url.path == "/api/printer/tool"
    assert r.last_json() == {"command": "target", "targets": {"tool0": 200}}


def test_set_bed_temperature_body(monkeypatch):
    r = Router()
    r.add("POST", "/api/printer/bed", status=204)
    install(monkeypatch, r)
    run(
        octoprint_set_temperature(
            SetTemperatureInput(heater="bed", target=60, confirm=True)
        )
    )
    assert r.last.url.path == "/api/printer/bed"
    assert r.last_json() == {"command": "target", "target": 60}


def test_set_temperature_dry_run(monkeypatch):
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_set_temperature(SetTemperatureInput(heater="tool", target=200)))
    assert "confirm=true" in out
    assert r.requests == []


# --------------------------------------------------------------------------- #
# Home / Move
# --------------------------------------------------------------------------- #
def test_home_body(monkeypatch):
    r = Router()
    r.add("POST", "/api/printer/printhead", status=204)
    install(monkeypatch, r)
    out = run(octoprint_home(HomeInput(axes=["x", "y"], confirm=True)))
    assert not out.startswith("Error:")
    assert r.last.url.path == "/api/printer/printhead"
    assert r.last_json() == {"command": "home", "axes": ["x", "y"]}


def test_move_body_relative(monkeypatch):
    r = Router()
    r.add("POST", "/api/printer/printhead", status=204)
    install(monkeypatch, r)
    out = run(octoprint_move(MoveInput(x=10, z=-5, speed=3000, confirm=True)))
    assert not out.startswith("Error:")
    body = r.last_json()
    assert body["command"] == "jog"
    assert body["absolute"] is False
    assert body["x"] == 10 and body["z"] == -5
    assert body["speed"] == 3000
    assert "y" not in body  # omitted axis not sent


def test_home_and_move_dry_runs_send_nothing(monkeypatch):
    r = Router()
    install(monkeypatch, r)
    assert "confirm=true" in run(octoprint_home(HomeInput()))
    assert "confirm=true" in run(octoprint_move(MoveInput(x=5)))
    assert r.requests == []


# --------------------------------------------------------------------------- #
# Error mapping & config
# --------------------------------------------------------------------------- #
def test_missing_config_is_friendly(monkeypatch):
    r = Router()
    install(monkeypatch, r, url=None, key=None)
    out = run(octoprint_get_status(StatusInput()))
    assert out.startswith("Error:")
    assert "OCTOPRINT_URL" in out and "OCTOPRINT_API_KEY" in out
    assert r.requests == []  # never attempted a request without config


def test_401_maps_to_auth_error(monkeypatch):
    r = Router()
    r.add("GET", "/api/version", status=401, body={"error": "invalid key"})
    r.add("GET", "/api/connection", status=401, body={"error": "invalid key"})
    install(monkeypatch, r)
    out = run(octoprint_get_status(StatusInput()))
    assert "401" in out
    assert TEST_KEY not in out  # never leak the key in an error


def test_409_on_start_print_maps_to_conflict(monkeypatch):
    r = Router()
    r.add(
        "POST",
        "/api/files/local/cup.gcode",
        status=409,
        body={"error": "not operational"},
    )
    install(monkeypatch, r)
    out = run(octoprint_start_print(StartPrintInput(path="cup.gcode", confirm=True)))
    assert "409" in out
    assert "octoprint_connect" in out  # actionable hint


def test_connect_error_maps_to_unreachable(monkeypatch):
    r = Router()
    r.add("GET", "/api/version", raises=httpx.ConnectError("refused"))
    r.add("GET", "/api/connection", raises=httpx.ConnectError("refused"))
    install(monkeypatch, r)
    out = run(octoprint_get_status(StatusInput()))
    assert "Could not reach OctoPrint" in out
    assert TEST_URL in out


def test_api_key_never_appears_in_any_output(monkeypatch, tmp_path):
    """Security guard: the key is sent only in the header, never echoed."""
    gco = tmp_path / "cup.gcode"
    gco.write_text("G28\n")
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add("GET", "/api/connection", body={"current": {"state": "Operational"}})
    r.add(
        "GET",
        "/api/printer",
        body={
            "state": {"text": "Operational", "flags": {"operational": True}},
            "temperature": {},
        },
    )
    r.add("GET", "/api/job", body={"state": "Operational", "job": {}, "progress": {}})
    r.add("POST", "/api/printer/bed", status=204)
    r.add(
        "POST",
        "/api/files/local",
        status=201,
        body={"files": {"local": {"path": "cup.gcode"}}},
    )
    install(monkeypatch, r)

    outputs = [
        run(octoprint_get_status(StatusInput())),
        run(octoprint_get_status(StatusInput(response_format="json"))),
        run(octoprint_get_job(JobStatusInput())),
        run(
            octoprint_set_temperature(
                SetTemperatureInput(heater="bed", target=60, confirm=True)
            )
        ),
        run(octoprint_upload_file(UploadInput(gcode_path=str(gco)))),
    ]
    assert all(TEST_KEY not in o for o in outputs)
    # …but the key WAS sent in the header on every request.
    assert all(req.headers.get("x-api-key") == TEST_KEY for req in r.requests)


# --------------------------------------------------------------------------- #
# Structured output (MCP 2025-06-18 spec)
# --------------------------------------------------------------------------- #
def test_json_status_returns_model_instance(monkeypatch):
    """JSON-format returns a StatusResult Pydantic instance, not a JSON string."""
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add(
        "GET",
        "/api/connection",
        body={"current": {"state": "Operational", "port": "COM3", "baudrate": 115200}},
    )
    r.add(
        "GET",
        "/api/printer",
        body={
            "state": {"text": "Operational", "flags": {"operational": True}},
            "temperature": {"tool0": {"actual": 23.1, "target": 0.0}},
        },
    )
    install(monkeypatch, r)

    out = run(octoprint_get_status(StatusInput(response_format="json")))
    assert isinstance(out, StatusResult)
    assert out.server.version == "1.9.3"
    assert out.connection.baudrate == 115200
    assert out.temperatures["tool0"].actual == 23.1


def test_markdown_status_returns_str(monkeypatch):
    """Markdown-format still returns a plain string (backward compat)."""
    r = Router()
    r.add("GET", "/api/version", body={"server": "1.9.3", "api": "0.1"})
    r.add("GET", "/api/connection", body={"current": {"state": "Operational"}})
    r.add(
        "GET",
        "/api/printer",
        body={
            "state": {"text": "Operational", "flags": {"operational": True}},
            "temperature": {},
        },
    )
    install(monkeypatch, r)

    out = run(octoprint_get_status(StatusInput()))
    assert isinstance(out, str)
    assert "Operational" in out


def test_json_connect_dry_run_returns_dryrun_model(monkeypatch):
    """Dry-run on the JSON path returns a DryRunPreview model."""
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_connect(ConnectInput(action="connect", response_format="json")))
    assert isinstance(out, DryRunPreview)
    assert out.dry_run is True
    assert out.action == "connect"
    assert r.requests == []  # nothing sent


def test_json_connect_actuated_returns_connect_result(monkeypatch):
    """Actuated connect on the JSON path returns a ConnectResult model."""
    r = Router()
    r.add("POST", "/api/connection", status=204)
    install(monkeypatch, r)
    out = run(
        octoprint_connect(
            ConnectInput(action="connect", confirm=True, response_format="json")
        )
    )
    from printmcp.octoprint import ConnectResult

    assert isinstance(out, ConnectResult)
    assert out.ok is True
    assert out.action == "connect"


def test_json_list_files_returns_filelist_model(monkeypatch):
    """JSON-format list_files returns a FileListResult model."""
    r = Router()
    r.add(
        "GET",
        "/api/files/local",
        body={
            "files": [
                {
                    "type": "machinecode",
                    "name": "a.gcode",
                    "path": "a.gcode",
                    "size": 100,
                    "date": 1,
                }
            ]
        },
    )
    install(monkeypatch, r)
    out = run(octoprint_list_files(ListFilesInput(response_format="json")))
    assert isinstance(out, FileListResult)
    assert out.count == 1
    assert out.files[0].name == "a.gcode"
    assert out.files[0].size_bytes == 100


def test_json_upload_returns_upload_result(monkeypatch, tmp_path):
    """JSON-format upload returns an UploadResult model."""
    gco = tmp_path / "cup.gcode"
    gco.write_text("G28\n")
    r = Router()
    r.add(
        "POST",
        "/api/files/local",
        status=201,
        body={"files": {"local": {"name": "cup.gcode", "path": "cup.gcode"}}},
    )
    install(monkeypatch, r)
    out = run(
        octoprint_upload_file(UploadInput(gcode_path=str(gco), response_format="json"))
    )
    assert isinstance(out, UploadResult)
    assert out.uploaded == "cup.gcode"
    assert out.server_path == "cup.gcode"


def test_json_job_returns_job_result_model(monkeypatch):
    """JSON-format get_job returns a JobResult model."""
    r = Router()
    r.add(
        "GET",
        "/api/job",
        body={
            "state": "Printing",
            "job": {"file": {"name": "cup.gcode"}},
            "progress": {
                "completion": 42.456,
                "printTime": 1800,
                "printTimeLeft": 3661,
            },
        },
    )
    install(monkeypatch, r)
    from printmcp.octoprint import JobResult

    out = run(octoprint_get_job(JobStatusInput(response_format="json")))
    assert isinstance(out, JobResult)
    assert out.state == "Printing"
    assert out.file == "cup.gcode"
    assert out.completion_percent == 42.5
    assert out.print_time_s == 1800
    assert out.print_time_left_s == 3661


def test_markdown_connect_dry_run_returns_str(monkeypatch):
    """Dry-run on the markdown path still returns a plain string."""
    r = Router()
    install(monkeypatch, r)
    out = run(octoprint_connect(ConnectInput(action="connect")))
    assert isinstance(out, str)
    assert "confirm=true" in out
    assert r.requests == []
