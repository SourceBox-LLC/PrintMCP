# 🛡️ Safety Model

Level 3 drives a **physical machine** with heaters that reach 300 °C and motors that move real
mass. A careless or mistaken tool call could start an unwanted print, command a dangerous
temperature, or crash the print head. PrintMCP is designed so that **no physical action happens
by accident.**

This page explains how that protection works and what your responsibilities are.

---

## The core rule: `confirm=true`

Every tool that physically actuates the printer takes a `confirm` parameter that defaults to
`false`. When `confirm` is `false` (or omitted), the tool performs a **dry run**:

- It validates your inputs and checks configuration.
- It describes *exactly* what it **would** do.
- It **sends nothing** to the printer.

Only when you pass `confirm=true` does the command actually reach the machine.

```text
octoprint_start_print(path="cup.gcode")
→ 🟡 "Safety check - nothing was sent to the printer.
      This would select 'cup.gcode' and begin printing it on the physical machine.
      Re-run with confirm=true to actually start the print…"

octoprint_start_print(path="cup.gcode", confirm=true)
→ 🟢 "Started printing 'cup.gcode'."
```

This **preview-then-commit** pattern is especially valuable when an AI assistant is driving:
the assistant can show you the dry-run result, and you decide whether to authorize the real
action.

---

## Which tools need `confirm`?

| Tool | Needs `confirm`? | Why |
|------|:----------------:|-----|
| `octoprint_get_status` | 👁️ No | Read-only. |
| `octoprint_list_files` | 👁️ No | Read-only. |
| `octoprint_get_job` | 👁️ No | Read-only. |
| `octoprint_connect` | 🔒 Yes | Changes the printer's connection state. |
| `octoprint_upload_file` | 🔒 Only to print | Uploading is harmless; `print_after_upload=true` starts a print. |
| `octoprint_start_print` | 🔒 Yes | Starts heaters + motors. |
| `octoprint_control_job` | 🔒 Yes | Pause/resume/**cancel** (cancel is destructive). |
| `octoprint_set_temperature` | 🔒 Yes | Drives a heater. |
| `octoprint_home` | 🔒 Yes | Moves the head/bed. |
| `octoprint_move` | 🔒 Yes | Moves the head. |

> [!NOTE]
> **Uploading is intentionally not gated.** Sending a file to OctoPrint's storage doesn't move
> the machine, so `octoprint_upload_file` runs without `confirm` — *unless* you ask it to print
> immediately (`print_after_upload=true`), which does require `confirm=true`.

---

## Additional guardrails

Beyond the confirm gate, a few defenses are built in:

### Temperature ceilings
`octoprint_set_temperature` enforces software limits **on top of** your firmware's own: the bed
is capped at **140 °C** and the tool at **300 °C**. A fat-fingered `target=2000` is rejected
before it's ever sent. (`target=0` is always allowed — it means "off".)

### Movement bounds
`octoprint_move` limits each relative move to ±500 mm and the feedrate to 1–12000 mm/min, so a
typo can't fling the head across the room at an absurd speed.

### Readiness checks
`octoprint_get_status` reports `ready: true` **only** when the printer is operational and not
already printing, paused, pausing, resuming, cancelling, finishing, or in an error state. This
keeps an assistant from starting a print on a busy or faulted machine.

### Credential protection
Your `OCTOPRINT_API_KEY` is sent **only** in the `X-Api-Key` header to your configured
`OCTOPRINT_URL`. It is never written into any tool's output or error text — there's an automated
test that enforces this. See [Configuration](configuration.md#octoprint-level-3).

---

## What PrintMCP does *not* protect against

The software gate is a backstop, not a babysitter. **You are still responsible for the physical
print.** In particular:

> [!WARNING]
> - **Never run an unattended print you haven't supervised starting.** Stay within reach of the
>   printer's power for the first layers.
> - **Confirm the bed is clear** before `octoprint_start_print` or `octoprint_home` — PrintMCP
>   can't see the bed.
> - **Match temperatures to your filament.** 200/60 °C suits PLA; other materials differ.
> - **Have a hardware emergency stop** (power switch / firmware abort) available. A cancelled
>   job stops sending G-code but won't substitute for cutting power in a real emergency.
> - **`cancel` is irreversible** — the partial print is wasted.

---

## Design rationale (for contributors)

The confirm gate lives in each tool, not in the transport layer, so the *intent* is explicit at
the call site and visible in the tool's schema. Dry-run responses are produced by a single
`_confirm_required(...)` helper for consistency, and the gate is checked **before** any network
call is built — verified by tests asserting that `confirm=false` produces **zero** HTTP
requests. See [Architecture](architecture.md) for how this fits the broader design.
