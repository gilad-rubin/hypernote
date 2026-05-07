# Development Docs

This folder is internal reference for the shipped Hypernote architecture.

Public workflow docs live in `docs/`. This folder is for implementation-facing notes only.

Read these first:

- [Current Architecture](current-architecture.md)
- [Module Map](module-map.md)
- [Testing and Verification](testing-and-verification.md)
- [Release Process](release.md)
- [CLI Agent Ergonomics Rollout](cli-agent-ergonomics-rollout.md)

Releases: see [Release Process](release.md). Always via PR — release-prep
commits go through review like feature work. Workflow lives at
[`.github/workflows/release.yml`](../.github/workflows/release.yml); PyPI
auth via the `PYPI_API_TOKEN` GitHub Actions secret.

Rules:

- keep this folder small
- document shipped behavior, not aspirational redesigns
- when behavior changes, update `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/` together
- prefer one short source of truth per principle instead of repeating long process notes across many docs
- use this folder for implementation discipline and architecture notes; keep public contract wording in `docs/`
- for cross-surface changes, write down the invariants that must stay true across variants, not just the happy-path workflow
