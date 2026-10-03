# Progress report: CI compile-failure reproduction pipeline

*Wen, KTH master's thesis (examiner: Prof. Cyrille Artho). Status as of 2026-10-03.*

## Summary

Following the examiner's direction to move away from embedded projects, I screened
27 open-source projects and provisionally chose **meilisearch** (Rust/Cargo). I built a
pipeline that archives its failing CI runs, extracts compile-failure cases with their
human fixes, rebuilds them in Docker, and runs a pluggable repair loop. On a first
sample, **10 of 10 cases reproduced exactly** (same error code, file and line as CI)
and all their human fixes compiled. The repair loop is ready; the LLM repairers are
the next step.

## 1. Project selection

The screening script (`screening/screen_repos.py`) counted CI runs and failures over
90 days and sampled 20 failed runs per repository to estimate how many failed with a
compiler error. Spot checks of the matched log lines led to one classifier fix (C/C++
configure probes such as `conftest.c` were counted as compile errors).

| Criterion (examiner) | meilisearch |
|---|---|
| Open-source licence | MIT, **but** "Enterprise Edition" code is BUSL-1.1 (see open questions) |
| 1–2 languages / build tools | Rust / Cargo |
| Build error rate ≥ 20–25 % | 37 % of CI runs fail (42 % of PR runs) |
| Hundreds of failures in 6 months | ~250 compile failures per 6 months (estimate, per run) |
| Parseable compiler output | rustc: error codes, `--> file:line:col`, JSON output |

Runner-up: microsoft/TypeScript (now Go + TypeScript). Others had too low a failure
rate (ruff, rust-analyzer, curl) or OS-specific errors (tauri).

## 2. Pipeline

Code: `ci_repair/` (Python package, `ci-repair` CLI), 12 unit tests.

```
collect ──▶ cases ──▶ reproduce ──▶ repair
```

1. **collect.** Archives every failed run of the two build workflows with full job
   logs (GitHub deletes logs after 90 days). Incremental.
2. **cases.** Parses compiler diagnostics from the logs. Counted: rustc errors with an
   E-code, rustc lints turned into errors by `RUSTFLAGS="-D warnings"`, and code-less
   errors such as parse errors. Not counted: clippy lints.
   - A *case* is one source tree that failed to compile. For PRs, that is the merge of
     the PR head into the base commit, as CI built it; both SHAs come from the checkout
     log. Jobs failing on the same tree are merged, so failures are not overcounted.
   - The *human fix* is the first later commit on the branch that adds new work and
     on which all the failing jobs compile.
   - Jobs built with `--features enterprise` are excluded (BUSL code).
3. **reproduce.** Rebuilds the tree in a Docker image pinned to the toolchain in
   the tree's `rust-toolchain.toml`.
   - It runs `cargo fetch`, then a warm-up build with network (some build scripts
     download data), then the measured `cargo check` with **no network**, limited to
     the crates with errors and using flags derived from the failed CI step.
   - *Reproduced* means every CI error (code, file, line) appears locally **and** the
     fix tree compiles.
4. **repair.** Attempt loop with a fixed budget (default 5).
   - A repairer implements `propose(context, history) → diff`; the harness applies the
     diff, rebuilds and records the errors.
   - Two reference repairers validate the harness: `oracle` (replays the human fix)
     and `noop` (empty patch).

## 3. Results so far

**Dataset** (CI runs from 2026-07-06 to 2026-09-29):

| | Count |
|---|---|
| Failed CI runs archived (build workflows) | 340 (306 PR, 35 nightly, 5 merge queue) |
| Compile-failure cases | 53 |
| Usable (reproducible job type, open-source code) | 48, from 14 PRs |
| With a human fix | 39 (33 after amend + force-push, 6 plain follow-up commits) |

Error types in the 48 usable cases:

| Errors in the case | Cases |
|---|---|
| Only `-D warnings` lints (e.g. unused import, deprecated call) | 23 |
| Only real type or name errors (e.g. `E0308` mismatched types, `E0433` unresolved path) | 10 |
| Both | 15 |

The median case has 4 errors.

**Reproduction** (the 10 most recent cases with a fix):

| | Result |
|---|---|
| Failing tree reproduces CI errors exactly | 10 / 10 |
| Fix tree compiles | 10 / 10 |
| Build time per case | 1.5–3 min (one failing and one fix build per CI command variant) |
| Toolchains encountered | 1.91.1 and 1.98.1 (picked up automatically) |

**Harness check:** `oracle` repaired 3 of 3 cases on the first attempt, and `noop`
repaired 0 of 1, as expected.

## 4. Problems found during validation

All four were fixed and are covered by tests. They are relevant to the method chapter.

- **Stale build artifacts.** All trees share one Cargo target directory, and Cargo
  decides freshness by file timestamps only. A failing tree checked out before its fix
  tree was built reused the fix's artifacts and was reported as compiling. Fixed by
  refreshing timestamps before every build.
- **Errors inside macros.** For warnings raised inside a dependency's macro, cargo's
  JSON points into the dependency, while CI's text log points to the project file that
  calls it. Fixed by following the macro expansion back to the call site.
- **Reverts mistaken for fixes.** Reruns of older commits and force-pushes back to an
  earlier state looked like fixes. Now a fix must add commits (checked with GitHub's
  compare API).
- **Network needed during the build.** The `lindera` tokenizer downloads dictionaries
  during the build, so a fully offline build always failed. Handled by the warm-up
  build, with downloads cached across cases.

## 5. Limitations and threats to validity

- **Sample size.** The 10 reproduced cases come from only 3 PRs. The whole dataset is
  clustered too: 14 PRs, with up to 8 cases per PR.
- **Dataset size.** 39 cases with fixes after 90 days. Growing it means rerunning
  `collect` regularly; logs older than 90 days are already gone.
- **Differences from CI:**
  - builds run on arm64 (Apple Silicon), while CI mostly ran x86_64;
  - `cargo check` misses errors that only appear at code generation or link time;
  - debug profile, while some CI steps used release;
  - the nightly "almost all features" job is not reproduced yet.
- **Data leakage.** The human fixes are public, and an LLM may have seen them in
  training. The main evaluation should use failures after the model's training cutoff.
- **Resources.** A full-scale run needs more disk than the development laptop has
  (about 30 GB free); a Linux machine is preferable.

## 6. Open questions for the examiner

1. Is meilisearch acceptable, given that its Enterprise Edition code is under BUSL-1.1
   and is excluded from the dataset?
2. What does "build failure" mean in the 20–25 % criterion: any CI failure, or
   compilation failures only? And is the rate counted over all runs, PR runs, or build
   jobs?
3. Is the comparison of a single-shot repair loop with an agentic one in scope?

## 7. Next steps

1. Reproduce all 39 cases and report the reproduction rate together with cases per PR.
2. Implement the single-shot and agentic LLM repairers behind the `Repairer` interface,
   with the same model and attempt budget.
3. Evaluate beyond "it compiles": run the affected crates' tests, compare with the human
   fix, and manually review a sample of patches.
4. Formulate a hypothesis from the observed error mix. Nearly half the cases are
   lint-only, which are likely easy; the rest are type and resolution errors.
5. Rewrite both thesis proposals around meilisearch.
