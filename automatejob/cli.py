"""Command-line interface: collect, list, rescore, export, and manage jobs."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from pathlib import Path

from .collector import Collector
from .companies import CompanyRegistry
from .config import AppConfig
from .storage import Storage

log = logging.getLogger("automatejob")


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _load_registry(cfg: AppConfig) -> CompanyRegistry:
    path = Path(cfg.companies_path)
    if not path.exists():
        log.warning("companies registry not found at %s; continuing without it", path)
        return CompanyRegistry([])
    return CompanyRegistry.load(path)


def _format_reasons(reasons) -> str:
    lines = []
    for r in reasons:
        pts = r.get("points", 0.0)
        sign = "+" if pts >= 0 else ""
        lines.append(f"      {sign}{pts:g} [{r.get('factor', '?')}] {r.get('detail', '')}")
    return "\n".join(lines)


def _print_job(job: dict, show_reasons: bool = True) -> None:
    score = job["score"]
    new_tag = "[NEW] " if job["is_new"] else ""
    status = f"[{job.get('status', 'open')}] " if job.get("status") != "open" else ""
    elig = job.get("eligibility", "") or ""
    print(f"{score:5.1f} {new_tag}{status}{job['title']} @ {job['company']}  ({elig})")
    loc = job["location"] or job["workplace_type"] or "(location unknown)"
    seniority = f"  seniority={job.get('seniority', '')}" if job.get("seniority") else ""
    print(f"        {loc}  [{job['source_type']}:{job['source_token']}]{seniority}")
    print(f"        {job['url']}")
    if show_reasons and job.get("score_reasons"):
        try:
            reasons = json.loads(job["score_reasons"])
            print(_format_reasons(reasons))
        except (json.JSONDecodeError, TypeError):
            pass
    print()


def _open_storage(cfg: AppConfig) -> Storage:
    return Storage(cfg.db_path)


def cmd_collect(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        collector = Collector(cfg, storage, registry=_load_registry(cfg))
        new_jobs, errors = collector.collect()

        print(f"Collected from {len(cfg.sources)} source(s): "
              f"{len(new_jobs)} newly discovered.\n")

        if errors:
            print(f"{len(errors)} source(s) failed:")
            for e in errors:
                print(f"  - {e['source']}: {e['error']}")
            print()

        stats = storage.stats()
        elig = stats.get("eligibility", {})
        print("Eligibility breakdown (all jobs):")
        print(f"  confirmed:          {elig.get('confirmed', 0)}")
        print(f"  needs_verification: {elig.get('needs_verification', 0)}")
        print(f"  excluded:           {elig.get('excluded', 0)}")
        print(f"  total stored:       {stats['total']}")
        print()

        shown = new_jobs
        if args.min_score is not None:
            shown = [j for j in shown if j.score >= args.min_score]
        limit = args.limit if args.limit is not None else cfg.list_limit
        shown = shown[:limit]

        if shown:
            print(f"Top {len(shown)} newly discovered job(s):\n")
            for job in shown:
                row = job.to_row()
                row["is_new"] = 1
                row["status"] = "open"
                _print_job(row)
        else:
            print("No newly discovered jobs matched the current filters.")
        return 0
    finally:
        storage.close()


def cmd_list(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        rows = storage.list_jobs(
            only_new=not args.all,
            min_score=args.min_score,
            limit=args.limit if args.limit is not None else cfg.list_limit,
            source_type=args.source,
            eligibility=args.eligibility,
            only_qualified=args.qualified,
            status=None if args.all else "open",
        )
        if not rows:
            print("No jobs found." + ("" if args.all else " Run `list --all` to see everything."))
            return 0
        for row in rows:
            _print_job(row, show_reasons=not args.no_reasons)
        if args.mark_seen and rows:
            n = storage.mark_seen(only_new=not args.all)
            print(f"Marked {n} job(s) as seen.")
        return 0
    finally:
        storage.close()


def cmd_rescore(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        collector = Collector(cfg, storage, registry=_load_registry(cfg))
        n = collector.rescore()
        print(f"Re-scored {n} stored job(s) in place against the current config "
              "(no refetch).")
        return 0
    finally:
        storage.close()


def cmd_export(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        rows = storage.list_jobs(
            only_new=False,
            min_score=None,
            limit=None,
            source_type=None,
            eligibility=None if args.all else "confirmed",
            only_qualified=False,
            status=None if args.all else "open",
        )
        data = storage.export_rows(rows)
        fmt = args.format.lower()
        if fmt == "json":
            text = json.dumps(data, indent=2, ensure_ascii=False)
        elif fmt == "csv":
            buf = io.StringIO()
            if data:
                writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
                writer.writeheader()
                writer.writerows(data)
            text = buf.getvalue()
        else:
            print(f"error: unknown format {args.format!r} (use csv or json)", file=sys.stderr)
            return 2
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"Wrote {len(data)} job(s) to {args.output}")
        else:
            sys.stdout.write(text + ("\n" if fmt == "csv" and text else ""))
        return 0
    finally:
        storage.close()


def cmd_stats(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        s = storage.stats()
        print(f"total jobs:            {s['total']}")
        print(f"  open:                {s['open']}")
        print(f"  closed:              {s['closed']}")
        print("by eligibility:")
        for k, v in sorted(s.get("eligibility", {}).items()):
            print(f"  {k:22s} {v}")
        dups = storage.find_cross_board_duplicates()
        print(f"cross-board duplicate groups: {len(dups)}")
        return 0
    finally:
        storage.close()


def cmd_sources(cfg: AppConfig, args) -> int:
    for sc in cfg.sources:
        print(f"{sc.type:10s} {sc.token:20s} {sc.name}")
    print(f"\n{len(cfg.sources)} source(s) configured.")
    return 0


def cmd_companies(cfg: AppConfig, args) -> int:
    reg = _load_registry(cfg)
    for c in sorted(reg.all(), key=lambda c: c.name.lower()):
        mumbai = {True: "yes", False: "no", None: "unverified"}[c.mumbai_office]
        print(f"{c.name:14s} ats={c.ats_type or '-':10s} "
              f"mumbai_office={mumbai:10s} remote={c.remote_eligibility}")
        if c.careers_url:
            print(f"              careers: {c.careers_url}")
        if c.last_checked:
            print(f"              checked: {c.last_checked}")
    print(f"\n{len(reg.all())} company(ies) in registry.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="automatejob",
        description="Personal AI-powered job hunting assistant (reads public job boards).",
    )
    p.add_argument("-c", "--config", default="config.toml",
                   help="path to config.toml (default: ./config.toml)")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="increase log verbosity (-v info, -vv debug)")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("collect", help="fetch from all sources and list newly discovered jobs")
    pc.add_argument("--min-score", type=float, default=None,
                    help="only show new jobs scoring at least this")
    pc.add_argument("--limit", type=int, default=None,
                    help="cap the number of new jobs shown")
    pc.set_defaults(func=cmd_collect)

    pl = sub.add_parser("list", help="list stored jobs")
    pl.add_argument("--all", action="store_true", help="show all jobs, not just new ones")
    pl.add_argument("--qualified", action="store_true",
                    help="only confirmed-eligible jobs")
    pl.add_argument("--eligibility", type=str, default=None, choices=[
        "confirmed", "excluded", "needs_verification"],
        help="filter by eligibility status")
    pl.add_argument("--min-score", type=float, default=None)
    pl.add_argument("--limit", type=int, default=None)
    pl.add_argument("--source", type=str, default=None,
                    help="filter by source type (greenhouse/lever/ashby)")
    pl.add_argument("--mark-seen", action="store_true", help="clear the 'new' flag after listing")
    pl.add_argument("--no-reasons", action="store_true", help="hide scoring reasons")
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("rescore", help="re-score stored jobs in place (no refetch)")
    pr.set_defaults(func=cmd_rescore)

    pe = sub.add_parser("export", help="export stored jobs to CSV/JSON")
    pe.add_argument("--format", type=str, default="csv", choices=["csv", "json"])
    pe.add_argument("--all", action="store_true",
                    help="export every job; default is confirmed-eligible only")
    pe.add_argument("-o", "--output", type=str, default=None, help="write to a file")
    pe.set_defaults(func=cmd_export)

    ps = sub.add_parser("stats", help="show aggregate database statistics")
    ps.set_defaults(func=cmd_stats)

    pso = sub.add_parser("sources", help="list configured sources")
    pso.set_defaults(func=cmd_sources)

    pco = sub.add_parser("companies", help="list the company registry")
    pco.set_defaults(func=cmd_companies)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"error: config file not found: {config_path}", file=sys.stderr)
        return 2
    try:
        cfg = AppConfig.load(config_path)
    except Exception as exc:
        print(f"error: failed to load config {config_path}: {exc}", file=sys.stderr)
        return 2

    if args.command != "companies" and not cfg.sources:
        print("error: no sources configured in config file", file=sys.stderr)
        return 2

    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
