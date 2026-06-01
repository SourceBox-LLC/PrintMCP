# 🛠️ Tool Reference (for Developers)

> **Audience:** developers integrating PrintMCP into an MCP client, debugging tool calls, or
> extending the server. **Regular users never need this** — you just talk to your assistant
> ([start with the tutorials](../README.md#-start-here)).

PrintMCP exposes **14 tools** over the [Model Context Protocol](https://modelcontextprotocol.io),
grouped into the three pipeline levels. This page documents the cross-cutting contract every tool
shares — the invocation envelope, schemas, annotations, response formats, error handling, and the
safety gate — then points you to the per-tool parameter pages.

| Per-level reference | Tools |
|---------------------|-------|
| [Level 1 · Thingiverse](thingiverse.md) | `thingiverse_search_models`, `thingiverse_get_model`, `thingiverse_download_model` |
| [Level 2 · Cura](cura.md) | `cura_slice_model` |
| [Level 3 · OctoPrint](octoprint.md) | `octoprint_get_status`, `octoprint_list_files`, `octoprint_get_job`, `octoprint_connect`, `octoprint_upload_file`, `octoprint_start_print`, `octoprint_control_job`, `octoprint_set_temperature`, `octoprint_home`, `octoprint_move` |

---

## The invocation model

PrintMCP is a **stdio MCP server** built on [FastMCP](https://modelcontextprotocol.io). Each tool
is an async function taking a single Pydantic input model named `params`, which has a direct and
important consequence for the wire format:

> [!IMPORTANT]
> **Arguments are wrapped in a `params` envelope.** Every tool's input schema is
> `{"type": "object", "required": ["params"], "properties": {"params": {"$ref": …}}}`. So a
> `tools/call` passes the real fields *nested under* `params`, not at the top level:
>
> ```jsonc
> // tools/call arguments for octoprint_set_temperature
> {
>   "params": {
>     "heater": "tool",
>     "target": 200,
>     "confirm": true
>   }
> }
> ```
>
> A common integration bug is sending `{"heater": "tool", …}` at the top level — that fails schema
> validation because the required `params` key is missing.

### Output schema

Every tool returns a **string**, so FastMCP advertises the same output schema for all of them:

```json
{
  "type": "object",
  "required": ["result"],
  "properties": { "result": { "type": "string", "title": "..." } }
}
```

The `result` string is **either human-readable Markdown or a JSON document**, depending on the
`response_format` field you pass (see [Response formats](#response-formats)). When you request
`json`, the value is a JSON string *carried inside* the `result` field — so even though FastMCP
surfaces it as structured output (`{"result": "..."}`), you still `json.loads()` the inner string
to get the documented object; the structure is one level of string-wrapping deep.

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

- **`readOnly`** — the tool only observes; it changes no state (local or remote). All three
  monitoring tools and the two Thingiverse query tools.
- **`destructive`** — the tool can irreversibly destroy work. Only `octoprint_control_job` (its
  `cancel` action abandons a print). Note that `octoprint_start_print` is *not* flagged
  destructive — it's consequential but additive — yet it still requires `confirm` (see
  [the safety gate](#the-safety-gate-confirmtrue)).
- **`idempotent`** — calling again with the same args lands the same state. `cura_slice_model` is
  idempotent (re-slicing overwrites the same `.gcode`); `octoprint_set_temperature` is (setting
  200 °C twice is one outcome). Uploads, downloads, start-print, and jog are **not** idempotent.
- **`openWorld`** — the tool reaches an external system (Thingiverse or the printer). Only
  `cura_slice_model` is closed-world: it shells out to a **local** CuraEngine binary.

> [!NOTE]
> Annotations are advisory metadata, not enforcement. The actual safety enforcement is the
> `confirm` gate inside each actuating tool — see below.

---

## Shared input contract

Every input model is Pydantic v2 with the same config:

```python
model_config = ConfigDict(
    str_strip_whitespace=True,   # trims whitespace on str fields
    validate_assignment=True,
    extra="forbid",              # unknown fields are REJECTED, not ignored
)
```

Implications for callers:

- **Unknown fields are a hard error.** `extra="forbid"` means a typo'd field name (`temperatures`
  instead of `target`) fails validation rather than being silently dropped.
- **Constraints are enforced pre-execution.** Ranges (`target` 0–300, `layer_height` 0.05–0.6),
  enums (`heater` ∈ {`tool`,`bed`}), and string limits are checked by Pydantic before any network
  or subprocess work happens. Violations return a validation error, never a partial action.
- **Enums are plain strings on the wire.** `ResponseFormat`, `Heater`, `AdhesionType`, `JobAction`,
  `ConnectAction` all serialize as their string value (`"markdown"`, `"bed"`, `"cancel"`, …).

See each per-tool page for the full field list, types, defaults, and ranges.

---

## Response formats

Every tool accepts `response_format`, defaulting to `"markdown"`:

| Value | `result` contains | Use when |
|-------|-------------------|----------|
| `"markdown"` | Human-readable Markdown | Surfacing to a person / an assistant's chat |
| `"json"` | A JSON **string** (parse it) | Programmatic consumption, chaining tools |

The `json` form returns a stable, documented shape per tool (see the per-tool pages for exact
keys). Example — `octoprint_get_job` with `response_format: "json"` yields a `result` string of:

```json
{
  "state": "Printing",
  "file": "Coffee_Cup.A.1.gcode",
  "completion_percent": 42.5,
  "print_time_s": 1800,
  "print_time_left_s": 3661
}
```

> [!TIP]
> When chaining tools programmatically, request `json` and parse `result` — it's far more robust
> than scraping the Markdown.

---

## The safety gate (`confirm=true`)

Tools that physically actuate the printer take a `confirm: bool = false`. The gate is checked
**before any request is built**, so a dry run is guaranteed to send nothing over the network.

**Gated tools:** `octoprint_connect`, `octoprint_start_print`, `octoprint_control_job`,
`octoprint_set_temperature`, `octoprint_home`, `octoprint_move`, and `octoprint_upload_file`
*only when* `print_after_upload=true` (plain uploads are not gated).

```jsonc
// confirm omitted/false → dry-run preview, zero network I/O
{ "params": { "path": "cup.gcode" } }
// → result: "Safety check - nothing was sent to the printer. This would …"

// confirm true → actuates
{ "params": { "path": "cup.gcode", "confirm": true } }
// → result: "Started printing 'cup.gcode'. …"
```

With `response_format: "json"`, a dry run returns a machine-checkable object:

```json
{ "dry_run": true, "action": "start the print", "detail": "…", "message": "…" }
```

This is verified by tests asserting that `confirm=false` produces **zero** HTTP requests. Full
rationale and the additional guardrails (temperature ceilings, movement bounds, readiness checks)
are in the [Safety Model](../safety.md).

---

## Error handling

Tools **do not raise** across the MCP boundary for operational failures. They catch exceptions and
return an actionable `result` string beginning with `Error:`. Each level maps its failure modes:

| Condition | Example `result` |
|-----------|------------------|
| Missing config | `Error: THINGIVERSE_TOKEN is not set …` / `Error: OCTOPRINT_URL and OCTOPRINT_API_KEY not set …` |
| HTTP 401 | `Error: Authentication failed (401): …` |
| HTTP 409 (printer busy/disconnected) | `Error: Conflict (409): the printer is not in a state …` |
| Host unreachable | `Error: Could not reach OctoPrint at <url>. …` |
| Bad local input | `Error: model file not found: …` / `Error: '<ext>' is not G-code. …` |

Two guarantees worth relying on:

> [!IMPORTANT]
> - **Secrets never appear in output.** The OctoPrint API key is sent only in the `X-Api-Key`
>   header and is never echoed into any `result` or error string (enforced by a test). The
>   Thingiverse token is likewise header-only.
> - **No raw tracebacks.** Unexpected exceptions are still formatted as
>   `Error: Unexpected <Type>: <message>` rather than crashing the tool call.
>
> Schema/validation failures (e.g. a missing `params` envelope or an out-of-range value) are the
> exception — those surface as MCP tool errors from the framework layer, *before* the tool body
> runs.

---

## Calling tools programmatically

You normally reach these tools through an MCP client, but you can exercise them directly in Python
— handy for testing or scripting. Because `@mcp.tool` returns the function unchanged, the
coroutines are callable with their Pydantic input model:

```python
import asyncio
from printmcp.octoprint import octoprint_get_status, StatusInput

print(asyncio.run(octoprint_get_status(StatusInput(response_format="json"))))
```

To go through the MCP layer instead (exercising schema validation and the `params` envelope):

```python
import asyncio
import printmcp.thingiverse, printmcp.cura, printmcp.octoprint  # noqa: F401 (register tools)
from printmcp.app import mcp

async def main():
    tools = await mcp.list_tools()
    print([t.name for t in tools])            # all 14

    # call_tool returns a tuple: (content_blocks, structured_output).
    content, structured = await mcp.call_tool(
        "octoprint_get_status",
        {"params": {"response_format": "json"}},   # note the params envelope
    )
    print(content[0].text)        # the result string (Markdown or JSON)
    print(structured["result"])   # same string, under the output schema's "result" key

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
   extra="forbid")`) with typed, constrained `Field`s.
2. Write an `async def` decorated with `@mcp.tool(name=…, annotations={…})` taking a single
   `params` argument, returning a `str`.
3. Honor the shared contract: support `response_format`, gate any physical action behind
   `confirm`, and return `Error: …` strings rather than raising.
4. Import the module in [`server.py`](../architecture.md#tool-registration) so it registers, and
   add offline tests (mock transport for any HTTP).

The full design — module layout, registration, the per-level notes, and how to add a new *source*
(e.g. `printables_*`) or *print backend* (e.g. `moonraker_*`) — is in
[Architecture](../architecture.md).
