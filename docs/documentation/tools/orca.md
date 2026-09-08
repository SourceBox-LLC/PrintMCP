# 🧊 Level 2 · OrcaSlicer Tools

<sub>↑ [Tool reference overview](README.md) — the shared invocation model, schemas, and error contract.</sub>

Level 2 is **slicing** — converting a 3D model into the G-code a printer actually executes. This
page covers **OrcaSlicer** (an alternative to [Cura](cura.md)). It drives the OrcaSlicer CLI; the
slicer is auto-detected on native installs or Flatpak. See
[Configuration → OrcaSlicer](../configuration.md#orcaslicer-level-2-alternative-to-cura) to pin a
non-standard setup.

| Tool | Purpose |
|------|---------|
| [`orca_list_profiles`](#orca_list_profiles) | List available machine / process / filament presets. |
| [`orca_slice_model`](#orca_slice_model) | Slice a local model file into printer-ready G-code. |

OrcaSlicer uses a **3-tier preset model**: a **machine** preset (your printer), a **process**
preset (print quality / layer height), and a **filament** preset (material). All three are
selected by *name* when you slice. `orca_list_profiles` exists precisely so an agent (or you) can
discover the exact names — preset names differ per vendor and per nozzle size.

---

## `orca_list_profiles`

Lists the presets available in a tier, with an optional name filter. Read-only; safe to call any
time.

### Parameters

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `tier` | str | `machine` | `machine` \| `process` \| `filament` | Which preset tier to list. |
| `filter` | str \| null | `null` | ≤ 100 chars | Case-insensitive substring filter (e.g. `Ender`, `PLA`). |

### Returns

```json
{
  "tier": "machine",
  "count": 3,
  "presets": [
    { "name": "Creality Ender-3 Pro", "file": "Creality Ender-3 Pro.json" },
    { "name": "Creality Ender-3 Pro 0.2 nozzle", "file": "Creality Ender-3 Pro 0.2 nozzle.json" },
    { "name": "Creality Ender-3 Pro 0.4 nozzle", "file": "Creality Ender-3 Pro 0.4 nozzle.json" }
  ]
}
```

### Example

```text
orca_list_profiles(tier="machine", filter="Ender")
# -> presets include "Creality Ender-3 Pro 0.4 nozzle"
```

---

## `orca_slice_model`

Takes a downloaded model file (e.g. from `thingiverse_download_model`) and produces a `.gcode`
file using the chosen 3-tier presets, reporting the estimated print time and filament usage parsed
from the produced G-code.

This tool writes a file but does **not** touch a printer, so it needs no `confirm`.

### Parameters

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `model_path` | str | — (required) | non-empty | Path to a local model file. |
| `machine` | str | — (required) | a preset name | Machine preset, e.g. `Creality Ender-3 Pro 0.4 nozzle`. |
| `process` | str | — (required) | a preset name | Process (print/quality) preset, e.g. `0.20mm Standard @Creality Ender3 Pro 0.4`. |
| `filament` | str | — (required) | a preset name | Filament preset, e.g. `Creality Generic PLA`. |
| `output_path` | str \| null | `null` | — | Where to write the `.gcode`. Defaults to the model path with a `.gcode` extension. |
| `overrides` | object \| null | `null` | — | Optional map of OrcaSlicer setting overrides applied to the preset copies, e.g. `{"layer_height": 0.16, "sparse_infill_density": "15%"}` or `{"enable_support": true}`. |
| `response_format` | str | `markdown` | `markdown` \| `json` | Output format. |

### Accepted input formats

`.stl`, `.obj`, `.3mf`, `.amf`, `.ply`, `.step`, `.stp`.

### Returns

```json
{
  "slicer": "orcaslicer",
  "model": "/home/me/Downloads/cube.stl",
  "gcode_path": "/home/me/Downloads/cube.gcode",
  "gcode_size_bytes": 282084,
  "settings": {
    "machine": "Creality Ender-3 Pro 0.4 nozzle",
    "process": "0.20mm Standard @Creality Ender3 Pro 0.4",
    "filament": "Creality Generic PLA"
  },
  "stats": { "print_time": "24m 21s", "filament_m": 1.331 }
}
```

### Examples

```text
# Slice with the standard Ender-3 Pro 0.4 nozzle preset trio
orca_slice_model(
  model_path="/home/me/Downloads/benchy.stl",
  machine="Creality Ender-3 Pro 0.4 nozzle",
  process="0.20mm Standard @Creality Ender3 Pro 0.4",
  filament="Creality Generic PLA"
)

# Same printer, but finer layers + supports via overrides
orca_slice_model(
  model_path="/home/me/Downloads/dragon.stl",
  machine="Creality Ender-3 Pro 0.4 nozzle",
  process="0.20mm Standard @Creality Ender3 Pro 0.4",
  filament="Creality Generic PLA",
  overrides={"layer_height": 0.12, "enable_support": true}
)
```

---

## How it works under the hood

- **Presets resolve by name.** `orca_slice_model` searches every vendor's `machine/`,
  `process/`, and `filament/` dirs (plus the global `OrcaFilamentLibrary`) for the named preset
  and resolves it to a JSON file. If a name isn't found, the error suggests the closest matches.
- **Overrides are applied to copies.** The CLI doesn't accept bare `key=value` overrides, and a
  second machine-type file in `--load-settings` errors, so overrides are written into the staged
  preset *copies* that get handed to the slicer.
- **Flatpak runs in a sandbox.** The launcher grants the sandbox access (`--filesystem=`) to the
  staging dir, the output dir, and the model's directory before slicing.
- **Stats come from the G-code footer** (the `CONFIG_BLOCK` tail): estimated print time and
  filament used (`filament used [mm]` → reported in metres).

More detail: [Architecture → Level 2](../architecture.md#level-2--slicing-curapy-and-orca-py).

---

## Common errors

| Message | Cause | Fix |
|---------|-------|-----|
| `Unknown machine preset '…'` | Preset name not found | Call `orca_list_profiles(tier="machine")` to get the exact name. |
| `Unknown process preset '…'` | Same for process | Match the process to your nozzle size (the `@<name> 0.4 nozzle` suffix). |
| `Model file not found: …` | Bad `model_path` | Use the path from the download step. |
| `Unsupported model type '.…'` | Extension not sliceable | Use `.stl/.obj/.3mf/.amf/.ply/.step/.stp`. |
| `OrcaSlicer failed to slice …` | Engine error | Read the detail; often a bad setting or a preset that doesn't match the machine. |
| `Could not find the OrcaSlicer CLI …` | Not installed / not detected | Install OrcaSlicer, or set `PRINTMCP_ORCA_COMMAND` ([config](../configuration.md#printmcp_orca_command)). |

See the [Troubleshooting](../troubleshooting.md) guide for more.

---

**Next:** send your G-code to the printer — [Level 3 · OctoPrint Tools](octoprint.md).
