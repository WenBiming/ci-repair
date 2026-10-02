"""Rebuild a case's source tree in Docker and compare the errors with CI.

For each case:
  1. Recreate the tree CI compiled: base commit with the PR head merged in.
  2. Pick the toolchain from that tree's rust-toolchain.toml (one image per version).
  3. `cargo fetch` with network, then `cargo check --offline` with --network none,
     limited to the crates that had errors, with the CI's RUSTFLAGS.
  4. Same for the fix tree; it must compile.

A case counts as reproduced when every CI error (code, file, line) appears locally
and the fix tree compiles.
"""

import gzip
import json
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

from .rustdiag import from_cargo_json

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "rust.Dockerfile"
GIT_ID = ["-c", "user.name=ci-repair", "-c", "user.email=ci-repair@localhost"]


def sh(cmd, cwd=None, check=True, timeout=None, **kw):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True,
                          timeout=timeout, **kw)


# --------------------------------------------------------------------------- git

class Repo:
    """A local clone used to materialise CI source trees as git worktrees."""

    def __init__(self, project, work_dir):
        self.project = project
        self.path = Path(work_dir) / project.repo
        self.trees = Path(work_dir) / "trees"

    def ensure_clone(self):
        if not (self.path / ".git").exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            sh(["git", "clone", "-q", f"https://github.com/{self.project.slug}.git",
                str(self.path)], timeout=1800)

    def ensure_commit(self, sha):
        if sh(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=self.path,
              check=False).returncode != 0:
            sh(["git", "fetch", "-q", "origin", sha], cwd=self.path, timeout=600)

    def make_tree(self, name, head, base=None):
        """Worktree with `head` (merged into `base` if given). Returns (path, error)."""
        dest = self.trees / name
        self.remove_tree(dest)
        for sha in filter(None, (head, base)):
            try:
                self.ensure_commit(sha)
            except subprocess.CalledProcessError as e:
                return None, f"commit {sha[:7]} unavailable: {e.stderr.strip()[:200]}"
        sh(["git", "worktree", "add", "-q", "--detach", str(dest), base or head], cwd=self.path)
        if base:
            r = sh(["git", *GIT_ID, "merge", "-q", "--no-ff", "--no-edit", head],
                   cwd=dest, check=False)
            if r.returncode != 0:
                return dest, "merge conflict"
        return dest, None

    def remove_tree(self, dest):
        if Path(dest).exists():
            sh(["git", "worktree", "remove", "--force", str(dest)], cwd=self.path, check=False)
            shutil.rmtree(dest, ignore_errors=True)
        sh(["git", "worktree", "prune"], cwd=self.path, check=False)


# --------------------------------------------------------------------------- cargo

def toolchain_version(tree):
    p = Path(tree) / "rust-toolchain.toml"
    if p.exists():
        return tomllib.loads(p.read_text())["toolchain"]["channel"]
    p = Path(tree) / "rust-toolchain"
    if p.exists():
        return p.read_text().strip()
    return "stable"


def package_of(tree, file):
    """Name of the cargo package containing `file` (path relative to the tree)."""
    d = (Path(tree) / file).parent
    root = Path(tree).resolve()
    while d.resolve() != root and root in d.resolve().parents:
        manifest = d / "Cargo.toml"
        if manifest.exists():
            pkg = tomllib.loads(manifest.read_text()).get("package")
            if pkg:
                return pkg["name"]
        d = d.parent
    return None


def scoped_args(cargo_args, packages):
    """Replace --workspace with -p for each package that had errors."""
    if not packages or "--workspace" not in cargo_args:
        return list(cargo_args)
    out = [a for a in cargo_args if a != "--workspace"]
    for p in sorted(packages):
        out += ["-p", p]
    return out


class Docker:
    def __init__(self, project):
        self.project = project
        self.registry = f"ci-repair-{project.repo}-cargo-registry"
        self.gitdeps = f"ci-repair-{project.repo}-cargo-git"

    def image(self, version):
        tag = f"ci-repair-rust:{version}"
        if sh(["docker", "image", "inspect", tag], check=False).returncode != 0:
            print(f"    building image {tag}", flush=True)
            sh(["docker", "build", "-q", "--build-arg", f"RUST_VERSION={version}",
                "-t", tag, "-f", str(DOCKERFILE), str(DOCKERFILE.parent)], timeout=3600)
        return tag

    def run(self, version, tree, cmd, network=True, timeout=7200):
        image = self.image(version)
        target = f"ci-repair-{self.project.repo}-target-{version}"
        env = {"RUSTFLAGS": self.project.rustflags, **self.project.env}
        args = ["docker", "run", "--rm",
                "-v", f"{self.registry}:/usr/local/cargo/registry",
                "-v", f"{self.gitdeps}:/usr/local/cargo/git",
                "-v", f"{target}:/target",
                "-v", f"{Path(tree).resolve()}:/src"]
        if not network:
            args += ["--network", "none"]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        return sh([*args, image, *cmd], check=False, timeout=timeout)


