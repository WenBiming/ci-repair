"""Command-line entry point: `ci-repair <command> --project meilisearch ...`."""

import argparse
from pathlib import Path

from .github import GitHub
from .project import PROJECTS

ROOT = Path(__file__).resolve().parent.parent


def _data_dir(args):
    return Path(args.data or ROOT / "data" / args.project)


def _gh():
    return GitHub(ROOT / "data" / ".cache")


def cmd_collect(args):
    from .collect import collect
    seen, new = collect(_gh(), PROJECTS[args.project], _data_dir(args), days=args.days)
    print(f"archived {seen} failed runs ({new} new) in {_data_dir(args)}")


def _load_cases(args):
    import json
    path = _data_dir(args) / "cases.jsonl"
    cases = [json.loads(l) for l in path.read_text().splitlines()]
    if getattr(args, "ids", None):
        wanted = set(args.ids)
        cases = [c for c in cases if c["id"] in wanted]
    else:
        cases = [c for c in cases if c["status"] == "ok" and (c["fix"] or args.include_no_fix)]
        if args.limit:
            cases = cases[-args.limit:]  # most recent
    return cases


def _env(args):
    from .reproduce import Docker, Repo
    project = PROJECTS[args.project]
    repo = Repo(project, ROOT / "work")
    repo.ensure_clone()
    return project, repo, Docker(project)


def cmd_cases(args):
    from .cases import build_cases
    cases = build_cases(_gh(), PROJECTS[args.project], _data_dir(args),
                        find_fixes=not args.no_fix)
    ok = [c for c in cases if c["status"] == "ok"]
    print(f"{len(cases)} compile-failure cases, {len(ok)} usable, "
          f"{sum(1 for c in ok if c['fix'])} with a fix -> {_data_dir(args) / 'cases.jsonl'}")


def cmd_reproduce(args):
    from .reproduce import reproduce_case
    project, repo, docker = _env(args)
    cases = _load_cases(args)
    out = _data_dir(args) / "repro"
    tally = {}
    for i, case in enumerate(cases, 1):
        if (out / f"{case['id']}.json").exists() and not args.force:
            continue
        print(f"[{i}/{len(cases)}] {case['id']}", flush=True)
        r = reproduce_case(case, project, repo, docker, out, keep_tree=args.keep_tree)
        tally[r["status"]] = tally.get(r["status"], 0) + 1
        print(f"  -> {r['status']}", flush=True)
    print("done:", tally)


def cmd_repair(args):
    from .repair import REPAIRERS, repair_case
    project, repo, docker = _env(args)
    repairer = REPAIRERS[args.repairer](repo)
    out = _data_dir(args) / "repair"
    for case in _load_cases(args):
        print(f"{case['id']} ({repairer.name}, budget {args.budget})", flush=True)
        r = repair_case(case, repairer, repo, docker, out, budget=args.budget)
        print(f"  -> {r['status']}", flush=True)


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default="meilisearch", choices=sorted(PROJECTS))
    common.add_argument("--data", help="data directory (default: data/<project>)")
    ap = argparse.ArgumentParser(prog="ci-repair")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", parents=[common],
                       help="archive failed CI runs and their logs")
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("cases", parents=[common],
                       help="extract compile-failure cases and their fixes from the archive")
    p.add_argument("--no-fix", action="store_true", help="skip the fix-commit search")
    p.set_defaults(func=cmd_cases)

    select = argparse.ArgumentParser(add_help=False)
    select.add_argument("ids", nargs="*", help="case ids (default: all usable cases)")
    select.add_argument("--limit", type=int, help="only the N most recent cases")
    select.add_argument("--include-no-fix", action="store_true",
                        help="also cases without a known fix commit")

    p = sub.add_parser("reproduce", parents=[common, select],
                       help="rebuild cases in Docker and compare with CI")
    p.add_argument("--force", action="store_true", help="redo cases already reproduced")
    p.add_argument("--keep-tree", action="store_true", help="keep worktrees for inspection")
    p.set_defaults(func=cmd_reproduce)

    p = sub.add_parser("repair", parents=[common, select], help="run a repairer on cases")
    p.add_argument("--repairer", default="oracle", choices=["oracle", "noop"])
    p.add_argument("--budget", type=int, default=5)
    p.set_defaults(func=cmd_repair)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
