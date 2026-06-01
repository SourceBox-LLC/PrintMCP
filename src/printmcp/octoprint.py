#!/usr/bin/env python3
"""OctoPrint integration for PrintMCP (Level 3: manage & control printing).

Talks to an OctoPrint server's REST API to upload G-code, start/pause/cancel
prints, monitor progress, and drive the printer's heaters and motors. This is
the final stage of the pipeline (download -> slice -> *print*).

Connection is configured via the ``OCTOPRINT_URL`` and ``OCTOPRINT_API_KEY``
environment variables (a project ``.env`` is loaded automatically). The API key
is sent only in the ``X-Api-Key`` header to that one configured host.

Tools that physically actuate the machine (start a print, set a temperature,
home or move the head, pause/cancel) require an explicit ``confirm=true``. Called
with ``confirm=false`` (the default) they perform a dry run: they describe what
*would* happen and send nothing. This guards a real machine with live heaters
and motors against an accidental tool call.

Read-only tools:
- ``octoprint_get_status``   - connection state, printer state, temperatures
- ``octoprint_list_files``   - G-code files available on the server
- ``octoprint_get_job``      - the active job and its progress

Action tools (``confirm=true`` to actuate):
- ``octoprint_connect``      - open/close the printer's serial connection
- ``octoprint_upload_file``  - upload a local .gcode to the server
- ``octoprint_start_print``  - select a server file and begin printing
- ``octoprint_control_job``  - pause / resume / cancel the active job
- ``octoprint_set_temperature`` - set a tool or bed target temperature
- ``octoprint_home``         - home one or more axes
- ``octoprint_move``         - jog the print head relative to its position
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .app import mcp
from .config import get_octoprint_api_key, get_octoprint_url

REQUEST_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 600.0

# Local files acceptable to upload as printer instructions.
GCODE_EXTENSIONS = {".gcode", ".gco", ".g"}

# Safety ceilings for setpoints, so a typo can't command a wild temperature.
# Firmware enforces its own limits too; these just catch obvious mistakes.
MAX_TOOL_TEMP = 300
MAX_BED_TEMP = 140


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


class Heater(str, Enum):
    """Which heater to target."""

    TOOL = "tool"
    BED = "bed"


class JobAction(str, Enum):
    """Lifecycle commands for the active print job."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


class ConnectAction(str, Enum):
    """Open or close the printer's serial connection."""

    CONNECT = "connect"
    DISCONNECT = "disconnect"


class _MissingConfigError(RuntimeError):
    """Raised when the OctoPrint URL or API key is not configured."""


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _config() -> tuple[str, str]:
    """Return (base_url, api_key) or raise a helpful _MissingConfigError."""
    url = get_octoprint_url()
    key = get_octoprint_api_key()
    if not url or not key:
        missing = " and ".join(
            n for n, v in (("OCTOPRINT_URL", url), ("OCTOPRINT_API_KEY", key)) if not v
        )
        raise _MissingConfigError(
            f"{missing} not set. Point PrintMCP at your printer's OctoPrint: set "
            "OCTOPRINT_URL (e.g. http://octopi.local or http://<ip>:<port>) and "
            "OCTOPRINT_API_KEY (OctoPrint > Settings > API, or a per-user app key) "
            "as environment variables or in the project .env file."
        )
    return url, key


def _headers() -> dict[str, str]:
    _, key = _config()
    return {"X-Api-Key": key}


async def _request(
    method: str,
    path: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    **kwargs: Any,
) -> httpx.Response:
    """Send an authenticated request to the OctoPrint API and raise on error."""
    # Read config once so the URL and key can never come from different reads
    # (e.g. if the environment changed mid-request).
    base, key = _config()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method,
            f"{base}/{path.lstrip('/')}",
            headers={"X-Api-Key": key},
            **kwargs,
        )
        resp.raise_for_status()
        return resp


async def _get_json(path: str) -> Any:
    return (await _request("GET", path)).json()


