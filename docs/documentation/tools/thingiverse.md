# 🔎 Level 1 · Thingiverse Tools

<sub>↑ [Tool reference overview](README.md) — the shared invocation model, schemas, and error contract.</sub>

Level 1 is **sourcing** — finding printable models and pulling their files onto your disk. It
talks to the [Thingiverse REST API](https://www.thingiverse.com/developers) and requires a
`THINGIVERSE_TOKEN` ([how to get one](../configuration.md#thingiverse-level-1)).

All three tools are 👁️ **read-only** with respect to your machine (download writes files to
disk but touches nothing else) and accept `response_format` (`markdown` or `json`).

| Tool | Purpose |
|------|---------|
| [`thingiverse_search_models`](#thingiverse_search_models) | Keyword search for printable "things". |
| [`thingiverse_get_model`](#thingiverse_get_model) | Details + **license** + file list for one thing. |
| [`thingiverse_download_model`](#thingiverse_download_model) | Download a thing's files to local disk. |

---

## `thingiverse_search_models`

Search Thingiverse for printable models by keyword. Use this first when someone wants to print
something.

### Parameters

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `query` | str | — (required) | 1–200 chars | Search terms, e.g. `"coffee cup"`. |
| `limit` | int | `20` | 1–30 | Max results to return. |
| `page` | int | `1` | ≥ 1 | 1-based page for pagination. |
| `response_format` | str | `markdown` | `markdown` \| `json` | Output format. |

### Returns

A list of candidate models. In JSON:

```json
{
  "query": "coffee cup",
  "total": 1234,
  "count": 20,
  "page": 1,
  "results": [
    {"id": 159884, "name": "Coffee Cup", "creator": "Barspin",
     "url": "https://www.thingiverse.com/thing:159884",
     "thumbnail": "https://...", "like_count": 42, "is_nsfw": false}
  ]
}
```

### Example

```text
thingiverse_search_models(query="coffee cup", limit=5)
```

> [!TIP]
> Results are lightweight summaries. Take an `id` and call
> [`thingiverse_get_model`](#thingiverse_get_model) to see the license and files before
> downloading.

---

## `thingiverse_get_model`

Get full details for one thing, **including its license** (many models are non-commercial) and
the list of downloadable files with their IDs and sizes.

### Parameters

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `thing_id` | int | — (required) | ≥ 1 | The thing ID, from search results. |
| `response_format` | str | `markdown` | `markdown` \| `json` | Output format. |

### Returns

```json
{
  "id": 159884, "name": "Coffee Cup", "creator": "Barspin",
  "license": "Creative Commons - Attribution",
  "url": "https://www.thingiverse.com/thing:159884",
  "description": "A simple coffee cup…",
  "file_count": 2,
  "files": [
    {"file_id": 12345, "name": "Coffee_Cup.A.1.stl", "size_bytes": 35854899,
     "download_url": "https://..."}
  ]
}
```

### Example

```text
thingiverse_get_model(thing_id=159884)
```

> [!IMPORTANT]
> **Always check the license.** It's surfaced here precisely so models aren't misused. Respect
> it before reusing, remixing, or selling a print. See [Model licensing](../../README.md#️-model-licensing).

---

## `thingiverse_download_model`

Download a thing's files to the local download directory. By default it grabs only **printable
model files** (`.stl`, `.3mf`, `.obj`, `.step`, `.stp`, `.amf`, `.ply`).

### Parameters

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `thing_id` | int | — (required) | ≥ 1 | The thing whose files to download. |
| `file_id` | int \| null | `null` | ≥ 1 | Download just this one file (from `get_model`). Omit for all model files. |
| `include_all_files` | bool | `false` | — | Also fetch non-model files (images, READMEs, …). |
| `dest_subdir` | str \| null | `null` | ≤ 128 chars | Destination subfolder name. Defaults to `thing-<id>`; path separators are stripped. |
| `response_format` | str | `markdown` | `markdown` \| `json` | Output format. |

### Returns

```json
{
  "thing_id": 159884, "name": "Coffee Cup",
  "license": "Creative Commons - Attribution",
  "dest_dir": "C:\\Users\\Sbuss\\PrintMCP\\downloads\\thing-159884",
  "downloaded_count": 2,
  "files": [
    {"name": "Coffee_Cup.A.1.stl", "path": "C:\\...\\Coffee_Cup.A.1.stl", "size_bytes": 35854899}
  ],
  "skipped": [{"name": "preview.png", "reason": "not a model file (.png)"}]
}
```

### Where files land

`<download_dir>/<dest_subdir>/` — by default `~/PrintMCP/downloads/thing-<id>/`. See
[Configuration → Where print files are stored](../configuration.md#-where-print-files-are-stored).

### Examples

```text
thingiverse_download_model(thing_id=159884)                 # all model files
thingiverse_download_model(thing_id=159884, file_id=12345)  # just one file
thingiverse_download_model(thing_id=159884, dest_subdir="coffee-cup")
```

---

## Common errors

| Message | Cause | Fix |
|---------|-------|-----|
| `Error: THINGIVERSE_TOKEN is not set …` | No token configured | Set it in `.env` ([guide](../configuration.md#thingiverse-level-1)). |
| `Authentication failed (401) …` | Token missing/invalid/expired | Re-issue the token. |
| `Not found (404) …` | Wrong `thing_id` | Re-check the ID from search. |
| `Rate limited (429) …` | Too many requests | Wait and retry. |

See the [Troubleshooting](../troubleshooting.md) guide for more.

---

**Next:** once you've downloaded a model, slice it — [Level 2 · Cura Tools](cura.md).
