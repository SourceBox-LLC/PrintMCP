# 📦 Releasing PrintMCP to PyPI

PrintMCP publishes to [PyPI](https://pypi.org/project/printmcp/) automatically when a version tag
is pushed, using a scoped **PyPI API token** stored as the `PYPI_API_TOKEN` repo secret. The workflow
is [`.github/workflows/release.yml`](../.github/workflows/release.yml).

This page has two parts: a **one-time setup** (done once) and the **per-release** steps (a couple
of commands).

---

## One-time setup (maintainer)

### 1. Create the GitHub environment

In the GitHub repo: **Settings → Environments → New environment**, name it exactly **`pypi`**.
(Optional but recommended: add yourself as a required reviewer so each publish needs a click.)

### 2. Generate a PyPI API token

1. Sign in at <https://pypi.org> as [sourceboxai](https://pypi.org/user/sourceboxai/).
2. Go to **Account settings → API tokens → Add API token**.
3. Scope the token to the **`printmcp`** project (not "Entire account").
4. Copy the token — it starts with `pypi-`.

### 3. Add the token as a GitHub secret

In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**:

| Field | Value |
|-------|-------|
| Name | `PYPI_API_TOKEN` |
| Secret | *(paste the `pypi-...` token)* |

Or via the CLI:

```bash
gh secret set PYPI_API_TOKEN --repo SourceBox-LLC/PrintMCP
# paste the token when prompted
```

That's it — the workflow reads `PYPI_API_TOKEN` at publish time.

---

## Cutting a release (maintainer)

### 1. Bump the version

Edit `version` in [`pyproject.toml`](../pyproject.toml) (PrintMCP follows
[semantic versioning](https://semver.org/)):

```toml
[project]
version = "0.2.0"
```

Update [`CHANGELOG.md`](../CHANGELOG.md): move the items under `[Unreleased]` into a new
`[0.2.0] - YYYY-MM-DD` section, and refresh the compare links at the bottom.

Commit both to `master`:

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "release: v0.2.0"
git push origin master
```

> [!IMPORTANT]
> The tag must match the `pyproject.toml` version. The release workflow checks this and **fails
> the build** if they differ, so a forgotten version bump can't publish the wrong number.

### 2. Tag and push

```bash
git tag v0.2.0
git push origin v0.2.0
```

Pushing the `v*` tag triggers [`release.yml`](../.github/workflows/release.yml), which:

1. verifies the tag matches the package version,
2. builds the sdist + wheel with `uv build`,
3. validates metadata with `twine check`,
4. publishes to PyPI using `PYPI_API_TOKEN`.

Watch it under the repo's **Actions** tab (or `gh run watch`). When it's green, the new version is
live at <https://pypi.org/project/printmcp/>.

### 3. (Optional) Create a GitHub Release

Turn the tag into a GitHub Release for nicer changelog notes:

```bash
gh release create v0.2.0 --generate-notes
```

---

## Testing a release without publishing

To dry-run the build locally (exactly what CI does) before tagging:

```bash
rm -rf dist
uv build
uvx twine check dist/*
```

Both artifacts should report `PASSED`.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Tag vX does not match pyproject version` | Bump `version` in `pyproject.toml` (or retag). They must be equal. |
| Publish step: `InvalidDistribution` / 403 | The `PYPI_API_TOKEN` secret is missing, expired, or scoped to the wrong project. Regenerate and re-add it. |
| `File already exists` | That version was already published. PyPI is immutable — bump to a new version. |
| Workflow didn't trigger | The tag must start with `v` (e.g. `v0.2.0`) and be pushed (`git push origin v0.2.0`). |

More: [PyPI API tokens docs](https://docs.pypi.org/trusted-publishers/).