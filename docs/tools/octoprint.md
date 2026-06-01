# 🖨️ Level 3 · OctoPrint Tools

<sub>↑ [Tool reference overview](README.md) — the shared invocation model, schemas, and error contract.</sub>

Level 3 is **printing** — uploading G-code, starting and monitoring prints, and controlling the
printer's heaters and motors through the [OctoPrint REST API](https://docs.octoprint.org/en/master/api/).
Requires `OCTOPRINT_URL` and `OCTOPRINT_API_KEY` ([configure them](../configuration.md#octoprint-level-3)).

> [!WARNING]
> These tools drive a **real machine**. Every tool that physically actuates the printer
> requires `confirm=true`; without it you get a harmless dry-run preview and **nothing is
> sent**. Read the [Safety Model](../safety.md) before your first print.

| Tool | | Purpose |
|------|:--:|---------|
| [`octoprint_get_status`](#octoprint_get_status) | 👁️ | Connection + printer state + temperatures. |
| [`octoprint_list_files`](#octoprint_list_files) | 👁️ | List G-code on the server. |
| [`octoprint_get_job`](#octoprint_get_job) | 👁️ | Active job progress. |
| [`octoprint_connect`](#octoprint_connect) | 🔒 | Open/close the serial connection. |
| [`octoprint_upload_file`](#octoprint_upload_file) | 🔒¹ | Upload a local `.gcode`. |
| [`octoprint_start_print`](#octoprint_start_print) | 🔒 | Select a server file and start printing. |
| [`octoprint_control_job`](#octoprint_control_job) | 🔒 | Pause / resume / cancel. |
| [`octoprint_set_temperature`](#octoprint_set_temperature) | 🔒 | Set tool/bed temperature. |
| [`octoprint_home`](#octoprint_home) | 🔒 | Home axes. |
| [`octoprint_move`](#octoprint_move) | 🔒 | Jog the print head. |

<sub>👁️ read-only · 🔒 requires `confirm=true` · ¹ uploading alone is safe; only starting a print needs `confirm`.</sub>

All tools accept `response_format` (`markdown` or `json`).

---

## 👁️ Read-only tools

### `octoprint_get_status`

The printer's connection state, operational state, and live temperatures. **Call this first** —
before uploading or printing — to confirm the printer is connected and ready.

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `response_format` | str | `markdown` | `markdown` or `json`. |

Returns:

```json
{
  "server": {"version": "1.9.3", "api": "0.1"},
  "connection": {"state": "Operational", "port": "/dev/ttyUSB0", "baudrate": 115200},
  "printer_state": "Operational",
  "ready": true,
  "temperatures": {"tool0": {"actual": 23.1, "target": 0.0}, "bed": {"actual": 24.0, "target": 0.0}}
}
```

> [!NOTE]
> `ready` is `true` only when the printer is **operational and not** printing, paused, pausing,
> resuming, cancelling, finishing, or in an error state — so an agent won't start a print on a
> busy or faulted machine. If the printer isn't connected, the temperature section is omitted;
> bring it online with [`octoprint_connect`](#octoprint_connect).

---

### `octoprint_list_files`

List the G-code files stored on the server, newest first, each with the **server-side path** you
pass to `octoprint_start_print`. Folders are flattened.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `limit` | int | `50` | 1–200 | Max files to return. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

Returns:

```json
{
  "count": 1,
  "files": [
    {"name": "Coffee_Cup.A.1.gcode", "path": "Coffee_Cup.A.1.gcode",
     "size_bytes": 18555302, "date": 1716940000, "estimated_print_time_s": 23491}
  ]
}
```

---

### `octoprint_get_job`

The current job: file, percent complete, elapsed time, and estimated time remaining.

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `response_format` | str | `markdown` | `markdown` or `json`. |

Returns:

```json
{
  "state": "Printing", "file": "Coffee_Cup.A.1.gcode",
  "completion_percent": 42.5, "print_time_s": 1800, "print_time_left_s": 3661
}
```

---

## 🔒 Action tools (require `confirm=true`)

Each of these does a **dry run** when `confirm` is omitted or `false`: it describes what *would*
happen and sends nothing. Pass `confirm=true` to actuate. See [Safety Model](../safety.md).

### `octoprint_connect`

Open or close OctoPrint's serial connection to the printer. The printer must be **connected
(Operational)** before it can print or accept temperature/movement commands.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `action` | str | `connect` | `connect` \| `disconnect` | Open or close the connection. |
| `port` | str \| null | `null` | ≤ 128 chars | Serial port (e.g. `/dev/ttyUSB0`, `COM3`). Omit to auto-detect. |
| `baudrate` | int \| null | `null` | 1200–1000000 | Baud rate. Omit to auto-detect. |
| `confirm` | bool | `false` | — | Must be `true` to act. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_connect(confirm=true)                       # auto-detect port + baud
octoprint_connect(action="disconnect", confirm=true)
```

---

### `octoprint_upload_file`

Upload a local G-code file to the server. **Uploading alone does not move the machine**, so it
doesn't need `confirm`. Setting `print_after_upload=true` *does* start a physical print and so
requires `confirm=true`.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `gcode_path` | str | — (required) | non-empty | Local `.gcode`/`.gco`/`.g` file. |
| `dest_path` | str \| null | `null` | ≤ 256 chars | Server subfolder. Defaults to storage root. |
| `select` | bool | `false` | — | Select the file after upload (does not print). |
| `print_after_upload` | bool | `false` | — | Start printing right away (**requires `confirm`**). |
| `confirm` | bool | `false` | — | Required only when `print_after_upload` is true. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_upload_file(gcode_path="…\\Coffee_Cup.A.1.gcode")          # upload only
octoprint_upload_file(gcode_path="…\\cup.gcode", print_after_upload=true, confirm=true)
```

Returns the **server path** to use with `octoprint_start_print`.

---

### `octoprint_start_print`

Select a G-code file **already on the server** and start printing it. Physically starts the
printer (heaters + motors). The printer must be connected/operational first.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `path` | str | — (required) | ≤ 512 chars | Server-side path (from `octoprint_list_files`). |
| `confirm` | bool | `false` | — | Must be `true` to start. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_start_print(path="Coffee_Cup.A.1.gcode", confirm=true)
```

---

### `octoprint_control_job`

Pause, resume, or cancel the running job. **Cancelling abandons the print** (the partial object
is wasted) — it's marked destructive and requires `confirm=true`.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `action` | str | — (required) | `pause` \| `resume` \| `cancel` | What to do to the active job. |
| `confirm` | bool | `false` | — | Must be `true` to act. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_control_job(action="pause", confirm=true)
octoprint_control_job(action="cancel", confirm=true)
```

---

### `octoprint_set_temperature`

Set a target temperature for the nozzle (tool) or the heated bed. Driving a heater requires
`confirm=true`. **Target `0` turns the heater off.**

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `heater` | str | — (required) | `tool` \| `bed` | Which heater. |
| `target` | int | — (required) | 0–300 (bed capped at 140) | Target °C. `0` = off. |
| `tool_index` | int | `0` | 0–9 | Extruder index when `heater="tool"`. |
| `confirm` | bool | `false` | — | Must be `true` to act. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_set_temperature(heater="tool", target=200, confirm=true)   # preheat nozzle
octoprint_set_temperature(heater="bed", target=60, confirm=true)     # preheat bed
octoprint_set_temperature(heater="tool", target=0, confirm=true)     # cool down
```

> [!NOTE]
> A safety ceiling is enforced in software (bed ≤ 140 °C, tool ≤ 300 °C) on top of your
> firmware's own limits, so a typo can't command a wild temperature.

---

### `octoprint_home`

Home one or more axes (move them to their endstops). Moves the machine → `confirm=true`. The
printer must be operational and not mid-print.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `axes` | list[str] | `["x","y","z"]` | subset of `x`,`y`,`z` | Axes to home. |
| `confirm` | bool | `false` | — | Must be `true` to act. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_home(confirm=true)                 # home all three
octoprint_home(axes=["x","y"], confirm=true) # home X and Y only
```

---

### `octoprint_move`

Jog the print head by a **relative** offset (mm) on one or more axes. Moves the machine →
`confirm=true`. At least one of `x`/`y`/`z` is required.

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `x` | float \| null | `null` | −500…500 | Relative X move (mm). |
| `y` | float \| null | `null` | −500…500 | Relative Y move (mm). |
| `z` | float \| null | `null` | −500…500 | Relative Z move (mm). |
| `speed` | int \| null | `null` | 1–12000 | Feedrate mm/min. Omit for OctoPrint's default. |
| `confirm` | bool | `false` | — | Must be `true` to act. |
| `response_format` | str | `markdown` | | `markdown` or `json`. |

```text
octoprint_move(z=10, confirm=true)                  # raise Z 10 mm
octoprint_move(x=-5, y=5, speed=3000, confirm=true) # diagonal nudge
```

---

## A typical printing session

```text
1. octoprint_get_status()                                  👁️ is it connected & ready?
2. octoprint_connect(confirm=true)                         🔒 (only if not connected)
3. octoprint_upload_file(gcode_path="…\\cup.gcode")        🔒¹ → returns server path
4. octoprint_start_print(path="cup.gcode", confirm=true)   🔒 start
5. octoprint_get_job()                                     👁️ watch progress
   octoprint_control_job(action="cancel", confirm=true)    🔒 if needed
```

---

## Common errors

| Message | Cause | Fix |
|---------|-------|-----|
| `Error: OCTOPRINT_URL and OCTOPRINT_API_KEY not set …` | Not configured | Set both in `.env` ([config](../configuration.md#octoprint-level-3)). |
| `Could not reach OctoPrint at … ` | Printer off / wrong URL / different network | Verify it loads in a browser; check power and IP. |
| `Authentication failed (401) …` | Bad API key | Re-issue the key. |
| `Conflict (409): the printer is not in a state …` | Not connected, or no active job | Run `octoprint_get_status`; `octoprint_connect(confirm=true)`. |

See the [Troubleshooting](../troubleshooting.md) guide for the full list.

---

**Next:** put it all together — [Tutorial 4 · The Full Pipeline](../tutorials/04-end-to-end.md).
