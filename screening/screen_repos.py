#!/usr/bin/env python3
"""
Screen GitHub repositories as candidates for a CI compilation-failure repair thesis.

For each repo it estimates, over the last N days (default 90 = GitHub's max log retention):
  1. How many CI workflow runs there were, and what fraction failed.
  2. For a random sample of failed runs: which job/step failed, and whether the
     job log contains a genuine *compiler* error (vs. test failure, lint, infra).
  3. From that, an estimate of the number and share of runs that failed because
     of a compilation error -- the number that actually matters for the thesis.

Requirements checked against (examiner's email):
  - >= 20-25 % build error rate
  - a few hundred build failures in the last six months
  - 1-2 languages, 1-2 build tools, open-source license, parseable compiler output

Usage:
  export GITHUB_TOKEN=...            # required (5000 req/h); unauthenticated is 60/h
  python screen_repos.py candidates.txt --days 90 --sample 20 --out results
  python screen_repos.py owner/repo owner/repo2 ...

Output: results/summary.csv, results/summary.md, results/<owner>__<repo>.json
Responses are cached in results/cache/ so an interrupted run can be resumed.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests

API = "https://api.github.com"

# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Workflows that are clearly not CI builds (bots, housekeeping, publishing).
NON_CI_WORKFLOW = re.compile(
    r"label|stale|triage|welcome|greet|lock|close|assign|milestone|"
    r"release|publish|deploy|pages|docs?[-_ ]?(build|deploy)|website|"
    r"codeql|scorecard|dependabot|dependency[-_ ]review|cla\b|"
    r"sync|mirror|backport|benchmark|bench\b|nightly|cron|schedule|"
    r"cache|cleanup|comment|notify|discord|slack|size[-_ ]?label|audit",
    re.I,
)

# Job/step names -> coarse category (first match wins).
STEP_CATEGORIES = [
    ("lint", re.compile(r"lint|clippy|fmt|format|rustfmt|gofmt|vet|eslint|prettier|"
                        r"spotless|checkstyle|style|typos|spell|shellcheck|golangci", re.I)),
    ("build", re.compile(r"build|compile|cargo check|\bcheck\b|make|cmake|tsc|"
                         r"msbuild|gradle.*assemble|mvn.*compile|install", re.I)),
    ("test", re.compile(r"test|spec|e2e|integration|coverage|nextest|pytest|jest", re.I)),
    ("setup", re.compile(r"setup|checkout|cache|toolchain|download|fetch|"
                         r"install deps|dependencies", re.I)),
]

CONFIGURE_PROBE = (r"(conftest|CMakeScratch|CMakeTmp|TryCompile|CheckIncludeFile|"
                   r"CheckSymbolExists|CheckFunctionExists|CheckTypeSize|CheckCSource|"
                   r"apple-sdk|/CMake/[^/ ]+\.c)")

# Compiler-error signatures per toolchain. Matched against job logs.
COMPILE_ERROR_PATTERNS = {
    "rustc": re.compile(r"^.{0,40}error(\[E\d{4}\])?: (?!test failed|process didn't exit|"
                        r"could not compile.*due to previous)", re.M),
    "rustc_strict": re.compile(r"error\[E\d{4}\]:"),
    "go": re.compile(r"^.{0,40}\S+\.go:\d+:\d+: (undefined|cannot use|too many|not enough|"
                     r"missing|declared and not used|imported and not used|syntax error|"
                     r"invalid|assignment mismatch|\S+ redeclared|cannot|unknown field)", re.M),
    # Skips autoconf/CMake feature probes (conftest.c, TryCompile, Check*.c), which
    # fail on purpose and appear in almost every configure log.
    "gcc_clang": re.compile(r"(?<!\S)(?!\S*" + CONFIGURE_PROBE + r")"
                            r"\S+\.(c|cc|cpp|cxx|h|hpp|hh):\d+:\d+: (fatal )?error:"),
    "linker": re.compile(r"undefined reference to|ld(\.lld)?: error:|ld returned \d exit status|"
                         r"Undefined symbols for architecture"),
    "javac": re.compile(r"(\S+\.java:\[?\d+[,:\]]|\[ERROR\] .*\.java:\[\d+,\d+\]).{0,5}"
                        r"(error:)?.*(cannot find symbol|incompatible types|"
                        r"does not exist|cannot be applied|expected|unreported exception|"
                        r"is not abstract|might not have been)"),
    "kotlinc": re.compile(r"^e: .*\.kt:\(?\d+", re.M),
    "tsc": re.compile(r"error TS\d{4}:"),
    "csc": re.compile(r"error CS\d{4}:"),
    "swiftc": re.compile(r"\.swift:\d+:\d+: error:"),
}

# Signals that a failure is something else (useful context, not decisive).
OTHER_SIGNALS = {
    "test_failure": re.compile(r"test result: FAILED|--- FAIL:|FAIL\s+\S+\s+\d|"
                               r"Tests run:.*Failures: [1-9]|\d+ failing|"
                               r"failures:\n|AssertionError|panicked at", re.I),
    "lint_failure": re.compile(r"Diff in |would reformat|clippy::|golangci-lint|"
                               r"eslint|warning: .* \(-D warnings\)|"
                               r"needs formatting|gofmt", re.I),
    "infra": re.compile(r"No space left on device|The runner has received a shutdown|"
                        r"rate limit|ETIMEDOUT|Connection reset|503 Service|"
                        r"The operation was canceled|exceeded the maximum execution time|"
                        r"Could not resolve host|lost communication", re.I),
}

# ---------------------------------------------------------------------------
# HTTP with caching and rate-limit handling
# ---------------------------------------------------------------------------

class GH:
    def __init__(self, token, cache_dir):
        self.s = requests.Session()
        self.s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-repair-thesis-screening",
        })
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def _key(self, url, params):
        raw = url + "?" + json.dumps(params or {}, sort_keys=True)
        return self.cache / (hashlib.sha1(raw.encode()).hexdigest() + ".json")

    def _wait_for_rate_limit(self, r):
        if r.status_code in (403, 429) and (
            r.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in r.text.lower()
        ):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - time.time(), 0) + 5
            print(f"  rate limited; sleeping {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            return True
        return False

    def get_json(self, path, params=None):
        url = path if path.startswith("http") else API + path
        key = self._key(url, params)
        if key.exists():
            return json.loads(key.read_text())
        for attempt in range(5):
            r = self.s.get(url, params=params, timeout=60)
            self.calls += 1
            if self._wait_for_rate_limit(r):
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            key.write_text(json.dumps(data))
            return data
        raise RuntimeError(f"GET {url} failed repeatedly")

    def get_log(self, owner, repo, job_id, max_bytes=4_000_000):
        """Job log text (keeps the tail if huge; errors are usually near the end)."""
        key = self.cache / f"log_{owner}_{repo}_{job_id}.txt"
        if key.exists():
            return key.read_text(errors="replace")
        url = f"{API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        for attempt in range(5):
            r = self.s.get(url, timeout=120, stream=True)
            self.calls += 1
            if self._wait_for_rate_limit(r):
                continue
            if r.status_code in (404, 410):  # expired / deleted
                return None
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf.extend(chunk)
                if len(buf) > 2 * max_bytes:
                    del buf[: len(buf) - max_bytes]
            text = bytes(buf[-max_bytes:]).decode("utf-8", errors="replace")
            key.write_text(text)
            return text
        return None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TS_PREFIX = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", re.M)


def clean_log(text):
    return TS_PREFIX.sub("", ANSI.sub("", text))


def classify_name(name):
    for cat, rx in STEP_CATEGORIES:
        if rx.search(name or ""):
            return cat
    return "other"


def analyse_log(text):
    text = clean_log(text)
    hits = {k: len(rx.findall(text)) for k, rx in COMPILE_ERROR_PATTERNS.items()}
    # rustc generic pattern is noisy. Errors with an E-code are genuine compiler
    # errors; code-less ones (e.g. parse errors) count only with "could not compile"
    # and no sign that clippy / -D warnings produced them.
    if hits["rustc"] and not hits["rustc_strict"]:
        lint_origin = "clippy::" in text or "-D warnings" in text or "#[deny(" in text
        if "could not compile" not in text or lint_origin:
            hits["rustc"] = 0
    # Linker errors without a real C/C++ error usually come from configure probes.
    if hits["linker"] and not hits["gcc_clang"] and re.search(CONFIGURE_PROBE, text):
        hits["linker"] = 0
    other = {k: bool(rx.search(text)) for k, rx in OTHER_SIGNALS.items()}
    compilers = sorted(k for k, v in hits.items() if v and k != "rustc_strict")
    if hits["rustc_strict"]:
        compilers = sorted(set(compilers) | {"rustc"})
    return {"compile_error": bool(compilers), "compilers": compilers, **other}


def time_windows(days, step_days=7):
    end = dt.datetime.now(dt.timezone.utc).date()
    start = end - dt.timedelta(days=days)
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=step_days), end)
        yield cur, nxt
        cur = nxt


def count_runs(gh, owner, repo, wf_id, days, status=None, event=None):
    """Sum total_count over weekly windows (filtered queries cap at 1000 results)."""
    total = 0
    for a, b in time_windows(days):
        params = {"per_page": 1, "created": f"{a}..{b - dt.timedelta(days=1)}"}
        if status:
            params["status"] = status
        if event:
            params["event"] = event
        data = gh.get_json(f"/repos/{owner}/{repo}/actions/workflows/{wf_id}/runs", params)
        total += (data or {}).get("total_count", 0)
    return total


def list_failed_runs(gh, owner, repo, wf_id, days, limit):
    runs = []
    for a, b in time_windows(days):
        params = {"per_page": 100, "status": "failure",
                  "created": f"{a}..{b - dt.timedelta(days=1)}"}
        data = gh.get_json(f"/repos/{owner}/{repo}/actions/workflows/{wf_id}/runs", params)
        runs.extend((data or {}).get("workflow_runs", []))
        if len(runs) >= limit * 20:
            break
    return runs


def screen_repo(gh, full_name, days, sample, seed, workflow_filter=None):
    owner, repo = full_name.split("/")
    info = gh.get_json(f"/repos/{owner}/{repo}")
    if not info:
        return {"repo": full_name, "error": "not found"}
    langs = gh.get_json(f"/repos/{owner}/{repo}/languages") or {}
    lang_total = sum(langs.values()) or 1
    lang_share = {k: round(v / lang_total, 3) for k, v in
                  sorted(langs.items(), key=lambda kv: -kv[1])}
    sig_langs = [k for k, v in lang_share.items() if v >= 0.10]

    wfs = (gh.get_json(f"/repos/{owner}/{repo}/actions/workflows", {"per_page": 100})
           or {}).get("workflows", [])
    ci_wfs = []
    for w in wfs:
        if w.get("state") != "active":
            continue
        label = f"{w['name']} {w['path']}"
        if workflow_filter:
            if re.search(workflow_filter, label, re.I):
                ci_wfs.append(w)
        elif not NON_CI_WORKFLOW.search(label):
            ci_wfs.append(w)

    result = {
        "repo": full_name,
        "license": (info.get("license") or {}).get("spdx_id"),
        "stars": info.get("stargazers_count"),
        "languages": lang_share,
        "significant_languages": sig_langs,
        "ci_workflows": [w["path"] for w in ci_wfs],
        "excluded_workflows": [w["path"] for w in wfs if w not in ci_wfs],
        "days": days,
        "workflows": [],
    }

    total_runs = total_fail = total_pr_runs = total_pr_fail = 0
    failed_pool = []
    for w in ci_wfs:
        n = count_runs(gh, owner, repo, w["id"], days)
        if n == 0:
            continue
        f = count_runs(gh, owner, repo, w["id"], days, status="failure")
        npr = count_runs(gh, owner, repo, w["id"], days, event="pull_request")
        fpr = count_runs(gh, owner, repo, w["id"], days, status="failure", event="pull_request")
        result["workflows"].append({"path": w["path"], "runs": n, "failed": f,
                                    "pr_runs": npr, "pr_failed": fpr})
        total_runs += n
        total_fail += f
        total_pr_runs += npr
        total_pr_fail += fpr
        if f:
            failed_pool.extend(list_failed_runs(gh, owner, repo, w["id"], days, sample))

    result.update(runs=total_runs, failed=total_fail,
                  fail_rate=round(total_fail / total_runs, 3) if total_runs else None,
                  pr_runs=total_pr_runs, pr_failed=total_pr_fail,
                  pr_fail_rate=round(total_pr_fail / total_pr_runs, 3) if total_pr_runs else None)

    # --- sample failed runs and inspect their failing jobs/logs -------------
    rng = random.Random(seed)
    picked = rng.sample(failed_pool, min(sample, len(failed_pool)))
    samples = []
    for run in picked:
        jobs = (gh.get_json(f"/repos/{owner}/{repo}/actions/runs/{run['id']}/jobs",
                            {"filter": "latest", "per_page": 100}) or {}).get("jobs", [])
        failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
        run_rec = {"run_id": run["id"], "event": run.get("event"),
                   "workflow": run.get("path"), "created_at": run.get("created_at"),
                   "html_url": run.get("html_url"), "jobs": []}
        # inspect up to 3 failed jobs per run (matrix builds often fail identically)
        for j in failed_jobs[:3]:
            failed_steps = [s["name"] for s in j.get("steps", [])
                            if s.get("conclusion") == "failure"]
            log = gh.get_log(owner, repo, j["id"])
            rec = {"job": j["name"], "failed_steps": failed_steps,
                   "step_category": classify_name(" ".join(failed_steps) or j["name"]),
                   "log_available": log is not None}
            if log is not None:
                rec.update(analyse_log(log))
            run_rec["jobs"].append(rec)
        run_rec["log_available"] = any(j["log_available"] for j in run_rec["jobs"])
        run_rec["compile_error"] = any(j.get("compile_error") for j in run_rec["jobs"])
        samples.append(run_rec)

    with_logs = [s for s in samples if s["log_available"]]
    compile_runs = [s for s in with_logs if s["compile_error"]]
    share = len(compile_runs) / len(with_logs) if with_logs else None
    cats = {}
    for s in with_logs:
        for j in s["jobs"]:
            cats[j["step_category"]] = cats.get(j["step_category"], 0) + 1
    compilers = {}
    for s in compile_runs:
        for j in s["jobs"]:
            for c in j.get("compilers", []):
                compilers[c] = compilers.get(c, 0) + 1

    result.update(
        sampled_failed_runs=len(samples),
        sampled_with_logs=len(with_logs),
        compile_share_of_failures=round(share, 3) if share is not None else None,
        est_compile_failures=round(share * total_fail) if share is not None else None,
        est_compile_fail_rate=round(share * total_fail / total_runs, 3)
        if share is not None and total_runs else None,
        failed_step_categories=cats,
        compilers_seen=compilers,
        samples=samples,
    )
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

COLUMNS = ["repo", "license", "stars", "significant_languages", "runs", "failed",
           "fail_rate", "pr_fail_rate", "sampled_with_logs", "compile_share_of_failures",
           "est_compile_failures", "est_compile_fail_rate", "compilers_seen",
           "failed_step_categories", "ci_workflows"]


def fmt(v):
    if isinstance(v, float):
        return f"{v:.1%}" if v <= 1 else f"{v:.0f}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def write_reports(results, out, days):
    out = Path(out)
    rows = [r for r in results if "error" not in r]
    rows.sort(key=lambda r: -(r.get("est_compile_failures") or 0))
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in COLUMNS])

    scale = 180 / days
    lines = [f"# CI compilation-failure screening (last {days} days)", "",
             "Est. compile failures = sampled share of failed runs whose log shows a "
             f"compiler error x total failed CI runs. '6-mo est.' scales by {scale:.1f}.", "",
             "| Repo | Langs | Runs | Fail % | PR fail % | Compile share of failures (n) "
             "| Est. compile failures | 6-mo est. | Compile fail % of runs |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        share = r.get("compile_share_of_failures")
        est = r.get("est_compile_failures")
        lines.append(
            f"| {r['repo']} | {', '.join(r['significant_languages'])} | {r['runs']} "
            f"| {fmt(r['fail_rate'])} | {fmt(r['pr_fail_rate'])} "
            f"| {fmt(share)} ({r['sampled_with_logs']}) | {fmt(est)} "
            f"| {'' if est is None else round(est * scale)} | {fmt(r['est_compile_fail_rate'])} |")
    for r in results:
        if "error" in r:
            lines.append(f"\n- {r['repo']}: {r['error']}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="+", help="owner/repo entries, or a file with one per line")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--sample", type=int, default=20, help="failed runs to inspect per repo")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workflow", help="regex: only workflows whose name/path match")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    repos = []
    for item in args.repos:
        p = Path(item)
        if p.is_file():
            repos += [l.split("#")[0].strip() for l in p.read_text().splitlines()]
        else:
            repos.append(item)
    repos = [r for r in repos if r and "/" in r]

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("warning: no GITHUB_TOKEN; limited to 60 requests/hour", file=sys.stderr)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    gh = GH(token, out / "cache")

    results = []
    for i, full in enumerate(repos, 1):
        print(f"[{i}/{len(repos)}] {full}", file=sys.stderr)
        try:
            r = screen_repo(gh, full, args.days, args.sample, args.seed, args.workflow)
        except Exception as e:  # keep going on one bad repo
            r = {"repo": full, "error": f"{type(e).__name__}: {e}"}
        (out / f"{full.replace('/', '__')}.json").write_text(json.dumps(r, indent=2))
        results.append(r)
        if "error" not in r:
            print(f"    runs={r['runs']} failed={r['failed']} fail_rate={fmt(r['fail_rate'])} "
                  f"compile_share={fmt(r['compile_share_of_failures'])} "
                  f"est_compile_failures={r['est_compile_failures']}  (api calls so far: {gh.calls})",
                  file=sys.stderr)
        else:
            print(f"    {r['error']}", file=sys.stderr)
        write_reports(results, out, args.days)
    print(f"done; wrote {out/'summary.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
