# Tutorial 2 · Slice for Your Printer

> **Goal:** turn the model you downloaded into printer-ready **G-code**, and read its estimated
> print time and filament use.
> **Time:** ~5 minutes (plus slicing time) · **You need:** Ultimaker Cura installed
> ([setup](../getting-started.md)) and a model from [Tutorial 1](01-find-and-download.md).

Slicing converts a 3D *shape* (`.stl`) into the precise *instructions* (`.gcode`) your printer
executes — layer by layer, at the speeds and temperatures you choose. This step is **fully
local**: no printer required.

We'll continue with the coffee cup from Tutorial 1.

---

## Step 1 — Slice with defaults

The simplest call points at your downloaded model:

```text
cura_slice_model(model_path="C:\\Users\\Sbuss\\PrintMCP\\downloads\\thing-159884\\Coffee_Cup.A.1.stl")
```

The defaults are sensible for PLA on a **Creality Ender-3 Pro**: `0.2 mm` layers, `20%` infill,
no supports, skirt adhesion, 200 °C nozzle / 60 °C bed.

```markdown
# Sliced Coffee_Cup.A.1.stl

- Printer: creality_ender3pro
- G-code: C:\Users\Sbuss\PrintMCP\downloads\thing-159884\Coffee_Cup.A.1.gcode
- Size: 18555302 bytes
- Estimated print time: 6h 31m 31s
- Filament: 25.476 m (61277 mm3)
- Settings: 0.2mm layers, 20% infill, adhesion skirt, supports off
```

### Where did the G-code go?

**Right next to the model**, with a `.gcode` extension:

```
C:\Users\Sbuss\PrintMCP\downloads\thing-159884\
├── Coffee_Cup.A.1.stl
└── Coffee_Cup.A.1.gcode      ← new! ready to print
```

That's the file you'll upload in [Tutorial 3](03-print-with-octoprint.md).

---

## Step 2 — Tune the settings (optional)

Real prints are a trade-off between speed, strength, and finish. A few common adjustments:

### Finer detail (slower)

```text
cura_slice_model(model_path="…\\Coffee_Cup.A.1.stl", layer_height=0.12)
```

### Stronger part (more plastic)

```text
cura_slice_model(model_path="…\\Coffee_Cup.A.1.stl", infill_density=40)
```

### Add supports (for overhangs)

```text
cura_slice_model(model_path="…\\Coffee_Cup.A.1.stl", supports=true)
```

### A different material's temperatures

```text
cura_slice_model(
  model_path="…\\Coffee_Cup.A.1.stl",
  material_print_temperature=215,   # e.g. PETG-ish
  material_bed_temperature=70
)
```

### Cheat sheet

| Want… | Change | Toward |
|-------|--------|--------|
| Faster / less filament | `layer_height` ↑, `infill_density` ↓ | 0.28 mm, 10–15% |
| Finer / stronger | `layer_height` ↓, `infill_density` ↑ | 0.12 mm, 40–60% |
| Reliable adhesion (warp-prone) | `adhesion_type="brim"` or `"raft"` | — |
| Steep overhangs | `supports=true` | — |

> [!TIP]
> Re-slicing **overwrites** the `.gcode` at the same path. To keep multiple variants, give each
> its own `output_path`, e.g. `output_path="…\\cup_fine.gcode"`.

---

## Step 3 — Sanity-check the estimate

The output's `Estimated print time` and `Filament` come straight from CuraEngine. Use them to:

- **Confirm settings did what you expected** — finer layers → longer time; more infill → more
  filament.
- **Check you have enough filament** on the spool before committing.
- **Catch mistakes early** — a wildly off estimate often means the wrong layer height or scale.

> [!NOTE]
> For the coffee cup: 0.2 mm / 20% gives ~6½ hours and ~25 m of filament. Bumping to 0.3 mm
> layers and 10% infill drops it substantially — a good "draft print" recipe.

---

## ✅ Checkpoint

You have a `.gcode` file next to your model, with a realistic time/filament estimate. 

**Next:** [Tutorial 3 · Print with OctoPrint](03-print-with-octoprint.md) — send it to the
printer and watch it run.

> [!IMPORTANT]
> If you don't have an OctoPrint-connected printer, you can still stop here with a valid G-code
> file — copy it to an SD card and print the traditional way.

---

<sub>Full parameter reference: [Level 2 · Cura Tools](../tools/cura.md).</sub>