def build(docker, tree, cargo_args):
    """Fetch deps, then compile offline. Returns a result dict."""
    version = toolchain_version(tree)
    t0 = time.time()
    fetch = docker.run(version, tree, ["cargo", "fetch", "--locked"])
    if fetch.returncode != 0:
        return {"toolchain": version, "status": "fetch_failed", "stderr": fetch.stderr[-4000:],
                "seconds": round(time.time() - t0)}
    check = docker.run(version, tree, ["cargo", "check", "--locked", "--offline",
                                       "--message-format=json", *cargo_args], network=False)
    errors = [d for d in from_cargo_json(check.stdout) if d.is_compile_error]
    return {
        "toolchain": version,
        "status": "ok" if check.returncode == 0 else "failed",
        "exit_code": check.returncode,
        "cargo_args": list(cargo_args),
        "errors": [d.to_dict() for d in errors],
        "stdout": check.stdout,
        "stderr_tail": check.stderr[-4000:],
        "seconds": round(time.time() - t0),
    }


# --------------------------------------------------------------------------- compare

def _key(e):
    return (e["code"], e["file"], e["line"])


def compare(ci_errors, local_errors):
    ci = {_key(e) for e in ci_errors}
    local = {_key(e) for e in local_errors}
    if not local:
        verdict = "no_error"
    elif ci <= local:
        verdict = "exact"
    elif ci & local:
        verdict = "partial"
    elif {(c, f) for c, f, _ in ci} & {(c, f) for c, f, _ in local}:
        verdict = "same_file_other_line"
    else:
        verdict = "different"
    return {"verdict": verdict, "ci": len(ci), "local": len(local), "matched": len(ci & local)}


def arg_sets(case):
    """Distinct cargo argument sets of the case's usable failing jobs, with their errors."""
    sets = {}
    for j in case["jobs"]:
        if j["excluded"] or not j["cargo_args"]:
            continue
        sets.setdefault(tuple(j["cargo_args"]), []).extend(j["errors"])
    return sets


def reproduce_case(case, project, repo, docker, out_dir, keep_tree=False):
    out_dir = Path(out_dir)
    result = {"id": case["id"], "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": []}

    def save():
        (out_dir / f"{case['id']}.json").write_text(json.dumps(result, indent=1))

    out_dir.mkdir(parents=True, exist_ok=True)
    tree, err = repo.make_tree(case["id"], case["head_sha"], case["base_sha"])
    if err:
        result["status"] = "tree_error"
        result["error"] = err
        repo.remove_tree(repo.trees / case["id"])
        save()
        return result

    fix_tree = None
    try:
        for args, ci_errors in arg_sets(case).items():
            packages = {package_of(tree, e["file"]) for e in ci_errors} - {None}
            cargo_args = scoped_args(args, packages)
            print(f"    failing tree: cargo check {' '.join(cargo_args)}", flush=True)
            fail = build(docker, tree, cargo_args)
            entry = {"cargo_args": cargo_args, "fail": _summary(fail),
                     "match": compare(ci_errors, fail.get("errors", []))}
            _store_output(out_dir, case["id"], "fail", fail)
            if case.get("fix"):
                if fix_tree is None:
                    fix_tree, err = repo.make_tree(case["id"] + "-fix", case["fix"]["head_sha"],
                                                   case["fix"].get("base_sha"))
                if err:
                    entry["fix"] = {"status": "tree_error", "error": err}
                else:
                    print(f"    fix tree:     cargo check {' '.join(cargo_args)}", flush=True)
                    fix = build(docker, fix_tree, cargo_args)
                    entry["fix"] = _summary(fix)
                    _store_output(out_dir, case["id"], "fix", fix)
            result["runs"].append(entry)
    finally:
        if not keep_tree:
            repo.remove_tree(repo.trees / case["id"])
            repo.remove_tree(repo.trees / (case["id"] + "-fix"))

    runs = result["runs"]
    result["reproduced_failure"] = bool(runs) and all(r["match"]["verdict"] == "exact" for r in runs)
    result["fix_compiles"] = (bool(runs) and all(r.get("fix", {}).get("status") == "ok" for r in runs)
                              if case.get("fix") else None)
    result["status"] = "reproduced" if result["reproduced_failure"] and result["fix_compiles"] \
        else "failure_only" if result["reproduced_failure"] else "not_reproduced"
    save()
    return result


def _summary(build_result):
    keep = ("toolchain", "status", "exit_code", "seconds")
    s = {k: build_result.get(k) for k in keep if k in build_result}
    s["errors"] = build_result.get("errors", [])
    if build_result.get("status") != "ok":
        s["stderr_tail"] = (build_result.get("stderr_tail") or build_result.get("stderr") or "")[-1500:]
    return s


def _store_output(out_dir, case_id, which, build_result):
    """Raw cargo JSON output, gzipped (git-ignored)."""
    p = out_dir / "raw" / f"{case_id}-{which}.jsonl.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress((build_result.get("stdout") or "").encode()))
