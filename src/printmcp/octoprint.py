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

from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
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


def _markdown(text: str, structured: dict[str, Any]) -> CallToolResult:
    """Build a CallToolResult carrying markdown text + matching structuredContent."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured,
    )


# --------------------------------------------------------------------------- #
# Structured output models (MCP 2025-06-18 spec)
# --------------------------------------------------------------------------- #
class ServerInfo(BaseModel):
    """OctoPrint server version information."""

    model_config = ConfigDict(extra="ignore")

    version: str | None = None
    api: str | None = None


class ConnectionInfo(BaseModel):
    """Current serial-connection state."""

    model_config = ConfigDict(extra="ignore")

    state: str | None = None
    port: str | None = None
    baudrate: int | None = None


class TemperatureReading(BaseModel):
    """One heater's actual / target temperature."""

    model_config = ConfigDict(extra="ignore")

    actual: float | None = None
    target: float | None = None


class StatusResult(BaseModel):
    """Structured result of ``octoprint_get_status``."""

    model_config = ConfigDict(extra="ignore")

    server: ServerInfo = Field(default_factory=ServerInfo)
    connection: ConnectionInfo = Field(default_factory=ConnectionInfo)
    printer_state: str | None = None
    ready: bool = False
    temperatures: dict[str, TemperatureReading] = Field(default_factory=dict)


class FileEntry(BaseModel):
    """One G-code file stored on the OctoPrint server."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    path: str | None = None
    size_bytes: int | None = None
    date: int | None = None
    estimated_print_time_s: float | None = None


class FileListResult(BaseModel):
    """Structured result of ``octoprint_list_files``."""

    model_config = ConfigDict(extra="ignore")

    count: int
    files: list[FileEntry] = []


class JobResult(BaseModel):
    """Structured result of ``octoprint_get_job``."""

    model_config = ConfigDict(extra="ignore")

    state: str | None = None
    file: str | None = None
    completion_percent: float | None = None
    print_time_s: float | None = None
    print_time_left_s: float | None = None


class ConnectResult(BaseModel):
    """Structured result of ``octoprint_connect``."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    action: str
    dry_run: bool = False
    detail: str | None = None


class UploadResult(BaseModel):
    """Structured result of ``octoprint_upload_file``."""

    model_config = ConfigDict(extra="ignore")

    uploaded: str
    server_path: str
    selected: bool = False
    printing: bool = False
    dry_run: bool = False
    detail: str | None = None


class StartPrintResult(BaseModel):
    """Structured result of ``octoprint_start_print``."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    printing: str
    dry_run: bool = False
    detail: str | None = None


class ControlJobResult(BaseModel):
    """Structured result of ``octoprint_control_job``."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    action: str
    dry_run: bool = False
    detail: str | None = None


class TemperatureResult(BaseModel):
    """Structured result of ``octoprint_set_temperature``."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    heater: str
    target: int
    dry_run: bool = False
    detail: str | None = None


class HomeResult(BaseModel):
    """Structured result of ``octoprint_home``."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    homed: list[str]
    dry_run: bool = False
    detail: str | None = None


