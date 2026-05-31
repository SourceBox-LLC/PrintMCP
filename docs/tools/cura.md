# 🧊 Level 2 · Cura Tools

Level 2 is **slicing** — converting a 3D model into the G-code a printer actually executes. It
drives the headless **CuraEngine** bundled with [Ultimaker Cura](https://ultimaker.com/software/ultimaker-cura/)
(not the GUI). Cura is auto-detected on Windows; see
[Configuration → Cura](../configuration.md#cura-level-2) to point at a non-standard install.

| Tool | Purpose |
|------|---------|
| [`cura_slice_model`](#cura_slice_model) | Slice a local model file into printer-ready G-code. |

---

## `cura_slice_model`

Takes a downloaded model file (e.g. from `thingiverse_download_model`) and produces a `.gcode`
file for a specific printer, reporting the estimated print time and filament usage.

This tool writes a file but does **not** touch a printer, so it needs no `confirm`.

### Parameters

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `model_path` | str | — (required) | non-empty | Path to a local model file. |
| `printer` | str | `creality_ender3pro` | ≤ 128 chars, an id (not a path) | Cura printer-definition id (the `.def.json` filename without extension). |
| `layer_height` | float | `0.2` | 0.05–0.6 | Layer height in mm. Lower = finer but slower. |
| `infill_density` | int | `20` | 0–100 | Infill as a percentage. |
| `supports` | bool | `false` | — | Generate support structures for overhangs. |
| `adhesion_type` | str | `skirt` | `skirt` \| `brim` \| `raft` \| `none` | Build-plate adhesion strategy. |
| `material_print_temperature` | int | `200` | 150–300 | Nozzle temperature °C (~200 for PLA). |
| `material_bed_temperature` | int | `60` | 0–120 | Bed temperature °C (~60 for PLA). |
| `output_path` | str \| null | `null` | — | Where to write the `.gcode`. Defaults to the model path with a `.gcode` extension. |
| `response_format` | str | `markdown` | `markdown` \| `json` | Output format. |

### Accepted input formats

`.stl`, `.obj`, `.3mf`, `.amf`, `.ply`.

### Returns

```json
{
  "model": "Coffee_Cup.A.1.stl",
  "printer": "creality_ender3pro",
  "gcode_path": "C:\\Users\\Sbuss\\PrintMCP\\downloads\\thing-159884\\Coffee_Cup.A.1.gcode",
  "gcode_size_bytes": 18555302,
  "settings": {
    "layer_height": 0.2, "infill_density": 20, "supports": false,
    "adhesion_type": "skirt", "material_print_temperature": 200,
    "material_bed_temperature": 60
  },
  "stats": {
    "print_time": "6h 31m 31s", "print_time_s": 23491,
    "filament_m": 25.476, "filament_mm3": 61277
  }
}
```

### Where the G-code lands

By default, **next to the source model**: slicing `…\cup.stl` produces `…\cup.gcode`. Override
with `output_path`. See
[Configuration → Where print files are stored](../configuration.md#-where-print-files-are-stored).

### Examples

```text
# Defaults: Ender-3 Pro, 0.2 mm, 20% infill, PLA temps
cura_slice_model(model_path="C:\\Users\\Sbuss\\PrintMCP\\downloads\\thing-159884\\Coffee_Cup.A.1.stl")

# Finer, denser, with supports
cura_slice_model(
  model_path="…\\Coffee_Cup.A.1.stl",
  layer_height=0.12, infill_density=40, supports=true
)

# A different printer definition + custom output path
cura_slice_model(model_path="…\\part.stl", printer="ultimaker_s5", output_path="D:\\gcode\\part.gcode")
```

---

## Choosing settings: a quick primer

| Setting | Faster / cheaper | Stronger / prettier |
|---------|------------------|---------------------|
| **Layer height** | Higher (0.28–0.3 mm) | Lower (0.12–0.16 mm) |
| **Infill** | Lower (10–15%) | Higher (40–60%) |
| **Supports** | Off (design permitting) | On for steep overhangs |
| **Adhesion** | `skirt` (least waste) | `brim`/`raft` for warp-prone prints |

> [!TIP]
> For a tall, flat-bottomed object like a cup, `skirt` adhesion and ~20% infill at 0.2 mm is a
> sensible default — that's exactly what the Tutorial 2 example uses.

---

## How it works under the hood

CuraEngine is invoked headlessly. A few things PrintMCP handles for you so the engine produces
correct output:

- **Printer definitions** are resolved from Cura's bundled resources by the `printer` id, with
  the extruder-train search path set so inherited definitions resolve.
- **Global settings precede the model** on the command line — otherwise CuraEngine treats them
  as per-mesh and silently ignores global-only ones like `layer_height`.
- **Print-time and filament stats** are read from the engine's own log output (the G-code
  header's `;TIME`/`;Filament` fields are placeholders when run standalone).

You don't need to manage any of that — it's documented in [Architecture](../architecture.md)
for the curious.

---

## Common errors

| Message | Cause | Fix |
|---------|-------|-----|
| `Error: model file not found: …` | Bad `model_path` | Check the path from the download step. |
| `Error: '…' is not a sliceable model …` | Unsupported extension | Use `.stl/.obj/.3mf/.amf/.ply`. |
| `Could not find Ultimaker Cura …` | Cura not installed/detected | Install Cura, or set `PRINTMCP_CURA_DIR` ([config](../configuration.md#cura-level-2)). |
| `printer definition '…' not found …` | Unknown `printer` id | Use a valid Cura definition id. |
| `slicing failed (CuraEngine exit N): …` | Engine error | Read the detail; often a bad setting or a model larger than the build volume. |

See the [Troubleshooting](../troubleshooting.md) guide for more.

---

**Next:** send your G-code to the printer — [Level 3 · OctoPrint Tools](octoprint.md).
