# Master's thesis: LLM repair of CI compilation failures

Context handed over from a claude.ai session (2026-10-02). Student: Wen (KTH).
Examiner: Prof. Cyrille Artho (KTH, TCS/EECS).

## Goal

Build a system that (1) reproduces historical CI build failures of an open-source
project in an isolated environment and (2) passes processed compiler output to an
LLM that proposes repairs, validated by rebuilding. Student's own extension (not yet
confirmed in scope with the examiner): compare a single-shot repair loop against an
agentic, tool-using repair loop with the same model and attempt budget.

## Source papers (by the examiner's group)

- Fu et al., "Where did we fail? -- Reproducing build failures in embedded open source
  software", EASE 2026, arXiv:2604.27075. PhantomRun reconstruction: 91.8% of 4,628
  failing CI builds reproduced (RTEMS, Zephyr, OpenIPC, STM32). Parses CI YAML,
  generates Dockerfile + build script, reruns the build stage in isolation.
- Fu et al., "PhantomRun: Auto Repair of Compilation Errors in Embedded Open Source
  Software", MSR 2026, arXiv:2602.20284. Fixed prompt (source file + error log + one
  human-fix example), patch, rebuild, up to 5 attempts. Best pass rate 45%
  (CodeLlama, same-project examples). Four-layer log parsing; five error categories
  (environment setup, syntax, hardware dependency, non-hardware dependency, compiler
  configuration). Stats: Chi-square and Friedman tests.

## Examiner's direction (email, 2026-10-02) -- this overrides the earlier plan

Do NOT use the four embedded projects: hardware dependence is hard to control.
Find a new open-source project with:
- open-source license(s)
- 1-2 programming languages
- 1-2 build tools
- a few hundred build failures (or more) in the last six months
- build error rate reasonably high (>= 20-25%)
- compiler output that is easy to parse

## Decisions and reasoning so far

- No hardware simulator (QEMU/Renode) needed: the new project only needs to compile
  on ordinary Linux. `act` is fine for a quick look but not as the experiment basis;
  reproduce only the compile step in a pinned Docker image instead.
- "Build failures" must mean compilation failures. CI failures in mature projects
  are mostly tests/lint/infra; clarify with the examiner whether the 20-25% refers to
  compile failures and of which runs (all, PR, build job).
- Favour toolchains with machine-readable diagnostics: Rust/Cargo
  (`--message-format=json`) is the cleanest fit; Go, Java (Maven/Gradle), TypeScript
  (tsc) also reasonable. C/C++ often breaks the 1-2 build tool rule.
- GitHub Actions keeps public logs for at most 90 days: start archiving logs of the
  chosen project immediately.
- The earlier hypothesis (agentic gains concentrated on hardware-dependency errors)
  no longer applies; a new hypothesis must come from the failure types the chosen
  project actually has.

## Files

- `screening/screen_repos.py` -- screens GitHub repos. Over the last N days (default
  90) it counts CI runs and failures (all and PR-only, skipping bot/housekeeping
  workflows), samples failed runs, downloads failed-job logs, and detects real
  compiler errors (rustc, go, gcc/clang, linker, javac, kotlinc, tsc, csc, swiftc)
  versus test/lint/infra failures. Outputs `results/summary.md`, `summary.csv`, and
  per-repo JSON; caches API responses so runs can resume.
  Run: `pip install requests`, `export GITHUB_TOKEN=...`,
  `python screen_repos.py candidates.txt --days 90 --sample 20 --out results`.
  About 4,000-5,000 API calls for all candidates (~1 h of rate limit).
  Classifier tested on synthetic logs only; clippy lint errors are deliberately not
  counted as compile errors. Not yet run on real data (the claude.ai workspace had no
  linked GitHub account). With 20 samples per repo the compile share is a rough
  estimate: use it to shortlist, then sample more heavily.
- `screening/candidates.txt` -- 27 starting-guess repos (Rust, Go, Java, TS, C/C++).
- `thesis_proposal.docx` -- long proposal (embedded framing, now outdated).
- `kth_degree_project_proposal.docx` -- KTH template version (embedded framing, now
  outdated; personal sections still placeholders).

