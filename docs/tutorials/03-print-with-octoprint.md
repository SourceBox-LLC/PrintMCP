# Tutorial 3 · Print with OctoPrint

> **Goal:** upload your sliced G-code to the printer, start the print safely, and monitor it to
> completion.
> **Time:** ~5 minutes to set up (then however long the print takes) · **You need:** a printer
> running OctoPrint, with `OCTOPRINT_URL` and `OCTOPRINT_API_KEY` configured
> ([setup](../getting-started.md)), and a `.gcode` file from [Tutorial 2](02-slice-for-your-printer.md).

This is where bits become atoms. Because these tools drive a **real machine**, this tutorial
also teaches the safety model as you go.

> [!WARNING]
> **Read this first.** Tools that move the printer (heaters, motors, starting a print) require
> `confirm=true`. Called without it, they show a *dry-run preview* and send nothing. This is a
> feature — it lets you (or an AI assistant) preview a physical action before committing. Full
> details: [Safety Model](../safety.md).

---

## Step 1 — Check the printer is reachable and ready 👁️

Always start here. This is **read-only** — completely safe.

```text
octoprint_get_status()
```

```markdown
# Printer status

- OctoPrint: 1.9.3 (API 0.1)
- Connection: Operational
- Printer state: Operational
- Ready to print: yes

## Temperatures
- Bed: 24.0°C -> 0.0°C target
- tool0: 23.1°C -> 0.0°C target
```

**What to look for:** `Connection: Operational` and `Ready to print: yes`.

If instead you see `Could not reach OctoPrint …`, the printer is off, on a different network, or
the URL is wrong — see [Troubleshooting](../troubleshooting.md). If it's
reachable but **not** connected, do Step 2; otherwise skip it.

---

## Step 2 — Connect the printer (if needed) 🔒

If `octoprint_get_status` showed the printer disconnected, open the serial connection. This is
the first tool that needs `confirm`.

**Preview first (dry run):**

```text
octoprint_connect()
```

```text
Safety check - nothing was sent to the printer.

This would connect the printer.

Re-run with confirm=true to actually connect the physical machine.
```

**Then actually connect:**

```text
octoprint_connect(confirm=true)
```

Re-run `octoprint_get_status()` and confirm it's now `Operational`.

> [!TIP]
> Usually you can let OctoPrint auto-detect the port and baud rate. If you have multiple serial
> devices, specify them: `octoprint_connect(port="/dev/ttyUSB0", baudrate=115200, confirm=true)`.

---

## Step 3 — Upload your G-code 🔒¹

Send the sliced file to the printer. **Uploading by itself doesn't move anything**, so it
doesn't need `confirm`:

```text
octoprint_upload_file(gcode_path="C:\\Users\\Sbuss\\PrintMCP\\downloads\\thing-159884\\Coffee_Cup.A.1.gcode")
```

```markdown
# Uploaded Coffee_Cup.A.1.gcode

- Server path: `Coffee_Cup.A.1.gcode`
- Next: `octoprint_start_print(path="Coffee_Cup.A.1.gcode", confirm=true)`
```

Note the **Server path** — that's how you'll refer to the file from now on (it lives on the
printer, not your PC). You can confirm it's there:

```text
octoprint_list_files()
```

---

## Step 4 — Start the print 🔒

This **physically starts** the printer. Preview it first:

```text
octoprint_start_print(path="Coffee_Cup.A.1.gcode")
```

```text
Safety check - nothing was sent to the printer.

This would select 'Coffee_Cup.A.1.gcode' and begin printing it on the physical machine.

Re-run with confirm=true to actually start the print the physical machine.
```

Happy with it? Commit:

```text
octoprint_start_print(path="Coffee_Cup.A.1.gcode", confirm=true)
```

```text
Started printing 'Coffee_Cup.A.1.gcode'. Monitor it with octoprint_get_job.
```

The printer will now heat up and begin. 🎉

> [!TIP]
> **Shortcut:** you can upload and start in one call with
> `octoprint_upload_file(gcode_path="…", print_after_upload=true, confirm=true)`. The separate
> steps are clearer for your first run.

---

## Step 5 — Monitor progress 👁️

Check in anytime — read-only, safe to call repeatedly:

```text
octoprint_get_job()
```

```markdown
# Current job

- State: Printing
- File: Coffee_Cup.A.1.gcode
- Progress: 42.5%
- Elapsed: 2h 46m 0s
- Remaining (est.): 3h 45m 0s
```

---

## Step 6 — Pause, resume, or cancel (if needed) 🔒

| Situation | Call |
|-----------|------|
| Filament tangle, need a moment | `octoprint_control_job(action="pause", confirm=true)` |
| Ready to continue | `octoprint_control_job(action="resume", confirm=true)` |
| Something's wrong, abandon it | `octoprint_control_job(action="cancel", confirm=true)` |

> [!WARNING]
> **Cancelling wastes the partial print** — it's marked destructive and always requires
> `confirm=true`. There's no undo.

---

## Manual controls (optional) 🔒

For setup, maintenance, or recovery, you can drive the machine directly (printer must be
operational and **not** mid-print):

```text
octoprint_home(confirm=true)                                  # home all axes
octoprint_set_temperature(heater="tool", target=200, confirm=true)  # preheat nozzle
octoprint_set_temperature(heater="bed", target=60, confirm=true)    # preheat bed
octoprint_move(z=10, confirm=true)                            # raise the head 10 mm
octoprint_set_temperature(heater="tool", target=0, confirm=true)    # cool down (0 = off)
```

---

## ✅ Checkpoint

You uploaded G-code, previewed and started a real print, and watched it progress — all through
the safety gate. 

**Next:** [Tutorial 4 · The Full Pipeline](04-end-to-end.md) — chain every level into a single
idea-to-object run.

---

<sub>Full parameter reference: [Level 3 · OctoPrint Tools](../tools/octoprint.md) ·
Safety details: [Safety Model](../safety.md).</sub>
