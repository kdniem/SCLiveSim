# Contributing to SC LiveSim

Thanks for your interest! This project is built in public from the first commit.

## Ground rules

- **Schema first.** Changes to the core schema need an issue and discussion
  first. The schema is versioned separately (SemVer) and published as JSON Schema.
  Industry-specific fields go into `attributes`, not new tables.
- **Reproducibility.** Same scenario version + same seed + same engine version
  must produce an identical event stream. The determinism test must always pass.
- **Architecture decisions** go into `docs/adr/NNNN-title.md`.
- **No raw datasets, secrets or `.env` files** in commits.
- **Licenses.** No dependencies under the Business Source License or other
  non-OSI licenses in the core. Name the license of every new dependency or
  dataset in your pull request.

## Development setup

```bash
uv sync
cp .env.example .env
docker compose up -d
```

Before opening a pull request:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`).
- Keep pull requests small and reviewable. Every feature comes with tests.
- Write code, docs and comments in English, for people who are new to the project.

## Licensing of contributions

By contributing, you agree that your code is licensed under Apache-2.0 and that
schema and scenario contributions are licensed under CC BY 4.0.
