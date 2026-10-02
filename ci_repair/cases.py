"""Turn archived CI runs into compile-failure cases with their human fix.

A case is one source tree that failed to compile in CI: for pull_request runs the
merge of the PR head into the base commit (as built by actions/checkout), otherwise
the head commit. Several failing jobs on the same tree are merged into one case.

The fix is the first later run of the same workflow on the same branch in which
every job that failed to compile now compiles (it succeeds, or fails for some
other reason such as a test).
"""

import gzip
import json
from collections import defaultdict
from pathlib import Path

from .collect import fetch_run, log_path
from .rustdiag import from_ci_log, parse_checkout


def read_log(data_dir, job_id):
    p = log_path(data_dir, job_id)
    return gzip.decompress(p.read_bytes()).decode("utf-8", errors="replace") if p.exists() else None


def load_runs(data_dir):
    for p in sorted((Path(data_dir) / "runs").glob("*.json")):
        yield json.loads(p.read_text())


def failed_step(job):
    return next((s["name"] for s in job["steps"] if s["conclusion"] == "failure"), None)


def compile_failures(project, run, data_dir):
    """Failed jobs of a run whose log shows compile errors."""
    out = []
    for job in run["jobs"]:
        if job["conclusion"] != "failure":
            continue
        log = read_log(data_dir, job["id"])
        if log is None:
            continue
        errors = [d for d in from_ci_log(log) if d.is_compile_error]
        if not errors:
            continue
        step = failed_step(job)
        excluded = bool(project.excluded_job and project.excluded_job.search(job["name"]))
        out.append({
            "run_id": run["id"],
            "job_id": job["id"],
            "job": job["name"],
            "step": step,
            "cargo_args": project.cargo_args_for_step(step),
            "excluded": excluded,
            "checkout": parse_checkout(log),
            "errors": [d.to_dict() for d in errors],
        })
    return out


def _tree_key(run, failure):
    co = failure["checkout"]
    if run["event"] == "pull_request" and co:
        return co["head"], co["base"]
    return run["head_sha"], None


def _status(jobs):
    usable = [j for j in jobs if not j["excluded"]]
    if not usable:
        return "excluded_only"
    if not any(j["cargo_args"] for j in usable):
        return "unsupported_step"
    return "ok"


def group_cases(project, data_dir):
    groups = defaultdict(lambda: {"runs": {}, "jobs": []})
    for run in load_runs(data_dir):
        if run["conclusion"] != "failure":
            continue
        for f in compile_failures(project, run, data_dir):
            g = groups[_tree_key(run, f)]
            g["runs"][run["id"]] = run
            g["jobs"].append(f)

    cases = []
    for (head, base), g in groups.items():
        first = min(g["runs"].values(), key=lambda r: r["created_at"])
        pr = next((j["checkout"]["pr"] for j in g["jobs"] if j["checkout"]), None) \
            or (first["pull_requests"] or [None])[0]
        tag = f"pr{pr}" if pr else first["event"]
        usable = [j for j in g["jobs"] if not j["excluded"] and j["cargo_args"]]
        errors = {tuple(sorted(e.items())): e for j in usable for e in j["errors"]}
        cases.append({
            "id": f"{tag}-{head[:7]}" + (f"-{base[:7]}" if base else ""),
            "project": project.slug,
            "event": first["event"],
            "workflow": first["workflow"],
            "pr": pr,
            "head_sha": head,
            "base_sha": base,
            "head_branch": first["head_branch"],
            "head_repo": first["head_repo"],
            "created_at": first["created_at"],
            "status": _status(g["jobs"]),
            "jobs": sorted(g["jobs"], key=lambda j: j["job_id"]),
            "errors": list(errors.values()),
            "fix": None,
        })
    return sorted(cases, key=lambda c: c["created_at"])


def _job_compiles(project, job, data_dir):
    """True / False / None (unknown) for whether a job got past compilation."""
    if job["conclusion"] == "success":
        return True
    if job["conclusion"] != "failure":
        return None
    log = read_log(data_dir, job["id"])
    if log is None:
        return None
    return not any(d.is_compile_error for d in from_ci_log(log))


def find_fix(gh, project, case, data_dir):
    """First later run on the same branch where all compile-failing jobs compile."""
    if case["event"] not in ("pull_request", "schedule", "push", "workflow_dispatch"):
        return None, f"no fix search for event {case['event']}"
    failing_jobs = {j["job"] for j in case["jobs"] if not j["excluded"] and j["cargo_args"]}
    if not failing_jobs:
        return None, "no usable failing jobs"
    runs = gh.paginate(
        f"/repos/{project.owner}/{project.repo}/actions/workflows/"
        f"{Path(case['workflow']).name}/runs",
        {"branch": case["head_branch"], "event": case["event"]}, item_key="workflow_runs",
        fresh=True, max_pages=5)
    later = sorted((r for r in runs
                    if r["created_at"] > case["created_at"]
                    and r["head_sha"] != case["head_sha"]
                    and r["status"] == "completed"
                    and (r.get("head_repository") or {}).get("full_name") == case["head_repo"]),
                   key=lambda r: r["created_at"])
    for run in later:
        rec = fetch_run(gh, project, run["id"], data_dir)
        jobs = {j["name"]: j for j in rec["jobs"]}
        verdicts = [_job_compiles(project, jobs[n], data_dir) if n in jobs else None
                    for n in failing_jobs]
        if False in verdicts:
            continue  # still broken
        if all(verdicts):
            fix = {"head_sha": run["head_sha"], "run_id": run["id"],
                   "created_at": run["created_at"], "base_sha": None}
            # Base of the merge commit CI built, from any job's checkout log.
            job = jobs[sorted(failing_jobs)[0]]
            log = gh.download_log(project.owner, project.repo, job["id"],
                                  log_path(data_dir, job["id"]))
            co = parse_checkout(log or "")
            if co:
                fix["base_sha"] = co["base"]
            return fix, None
    return None, "no later compiling run on this branch"


def build_cases(gh, project, data_dir, find_fixes=True, verbose=True):
    cases = group_cases(project, data_dir)
    for i, case in enumerate(cases, 1):
        if find_fixes and case["status"] == "ok":
            case["fix"], case["fix_note"] = find_fix(gh, project, case, data_dir)
        if verbose:
            fix = case["fix"]["head_sha"][:7] if case["fix"] else case.get("fix_note") or "-"
            print(f"  [{i}/{len(cases)}] {case['id']:32} {case['status']:16} "
                  f"errors={len(case['errors']):<3} fix={fix}", flush=True)
    out = Path(data_dir) / "cases.jsonl"
    out.write_text("".join(json.dumps(c) + "\n" for c in cases))
    return cases
