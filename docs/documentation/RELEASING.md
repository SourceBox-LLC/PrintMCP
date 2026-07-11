# 📦 Releasing PrintMCP to PyPI

PrintMCP publishes to [PyPI](https://pypi.org/project/printmcp/) automatically when a version tag
is pushed, using **[Trusted Publishing](https://docs.pypi.org/trusted-publishers/)** (OIDC) — no
API tokens or passwords are stored anywhere. The workflow is
[`.github/workflows/release.yml`](../.github/workflows/release.yml).

This page has two parts: a **one-time setup** (done once, on the PyPI website) and the
**per-release** steps (a couple of commands).

---

## One-time setup (maintainer, on pypi.org)

You only do this once. It tells PyPI to trust releases coming from this repo's GitHub Actions.

### 1. Create the GitHub environment

In the GitHub repo: **Settings → Environments → New environment**, name it exactly **`pypi`**.
(Optional but recommended: add yourself as a required reviewer so each publish needs a click.)

### 2. Register the Trusted Publisher on PyPI

Because `printmcp` doesn't exist on PyPI yet, use a **pending publisher**:

1. Sign in at <https://pypi.org> (create an account if needed).
2. Go to **<https://pypi.org/manage/account/publishing/>**.
3. Under **Add a new pending publisher → GitHub**, fill in **exactly**:

   | Field | Value |
   |-------|-------|
   | PyPI Project Name | `printmcp` |
   | Owner | `SourceBox-LLC` |
   | Repository name | `PrintMCP` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

4. Click **Add**.

> [!NOTE]
> A *pending* publisher doesn't reserve the name — it's claimed on the first successful publish.
> The project name must match `name` in [`pyproject.toml`](../pyproject.toml) (PyPI treats `-` and
> `_` as equivalent). After the first release it becomes a normal publisher; no further setup.

That's it. No secrets are added to GitHub — OIDC handles authentication at publish time.

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
4. publishes to PyPI via Trusted Publishing.

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

To rehearse the *whole* publish flow against a throwaway index, add a
[TestPyPI](https://test.pypi.org) pending publisher with the same details, then temporarily point
the publish step at it with `with: { repository-url: https://test.pypi.org/legacy/ }`. Remove that
before publishing for real.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Tag vX does not match pyproject version` | Bump `version` in `pyproject.toml` (or retag). They must be equal. |
| Publish step: `not authorized` / OIDC error | The pending publisher details don't match. Re-check owner `SourceBox-LLC`, repo `PrintMCP`, workflow `release.yml`, environment `pypi`. |
| `File already exists` | That version was already published. PyPI is immutable — bump to a new version. |
| Workflow didn't trigger | The tag must start with `v` (e.g. `v0.2.0`) and be pushed (`git push origin v0.2.0`). |

More: [PyPI Trusted Publishers docs](https://docs.pypi.org/trusted-publishers/).
