# Development Docs

This folder is internal reference for the shipped Hypernote architecture.

Public workflow docs live in `docs/`. This folder is for implementation-facing notes only.

Read these first:

- [Current Architecture](current-architecture.md)
- [Module Map](module-map.md)
- [Testing and Verification](testing-and-verification.md)
- [CLI Agent Ergonomics Rollout](cli-agent-ergonomics-rollout.md)
- [VS Code Extension Notes](vscode-extension.md)

Release path:

- trigger the release workflow from GitHub Actions UI or `gh workflow run release.yml -f version=X.Y.Z`
- the workflow bumps `pyproject.toml`, syncs `uv.lock`, commits, tags, builds, tests, creates a GitHub release, and publishes to PyPI
- PyPI auth comes from the GitHub Actions secret `PYPI_API_TOKEN`
- see [release.yml](../.github/workflows/release.yml)

Rules:

- keep this folder small
- document shipped behavior, not aspirational redesigns
- when behavior changes, update `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/` together
