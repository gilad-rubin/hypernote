# Development Docs

This folder is internal reference for the shipped Hypernote architecture.

Public workflow docs live in `docs/`. This folder is for implementation-facing notes only.

Read these first:

- [Current Architecture](/Users/giladrubin/python_workspace/hypernote/dev/current-architecture.md)
- [Module Map](/Users/giladrubin/python_workspace/hypernote/dev/module-map.md)
- [Testing and Verification](/Users/giladrubin/python_workspace/hypernote/dev/testing-and-verification.md)

Release path:

- pushing a tag like `v0.1.0` runs [release.yml](/Users/giladrubin/python_workspace/hypernote/.github/workflows/release.yml)
- the workflow verifies the tag matches `pyproject.toml`, builds `dist/`, creates a GitHub release, and publishes to PyPI as `hypernote`
- PyPI auth comes from the GitHub Actions secret `PYPI_API_TOKEN`

Rules:

- keep this folder small
- document shipped behavior, not aspirational redesigns
- when behavior changes, update `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/` together
