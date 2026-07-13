# ADR-010: Automated quality gates — CI + pre-commit

- **Status:** Accepted; environment mechanism superseded by ADR-014 / Beta 0
- **Date:** 2026-05-25
- **Project phase:** 1 (Foundation)
- **Authors:** Janek Płoński, Codex

> **Implementation note (2026-07-13):** the decision to run the same `make check` gate in
> CI and locally remains active. The original micromamba/`environment.yml` implementation
> is historical: MR-Session 3 Beta now pins managed CPython 3.12 and `uv`, installs from
> `uv.lock`, and runs the unchanged quality-gate contract through `uv run --locked`.

## Context

Phase 1 already has the local quality toolchain: `ruff`, `mypy`, `pytest`, a
Makefile-level `make check` gate, and a conda `environment.yml` that installs
Python 3.11 plus TA-Lib from conda-forge. The remaining gap is automation:
local checks are documented, but nothing enforces them on GitHub pushes or pull
requests, and there is no committed pre-commit configuration.

The project also has one important CI constraint: tests must be deterministic
and must not require Binance/API access, secrets, or live network availability.
Live exchange checks are useful during manual diagnostics, but they are not a
valid default for `make check` or GitHub Actions.

The Foundation phase goal is intentionally small: a boring, reproducible gate
that runs the same command locally and remotely. Coverage reporting,
dependency-update automation, conventional commit enforcement, and deployment
automation are out of scope for this phase.

## Decision

Use GitHub Actions as the CI platform and make `make check` the only CI quality
gate.

The workflow lives at `.github/workflows/check.yml` and runs on:

- `pull_request`
- `push` to `master`

The CI environment is created from `environment.yml` with
`mamba-org/setup-micromamba@v2`. The workflow uses the single project-supported
Python version, Python 3.11, and installs the package with:

```bash
python -m pip install -e ".[dev]"
```

The workflow then runs:

```bash
make check
```

No secrets are configured for CI. Tests that touch live exchanges must be marked
`live` and skipped unless explicitly opted in locally.

Use pre-commit for fast local file-level checks. The committed
`.pre-commit-config.yaml` includes:

- `pre-commit/pre-commit-hooks` for whitespace, EOF, YAML/TOML syntax, merge
  conflicts, and large added files.
- `astral-sh/ruff-pre-commit` with `ruff-check` and `ruff-format`.

Do not run `mypy` in pre-commit. `mypy` remains part of the heavier project gate
through `make check` locally and in CI.

Do not add coverage reporting, conventional commit hooks, Renovate, Dependabot,
or pre-commit autoupdate automation in Phase 1.

## Consequences

**Positive:**

- Local and remote quality gates use the same command: `make check`.
- GitHub checks run on both direct `master` pushes and pull requests.
- The CI environment matches local setup by using `environment.yml` and
  conda-forge TA-Lib.
- Pre-commit catches cheap formatting and file hygiene issues before commit.
- CI remains deterministic because live API tests are opt-in.

**Negative / costs:**

- First CI runs may still be slow while the micromamba cache warms up.
- Hook versions are pinned and will need manual updates when desired.
- `mypy` feedback happens at `make check` time rather than every commit.

**Risks:**

- If a future test performs network I/O during import or collection, CI can
  become flaky again. Such tests must be converted to explicit `live` tests.
- If `environment.yml` and `pyproject.toml` drift, CI can reproduce the wrong
  environment. Dependency changes must continue to update the relevant files
  together.

## Alternatives Considered

- **Run pytest/ruff/mypy directly in GitHub Actions** — rejected because
  `make check` is already the project quality gate and avoids duplicated CI
  logic.
- **Pure pip CI without conda** — rejected because TA-Lib is intentionally
  installed from conda-forge through `environment.yml`.
- **Add mypy to pre-commit** — rejected because it makes commits slower and
  duplicates the heavier `make check` gate. Strict-on-new remains enforced by
  mypy in CI.
- **Coverage reporting** — rejected for Phase 1 because it adds overhead without
  changing the Foundation cleanup goal.
- **Conventional commit hooks** — rejected because this is a solo project and
  commit-message enforcement is unnecessary overhead.
- **Renovate/Dependabot/pre-commit autoupdate** — rejected for MVP to avoid noisy
  maintenance churn.

## References

- `Makefile` — `make check`, `make precommit-install`, `make precommit-run`
- `environment.yml` — Python 3.11 + TA-Lib conda environment
- `pyproject.toml` — ruff, mypy, pytest, and CLI configuration
- ADR-002 — pyproject, hatchling, conda, ruff, mypy
- ADR-006 — logging entry-point conventions