## Screening status (2026-10-02)

First full run done: `results/summary.md` (90 days, 20 samples/repo, ~14k API calls).
Env: `.venv` (Python 3.12, requests); token via `GITHUB_TOKEN=$(gh auth token)`.
Classifier fix: C/C++ errors from autoconf/CMake probes (conftest.c, TryCompile,
Check*.c, CMake/*.c) and probe-only linker errors are no longer counted (curl dropped
from 63% to 32% compile share). Spot-checked logs: Rust E-code and Go/tsc hits are real.
Fit vs. examiner criteria (fail rate >= 20-25%, hundreds of compile failures / 6 mo):
- meilisearch/meilisearch (Rust): 37% fail, ~10% of runs compile failures, ~250/6 mo. Best fit.
- microsoft/TypeScript (now Go + TS, typescript-go): 29% PR fail, ~390/6 mo.
- tauri-apps/tauri (Rust): ~2,500/6 mo but 11% fail rate; many errors are OS-specific
  (gtk, windows crate) or missing targets -- platform dependence, like the embedded case.
- ruff, rust-analyzer: plenty of compile failures, but 6-7% fail rate.
Caveat: counts are per run; one bad commit fails several workflows. Deduplicate by
head commit before quoting numbers.

## Experiment plan (once a project is chosen)

1. Dataset: per compile failure store failing commit, CI log, toolchain version at
   the time, and fix commit (next passing commit on the same PR).
2. Reproduce in Docker with the exact toolchain; pre-fetch dependencies so rebuilds
   are offline and deterministic.
3. Validate: failing commit must fail with the same error (file, line, code) as CI;
   fix commit must build. Report this reproduction rate.
4. Repair loop: processed error + context to the LLM, apply patch, rebuild, fixed
   attempt budget. Baseline single-shot vs agentic, same model and budget.
5. Success: compiles is necessary but not sufficient. Also run affected-package
   tests, compare to the human fix, manually review a sample (plausible vs correct).
- Threat to validity: data leakage (model may have seen public human fixes) -- use
  failures after the model's training cutoff for the main evaluation.
- Speed: compile only the affected package (`cargo check -p`, `go build ./pkg/...`).

## Pipeline (2026-10-02)

Project chosen (provisionally, pending examiner): meilisearch/meilisearch. The pipeline
is the `ci_repair` package; usage, definitions and known differences from CI are in
README.md. Run `.venv/bin/python -m pytest -q tests` after changing parsers.
- Licence: repo is MIT + BUSL-1.1 "Enterprise Edition"; enterprise jobs are excluded.
  Needs the examiner's OK.
- Disk: Mac has ~40 GB free; Docker target volumes grow large. Full-scale runs better
  on a Linux machine. `docker volume ls | grep ci-repair` to inspect/clean.
- Rerun `ci-repair collect` at least monthly so logs are archived before they expire;
  `data/*/logs/` is git-ignored, so back it up separately.

Status 2026-10-02: 340 failed runs archived (90 days) -> 53 cases, 48 usable, 39 with a
human fix (33 of them after amend + force-push). First 10 reproduced cases: 10/10 exact
(but only 3 PRs). Harness check: oracle repaired 3/3, noop 0/1.
Bugs found and fixed on the way (worth a sentence in the thesis method): stale cargo
artifacts across trees mounted at the same path (mtime freshness), macro spans pointing
into dependencies, "fixes" that were reverts/reruns, build scripts needing network.

## Next steps

1. Confirm with the examiner: meilisearch + enterprise exclusion; definition of build
   failure / rate denominator; whether the agentic comparison is in scope.
2. Reproduce all 39 cases (`ci-repair reproduce`); check the dataset is not dominated
   by a few PRs (report cases per PR); grow it by rerunning collect over time.
3. Add the LLM repairers (single-shot, agentic) behind the `Repairer` interface.
4. Rewrite both proposals around meilisearch and a new hypothesis.

## Preference

When running shell commands, state the exact command as inline code in the
response text before or while running it.
