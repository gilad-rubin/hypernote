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
# 3. version bump
sed -i '' -E "0,/^version = \".*\"/s//version = \"$VERSION\"/" pyproject.toml
uv lock
```

```bash
# 4. PR
git add CHANGELOG.md pyproject.toml uv.lock
git commit -m "chore: prepare v$VERSION release"
git push -u origin release/v$VERSION
gh pr create \
  --title "chore: prepare v$VERSION release" \
  --body "Release prep for v$VERSION. CHANGELOG section moved, version bumped, lock refreshed. Triggers release workflow on merge."
```

```bash
# 5. wait for CI, address any review, merge into master
gh pr merge --merge   # or --squash, depending on team preference
```

```bash
# 6. trigger the release workflow
gh workflow run release.yml -f version=$VERSION -f publish_to_pypi=true -f create_draft=false
gh run list --workflow=release.yml --limit 1
```

The release workflow then:

1. Validates the version string is semver-shaped.
2. Re-runs `sed` on `pyproject.toml` against the merge commit (no-op if PR already bumped it).
3. Builds wheel + sdist via `uv build`.
4. Verifies the wheel installs with `uv run --isolated --no-project --with dist/*.whl python -c "import hypernote; print('ok')"`.
5. Runs the full test suite under `--extra dev`, including Playwright with `--with-deps chromium`.
6. Creates and pushes the `vX.Y.Z` git tag.
7. Creates the GitHub release.
8. Publishes wheel + sdist to PyPI under `PYPI_API_TOKEN` (configured as a GitHub Actions secret).

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
- **Do not bump the version on master without also running the release workflow.** A version bump that never gets tagged + published produces installs of "0.X.Y from git" that disagree with PyPI.
- **Do not include local-only work in the CHANGELOG.** If `git ls-tree origin/master` doesn't show the files, they are not shipping. Drop the line or commit the files first.
- **Do not skip the integration test step before pressing the release button.** Browser tests catch the kernel-control regressions that unit tests cannot — late-open streaming, Lab Stop button, Lab Restart cleanup. CI runs them, but a local pass before opening the release PR catches problems faster.