def _handle_error(e: Exception) -> str:
    """Map exceptions to concise, actionable error strings (never leaks the key)."""
    if isinstance(e, _MissingConfigError):
        return f"Error: {e}"
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        mapping = {
            400: "Bad request (400): OctoPrint rejected the command parameters.",
            401: "Authentication failed (401): OCTOPRINT_API_KEY is missing or invalid.",
            403: "Permission denied (403): this API key lacks rights for that action.",
            404: "Not found (404): check the file path or that the endpoint exists.",
            409: (
                "Conflict (409): the printer is not in a state for that command "
                "(often: not connected/operational, or no active job). "
                "Check octoprint_get_status; connect with octoprint_connect."
            ),
            415: "Unsupported media (415): that file type isn't accepted.",
        }
        return "Error: " + mapping.get(
            code, f"OctoPrint request failed with status {code}."
        )
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        url = get_octoprint_url() or "the configured URL"
        return (
            f"Error: Could not reach OctoPrint at {url}. Is OctoPrint running and "
            "the URL correct/reachable from this machine?"
        )
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request to OctoPrint timed out. Please try again."
    if isinstance(e, httpx.HTTPError):
        return f"Error: Network error contacting OctoPrint ({type(e).__name__})."
    return f"Error: Unexpected {type(e).__name__}: {e}"


# OctoPrint state flags that mean the printer is NOT free to accept a new print
# even though ``operational`` may still be true (paused mid-job, erroring, or
# busy finishing/cancelling). Gating on these prevents reporting a busy or
# faulted printer as "ready to print".
_NOT_READY_FLAGS = (
    "printing",
    "paused",
    "pausing",
    "resuming",
    "cancelling",
    "finishing",
    "error",
    "closedOrError",
)


def _is_ready(flags: dict[str, Any]) -> bool:
    """True only if the printer is operational and not busy/paused/errored."""
    if not isinstance(flags, dict) or not flags.get("operational"):
        return False
    return not any(flags.get(f) for f in _NOT_READY_FLAGS)


def _fmt_temps(temps: dict[str, Any]) -> list[str]:
    """Render the ``temperature`` block of /api/printer as human lines."""
    lines: list[str] = []
    for name in sorted(temps):
        t = temps[name]
        if not isinstance(t, dict):
            continue
        actual = t.get("actual")
        target = t.get("target")
        label = "Bed" if name == "bed" else name
        lines.append(f"- {label}: {actual}°C -> {target}°C target")
    return lines


def _confirm_required(action: str, detail: str, fmt: ResponseFormat) -> str:
    """Build the dry-run response for an unconfirmed physical action."""
    if fmt == ResponseFormat.JSON:
        return json.dumps(
            {
                "dry_run": True,
                "action": action,
                "detail": detail,
                "message": "No command was sent. Re-run with confirm=true to actuate the printer.",
            },
            indent=2,
        )
    return (
        f"Safety check - nothing was sent to the printer.\n\n"
        f"This would {detail}.\n\n"
        f"Re-run with confirm=true to actually {action} the physical machine."
    )


