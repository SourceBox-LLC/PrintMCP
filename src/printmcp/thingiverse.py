#!/usr/bin/env python3
"""Thingiverse integration for PrintMCP (Level 1: search & download models).

Exposes three tools:
- ``thingiverse_search_models``  - keyword search for printable things
- ``thingiverse_get_model``      - details for one thing (license + file list)
- ``thingiverse_download_model`` - download a thing's files to the local disk
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .app import mcp
from .config import THINGIVERSE_API_BASE, get_download_dir, get_token

REQUEST_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = 300.0

# Extensions treated as printable 3D model / slicer-input files.
MODEL_FILE_EXTENSIONS = {".stl", ".obj", ".3mf", ".step", ".stp", ".amf", ".ply"}

_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_HTML_TAG = re.compile(r"<[^>]+>")


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


class _MissingTokenError(RuntimeError):
    """Raised when no Thingiverse token is configured."""


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _auth_headers() -> dict[str, str]:
    token = get_token()
    if not token:
        raise _MissingTokenError(
            "THINGIVERSE_TOKEN is not set. Create an app at "
            "https://www.thingiverse.com/apps/create and set THINGIVERSE_TOKEN "
            "(environment variable or .env file) to its App Token."
        )
    return {"Authorization": f"Bearer {token}"}


async def _api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET a Thingiverse REST endpoint and return decoded JSON."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{THINGIVERSE_API_BASE}/{path.lstrip('/')}",
            params=params,
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()


def _handle_error(e: Exception) -> str:
    """Map exceptions to concise, actionable error strings."""
    if isinstance(e, _MissingTokenError):
        return f"Error: {e}"
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        mapping = {
            401: "Authentication failed (401): THINGIVERSE_TOKEN is missing, invalid, or expired.",
            403: "Permission denied (403): your token may lack access to this resource.",
            404: "Not found (404): check that the thing ID is correct.",
            429: "Rate limited (429) by Thingiverse: wait before retrying.",
        }
        return "Error: " + mapping.get(
            code, f"Thingiverse API request failed with status {code}."
        )
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request to Thingiverse timed out. Please try again."
    if isinstance(e, httpx.HTTPError):
        return f"Error: Network error contacting Thingiverse ({type(e).__name__})."
    return f"Error: Unexpected {type(e).__name__}: {e}"


def _safe_filename(name: str) -> str:
    """Reduce an arbitrary string to a safe single path-component filename."""
    name = name.replace("\\", "/").split("/")[-1]
    name = _FILENAME_UNSAFE.sub("_", name).strip().strip(".")
    return name or "file"


def _clean_text(value: str | None, limit: int = 500) -> str:
    """Strip HTML tags / collapse whitespace and truncate long text blocks."""
    if not value:
        return ""
    text = _HTML_TAG.sub("", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summarize_hit(hit: dict[str, Any]) -> dict[str, Any]:
    creator = hit.get("creator") or {}
    return {
        "id": hit.get("id"),
        "name": hit.get("name"),
        "creator": creator.get("name") if isinstance(creator, dict) else None,
        "url": hit.get("public_url"),
        "thumbnail": hit.get("thumbnail"),
        "like_count": hit.get("like_count"),
        "is_nsfw": hit.get("is_nsfw"),
    }


def _summarize_file(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": f.get("id"),
        "name": f.get("name"),
        "size_bytes": f.get("size"),
        "download_url": f.get("download_url"),
    }


# --------------------------------------------------------------------------- #
# Tool: search
# --------------------------------------------------------------------------- #
class SearchModelsInput(BaseModel):
    """Input for ``thingiverse_search_models``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    query: str = Field(
        ...,
        description="Search terms describing the object to print, e.g. 'coffee cup', 'phone stand', 'benchy'.",
        min_length=1,
        max_length=200,
    )
    limit: int = Field(
        default=20,
        description="Maximum number of results to return (1-30).",
        ge=1,
        le=30,
    )
    page: int = Field(
        default=1, description="1-based page number for pagination.", ge=1
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="'markdown' for human-readable output or 'json' for structured data.",
    )

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query cannot be blank")
        return v.strip()


