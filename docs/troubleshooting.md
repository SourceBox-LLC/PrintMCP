# 🔧 Troubleshooting

Every PrintMCP tool returns errors as readable `Error: …` strings rather than crashing, so the
first step is always to **read the message** — it usually names the fix. This page expands on the
common ones, grouped by level.

---

## General

### "A tool isn't showing up in my MCP client"
- Run the built-in check — it confirms the install and prints each level's config status:
  ```bash
  printmcp --check          # from source: uv run printmcp --check
  ```
  If the command itself errors, fix the install first (`uv sync`, or reinstall the package).
- Make sure your client config points at the right command/directory, and restart the client.

### "My `.env` changes aren't taking effect"
- Real environment variables **override** `.env`. Check you don't have a stale `OCTOPRINT_URL`
  (etc.) exported in your shell.
- `.env` is read from the working directory or a parent. Run from the project root.
- Restart the MCP client so it relaunches the server with the new environment.

---

## Level 1 · Thingiverse

| Message | Cause | Fix |
|---------|-------|-----|
| `Error: THINGIVERSE_TOKEN is not set …` | No token | Set `THINGIVERSE_TOKEN` in `.env` ([guide](configuration.md#thingiverse-level-1)). |
| `Authentication failed (401) …` | Token invalid/expired | Re-issue at <https://www.thingiverse.com/apps/create>. |
| `Permission denied (403) …` | Token lacks access | Check the app/token type. |
| `Not found (404) …` | Wrong `thing_id` | Re-check the ID from `thingiverse_search_models`. |
| `Rate limited (429) …` | Too many requests | Wait, then retry. |
| `No Thingiverse models found for '…'` | Query too narrow / typo | Broaden the query, check spelling. |
| `Request to Thingiverse timed out` | Network blip | Retry. |

> [!TIP]
> Downloaded to the wrong place? Files go to `PRINTMCP_DOWNLOAD_DIR` or `~/PrintMCP/downloads`.
> See [Configuration](configuration.md#-where-print-files-are-stored).

---

## Level 2 · Cura

| Message | Cause | Fix |
|---------|-------|-----|
| `Could not find Ultimaker Cura …` | Cura not installed or not auto-detected | Install Cura, or set `PRINTMCP_CURA_DIR` / `PRINTMCP_CURAENGINE` ([guide](configuration.md#cura-level-2)). |
| `CuraEngine executable not found at: …` | Wrong `PRINTMCP_CURAENGINE` | Point it at the real `CuraEngine.exe`. |
| `model file not found: …` | Bad `model_path` | Copy the exact path from the download step's output. |
| `'…' is not a sliceable model …` | Unsupported extension | Use `.stl/.obj/.3mf/.amf/.ply` (e.g. a `.stp` won't slice). |
| `printer definition '…' not found …` | Unknown `printer` id | Use a valid Cura definition id (default `creality_ender3pro`). |
| `slicing failed (CuraEngine exit N): …` | Engine-level error | Read the detail — often a bad setting or a model exceeding the build volume. |
| `CuraEngine timed out after 900s …` | Huge/complex model | Simplify the model or raise the layer height. |

**Verify what Cura PrintMCP found:**
```bash
uv run python -c "from printmcp.config import get_cura_paths; print(get_cura_paths())"
```

---

## Level 3 · OctoPrint

| Message | Cause | Fix |
|---------|-------|-----|
| `Error: OCTOPRINT_URL and OCTOPRINT_API_KEY not set …` | Not configured | Set both in `.env` ([guide](configuration.md#octoprint-level-3)). |
| `Could not reach OctoPrint at …` | Printer off / wrong URL / different network | See the checklist below. |
| `Authentication failed (401) …` | Bad/missing API key | Re-issue in OctoPrint → Settings → API. |
| `Permission denied (403) …` | Key lacks rights for that action | Use a key with the needed permissions. |
| `Not found (404) …` | Wrong file path/endpoint | Re-check the server path via `octoprint_list_files`. |
| `Conflict (409): the printer is not in a state …` | Not connected, or no active job | `octoprint_get_status`, then `octoprint_connect(confirm=true)`. |
| `Unsupported media (415) …` | File type rejected | Upload a `.gcode/.gco/.g`. |
| `Request to OctoPrint timed out` | Slow/unreachable host | Retry; check the network. |

### "Could not reach OctoPrint" checklist

1. **Open the URL in a browser** on this machine — e.g. `http://octopi.local` or
   `http://<ip>:<port>`. Does the OctoPrint UI load?
   - **Loads** → the URL is right; re-run the tool.
   - **Doesn't load** → continue.
2. **Is the OctoPi powered on and booted?** The Pi has its own power, separate from the printer.
   Give it ~60 s after power-up.
3. **Did its IP change?** DHCP may have reassigned it. Try `http://octopi.local`, or check your
   router's client list, then update `OCTOPRINT_URL` in `.env`.
4. **Same network?** This machine must be on the same LAN as the printer (no guest Wi-Fi / VPN
   in the way).

### "Ready to print: no" but the printer seems fine
`octoprint_get_status` reports `ready` only when the printer is **operational and idle**. If
it's paused, finishing, or in an error state, `ready` is `no` by design. Check `printer_state`
and the active job with `octoprint_get_job`.

### A `confirm`-gated tool "did nothing"
That's the [safety model](safety.md) working. You called it without `confirm=true`, so it
returned a dry-run preview and sent nothing. Re-run with `confirm=true` to actuate.

---

## Still stuck?

- Re-run the offline test suite to confirm the code itself is healthy:
  ```bash
  uv run pytest -q
  ```
- Check the [Configuration](configuration.md) and [Safety Model](safety.md) pages.
- For how the pieces fit together, see [Architecture](architecture.md).