class MoveResult(BaseModel):
    """Structured result of ``octoprint_move``."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    moved: dict[str, float]
    dry_run: bool = False
    detail: str | None = None


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
async def octoprint_get_status(
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> StatusResult:
    """Report the printer's connection state, operational state, and temperatures.

    Use this first to see whether the printer is connected and ready before
    uploading or printing. Reads OctoPrint's version, connection, and printer
    endpoints. If the printer is not connected the temperature section is
    omitted (and you can bring it online with ``octoprint_connect``).

    Args:
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        StatusResult with fields:
        {
          "server": {"version": str|null, "api": str|null},
          "connection": {"state": str|null, "port": str|null, "baudrate": int|null},
          "printer_state": str|null,
          "ready": bool,
          "temperatures": {"<name>": {"actual": float|null, "target": float|null}}
        }
        On failure: a ToolError is raised.
    """
    params = StatusInput(response_format=response_format)
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

        server = ServerInfo(
            version=version.get("server") if isinstance(version, dict) else None,
            api=version.get("api") if isinstance(version, dict) else None,
        )
        connection = ConnectionInfo(
            state=current.get("state"),
            port=current.get("port"),
            baudrate=current.get("baudrate"),
        )
        temperatures = {
            name: TemperatureReading(actual=t.get("actual"), target=t.get("target"))
            for name, t in temps.items()
            if isinstance(t, dict)
        }
        result = StatusResult(
            server=server,
            connection=connection,
            printer_state=printer_state,
            ready=ready,
            temperatures=temperatures,
        )

        if params.response_format == ResponseFormat.JSON:
            return result

        lines = ["# Printer status", ""]
        if result.server.version:
            lines.append(
                f"- OctoPrint: {result.server.version} (API {result.server.api})"
            )
        lines.append(f"- Connection: {result.connection.state or 'unknown'}")
        if result.connection.port:
            lines.append(
                f"- Port: {result.connection.port} @ {result.connection.baudrate} baud"
            )
        lines.append(f"- Printer state: {result.printer_state or 'unknown'}")
        lines.append(f"- Ready to print: {'yes' if result.ready else 'no'}")
        temp_lines = _fmt_temps(temps)
        if temp_lines:
            lines.extend(["", "## Temperatures", *temp_lines])
        return _markdown("\n".join(lines), result.model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_list_files(
    limit: int = 50,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> FileListResult:
    """List the G-code files stored on the OctoPrint server (local storage).

    Use this to find the server-side path to pass to ``octoprint_start_print``,
    or to confirm an upload landed. Folders are flattened; each file reports the
    ``path`` you use to select/print it.

    Args:
        limit: Maximum number of files to return (1-200, default 50).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        FileListResult with fields:
        {
          "count": int,
          "files": [{"name": str, "path": str, "size_bytes": int|null,
                     "date": int|null, "estimated_print_time_s": float|null}]
        }
        On failure: a ToolError is raised.
    """
    params = ListFilesInput(limit=limit, response_format=response_format)
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
            raise ToolError(
                "No G-code files found on the server. Upload one with octoprint_upload_file."
            )

        result = FileListResult(
            count=len(records),
            files=[FileEntry(**r) for r in records],
        )

        if params.response_format == ResponseFormat.JSON:
            return result

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
        return _markdown("\n".join(lines), result.model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_get_job(
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> JobResult:
    """Report the current print job and its progress.

    Use this to monitor a running print: which file, percent complete, elapsed
    time, and estimated time remaining.

    Args:
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        JobResult with fields:
        {
          "state": str|null,
          "file": str|null,
          "completion_percent": float|null,
          "print_time_s": float|null,
          "print_time_left_s": float|null
        }
        On failure: a ToolError is raised.
    """
    params = JobStatusInput(response_format=response_format)
    try:
        data = await _get_json("api/job")
        job = data.get("job", {}) if isinstance(data, dict) else {}
        progress = data.get("progress", {}) if isinstance(data, dict) else {}
        file_info = (job.get("file") or {}) if isinstance(job, dict) else {}

        completion = progress.get("completion") if isinstance(progress, dict) else None
        result = JobResult(
            state=data.get("state") if isinstance(data, dict) else None,
            file=file_info.get("name") if isinstance(file_info, dict) else None,
            completion_percent=round(completion, 1)
            if isinstance(completion, (int, float))
            else None,
            print_time_s=progress.get("printTime")
            if isinstance(progress, dict)
            else None,
            print_time_left_s=progress.get("printTimeLeft")
            if isinstance(progress, dict)
            else None,
        )

        if params.response_format == ResponseFormat.JSON:
            return result

        lines = ["# Current job", ""]
        lines.append(f"- State: {result.state or 'unknown'}")
        lines.append(f"- File: {result.file or 'none selected'}")
        if result.completion_percent is not None:
            lines.append(f"- Progress: {result.completion_percent}%")
        elapsed = _fmt_duration(result.print_time_s)
        if elapsed:
            lines.append(f"- Elapsed: {elapsed}")
        left = _fmt_duration(result.print_time_left_s)
        if left:
            lines.append(f"- Remaining (est.): {left}")
        return _markdown("\n".join(lines), result.model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_connect(
    action: ConnectAction = ConnectAction.CONNECT,
    port: str | None = None,
    baudrate: int | None = None,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> ConnectResult:
    """Open or close OctoPrint's serial connection to the printer.

    The printer must be *connected* (Operational) before it can print or accept
    temperature/movement commands. Call with action='connect' to bring it online
    (optionally specifying port/baudrate), or action='disconnect' to release it.
    Requires ``confirm=true`` to act.

    Args:
        action: 'connect' or 'disconnect' (default 'connect').
        port: Serial port, or None to auto-detect.
        baudrate: Baud rate, or None to auto-detect.
        confirm: Must be true to actuate (default false = dry run).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        ConnectResult with ok, action, dry_run, detail. On failure: a
        ToolError is raised.
    """
    params = ConnectInput(
        action=action,
        port=port,
        baudrate=baudrate,
        confirm=confirm,
        response_format=response_format,
    )
    try:
        _config()  # fail fast with a helpful message if unconfigured
        verb = params.action.value
        if not params.confirm:
            target = f" on port {params.port}" if params.port else ""
            detail = f"{verb} the printer{target}"
            result = ConnectResult(action=verb, dry_run=True, detail=detail)
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                f"Re-run with confirm=true to actually {verb} the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))

        if params.action == ConnectAction.CONNECT:
            body: dict[str, Any] = {"command": "connect"}
            if params.port:
                body["port"] = params.port
            if params.baudrate:
                body["baudrate"] = params.baudrate
        else:
            body = {"command": "disconnect"}
        await _request("POST", "api/connection", json=body)

        result = ConnectResult(ok=True, action=verb)
        if params.response_format == ResponseFormat.JSON:
            return result
        return _markdown(
            f"Sent '{verb}' to the printer. Check octoprint_get_status to confirm the new state.",
            result.model_dump(mode="json"),
        )
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_upload_file(
    gcode_path: str,
    dest_path: str | None = None,
    select: bool = False,
    print_after_upload: bool = False,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> UploadResult:
    """Upload a local G-code file to the OctoPrint server.

    Uploading itself does not move the machine, so it does not need confirm. If
    ``print_after_upload`` is set, that *does* start a physical print and so
    requires ``confirm=true``. After uploading you can print later with
    ``octoprint_start_print`` using the returned server path.

    Args:
        gcode_path: Local .gcode/.gco/.g file to upload.
        dest_path: Server subfolder (default: storage root).
        select: Select the file after upload (default false).
        print_after_upload: Start printing right away (default false).
        confirm: Required when print_after_upload is true.
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        UploadResult with uploaded, server_path, selected, printing, dry_run,
        detail. On failure: a ToolError is raised.
    """
    params = UploadInput(
        gcode_path=gcode_path,
        dest_path=dest_path,
        select=select,
        print_after_upload=print_after_upload,
        confirm=confirm,
        response_format=response_format,
    )
    try:
        _config()
        local = Path(params.gcode_path).expanduser()
        if not local.is_file():
            raise ToolError(f"Error: G-code file not found: {params.gcode_path}")
        if local.suffix.lower() not in GCODE_EXTENSIONS:
            raise ToolError(
                f"Error: '{local.suffix or 'no extension'}' is not G-code. "
                f"Expected one of: {', '.join(sorted(GCODE_EXTENSIONS))}."
            )

        if params.print_after_upload and not params.confirm:
            detail = f"upload {local.name} and immediately start printing it"
            result = UploadResult(
                uploaded=local.name,
                server_path=local.name,
                dry_run=True,
                detail=detail,
            )
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                "Re-run with confirm=true to actually print the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))

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

        result = UploadResult(
            uploaded=local.name,
            server_path=server_path,
            selected=bool(params.select or params.print_after_upload),
            printing=bool(params.print_after_upload),
        )
        if params.response_format == ResponseFormat.JSON:
            return result

        lines = [f"# Uploaded {local.name}", "", f"- Server path: `{server_path}`"]
        if params.print_after_upload:
            lines.append("- Printing: started")
        elif params.select:
            lines.append("- Selected on the printer (not yet printing)")
        else:
            lines.append(
                f'- Next: `octoprint_start_print(path="{server_path}", confirm=true)`'
            )
        return _markdown("\n".join(lines), result.model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_start_print(
    path: str,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> StartPrintResult:
    """Select a G-code file already on the server and start printing it.

    This physically starts the printer (heaters and motors), so it requires
    ``confirm=true``. The printer must be connected/operational first
    (``octoprint_get_status`` / ``octoprint_connect``). Use a ``path`` from
    ``octoprint_list_files``.

    Args:
        path: Server-side G-code path to print.
        confirm: Must be true to actuate (default false = dry run).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        StartPrintResult with ok, printing, dry_run, detail. On failure: a
        ToolError is raised.
    """
    params = StartPrintInput(
        path=path, confirm=confirm, response_format=response_format
    )
    try:
        _config()
        if not params.confirm:
            detail = (
                f"select '{params.path}' and begin printing it on the physical machine"
            )
            result = StartPrintResult(printing=params.path, dry_run=True, detail=detail)
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                "Re-run with confirm=true to actually start the print on the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))

        # Selecting with print=true both selects and starts the job.
        await _request(
            "POST",
            f"api/files/local/{quote(params.path, safe='/')}",
            json={"command": "select", "print": True},
        )
        result = StartPrintResult(ok=True, printing=params.path)
        if params.response_format == ResponseFormat.JSON:
            return result
        return _markdown(
            f"Started printing '{params.path}'. Monitor it with octoprint_get_job.",
            result.model_dump(mode="json"),
        )
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_control_job(
    action: JobAction,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> ControlJobResult:
    """Pause, resume, or cancel the currently running print job.

    Cancelling abandons the print (the partial object is wasted), so this is a
    consequential action and requires ``confirm=true``.

    Args:
        action: 'pause', 'resume', or 'cancel'.
        confirm: Must be true to actuate (default false = dry run).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        ControlJobResult with ok, action, dry_run, detail. On failure: a
        ToolError is raised.
    """
    params = ControlJobInput(
        action=action, confirm=confirm, response_format=response_format
    )
    try:
        _config()
        action_str = params.action.value
        if not params.confirm:
            detail = {
                "pause": "pause the running print",
                "resume": "resume the paused print",
                "cancel": "cancel and abandon the running print (the partial object is wasted)",
            }[action_str]
            result = ControlJobResult(action=action_str, dry_run=True, detail=detail)
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                f"Re-run with confirm=true to actually {action_str} the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))

        if params.action == JobAction.CANCEL:
            body = {"command": "cancel"}
        else:
            body = {"command": "pause", "action": action_str}
        await _request("POST", "api/job", json=body)

        result = ControlJobResult(ok=True, action=action_str)
        if params.response_format == ResponseFormat.JSON:
            return result
        return _markdown(
            f"Sent '{action_str}' to the active job.",
            result.model_dump(mode="json"),
        )
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_set_temperature(
    heater: Heater,
    target: int,
    tool_index: int = 0,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> TemperatureResult:
    """Set a target temperature for the nozzle (tool) or the heated bed.

    This drives a physical heater, so it requires ``confirm=true``. Setting 0
    turns the heater off. The printer must be connected/operational.

    Args:
        heater: 'tool' or 'bed'.
        target: Degrees C (0 = off). Bed capped at ~140, tool ~300.
        tool_index: Extruder index when heater='tool' (default 0).
        confirm: Must be true to actuate (default false = dry run).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        TemperatureResult with ok, heater, target, dry_run, detail. On failure:
        a ToolError is raised.
    """
    params = SetTemperatureInput(
        heater=heater,
        target=target,
        tool_index=tool_index,
        confirm=confirm,
        response_format=response_format,
    )
    try:
        _config()
        if params.heater == Heater.TOOL:
            who = f"tool{params.tool_index}"
        else:
            who = "bed"
        if not params.confirm:
            detail = f"set the {who} heater to {params.target}°C"
            result = TemperatureResult(
                dry_run=True, detail=detail, heater=who, target=params.target
            )
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                "Re-run with confirm=true to actually set the temperature on the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))

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

        result = TemperatureResult(ok=True, heater=who, target=params.target)
        if params.response_format == ResponseFormat.JSON:
            return result
        return _markdown(
            f"Set {who} target to {params.target}°C.",
            result.model_dump(mode="json"),
        )
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_home(
    axes: list[str] | None = None,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> HomeResult:
    """Home one or more printer axes (move them to their endstops).

    This moves the print head/bed, so it requires ``confirm=true``. The printer
    must be connected/operational and not mid-print.

    Args:
        axes: Subset of 'x','y','z' (default all three).
        confirm: Must be true to actuate (default false = dry run).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        HomeResult with ok, homed, dry_run, detail. On failure: a ToolError is
        raised.
    """
    params = HomeInput(
        axes=axes if axes is not None else ["x", "y", "z"],
        confirm=confirm,
        response_format=response_format,
    )
    try:
        _config()
        axes_str = ", ".join(params.axes)
        if not params.confirm:
            detail = f"home the {axes_str} axis/axes"
            result = HomeResult(dry_run=True, detail=detail, homed=params.axes)
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                "Re-run with confirm=true to actually home the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))
        await _request(
            "POST",
            "api/printer/printhead",
            json={"command": "home", "axes": params.axes},
        )
        result = HomeResult(ok=True, homed=params.axes)
        if params.response_format == ResponseFormat.JSON:
            return result
        return _markdown(f"Homed: {axes_str}.", result.model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e


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
async def octoprint_move(
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    speed: int | None = None,
    confirm: bool = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> MoveResult:
    """Jog the print head by a relative offset on one or more axes.

    This moves the machine, so it requires ``confirm=true``. Offsets are relative
    to the current position (in mm). The printer must be connected/operational
    and not mid-print.

    Args:
        x, y, z: Relative move per axis in mm (at least one).
        speed: Feedrate mm/min (default: OctoPrint's).
        confirm: Must be true to actuate (default false = dry run).
        response_format: "markdown" for human-readable output or "json" for structured data.

    Returns:
        MoveResult with ok, moved, dry_run, detail. On failure: a ToolError is
        raised.
    """
    params = MoveInput(
        x=x,
        y=y,
        z=z,
        speed=speed,
        confirm=confirm,
        response_format=response_format,
    )
    try:
        _config()
        moves = {
            ax: getattr(params, ax)
            for ax in ("x", "y", "z")
            if getattr(params, ax) is not None
        }
        desc = ", ".join(f"{ax.upper()}{v:+g}mm" for ax, v in moves.items())
        if not params.confirm:
            detail = f"move the head {desc}"
            result = MoveResult(dry_run=True, detail=detail, moved=moves)
            if params.response_format == ResponseFormat.JSON:
                return result
            md = (
                "Safety check - nothing was sent to the printer.\n\n"
                f"This would {detail}.\n\n"
                "Re-run with confirm=true to actually jog the physical machine."
            )
            return _markdown(md, result.model_dump(mode="json"))
        body: dict[str, Any] = {"command": "jog", "absolute": False, **moves}
        if params.speed is not None:
            body["speed"] = params.speed
        await _request("POST", "api/printer/printhead", json=body)
        result = MoveResult(ok=True, moved=moves)
        if params.response_format == ResponseFormat.JSON:
            return result
        return _markdown(f"Jogged the head: {desc}.", result.model_dump(mode="json"))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(_handle_error(e)) from e