# --------------------------------------------------------------------------- #
# Tool: status
# --------------------------------------------------------------------------- #
class StatusInput(BaseModel):
    """Input for ``octoprint_get_status``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="octoprint_get_status",
    annotations={
        "title": "Get OctoPrint Printer Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def octoprint_get_status(params: StatusInput) -> str:
    """Report the printer's connection state, operational state, and temperatures.

    Use this first to see whether the printer is connected and ready before
    uploading or printing. Reads OctoPrint's version, connection, and printer
    endpoints. If the printer is not connected the temperature section is
    omitted (and you can bring it online with ``octoprint_connect``).

    Args:
        params (StatusInput): Validated input containing:
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Markdown summary, or JSON of the form:
        {
          "server": {"version": str|null, "api": str|null},
          "connection": {"state": str|null, "port": str|null, "baudrate": int|null},
          "printer_state": str|null,
          "ready": bool,
          "temperatures": {"<name>": {"actual": float|null, "target": float|null}}
        }
        On failure: "Error: <reason>".
    """
    try:
        version: dict[str, Any] = {}
        try:
            version = await _get_json("api/version")
        except Exception:  # noqa: BLE001 - version is informational only
            pass

        conn = await _get_json("api/connection")
        current = conn.get("current", {}) if isinstance(conn, dict) else {}

        printer_state: str | None = None
        ready = False
        temps: dict[str, Any] = {}
        try:
            printer = await _get_json("api/printer")
            state = printer.get("state", {}) if isinstance(printer, dict) else {}
            printer_state = state.get("text") if isinstance(state, dict) else None
            flags = state.get("flags", {}) if isinstance(state, dict) else {}
            ready = _is_ready(flags)
            temps = printer.get("temperature", {}) if isinstance(printer, dict) else {}
        except httpx.HTTPStatusError as inner:
            if inner.response.status_code != 409:
                raise
            # 409 == printer not operational; connection state still tells the story.
            printer_state = current.get("state") if isinstance(current, dict) else None

        result = {
            "server": {
                "version": version.get("server") if isinstance(version, dict) else None,
                "api": version.get("api") if isinstance(version, dict) else None,
            },
            "connection": {
                "state": current.get("state"),
                "port": current.get("port"),
                "baudrate": current.get("baudrate"),
            },
            "printer_state": printer_state,
            "ready": ready,
            "temperatures": {
                name: {"actual": t.get("actual"), "target": t.get("target")}
                for name, t in temps.items()
                if isinstance(t, dict)
            },
        }

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(result, indent=2)

        lines = ["# Printer status", ""]
        if result["server"]["version"]:
            lines.append(
                f"- OctoPrint: {result['server']['version']} (API {result['server']['api']})"
            )
        lines.append(f"- Connection: {result['connection']['state'] or 'unknown'}")
        if result["connection"]["port"]:
            lines.append(
                f"- Port: {result['connection']['port']} @ {result['connection']['baudrate']} baud"
            )
        lines.append(f"- Printer state: {printer_state or 'unknown'}")
        lines.append(f"- Ready to print: {'yes' if ready else 'no'}")
        temp_lines = _fmt_temps(temps)
        if temp_lines:
            lines.extend(["", "## Temperatures", *temp_lines])
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: list files
# --------------------------------------------------------------------------- #
class ListFilesInput(BaseModel):
    """Input for ``octoprint_list_files``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    limit: int = Field(
        default=50,
        description="Maximum number of files to return (1-200).",
        ge=1,
        le=200,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


def _flatten_files(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk OctoPrint's (possibly nested) file listing into a flat list."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "folder":
            children = entry.get("children")
            if isinstance(children, list):
                out.extend(_flatten_files(children))
            continue
        out.append(entry)
    return out


@mcp.tool(
    name="octoprint_list_files",
    annotations={
        "title": "List G-code Files on OctoPrint",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def octoprint_list_files(params: ListFilesInput) -> str:
    """List the G-code files stored on the OctoPrint server (local storage).

    Use this to find the server-side path to pass to ``octoprint_start_print``,
    or to confirm an upload landed. Folders are flattened; each file reports the
    ``path`` you use to select/print it.

    Args:
        params (ListFilesInput): Validated input containing:
            - limit (int): max files to return, 1-200 (default 50).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Markdown list, or JSON of the form:
        {
          "count": int,
          "files": [{"name": str, "path": str, "size_bytes": int|null,
                     "date": int|null, "estimated_print_time_s": float|null}]
        }
        On failure: "Error: <reason>"; or "No G-code files found on the server."
    """
    try:
        data = await _get_json("api/files/local?recursive=true")
        raw = data.get("files", []) if isinstance(data, dict) else []
        files = _flatten_files(raw if isinstance(raw, list) else [])

        records: list[dict[str, Any]] = []
        for f in files:
            gcode = (f.get("gcodeAnalysis") or {}) if isinstance(f, dict) else {}
            est = gcode.get("estimatedPrintTime") if isinstance(gcode, dict) else None
            records.append(
                {
                    "name": f.get("name"),
                    "path": f.get("path"),
                    "size_bytes": f.get("size"),
                    "date": f.get("date"),
                    "estimated_print_time_s": est,
                }
            )
        records.sort(key=lambda r: r.get("date") or 0, reverse=True)
        records = records[: params.limit]

        if not records:
            return "No G-code files found on the server. Upload one with octoprint_upload_file."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"count": len(records), "files": records}, indent=2)

        lines = [f"# G-code files on the server ({len(records)})", ""]
        for r in records:
            size = (
                f"{r['size_bytes']} bytes"
                if r.get("size_bytes") is not None
                else "size unknown"
            )
            lines.append(f"## {r['name']}")
            lines.append(f"- path: `{r['path']}`")
            lines.append(f"- {size}")
            if r.get("estimated_print_time_s") is not None:
                lines.append(f"- est. print time: {int(r['estimated_print_time_s'])} s")
            lines.append(
                f'- Print: `octoprint_start_print(path="{r["path"]}", confirm=true)`'
            )
            lines.append("")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: job status
# --------------------------------------------------------------------------- #
class JobStatusInput(BaseModel):
    """Input for ``octoprint_get_job``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


def _fmt_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


@mcp.tool(
    name="octoprint_get_job",
    annotations={
        "title": "Get OctoPrint Job Progress",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def octoprint_get_job(params: JobStatusInput) -> str:
    """Report the current print job and its progress.

    Use this to monitor a running print: which file, percent complete, elapsed
    time, and estimated time remaining.

    Args:
        params (JobStatusInput): Validated input containing:
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Markdown summary, or JSON of the form:
        {
          "state": str|null,
          "file": str|null,
          "completion_percent": float|null,
          "print_time_s": int|null,
          "print_time_left_s": int|null
        }
        On failure: "Error: <reason>".
    """
    try:
        data = await _get_json("api/job")
        job = data.get("job", {}) if isinstance(data, dict) else {}
        progress = data.get("progress", {}) if isinstance(data, dict) else {}
        file_info = (job.get("file") or {}) if isinstance(job, dict) else {}

        completion = progress.get("completion") if isinstance(progress, dict) else None
        result = {
            "state": data.get("state") if isinstance(data, dict) else None,
            "file": file_info.get("name") if isinstance(file_info, dict) else None,
            "completion_percent": round(completion, 1)
            if isinstance(completion, (int, float))
            else None,
            "print_time_s": progress.get("printTime")
            if isinstance(progress, dict)
            else None,
            "print_time_left_s": progress.get("printTimeLeft")
            if isinstance(progress, dict)
            else None,
        }

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(result, indent=2)

        lines = ["# Current job", ""]
        lines.append(f"- State: {result['state'] or 'unknown'}")
        lines.append(f"- File: {result['file'] or 'none selected'}")
        if result["completion_percent"] is not None:
            lines.append(f"- Progress: {result['completion_percent']}%")
        elapsed = _fmt_duration(result["print_time_s"])
        if elapsed:
            lines.append(f"- Elapsed: {elapsed}")
        left = _fmt_duration(result["print_time_left_s"])
        if left:
            lines.append(f"- Remaining (est.): {left}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: connect / disconnect
# --------------------------------------------------------------------------- #
class ConnectInput(BaseModel):
    """Input for ``octoprint_connect``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    action: ConnectAction = Field(
        default=ConnectAction.CONNECT,
        description="'connect' to open the printer's serial connection, 'disconnect' to close it.",
    )
    port: str | None = Field(
        default=None,
        description="Serial port to connect (e.g. '/dev/ttyUSB0' or 'COM3'). Omit to let OctoPrint auto-detect.",
        max_length=128,
    )
    baudrate: int | None = Field(
        default=None,
        description="Baud rate (e.g. 115250). Omit to auto-detect.",
        ge=1200,
        le=1000000,
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to actually open/close the connection. False (default) does a dry run.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="octoprint_connect",
    annotations={
        "title": "Connect/Disconnect the Printer",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def octoprint_connect(params: ConnectInput) -> str:
    """Open or close OctoPrint's serial connection to the printer.

    The printer must be *connected* (Operational) before it can print or accept
    temperature/movement commands. Call with action='connect' to bring it online
    (optionally specifying port/baudrate), or action='disconnect' to release it.
    Requires ``confirm=true`` to act.

    Args:
        params (ConnectInput): Validated input containing:
            - action (str): 'connect' or 'disconnect' (default 'connect').
            - port (str|None): serial port, or None to auto-detect.
            - baudrate (int|None): baud rate, or None to auto-detect.
            - confirm (bool): must be true to actuate (default false = dry run).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Confirmation string (or dry-run preview), else "Error: <reason>".
    """
    try:
        _config()  # fail fast with a helpful message if unconfigured
        verb = params.action.value
        if not params.confirm:
            target = f" on port {params.port}" if params.port else ""
            return _confirm_required(
                verb, f"{verb} the printer{target}", params.response_format
            )

        if params.action == ConnectAction.CONNECT:
            body: dict[str, Any] = {"command": "connect"}
            if params.port:
                body["port"] = params.port
            if params.baudrate:
                body["baudrate"] = params.baudrate
        else:
            body = {"command": "disconnect"}
        await _request("POST", "api/connection", json=body)

        msg = f"Sent '{verb}' to the printer."
        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"ok": True, "action": verb}, indent=2)
        return msg + " Check octoprint_get_status to confirm the new state."
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: upload a local G-code file
# --------------------------------------------------------------------------- #
class UploadInput(BaseModel):
    """Input for ``octoprint_upload_file``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    gcode_path: str = Field(
        ...,
        description="Absolute path to a local .gcode/.gco/.g file (e.g. output of cura_slice_model).",
        min_length=1,
    )
    dest_path: str | None = Field(
        default=None,
        description="Server-side subfolder to upload into. Defaults to the root of local storage.",
        max_length=256,
    )
    select: bool = Field(
        default=False,
        description="Select the file on the printer after upload (does not start printing).",
    )
    print_after_upload: bool = Field(
        default=False,
        description="Start printing immediately after upload. Requires confirm=true (physical action).",
    )
    confirm: bool = Field(
        default=False,
        description="Required only when print_after_upload is true. Uploading alone does not need confirm.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="octoprint_upload_file",
    annotations={
        "title": "Upload G-code to OctoPrint",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def octoprint_upload_file(params: UploadInput) -> str:
    """Upload a local G-code file to the OctoPrint server.

    Uploading itself does not move the machine, so it does not need confirm. If
    ``print_after_upload`` is set, that *does* start a physical print and so
    requires ``confirm=true``. After uploading you can print later with
    ``octoprint_start_print`` using the returned server path.

    Args:
        params (UploadInput): Validated input containing:
            - gcode_path (str): local .gcode/.gco/.g file to upload.
            - dest_path (str|None): server subfolder (default: storage root).
            - select (bool): select the file after upload (default false).
            - print_after_upload (bool): start printing right away (default false).
            - confirm (bool): required when print_after_upload is true.
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Markdown/JSON summary with the server-side path, or "Error: <reason>".
    """
    try:
        _config()
        local = Path(params.gcode_path).expanduser()
        if not local.is_file():
            return f"Error: G-code file not found: {params.gcode_path}"
        if local.suffix.lower() not in GCODE_EXTENSIONS:
            return (
                f"Error: '{local.suffix or 'no extension'}' is not G-code. "
                f"Expected one of: {', '.join(sorted(GCODE_EXTENSIONS))}."
            )

        if params.print_after_upload and not params.confirm:
            return _confirm_required(
                "print",
                f"upload {local.name} and immediately start printing it",
                params.response_format,
            )

        data: dict[str, str] = {}
        if params.select or params.print_after_upload:
            data["select"] = "true"
        if params.print_after_upload:
            data["print"] = "true"
        if params.dest_path:
            data["path"] = params.dest_path

        with open(local, "rb") as fh:
            files = {"file": (local.name, fh, "application/octet-stream")}
            resp = await _request(
                "POST",
                "api/files/local",
                timeout=UPLOAD_TIMEOUT,
                data=data,
                files=files,
            )
        body = resp.json() if resp.content else {}
        dest = body.get("files", {}).get("local", {}) if isinstance(body, dict) else {}
        server_path = dest.get("path") or local.name

        result = {
            "uploaded": local.name,
            "server_path": server_path,
            "selected": bool(params.select or params.print_after_upload),
            "printing": bool(params.print_after_upload),
        }
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(result, indent=2)

        lines = [f"# Uploaded {local.name}", "", f"- Server path: `{server_path}`"]
        if params.print_after_upload:
            lines.append("- Printing: started")
        elif params.select:
            lines.append("- Selected on the printer (not yet printing)")
        else:
            lines.append(
                f'- Next: `octoprint_start_print(path="{server_path}", confirm=true)`'
            )
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: start a print
# --------------------------------------------------------------------------- #
class StartPrintInput(BaseModel):
    """Input for ``octoprint_start_print``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    path: str = Field(
        ...,
        description="Server-side path of the G-code to print (from octoprint_list_files), e.g. 'cup.gcode'.",
        min_length=1,
        max_length=512,
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to start the print. False (default) does a dry run.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="octoprint_start_print",
    annotations={
        "title": "Start a Print on OctoPrint",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def octoprint_start_print(params: StartPrintInput) -> str:
    """Select a G-code file already on the server and start printing it.

    This physically starts the printer (heaters and motors), so it requires
    ``confirm=true``. The printer must be connected/operational first
    (``octoprint_get_status`` / ``octoprint_connect``). Use a ``path`` from
    ``octoprint_list_files``.

    Args:
        params (StartPrintInput): Validated input containing:
            - path (str): server-side G-code path to print.
            - confirm (bool): must be true to actuate (default false = dry run).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Confirmation string (or dry-run preview), else "Error: <reason>".
    """
    try:
        _config()
        if not params.confirm:
            return _confirm_required(
                "start the print",
                f"select '{params.path}' and begin printing it on the physical machine",
                params.response_format,
            )
        # Selecting with print=true both selects and starts the job.
        await _request(
            "POST",
            f"api/files/local/{quote(params.path, safe='/')}",
            json={"command": "select", "print": True},
        )
        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"ok": True, "printing": params.path}, indent=2)
        return f"Started printing '{params.path}'. Monitor it with octoprint_get_job."
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: control the active job (pause / resume / cancel)
# --------------------------------------------------------------------------- #
class ControlJobInput(BaseModel):
    """Input for ``octoprint_control_job``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    action: JobAction = Field(
        ..., description="'pause', 'resume', or 'cancel' the active print job."
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to act. False (default) does a dry run.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="octoprint_control_job",
    annotations={
        "title": "Pause/Resume/Cancel the Print Job",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def octoprint_control_job(params: ControlJobInput) -> str:
    """Pause, resume, or cancel the currently running print job.

    Cancelling abandons the print (the partial object is wasted), so this is a
    consequential action and requires ``confirm=true``.

    Args:
        params (ControlJobInput): Validated input containing:
            - action (str): 'pause', 'resume', or 'cancel'.
            - confirm (bool): must be true to actuate (default false = dry run).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Confirmation string (or dry-run preview), else "Error: <reason>".
    """
    try:
        _config()
        action = params.action.value
        if not params.confirm:
            detail = {
                "pause": "pause the running print",
                "resume": "resume the paused print",
                "cancel": "cancel and abandon the running print (the partial object is wasted)",
            }[action]
            return _confirm_required(action, detail, params.response_format)

        if params.action == JobAction.CANCEL:
            body = {"command": "cancel"}
        else:
            body = {"command": "pause", "action": action}
        await _request("POST", "api/job", json=body)

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"ok": True, "action": action}, indent=2)
        return f"Sent '{action}' to the active job."
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: set a temperature
# --------------------------------------------------------------------------- #
class SetTemperatureInput(BaseModel):
    """Input for ``octoprint_set_temperature``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    heater: Heater = Field(
        ..., description="Which heater to set: 'tool' (nozzle) or 'bed'."
    )
    target: int = Field(
        ...,
        description="Target temperature in degrees C. 0 turns the heater off. Bed max ~140, tool max ~300.",
        ge=0,
        le=MAX_TOOL_TEMP,
    )
    tool_index: int = Field(
        default=0,
        description="Which tool/extruder when heater='tool' (default 0 -> tool0).",
        ge=0,
        le=9,
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to actually set the temperature. False (default) does a dry run.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )

    @model_validator(mode="after")
    def _bed_within_limit(self) -> SetTemperatureInput:
        if self.heater == Heater.BED and self.target > MAX_BED_TEMP:
            raise ValueError(f"bed target must be <= {MAX_BED_TEMP}°C")
        return self


@mcp.tool(
    name="octoprint_set_temperature",
    annotations={
        "title": "Set Tool/Bed Temperature",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def octoprint_set_temperature(params: SetTemperatureInput) -> str:
    """Set a target temperature for the nozzle (tool) or the heated bed.

    This drives a physical heater, so it requires ``confirm=true``. Setting 0
    turns the heater off. The printer must be connected/operational.

    Args:
        params (SetTemperatureInput): Validated input containing:
            - heater (str): 'tool' or 'bed'.
            - target (int): degrees C (0 = off). Bed capped at ~140, tool ~300.
            - tool_index (int): extruder index when heater='tool' (default 0).
            - confirm (bool): must be true to actuate (default false = dry run).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Confirmation string (or dry-run preview), else "Error: <reason>".
    """
    try:
        _config()
        if params.heater == Heater.TOOL:
            who = f"tool{params.tool_index}"
        else:
            who = "bed"
        if not params.confirm:
            return _confirm_required(
                "set the temperature",
                f"set the {who} heater to {params.target}°C",
                params.response_format,
            )

        if params.heater == Heater.TOOL:
            path = "api/printer/tool"
            body: dict[str, Any] = {
                "command": "target",
                "targets": {f"tool{params.tool_index}": params.target},
            }
        else:
            path = "api/printer/bed"
            body = {"command": "target", "target": params.target}
        await _request("POST", path, json=body)

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {"ok": True, "heater": who, "target": params.target}, indent=2
            )
        return f"Set {who} target to {params.target}°C."
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: home axes
# --------------------------------------------------------------------------- #
class HomeInput(BaseModel):
    """Input for ``octoprint_home``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    axes: list[str] = Field(
        default_factory=lambda: ["x", "y", "z"],
        description="Axes to home, any of 'x','y','z' (default all three).",
        min_length=1,
        max_length=3,
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to move the head. False (default) does a dry run.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )

    @field_validator("axes")
    @classmethod
    def _valid_axes(cls, v: list[str]) -> list[str]:
        norm = []
        for axis in v:
            a = axis.strip().lower()
            if a not in {"x", "y", "z"}:
                raise ValueError("axes may only contain 'x', 'y', 'z'")
            if a not in norm:
                norm.append(a)
        return norm


@mcp.tool(
    name="octoprint_home",
    annotations={
        "title": "Home Printer Axes",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def octoprint_home(params: HomeInput) -> str:
    """Home one or more printer axes (move them to their endstops).

    This moves the print head/bed, so it requires ``confirm=true``. The printer
    must be connected/operational and not mid-print.

    Args:
        params (HomeInput): Validated input containing:
            - axes (list[str]): subset of 'x','y','z' (default all).
            - confirm (bool): must be true to actuate (default false = dry run).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Confirmation string (or dry-run preview), else "Error: <reason>".
    """
    try:
        _config()
        axes_str = ", ".join(params.axes)
        if not params.confirm:
            return _confirm_required(
                "home the axes",
                f"home the {axes_str} axis/axes",
                params.response_format,
            )
        await _request(
            "POST",
            "api/printer/printhead",
            json={"command": "home", "axes": params.axes},
        )
        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"ok": True, "homed": params.axes}, indent=2)
        return f"Homed: {axes_str}."
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: jog / move the print head
# --------------------------------------------------------------------------- #
class MoveInput(BaseModel):
    """Input for ``octoprint_move``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    x: float | None = Field(
        default=None,
        description="Relative X move in mm (+/-). Omit for no X move.",
        ge=-500,
        le=500,
    )
    y: float | None = Field(
        default=None,
        description="Relative Y move in mm (+/-). Omit for no Y move.",
        ge=-500,
        le=500,
    )
    z: float | None = Field(
        default=None,
        description="Relative Z move in mm (+/-). Omit for no Z move.",
        ge=-500,
        le=500,
    )
    speed: int | None = Field(
        default=None,
        description="Feedrate in mm/min. Omit to use OctoPrint's default.",
        ge=1,
        le=12000,
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to move the head. False (default) does a dry run.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )

    @model_validator(mode="after")
    def _at_least_one_axis(self) -> MoveInput:
        if self.x is None and self.y is None and self.z is None:
            raise ValueError("specify at least one of x, y, z to move")
        return self


@mcp.tool(
    name="octoprint_move",
    annotations={
        "title": "Jog the Print Head",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def octoprint_move(params: MoveInput) -> str:
    """Jog the print head by a relative offset on one or more axes.

    This moves the machine, so it requires ``confirm=true``. Offsets are relative
    to the current position (in mm). The printer must be connected/operational
    and not mid-print.

    Args:
        params (MoveInput): Validated input containing:
            - x, y, z (float|None): relative move per axis in mm (at least one).
            - speed (int|None): feedrate mm/min (default: OctoPrint's).
            - confirm (bool): must be true to actuate (default false = dry run).
            - response_format (str): 'markdown' or 'json'.

    Returns:
        str: Confirmation string (or dry-run preview), else "Error: <reason>".
    """
    try:
        _config()
        moves = {
            ax: getattr(params, ax)
            for ax in ("x", "y", "z")
            if getattr(params, ax) is not None
        }
        desc = ", ".join(f"{ax.upper()}{v:+g}mm" for ax, v in moves.items())
        if not params.confirm:
            return _confirm_required(
                "jog the head", f"move the head {desc}", params.response_format
            )
        body: dict[str, Any] = {"command": "jog", "absolute": False, **moves}
        if params.speed is not None:
            body["speed"] = params.speed
        await _request("POST", "api/printer/printhead", json=body)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"ok": True, "moved": moves}, indent=2)
        return f"Jogged the head: {desc}."
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)
