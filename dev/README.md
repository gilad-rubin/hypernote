# Development Docs

This folder is internal reference for the shipped Hypernote architecture.

Public workflow docs live in `docs/`. This folder is for implementation-facing notes only.

Read these first:

- [Current Architecture](current-architecture.md)
- [Module Map](module-map.md)
- [Testing and Verification](testing-and-verification.md)
- [VS Code Extension Notes](vscode-extension.md)

Release path:

- pushing a tag like `v0.1.2` runs [release.yml](../.github/workflows/release.yml)
- the workflow verifies the tag matches `pyproject.toml`, builds `dist/`, creates or updates the GitHub release with `gh`, and publishes to PyPI as `hypernote`
- PyPI auth comes from the GitHub Actions secret `PYPI_API_TOKEN`

Rules:

- keep this folder small
- document shipped behavior, not aspirational redesigns
- when behavior changes, update `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/` together
