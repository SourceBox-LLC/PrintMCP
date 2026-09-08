# 🛠️ Tool Reference (for Developers)

> **Audience:** developers integrating PrintMCP into an MCP client, debugging tool calls, or
> extending the server. **Regular users never need this** — you just talk to your assistant
> ([start with the tutorials](../README.md#-start-here)).

PrintMCP exposes **16 tools** over the [Model Context Protocol](https://modelcontextprotocol.io),
grouped into the three pipeline levels. This page documents the cross-cutting contract every tool
shares — the invocation envelope, schemas, annotations, response formats, error handling, and the
safety gate — then points you to the per-tool parameter pages.

| Per-level reference | Tools |
|---------------------|-------|
| [Level 1 · Thingiverse](thingiverse.md) | `thingiverse_search_models`, `thingiverse_get_model`, `thingiverse_download_model` |
| [Level 2 · Cura](cura.md) | `cura_slice_model` |
| [Level 2 · OrcaSlicer](orca.md) | `orca_list_profiles`, `orca_slice_model` |
| [Level 3 · OctoPrint](octoprint.md) | `octoprint_get_status`, `octoprint_list_files`, `octoprint_get_job`, `octoprint_connect`, `octoprint_upload_file`, `octoprint_start_print`, `octoprint_control_job`, `octoprint_set_temperature`, `octoprint_home`, `octoprint_move` |

---

## The invocation model

PrintMCP is a **stdio MCP server** built on [FastMCP](https://modelcontextprotocol.io). Each tool
is an async function whose signature is **flat** — one keyword argument per field. Since v0.2.1
this is reflected in the wire format directly:

> [!IMPORTANT]
> **Arguments are passed at the top level — there is no `params` wrapper.** A tool's input schema
> lists each field as a top-level property, so a `tools/call` for `octoprint_set_temperature` looks
> like:
>
> ```jsonc
> {
>   "heater": "tool",
>   "target": 200,
>   "confirm": true
> }
> ```
>
> The older wrapped form (`{"params": {...}}`) was removed in 0.2.1; do not send it.

### Output: structured results

Every tool returns a **pure Pydantic model**, and FastMCP advertises a per-tool `outputSchema`
(MCP 2025-06-18 structured output). So a tool call's `structuredContent` is the model's fields at
the top level (not wrapped in a `result` key), and `content[0].text` carries a human-readable
Markdown rendering of the same data:

```jsonc
// structuredContent for octoprint_get_status
{
  "server": {"version": "1.10.3", "api": "1.10"},
  "connection": {"state": "Operational", "port": "/dev/ttyUSB0", "baudrate": 250000},
  "printer_state": "Operational",
  "ready": true,
  "temperatures": {"tool0": {"actual": 205.1, "target": 205.0}, "bed": {"actual": 60.2, "target": 60.0}}
}
```

> [!TIP]
> Prefer `structuredContent` for programmatic use — it's typed, stable, and validated against the
> output schema. The Markdown text is for surfacing to a person (or an LLM reading tool output). The
> `response_format` field toggles the text form between `markdown` (default) and `json`; either way,
> `structuredContent` is present.

---

## Tool annotations

Every tool carries [MCP annotations](https://modelcontextprotocol.io) so a client can reason about
safety and caching before calling. The complete matrix:

| Tool | `readOnly` | `destructive` | `idempotent` | `openWorld` | Required fields |
|------|:----------:|:-------------:|:------------:|:-----------:|-----------------|
| `thingiverse_search_models` | ✅ | — | ✅ | ✅ | `query` |
| `thingiverse_get_model` | ✅ | — | ✅ | ✅ | `thing_id` |
| `thingiverse_download_model` | — | — | — | ✅ | `thing_id` |
| `cura_slice_model` | — | — | ✅ | — | `model_path` |
| `orca_list_profiles` | ✅ | — | ✅ | — | — |
| `orca_slice_model` | — | — | ✅ | — | `model_path`, `machine`, `process`, `filament` |
| `octoprint_get_status` | ✅ | — | ✅ | ✅ | — |
| `octoprint_list_files` | ✅ | — | ✅ | ✅ | — |
| `octoprint_get_job` | ✅ | — | ✅ | ✅ | — |
| `octoprint_connect` | — | — | ✅ | ✅ | — |
| `octoprint_upload_file` | — | — | — | ✅ | `gcode_path` |
| `octoprint_start_print` | — | — | — | ✅ | `path` |
| `octoprint_control_job` | — | ✅ | — | ✅ | `action` |
| `octoprint_set_temperature` | — | — | ✅ | ✅ | `heater`, `target` |
| `octoprint_home` | — | — | ✅ | ✅ | — |
| `octoprint_move` | — | — | — | ✅ | — |

### Reading the hints

- **`readOnly`** — the tool only observes; it changes no state (local or remote). All monitoring
  tools, the two Thingiverse query tools, and `orca_list_profiles`.
- **`destructive`** — the tool can irreversibly destroy work. Only `octoprint_control_job` (its
  `cancel` action abandons a print). Note that `octoprint_start_print` is *not* flagged
  destructive — it's consequential but additive — yet it still requires `confirm` (see
  [the safety gate](#the-safety-gate-confirmtrue)).
- **`idempotent`** — calling again with the same args lands the same state. Both slicer tools are
  idempotent (re-slicing overwrites the same `.gcode`); `octoprint_set_temperature` is (setting
  200 °C twice is one outcome). Uploads, downloads, start-print, and jog are **not** idempotent.
- **`openWorld`** — the tool reaches an external system (Thingiverse or the printer). The slicer
  tools and `orca_list_profiles` are closed-world: they shell out to or read from **local** files
  and binaries.

> [!NOTE]
> Annotations are advisory metadata, not enforcement. The actual safety enforcement is the
> `confirm` gate inside each actuating tool — see below.

---

## Shared input contract

Every input field is declared on a Pydantic v2 model with the same config:

```python
model_config = ConfigDict(
    str_strip_whitespace=True,   # trims whitespace on str fields
    validate_assignment=True,
    extra="forbid",              # unknown fields are REJECTED, not ignored
)
```

Those fields appear as top-level properties of the tool's `inputSchema` (no wrapper). Implications
for callers:

- **Unknown fields are a hard error.** `extra="forbid"` means a typo'd field name (`temperatures`
  instead of `target`) fails validation rather than being silently dropped.
- **Constraints are enforced pre-execution.** Ranges (`target` 0–300 for tool / 0–140 for bed,
  `layer_height` 0.05–0.6), enums (`heater` ∈ {`tool`,`bed`}), and string limits are checked by
  Pydantic before any network or subprocess work happens. Violations return a validation error,
  never a partial action.
- **Enums are plain strings on the wire.** `ResponseFormat`, `Heater`, `AdhesionType`, `JobAction`,
  `ConnectAction`, `Tier` all serialize as their string value (`"markdown"`, `"bed"`, `"cancel"`,
  …).

See each per-tool page for the full field list, types, defaults, and ranges.

---

## The safety gate (`confirm=true`)

Tools that physically actuate the printer take a `confirm: bool = false`. The gate is checked
**before any request is built**, so a dry run is guaranteed to send nothing over the network.

**Gated tools:** `octoprint_connect`, `octoprint_start_print`, `octoprint_control_job`,
`octoprint_set_temperature`, `octoprint_home`, `octoprint_move`, and `octoprint_upload_file`
*only when* `print_after_upload=true` (plain uploads are not gated).

```jsonc
// confirm omitted/false → dry-run, zero network I/O
{ "path": "cup.gcode" }
// structuredContent: { "ok": true, "printing": "cup.gcode", "dry_run": true, "detail": "…" }

// confirm true → actuates
{ "path": "cup.gcode", "confirm": true }
// structuredContent: { "ok": true, "printing": "cup.gcode", "dry_run": false }
```

A dry run returns `dry_run: true` in its structured result. This is verified by tests asserting
that `confirm=false` produces **zero** HTTP requests. Full rationale and the additional guardrails
(temperature ceilings, movement bounds, readiness checks) are in the [Safety Model](../safety.md).

---

## Error handling

Operational failures surface as MCP **`ToolError`**s — a per-level `_handle_error(...)` maps them
to concise, actionable messages. They do not return an "Error:" string inside a successful result;
the call is reported as an error (`isError: true` on the wire).

| Condition | Example error message |
|-----------|-----------------------|
| Missing config | `THINGIVERSE_TOKEN is not set …` / `OCTOPRINT_URL and OCTOPRINT_API_KEY not set …` |
| HTTP 401 | `Authentication failed (401): …` |
| HTTP 409 (printer busy/disconnected) | `Conflict (409): the printer is not in a state …` |
| Host unreachable | `Could not reach OctoPrint at <url>. …` |
| Bad local input | `Model file not found: …` / `Unknown machine preset '…'` |
| Engine failure | `OrcaSlicer failed to slice …` / `slicing failed (CuraEngine exit N): …` |

Two guarantees worth relying on:

> [!IMPORTANT]
> - **Secrets never appear in output.** The OctoPrint API key is sent only in the `X-Api-Key`
>   header and is never echoed into any result or error string (enforced by a test). The
>   Thingiverse token is likewise header-only.
> - **No raw tracebacks.** Unexpected exceptions are still formatted as
>   `Unexpected <Type>: <message>` rather than crashing the tool call.
>
> Schema/validation failures (e.g. an out-of-range value) surface as MCP validation errors from the
> framework layer, *before* the tool body runs.

---

## Calling tools programmatically

You normally reach these tools through an MCP client, but you can exercise them directly in Python
— handy for testing or scripting. Because `@mcp.tool` returns the function unchanged, the
coroutines are callable with flat keyword arguments:

```python
import asyncio
from printmcp.octoprint import octoprint_get_status

# returns a StatusResult model (structured output)
print(asyncio.run(octoprint_get_status()))
```

To go through the MCP layer instead (exercising schema validation and the stdio transport):

```python
import asyncio
import printmcp.thingiverse, printmcp.cura, printmcp.orca, printmcp.octoprint  # noqa: F401 (register tools)
from printmcp.app import mcp

async def main():
    tools = await mcp.list_tools()
    print([t.name for t in tools])            # all 16

    # call_tool returns a tuple: (content_blocks, structured_output).
    content, structured = await mcp.call_tool(
        "octoprint_get_status",
        {"response_format": "json"},   # flat arguments (no params wrapper)
    )
    print(content[0].text)             # the human/Markdown rendering
    print(structured)                  # the typed result object (its fields at top level)

asyncio.run(main())
```

> [!TIP]
> Inspect the live schemas for any tool to confirm field names, defaults, and ranges:
> ```python
> import asyncio, json, printmcp.octoprint
> from printmcp.app import mcp
> t = {x.name: x for x in asyncio.run(mcp.list_tools())}["octoprint_move"]
> print(json.dumps(t.inputSchema, indent=2))
> ```

---

## Extending the tool set

Adding a tool follows the conventions above and the existing modules. In brief:

1. Define a Pydantic input model (`ConfigDict(str_strip_whitespace=True, validate_assignment=True,
   extra="forbid")`) with typed, constrained `Field`s — one field per argument.
2. Write an `async def` decorated with `@mcp.tool(name=…, annotations={…})` with a **flat**
   signature (one kwarg per model field) returning a **pure Pydantic result model**.
3. Honor the shared contract: support `response_format`, gate any physical action behind
   `confirm`, and raise `ToolError` with a clear message for operational failures.
4. Import the module in [`server.py`](../architecture.md#tool-registration) so it registers, and
   add offline tests (mock transport for any HTTP).

The full design — module layout, registration, the per-level notes, and how to add a new *source*
(e.g. `printables_*`) or *print backend* (e.g. `moonraker_*`) — is in
[Architecture](../architecture.md).
