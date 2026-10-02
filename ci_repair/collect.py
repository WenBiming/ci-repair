"""Archive failed CI runs: run/job metadata as JSON and every failed job's full log.

GitHub deletes Actions logs after at most 90 days, so this is meant to be rerun
regularly. Already archived runs and logs are skipped.

Layout:
  data/runs/<run_id>.json        run + jobs + steps metadata
  data/logs/<job_id>.txt.gz      full job log (git-ignored; back it up)
"""

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _weeks(days):
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    while start < end:
        stop = min(start + dt.timedelta(days=7), end)
        yield start, stop
        start = stop


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_record(run, jobs):
    return {
        "id": run["id"],
        "workflow": run["path"].split("@")[0],
        "name": run["name"],
        "event": run["event"],
        "head_sha": run["head_sha"],
        "head_branch": run["head_branch"],
        "head_repo": (run.get("head_repository") or {}).get("full_name"),
        "pull_requests": [p["number"] for p in run.get("pull_requests", [])],
        "created_at": run["created_at"],
        "run_attempt": run.get("run_attempt"),
        "conclusion": run["conclusion"],
        "html_url": run["html_url"],
        "jobs": [{
            "id": j["id"],
            "name": j["name"],
            "conclusion": j["conclusion"],
            "steps": [{"name": s["name"], "conclusion": s["conclusion"]}
                      for s in j.get("steps") or []],
        } for j in jobs],
    }


def fetch_run(gh, project, run_id, data_dir, with_logs=True):
    """Archive one run (any conclusion). Returns its record."""
    data_dir = Path(data_dir)
    path = data_dir / "runs" / f"{run_id}.json"
    if path.exists():
        rec = json.loads(path.read_text())
    else:
        base = f"/repos/{project.owner}/{project.repo}/actions/runs/{run_id}"
        run = gh.get_json(base)
        jobs = list(gh.paginate(f"{base}/jobs", item_key="jobs"))
        rec = run_record(run, jobs)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=1))
    if with_logs:
        for job in rec["jobs"]:
            if job["conclusion"] == "failure":
                gh.download_log(project.owner, project.repo, job["id"], log_path(data_dir, job["id"]))
    return rec


def log_path(data_dir, job_id):
    return Path(data_dir) / "logs" / f"{job_id}.txt.gz"


def collect(gh, project, data_dir, days=90, workers=8, verbose=True):
    """Archive all failed runs of the project's build workflows in the last `days` days."""
    n_new = n_seen = 0
    pool = ThreadPoolExecutor(workers)
    for wf in project.workflows:
        for start, stop in _weeks(days):
            runs = list(gh.paginate(
                f"/repos/{project.owner}/{project.repo}/actions/workflows/{wf}/runs",
                {"status": "failure", "created": f"{_iso(start)}..{_iso(stop)}"},
                item_key="workflow_runs", fresh=True))
            new = [r for r in runs
                   if not (Path(data_dir) / "runs" / f"{r['id']}.json").exists()]
            # Re-check old runs too: their logs may not all have been downloaded.
            list(pool.map(lambda r: fetch_run(gh, project, r["id"], data_dir), runs))
            n_seen += len(runs)
            n_new += len(new)
            if verbose:
                print(f"  {wf} {start:%Y-%m-%d}: {n_seen} failed runs so far "
                      f"({n_new} new, api calls {gh.calls})", flush=True)
    return n_seen, n_new
