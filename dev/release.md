# Release Process

The canonical release path. Always via PR — release-prep commits go through
the same review pipeline as feature work.

## Versioning

Hypernote follows [semver](https://semver.org/) under the 0.x umbrella:

- **patch** (`0.2.0` → `0.2.1`): bug fixes, doc-only changes, internal refactors that do not affect the public SDK / CLI / HTTP surface.
- **minor** (`0.2.0` → `0.3.0`): new features, new commands, new HTTP routes, new SDK methods, additive changes that do not break existing users. This is the common bump while we are pre-1.0.
- **major** (`0.x` → `1.0`): API stability commitment. Not yet relevant.

When in doubt, bump minor. Pre-1.0 we err on the side of one bump per
substantive change.

## Steps

```bash
# 0. master must be clean and green
git checkout master
git pull --ff-only origin master
uv run ruff check src/hypernote tests
uv run python -m pytest -q
HYPERNOTE_INTEGRATION=1 uv run python -m pytest -q tests/test_browser_regression.py
```

```bash
# 1. branch
VERSION=0.3.0  # ← set this
git checkout -b release/v$VERSION
```

```bash
# 2. CHANGELOG: move the Unreleased section to a new dated version section,
#    leave Unreleased empty above. Include a one-paragraph headline that
#    captures the user-visible story of the release.
$EDITOR CHANGELOG.md
```

```bash
# 3. version bump (portable across macOS and Linux)
VERSION=$VERSION uv run python - <<'PY'
import os, pathlib, re
p = pathlib.Path("pyproject.toml")
v = os.environ["VERSION"]
s, n = re.subn(r'^version = ".*"$', f'version = "{v}"', p.read_text(), count=1, flags=re.M)
if n != 1:
    raise SystemExit("expected exactly one top-level version line in pyproject.toml")
p.write_text(s)
PY
uv lock
```

The workflow's manual recovery path runs on `ubuntu-latest` and uses GNU
`sed -i -E` inline; this Python heredoc is the same edit but works the
same way on macOS and Linux without remembering which `sed` is installed.
Either way, the only change to `pyproject.toml` is the top-level
`version` line.

```bash
# 4. PR
git add CHANGELOG.md pyproject.toml uv.lock
git commit -m "chore: prepare v$VERSION release"
git push -u origin release/v$VERSION
gh pr create \
  --title "chore: prepare v$VERSION release" \
  --body "Release prep for v$VERSION. CHANGELOG section moved, version bumped, lock refreshed. The release workflow runs automatically after merge."
```

```bash
# 5. wait for CI, address any review, merge into master
gh pr merge --merge   # or --squash, depending on team preference
```

Merging a release PR to `master` is the release trigger. The release workflow
reads the checked-in version from `pyproject.toml`, skips if `vX.Y.Z` already
exists, and otherwise builds, tests, tags, publishes a GitHub release, and
publishes to PyPI. `workflow_dispatch` remains available only as a recovery
fallback; the normal path should not require a manual workflow run. Bot-authored
pushes from the recovery path are ignored so the workflow cannot self-trigger a
second release run.

The release workflow then, in order (matches
[`.github/workflows/release.yml`](../.github/workflows/release.yml)):

1. **Resolves and validates** the release version: `workflow_dispatch` uses the
   explicit input, while merge-to-`master` reads `project.version` from
   `pyproject.toml`.
2. **Skips duplicate releases** if the `vX.Y.Z` tag already exists on a
   merge-to-`master` run.
3. **Ensures the version is checked in.** Release PRs should already have the
   version bump and lock refresh. Manual fallback runs may still commit the
   version bump.
4. **Builds** wheel + sdist via `uv build`.
5. **Verifies the wheel** installs with `uv run --isolated --no-project --with dist/*.whl python -c "import hypernote; print('ok')"`.
6. **Uploads the build artifacts** for the publish job.
7. **Runs the full test suite** under `--extra dev`, including Playwright with `--with-deps chromium`.
8. **Creates and pushes the `vX.Y.Z` git tag** after build and tests pass,
   using the `github-actions[bot]` identity for the annotated tag.
   Reruns tolerate an existing tag only when it already points at the current
   commit.
9. **Creates or updates the GitHub release** from the tag.
10. **Publishes** wheel + sdist to PyPI under `PYPI_API_TOKEN` (configured as a GitHub Actions secret).

Past releases took ~2-3 minutes end-to-end.

## Verifying the release landed

```bash
gh release view v$VERSION
curl -s https://pypi.org/pypi/hypernote/json | python3 -c "import sys, json; d=json.load(sys.stdin); print('latest:', d['info']['version'])"
uv run --isolated --no-project --with hypernote==$VERSION python -c "import hypernote; print(hypernote.__file__)"
```

## If something is wrong after publish

PyPI releases are immutable — you can **yank** a bad release but not edit
or replace it.

- **yank**: `uv run twine upload --skip-existing dist/*` won't fix anything; instead use the PyPI web UI ("yank release") or `pip install pypi-cli && pypi-cli yank hypernote $VERSION`. Yanked releases stop being installed by default but stay available for pinned installs that already named the version.
- **roll forward**: cut a fresh patch release immediately (`$VERSION` + 1 patch) with the fix. PR + workflow path as above. Note in the CHANGELOG that the previous version was yanked and why.
- **GitHub release**: editable. You can update the description, mark prerelease, or delete the release without affecting the tag. Deleting the tag is allowed but discouraged — it confuses anyone who pinned to it.

## What NOT to do

- **Do not push release-prep commits directly to master.** All releases go through PR review, even single-line CHANGELOG / version bumps. The exceptions in the 0.1.x history (0.1.0, 0.1.1, 0.1.2 were direct pushes) predate this discipline and are not the model going forward.
- **Do not edit a CHANGELOG entry after the corresponding version is published.** If you need to correct it, file a follow-up PR that adds a "Note" line under the next version explaining the correction.
- **Do not bump the version outside a release PR.** The checked-in version bump
  is the automatic release trigger once the PR lands on `master`.
- **Do not include local-only work in the CHANGELOG.** If `git ls-tree origin/master` doesn't show the files, they are not shipping. Drop the line or commit the files first.
- **Do not skip the integration test step before opening the release PR.**
  Browser tests catch the kernel-control regressions that unit tests cannot —
  late-open streaming, Lab Stop button, Lab Restart cleanup. CI runs them, but a
  local pass before opening the release PR catches problems faster.