@mcp.tool(
    name="thingiverse_search_models",
    annotations={
        "title": "Search Thingiverse for 3D Models",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def thingiverse_search_models(params: SearchModelsInput) -> str:
    """Search Thingiverse for printable 3D models ("things") by keyword.

    Use this first when a user wants to print something ("I want a coffee mug")
    to discover candidate models. Returns lightweight summaries; call
    ``thingiverse_get_model`` for the license and downloadable files of a result.

    Args:
        params (SearchModelsInput): Validated input containing:
            - query (str): keywords, e.g. "coffee cup".
            - limit (int): max results, 1-30 (default 20).
            - page (int): 1-based page for pagination (default 1).
            - response_format (str): "markdown" or "json".

    Returns:
        str: Markdown list, or JSON of the form:
        {
          "query": str,
          "total": int,     # total matches reported by Thingiverse
          "count": int,     # results in this response
          "page": int,
          "results": [
            {"id": int, "name": str, "creator": str|null, "url": str|null,
             "thumbnail": str|null, "like_count": int|null, "is_nsfw": bool|null}
          ]
        }
        On failure: "Error: <reason>"; or "No Thingiverse models found for '<query>'."

    Examples:
        - "Find me a coffee mug to print" -> query="coffee mug".
        - Then pick an id and call thingiverse_get_model(thing_id=id).
    """
    try:
        data = await _api_get(
            f"search/{quote(params.query, safe='')}",
            params={"type": "things", "per_page": params.limit, "page": params.page},
        )
        if isinstance(data, dict):
            hits = data.get("hits", []) or []
            total = data.get("total", len(hits))
        elif isinstance(data, list):
            hits, total = data, len(data)
        else:
            hits, total = [], 0

        if not hits:
            return f"No Thingiverse models found for '{params.query}'."

        results = [_summarize_hit(h) for h in hits]

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "query": params.query,
                    "total": total,
                    "count": len(results),
                    "page": params.page,
                    "results": results,
                },
                indent=2,
            )

        lines = [
            f"# Thingiverse results for '{params.query}'",
            "",
            f"Showing {len(results)} of {total} matches (page {params.page}).",
            "",
        ]
        for r in results:
            lines.append(f"## {r['name']} (id: {r['id']})")
            if r.get("creator"):
                lines.append(f"- Creator: {r['creator']}")
            if r.get("url"):
                lines.append(f"- URL: {r['url']}")
            if r.get("like_count") is not None:
                lines.append(f"- Likes: {r['like_count']}")
            lines.append(f"- Next: `thingiverse_get_model(thing_id={r['id']})`")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 - surfaced as an actionable string
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: details
# --------------------------------------------------------------------------- #
class GetModelInput(BaseModel):
    """Input for ``thingiverse_get_model``."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    thing_id: int = Field(
        ..., description="Thingiverse thing ID, taken from search results.", ge=1
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="thingiverse_get_model",
    annotations={
        "title": "Get Thingiverse Model Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def thingiverse_get_model(params: GetModelInput) -> str:
    """Get details for one Thingiverse thing, including its license and files.

    Call after ``thingiverse_search_models`` to inspect a candidate before
    downloading: confirms the license (important - many models are
    non-commercial) and lists downloadable files with their IDs and sizes.

    Args:
        params (GetModelInput): Validated input containing:
            - thing_id (int): the thing ID.
            - response_format (str): "markdown" or "json".

    Returns:
        str: Markdown summary, or JSON of the form:
        {
          "id": int, "name": str, "creator": str|null, "license": str|null,
          "url": str|null, "description": str,
          "file_count": int,
          "files": [{"file_id": int, "name": str, "size_bytes": int|null,
                     "download_url": str|null}]
        }
        On failure: "Error: <reason>".

    Examples:
        - "What's the license on thing 123?" -> thing_id=123, read "license".
        - Then: thingiverse_download_model(thing_id=123).
    """
    try:
        thing = await _api_get(f"things/{params.thing_id}")
        files_raw = await _api_get(f"things/{params.thing_id}/files")
        files = (
            [_summarize_file(f) for f in files_raw]
            if isinstance(files_raw, list)
            else []
        )
        creator = thing.get("creator") or {}
        info = {
            "id": thing.get("id"),
            "name": thing.get("name"),
            "creator": creator.get("name") if isinstance(creator, dict) else None,
            "license": thing.get("license"),
            "url": thing.get("public_url"),
            "description": _clean_text(thing.get("description")),
            "file_count": len(files),
            "files": files,
        }

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(info, indent=2)

        lines = [f"# {info['name']} (id: {info['id']})", ""]
        if info["creator"]:
            lines.append(f"- Creator: {info['creator']}")
        lines.append(f"- License: {info['license'] or 'unknown - verify before reuse'}")
        if info["url"]:
            lines.append(f"- URL: {info['url']}")
        if info["description"]:
            lines.extend(["", info["description"]])
        lines.extend(["", f"## Files ({info['file_count']})"])
        if files:
            for f in files:
                size = (
                    f"{f['size_bytes']} bytes"
                    if f.get("size_bytes") is not None
                    else "size unknown"
                )
                lines.append(f"- {f['name']} (file_id: {f['file_id']}, {size})")
        else:
            lines.append("- No files listed.")
        lines.extend(
            ["", f"Download: `thingiverse_download_model(thing_id={info['id']})`"]
        )
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --------------------------------------------------------------------------- #
# Tool: download
# --------------------------------------------------------------------------- #
class DownloadModelInput(BaseModel):
    """Input for ``thingiverse_download_model``."""

    model_config = ConfigDict(
        str_strip_whitespace=True, validate_assignment=True, extra="forbid"
    )

    thing_id: int = Field(
        ..., description="Thingiverse thing ID whose files to download.", ge=1
    )
    file_id: int | None = Field(
        default=None,
        description="Download only this specific file ID (from thingiverse_get_model). If omitted, downloads all printable model files.",
        ge=1,
    )
    include_all_files: bool = Field(
        default=False,
        description="If true, download every file (images, READMEs, etc.), not just printable models (.stl/.3mf/.obj/.step/...).",
    )
    dest_subdir: str | None = Field(
        default=None,
        description="Subfolder name under the download directory. Defaults to 'thing-<id>'. Path separators are stripped.",
        max_length=128,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'."
    )


@mcp.tool(
    name="thingiverse_download_model",
    annotations={
        "title": "Download Thingiverse Model Files",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def thingiverse_download_model(params: DownloadModelInput) -> str:
    """Download a Thingiverse thing's files to the local download directory.

    By default downloads only printable model files (.stl, .3mf, .obj, .step,
    .stp, .amf, .ply). Files are saved under ``<download_dir>/<dest_subdir>``
    (default ``thing-<id>``). The download directory is ``PRINTMCP_DOWNLOAD_DIR``
    or the OS Downloads folder (``~/PrintMCP/downloads`` fallback). This is the
    hand-off point to Level 2 (slicing).

    Args:
        params (DownloadModelInput): Validated input containing:
            - thing_id (int): the thing ID.
            - file_id (int|None): download just this file; else all model files.
            - include_all_files (bool): include non-model files too.
            - dest_subdir (str|None): destination subfolder name.
            - response_format (str): "markdown" or "json".

    Returns:
        str: Markdown summary, or JSON of the form:
        {
          "thing_id": int, "name": str|null, "license": str|null,
          "dest_dir": str, "downloaded_count": int,
          "files": [{"name": str, "path": str, "size_bytes": int}],
          "skipped": [{"name": str, "reason": str}]
        }
        On failure or if nothing matched: "Error: <reason>".

    Examples:
        - "Download that mug" -> thing_id=<id> (fetches the .stl files).
        - "Grab only file 456" -> thing_id=<id>, file_id=456.

    Note:
        Respect the model's license (see thingiverse_get_model). Files are
        fetched from Thingiverse; large models may take time.
    """
    try:
        files_raw = await _api_get(f"things/{params.thing_id}/files")
        if not isinstance(files_raw, list) or not files_raw:
            return f"Error: Thing {params.thing_id} has no downloadable files."

        # Best-effort license/name lookup for the response (ignore failures).
        license_str: str | None = None
        name: str | None = None
        try:
            thing = await _api_get(f"things/{params.thing_id}")
            license_str = thing.get("license")
            name = thing.get("name")
        except Exception:  # noqa: BLE001 - license is a nicety, not required
            pass

        selected: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for f in files_raw:
            fname = f.get("name") or f"file-{f.get('id')}"
            if params.file_id is not None:
                if f.get("id") == params.file_id:
                    selected.append(f)
                continue
            ext = Path(fname).suffix.lower()
            if params.include_all_files or ext in MODEL_FILE_EXTENSIONS:
                selected.append(f)
            else:
                skipped.append(
                    {
                        "name": fname,
                        "reason": f"not a model file ({ext or 'no extension'})",
                    }
                )

        if not selected:
            if params.file_id is not None:
                return f"Error: file_id {params.file_id} not found on thing {params.thing_id}."
            return (
                f"Error: No printable model files (.stl/.3mf/.obj/...) on thing "
                f"{params.thing_id}. Pass include_all_files=true to fetch other files."
            )

        # Resolve destination, guarding against path traversal.
        root = get_download_dir().resolve()
        sub = (
            _safe_filename(params.dest_subdir)
            if params.dest_subdir
            else f"thing-{params.thing_id}"
        )
        dest = (root / sub).resolve()
        if dest != root and root not in dest.parents:
            return "Error: Invalid destination subdirectory."
        dest.mkdir(parents=True, exist_ok=True)

        downloaded: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            for f in selected:
                fname = _safe_filename(f.get("name") or f"file-{f.get('id')}")
                url = f.get("download_url") or (
                    f"{THINGIVERSE_API_BASE}/things/{params.thing_id}/files/{f.get('id')}/download"
                )
                target = dest / fname
                try:
                    # Auth header is sent to api.thingiverse.com; httpx drops it
                    # automatically on the cross-host redirect to the CDN.
                    async with client.stream(
                        "GET", url, headers=_auth_headers(), follow_redirects=True
                    ) as resp:
                        resp.raise_for_status()
                        with open(target, "wb") as fh:
                            async for chunk in resp.aiter_bytes(65536):
                                fh.write(chunk)
                    downloaded.append(
                        {
                            "name": fname,
                            "path": str(target),
                            "size_bytes": target.stat().st_size,
                        }
                    )
                except Exception as inner:  # noqa: BLE001 - record per-file failure
                    skipped.append({"name": fname, "reason": _handle_error(inner)})

        if not downloaded:
            return "Error: All downloads failed. " + (
                skipped[-1]["reason"] if skipped else ""
            )

        result = {
            "thing_id": params.thing_id,
            "name": name,
            "license": license_str,
            "dest_dir": str(dest),
            "downloaded_count": len(downloaded),
            "files": downloaded,
            "skipped": skipped,
        }
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(result, indent=2)

        lines = [f"# Downloaded {len(downloaded)} file(s) from thing {params.thing_id}"]
        if name:
            lines.append(f"- Model: {name}")
        lines.append(f"- License: {license_str or 'unknown - verify before reuse'}")
        lines.append(f"- Saved to: {dest}")
        lines.append("")
        for d in downloaded:
            lines.append(f"- {d['name']} ({d['size_bytes']} bytes)")
        if skipped:
            lines.append("")
            lines.append("## Skipped")
            for s in skipped:
                lines.append(f"- {s['name']}: {s['reason']}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)
