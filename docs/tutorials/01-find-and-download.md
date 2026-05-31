# Tutorial 1 · Find & Download a Model

> **Goal:** search Thingiverse for something to print, check its license, and download the
> model file to your computer.
> **Time:** ~5 minutes · **You need:** a `THINGIVERSE_TOKEN` ([setup](../getting-started.md)).

This is the first step of the pipeline. By the end you'll have a real `.stl` file on disk,
ready to slice in [Tutorial 2](02-slice-for-your-printer.md).

We'll use a **coffee cup** as the running example all the way through these tutorials.

---

## Step 1 — Search

Ask for what you want to print. The assistant calls:

```text
thingiverse_search_models(query="coffee cup", limit=5)
```

You'll get back a handful of candidates, each with an **id**, name, creator, and like count:

```markdown
# Thingiverse results for 'coffee cup'

Showing 5 of 1234 matches (page 1).

## Coffee Cup (id: 159884)
- Creator: Barspin
- URL: https://www.thingiverse.com/thing:159884
- Likes: 42
- Next: `thingiverse_get_model(thing_id=159884)`
```

> [!TIP]
> Too many / too few results? Adjust `limit` (1–30) or page through with `page=2`. Make the
> query more specific (`"coffee cup handle"`) to narrow it down.

---

## Step 2 — Inspect (and check the license!)

Pick an `id` and look at the details before downloading:

```text
thingiverse_get_model(thing_id=159884)
```

```markdown
# Coffee Cup (id: 159884)

- Creator: Barspin
- License: Creative Commons - Attribution
- URL: https://www.thingiverse.com/thing:159884

A simple coffee cup model…

## Files (2)
- Coffee_Cup.A.1.stl (file_id: 12345, 35854899 bytes)
- Coffee_Cup_By_Barspin_WO_Support_Piece.stl (file_id: 12346, 1684584 bytes)
```

> [!IMPORTANT]
> **Always read the `License` line.** Many Thingiverse models are non-commercial or require
> attribution. PrintMCP surfaces the license here precisely so you can respect it before
> printing, remixing, or selling. If it says `unknown`, verify on the model's page.

---

## Step 3 — Download

Grab the model files. By default PrintMCP downloads only **printable** files
(`.stl`, `.3mf`, `.obj`, `.step`, `.stp`, `.amf`, `.ply`) and skips images/READMEs.

```text
thingiverse_download_model(thing_id=159884)
```

```markdown
# Downloaded 2 file(s) from thing 159884
- Model: Coffee Cup
- License: Creative Commons - Attribution
- Saved to: C:\Users\Sbuss\PrintMCP\downloads\thing-159884

- Coffee_Cup.A.1.stl (35854899 bytes)
- Coffee_Cup_By_Barspin_WO_Support_Piece.stl (1684584 bytes)
```

### Where did the files go?

Into `~/PrintMCP/downloads/thing-<id>/` — on the reference machine:

```
C:\Users\Sbuss\PrintMCP\downloads\
└── thing-159884\
    ├── Coffee_Cup.A.1.stl                     ← we'll slice this in Tutorial 2
    └── Coffee_Cup_By_Barspin_WO_Support_Piece.stl
```

You can change the location with `PRINTMCP_DOWNLOAD_DIR`, or the subfolder name with the
`dest_subdir` parameter. Full details in
[Configuration → Where print files are stored](../configuration.md#-where-print-files-are-stored).

---

## Variations

**Download just one file** (using a `file_id` from Step 2):

```text
thingiverse_download_model(thing_id=159884, file_id=12345)
```

**Download everything**, including images and docs:

```text
thingiverse_download_model(thing_id=159884, include_all_files=true)
```

**Use a friendlier folder name:**

```text
thingiverse_download_model(thing_id=159884, dest_subdir="coffee-cup")
```

---

## ✅ Checkpoint

You now have a `.stl` file on disk and you've confirmed its license. 

**Next:** [Tutorial 2 · Slice for Your Printer](02-slice-for-your-printer.md) — turn this model
into G-code.

---

<sub>Full parameter reference: [Level 1 · Thingiverse Tools](../tools/thingiverse.md).</sub>
