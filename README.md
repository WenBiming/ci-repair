# ci-repair

Master's thesis project (KTH): reproduce historical CI compilation failures of an
open-source project in Docker, then repair them with an LLM, validated by rebuilding.
First target project: [meilisearch](https://github.com/meilisearch/meilisearch) (Rust/Cargo).

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

Needs Docker and a GitHub token (taken from `GITHUB_TOKEN` or `gh auth token`).

## Pipeline

```bash
.venv/bin/ci-repair collect --days 90     # archive failed CI runs + full job logs
.venv/bin/ci-repair cases                 # extract compile-failure cases + human fixes
.venv/bin/ci-repair reproduce --limit 10  # rebuild in Docker, compare with CI
.venv/bin/ci-repair repair --repairer oracle --limit 5   # harness check
```

| Step | Output | In git |
|---|---|---|
| collect | `data/<project>/runs/*.json`, `data/<project>/logs/*.txt.gz` | runs yes, logs no (back them up) |
| cases | `data/<project>/cases.jsonl` | yes |
| reproduce | `data/<project>/repro/<case>.json`, raw cargo output in `repro/raw/` | summaries yes, raw no |
| repair | `data/<project>/repair/<case>.<repairer>.json` | yes |

**Case.** One source tree that failed to compile in CI. For pull requests this is the
merge of the PR head into the base commit, as built by `actions/checkout` (both SHAs
are read from the job log). Compile errors are rustc errors with an E-code, rustc lints
promoted by `RUSTFLAGS="-D warnings"`, and code-less errors such as parse errors; clippy
lints are not counted. Jobs built with `--features enterprise` are excluded (BUSL-1.1).

**Fix.** The first later run of the same workflow on the same branch in which every
job that failed to compile now compiles.

**Reproduction.** Toolchain from the tree's `rust-toolchain.toml` (one image per
version, `docker/rust.Dockerfile`). `cargo fetch` with network, then
`cargo check --offline` in a container with `--network none`, limited to the crates
that had errors. The cargo arguments are derived from the failed CI step
(`ci_repair/project.py`). Reproduced = every CI error (code, file, line) appears
locally and the fix tree compiles.

**Repair.** `ci_repair/repair.py`: a repairer implements
`propose(context, history) -> unified diff`; the harness applies it, rebuilds, and
repeats up to the attempt budget. `oracle` (applies the human fix) and `noop` validate
the harness; the LLM repairers will be added next to them.

## Known differences from CI

- Builds run natively on arm64 (Apple Silicon); CI mostly ran x86_64.
- `cargo check` instead of `cargo build`/`test`: errors that only appear at codegen or
  link time are missed.
- Dev profile, while some CI steps used `--release`.
- The "almost all features" job (features chosen by `cargo xtask` at CI time) is not
  reproduced yet.

## Layout

```
ci_repair/   pipeline package (rustdiag, github, collect, cases, reproduce, repair, cli)
docker/      build image
screening/   project screening script and results (how meilisearch was chosen)
results/     screening output
data/        dataset (per project)
work/        clone + temporary worktrees (git-ignored)
```
