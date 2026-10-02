"""Repair harness: propose a patch, apply it, rebuild, repeat within an attempt budget.

A repairer only has to implement `propose(context, history) -> unified diff`. The
LLM repairers (single-shot loop and agentic loop) will plug in here; `oracle` and
`noop` exist to validate the harness itself.

Each attempt starts from the original failing tree (patches are not stacked), and
sees the earlier attempts' patches and resulting errors in `history`.
"""

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .reproduce import arg_sets, build, package_of, scoped_args, sh

CONTEXT_LINES = 20


@dataclass
class RepairContext:
    case: dict
    tree: Path
    cargo_args: list
    errors: list                                  # diagnostics from the local build
    snippets: dict = field(default_factory=dict)  # file -> numbered source excerpt


@dataclass
class Attempt:
    number: int
    patch: str
    applied: bool
    status: str               # ok | failed | apply_failed | empty_patch
    errors: list
    seconds: float


class Repairer(Protocol):
    name: str

    def propose(self, ctx: RepairContext, history: list[Attempt]) -> str: ...


class NoopRepairer:
    """Returns an empty patch. Sanity check: must never succeed."""
    name = "noop"

    def propose(self, ctx, history):
        return ""


class OracleRepairer:
    """Applies the human fix (PR head -> fix head). Sanity check: should succeed."""
    name = "oracle"

    def __init__(self, repo):
        self.repo = repo

    def propose(self, ctx, history):
        fix = ctx.case["fix"]
        self.repo.ensure_commit(fix["head_sha"])
        return sh(["git", "diff", "--binary", ctx.case["head_sha"], fix["head_sha"]],
                  cwd=self.repo.path).stdout


def snippets(tree, errors, context=CONTEXT_LINES):
    out = {}
    for e in errors:
        p = Path(tree) / e["file"]
        if e["file"] in out or not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        lo, hi = max(e["line"] - context, 1), min(e["line"] + context, len(lines))
        out[e["file"]] = "\n".join(f"{i:5} | {lines[i - 1]}" for i in range(lo, hi + 1))
    return out


def _reset(tree):
    sh(["git", "reset", "-q", "--hard"], cwd=tree)
    sh(["git", "clean", "-q", "-fd"], cwd=tree)


def _apply(tree, patch):
    r = subprocess.run(["git", "apply", "--3way", "--whitespace=nowarn", "-"], cwd=tree,
                       input=patch, text=True, capture_output=True)
    return r.returncode == 0, r.stderr[-2000:]


def repair_case(case, repairer, repo, docker, out_dir, budget=5):
    """Run the repair loop on one case. Returns and saves a result record."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sets = arg_sets(case)
    if not sets:
        return {"id": case["id"], "status": "no_usable_jobs"}
    args, ci_errors = next(iter(sets.items()))
    tree, err = repo.make_tree(case["id"] + "-repair", case["head_sha"], case["base_sha"])
    if err:
        repo.remove_tree(repo.trees / (case["id"] + "-repair"))
        return {"id": case["id"], "status": "tree_error", "error": err}

    result = {"id": case["id"], "repairer": repairer.name, "budget": budget,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "attempts": []}
    try:
        # The merge for a PR tree is a commit, so `git reset --hard` returns to it.
        packages = {package_of(tree, e["file"]) for e in ci_errors} - {None}
        cargo_args = scoped_args(args, packages)
        initial = build(docker, tree, cargo_args)
        result["initial"] = {"status": initial["status"], "errors": initial.get("errors", [])}
        if initial["status"] == "ok":
            result["status"] = "not_failing"
            return result
        errors = initial.get("errors", [])
        history = []
        for n in range(1, budget + 1):
            _reset(tree)
            ctx = RepairContext(case, tree, cargo_args, errors, snippets(tree, errors))
            t0 = time.time()
            patch = repairer.propose(ctx, history)
            if not patch.strip():
                att = Attempt(n, patch, False, "empty_patch", errors, time.time() - t0)
            else:
                applied, apply_err = _apply(tree, patch)
                if not applied:
                    att = Attempt(n, patch, False, "apply_failed",
                                  [{"apply_error": apply_err}], time.time() - t0)
                else:
                    b = build(docker, tree, cargo_args)
                    att = Attempt(n, patch, True, b["status"], b.get("errors", []),
                                  time.time() - t0)
                    errors = b.get("errors", []) or errors
            history.append(att)
            result["attempts"].append(att.__dict__)
            print(f"    attempt {n}: {att.status} ({len(att.errors)} errors)", flush=True)
            if att.status == "ok":
                break
        result["status"] = "repaired" if history and history[-1].status == "ok" else "unrepaired"
        return result
    finally:
        repo.remove_tree(tree)
        (out_dir / f"{case['id']}.{repairer.name}.json").write_text(json.dumps(result, indent=1))


REPAIRERS = {"noop": lambda repo: NoopRepairer(), "oracle": OracleRepairer}
