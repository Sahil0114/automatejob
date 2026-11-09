"""Command-line interface: collect, list, rescore, export, daily report, and manage jobs."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collector import Collector
from .companies import CompanyRegistry
from .company import CompanyStorage
from .config import AppConfig
from .discovery import CompanyDiscovery
from .profile import AnswerBank, CandidateProfile, UnverifiedField
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


def _fit_reasons(row: dict) -> str:
    parts = []
    if row.get("role_family"):
        parts.append(f"role={row['role_family']}")
    if row.get("seniority"):
        parts.append(f"seniority={row['seniority']}")
    pref = row.get("location_preference")
    if pref:
        parts.append(f"loc={pref}")
    if row.get("eligibility_reason"):
        parts.append(row["eligibility_reason"])
    return "; ".join(parts)


def _print_job(job: dict, show_reasons: bool = True) -> None:
    score = job["score"]
    new_tag = "[NEW] " if job["is_new"] else ""
    status = f"[{job.get('status', 'open')}] " if job.get("status") != "open" else ""
    pref = job.get("location_preference") or ""
    print(f"{score:5.1f} {new_tag}{status}{job['title']} @ {job['company']}  ({pref})")
    loc = job["location"] or job["workplace_type"] or "(location unknown)"
    extra = f"  role={job.get('role_family', '')}" if job.get("role_family") else ""
    extra += f"  seniority={job.get('seniority', '')}" if job.get("seniority") else ""
    print(f"        {loc}  [{job['source_type']}:{job['source_token']}]{extra}")
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


def _write_last_run(cfg: AppConfig, new_count: int, errors: list) -> Path:
    path = Path(cfg.reports_dir) / "last_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "new_jobs": new_count,
        "errors": errors,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _read_last_run(cfg: AppConfig) -> dict:
    path = Path(cfg.reports_dir) / "last_run.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def cmd_collect(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        collector = Collector(cfg, storage, registry=_load_registry(cfg))
        new_jobs, errors = collector.collect()
        _write_last_run(cfg, len(new_jobs), errors)

        print(f"Collected from {len(cfg.sources)} source(s): "
              f"{len(new_jobs)} newly discovered.\n")

        if errors:
            print(f"{len(errors)} source(s) failed:")
            for e in errors:
                print(f"  - {e['source']}: {e['error']}")
            print()

        stats = storage.stats()
        cats = stats.get("categories", {})
        print("Review buckets (partition ALL records):")
        print(f"  actionable:          {cats.get('actionable', 0)}")
        print(f"  relocation:          {cats.get('relocation', 0)}")
        print(f"  review:              {cats.get('review', 0)}")
        print(f"  needs_verification:  {cats.get('needs_verification', 0)}")
        print(f"  excluded:            {cats.get('excluded', 0)}")
        print(f"  closed:              {cats.get('closed', 0)}")
        print(f"  total stored:        {stats['total']}")
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
            location_preference=args.preference,
            only_actionable=args.actionable,
            category=args.category,
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
        print(f"Re-classified + re-scored {n} stored job(s) in place (no refetch).")
        return 0
    finally:
        storage.close()


def cmd_export(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        if args.all:
            rows = storage.list_jobs(only_new=False, min_score=None, limit=None,
                                     status=None)
        elif args.actionable:
            rows = storage.list_jobs(only_new=False, min_score=None, limit=None,
                                     only_actionable=True, status="open")
        else:
            rows = storage.list_jobs(only_new=False, min_score=None, limit=None,
                                     eligibility="confirmed", status="open")
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
        print(f"  actionable (open):   {s.get('actionable', 0)}")
        print("review buckets (non-overlapping partition of ALL records):")
        for k in ("actionable", "relocation", "review", "needs_verification",
                  "excluded", "closed"):
            print(f"  {k:20s} {s.get('categories', {}).get(k, 0)}")
        dups = storage.find_cross_board_duplicates()
        print(f"cross-board duplicate groups: {len(dups)}")
        return 0
    finally:
        storage.close()


def cmd_daily(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        out = []
        out.append(f"automatejob daily report — {datetime.now(timezone.utc).isoformat()}")
        out.append("")

        # Newly discovered actionable jobs.
        actionable = storage.list_jobs(only_new=not args.all,
                                       only_actionable=True, limit=args.limit,
                                       status="open")
        out.append(f"Newly discovered actionable jobs ({len(actionable)}):")
        if actionable:
            for r in actionable:
                out.append(f"  {r['title']} @ {r['company']} — {r['location'] or r['workplace_type']}")
                out.append(f"    {r['url']}")
                out.append(f"    fit: {_fit_reasons(r)}")
        else:
            out.append("  (none)")

        # Changed postings.
        changed = storage.conn.execute(
            "SELECT title, company, location, url, changed_at FROM jobs "
            "WHERE changed_at != '' ORDER BY changed_at DESC LIMIT ?", (args.limit or 20,)
        ).fetchall()
        out.append("")
        out.append(f"Changed postings ({len(changed)}):")
        for r in changed:
            out.append(f"  {r['title']} @ {r['company']} — {r['location']}  {r['url']}")

        # Closed roles.
        closed = storage.conn.execute(
            "SELECT title, company, location, url FROM jobs WHERE status = 'closed' "
            "ORDER BY last_seen_at DESC LIMIT ?", (args.limit or 20,)
        ).fetchall()
        out.append("")
        out.append(f"Closed roles ({len(closed)}):")
        for r in closed:
            out.append(f"  {r['title']} @ {r['company']} — {r['location']}")

        # Needs-verification queue.
        nv = storage.list_jobs(only_new=False, eligibility="needs_verification",
                               status="open", limit=args.limit or 20)
        nv_total = storage.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE eligibility='needs_verification' AND status='open'"
        ).fetchone()["c"]
        out.append("")
        out.append(f"Needs-verification queue ({nv_total} total, showing {len(nv)}):")
        for r in nv:
            out.append(f"  {r['title']} @ {r['company']} — {r['location']}  {r['url']}")

        # Failed sources (from last collect run).
        last = _read_last_run(cfg)
        errors = last.get("errors", [])
        out.append("")
        if errors:
            out.append(f"Failed sources ({len(errors)}):")
            for e in errors:
                out.append(f"  - {e['source']}: {e['error']}")
        else:
            out.append(f"Failed sources: 0 (last run {last.get('ran_at', 'unknown')})")

        text = "\n".join(out) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"Wrote daily report to {args.output}")
        else:
            sys.stdout.write(text)
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


def _company_storage(cfg: AppConfig) -> CompanyStorage:
    return CompanyStorage(cfg.companies_db_path)


def _print_company(c: dict) -> None:
    mumbai = {True: "yes", False: "no", None: "?"}[c["mumbai_office"]]
    techs = ", ".join(json.loads(c["technologies"] or "[]")[:6])
    print(f"{c['score']:5.1f} [{c['category'] or '-':20s}] {c['name']}")
    print(f"        domain={c['domain']}  mumbai_office={mumbai}  remote={c['remote_policy']}")
    if c.get("funding_stage") and c["funding_stage"] != "unknown":
        print(f"        funding={c['funding_stage']}  size={c['company_size']}")
    if techs:
        print(f"        tech: {techs}")
    if c.get("ats_type"):
        print(f"        ATS: {c['ats_type']} (token={c['ats_token']})")
    if c.get("website"):
        print(f"        {c['website']}")
    print()


def cmd_discover(cfg: AppConfig, args) -> int:
    cstore = _company_storage(cfg)
    jobs = Storage(cfg.db_path)
    try:
        disc = CompanyDiscovery(cstore, jobs_storage=jobs)
        total = disc.discover_yc(max_pages=args.yc_pages)
        print(f"Discovered {total} YC companies.")
        if args.ats:
            relevant = [disc._row_to_company(r) for r in
                        cstore.list(mumbai=True, limit=args.ats_limit)]
            n = disc.detect_ats_for(relevant, limit=args.ats_limit)
            print(f"ATS-detected + validated for {n} companies.")
        scored = disc.score_all()
        print(f"Scored {scored} companies.")
        s = cstore.stats()
        print(f"\nCompany DB totals:")
        print(f"  total:                {s['total']}")
        print(f"  mumbai_office:        {s['mumbai_office']}")
        print(f"  india/worldwide rm:   {s['india_remote']}")
        for cat, n in sorted(s.get("category", {}).items()):
            print(f"  {cat:22s} {n}")
        return 0
    finally:
        jobs.close()
        cstore.close()


def cmd_leads(cfg: AppConfig, args) -> int:
    cstore = _company_storage(cfg)
    try:
        if args.mumbai:
            rows = cstore.list(mumbai=True, limit=args.limit)
            label = "Mumbai-office companies"
        elif args.remote:
            rows = cstore.list(remote=args.remote, limit=args.limit)
            label = f"remote={args.remote} companies"
        elif args.category:
            rows = cstore.list(category=args.category, limit=args.limit)
            label = f"category={args.category} companies"
        else:
            rows = cstore.list(limit=args.limit)
            label = "top companies"
        if not rows:
            print(f"No {label} found.")
            return 0
        print(f"{label} ({len(rows)}):\n")
        for r in rows:
            _print_company(r)
        return 0
    finally:
        cstore.close()


def cmd_ats(cfg: AppConfig, args) -> int:
    from .ats_detect import detect_ats
    ats, token, url = detect_ats(args.name, args.domain or "", args.careers or "")
    if ats:
        print(f"{args.name}: {ats} (token={token})")
        print(f"  validated board: {url}")
    else:
        print(f"{args.name}: no verified ATS board found.")
    return 0


def cmd_profile(cfg: AppConfig, args) -> int:
    prof = CandidateProfile(args.path)
    print("Candidate profile (verified facts):")
    print(f"  portfolio:  {prof.portfolio or '(unset)'}")
    print(f"  tech:       {', '.join(prof.technologies[:8])}...")
    print(f"  roles:      {', '.join(r['label'] for r in prof.target_roles)}")
    missing = prof.unverified_fields()
    print(f"\nUnverified fields needing confirmation ({len(missing)}):")
    for m in missing:
        print(f"  - {m}")
    if args.bank:
        bank = AnswerBank(args.bank)
        print(f"\nAnswer bank questions ({len(bank.questions())}):")
        for q in bank.questions():
            print(f"  - {q}")
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
    pc.add_argument("--min-score", type=float, default=None)
    pc.add_argument("--limit", type=int, default=None)
    pc.set_defaults(func=cmd_collect)

    pl = sub.add_parser("list", help="list stored jobs")
    pl.add_argument("--all", action="store_true", help="show all jobs, not just new ones")
    pl.add_argument("--actionable", action="store_true",
                    help="only roles that pass ALL gates (open + role + seniority + location)")
    pl.add_argument("--eligibility", type=str, default=None, choices=[
        "confirmed", "excluded", "needs_verification"],
        help="filter by employment eligibility")
    pl.add_argument("--preference", type=str, default=None, choices=[
        "mumbai", "relocation", "remote"],
        help="filter by location preference")
    pl.add_argument("--category", type=str, default=None, choices=[
        "actionable", "relocation", "review", "needs_verification", "excluded", "closed"],
        help="filter by review-bucket category (non-overlapping)")
    pl.add_argument("--min-score", type=float, default=None)
    pl.add_argument("--limit", type=int, default=None)
    pl.add_argument("--source", type=str, default=None,
                    help="filter by source type (greenhouse/lever/ashby)")
    pl.add_argument("--mark-seen", action="store_true")
    pl.add_argument("--no-reasons", action="store_true", help="hide scoring reasons")
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("rescore", help="re-classify + re-score stored jobs in place (no refetch)")
    pr.set_defaults(func=cmd_rescore)

    pe = sub.add_parser("export", help="export stored jobs to CSV/JSON")
    pe.add_argument("--format", type=str, default="csv", choices=["csv", "json"])
    pe.add_argument("--all", action="store_true", help="export every job")
    pe.add_argument("--actionable", action="store_true",
                    help="export only actionable jobs (default: confirmed-eligible)")
    pe.add_argument("-o", "--output", type=str, default=None)
    pe.set_defaults(func=cmd_export)

    pd = sub.add_parser("daily", help="print a concise daily report")
    pd.add_argument("--all", action="store_true", help="show all actionable jobs, not just new")
    pd.add_argument("--limit", type=int, default=20)
    pd.add_argument("-o", "--output", type=str, default=None)
    pd.set_defaults(func=cmd_daily)

    ps = sub.add_parser("stats", help="show aggregate database statistics")
    ps.set_defaults(func=cmd_stats)

    pso = sub.add_parser("sources", help="list configured sources")
    pso.set_defaults(func=cmd_sources)

    pco = sub.add_parser("companies", help="list the company registry")
    pco.set_defaults(func=cmd_companies)

    pd = sub.add_parser("discover", help="run company discovery (YC directory + optional ATS)")
    pd.add_argument("--yc-pages", type=int, default=None,
                    help="cap YC directory pages to fetch (default: all)")
    pd.add_argument("--ats", action="store_true",
                    help="detect + validate ATS boards for discovered companies")
    pd.add_argument("--ats-limit", type=int, default=30,
                    help="max companies to ATS-probe (bounded)")
    pd.set_defaults(func=cmd_discover)

    pl = sub.add_parser("leads", help="show company shortlists from the discovery DB")
    pl.add_argument("--mumbai", action="store_true", help="Mumbai-office companies")
    pl.add_argument("--remote", type=str, default=None,
                    choices=["india_remote", "worldwide_remote"])
    pl.add_argument("--category", type=str, default=None,
                    choices=["active_hiring", "speculative_outreach", "research_lead"])
    pl.add_argument("--limit", type=int, default=30)
    pl.set_defaults(func=cmd_leads)

    pa = sub.add_parser("ats", help="detect + validate a company's ATS board")
    pa.add_argument("name")
    pa.add_argument("domain", nargs="?", default="")
    pa.add_argument("--careers", default="")
    pa.set_defaults(func=cmd_ats)

    pp = sub.add_parser("profile", help="show the verified candidate profile")
    pp.add_argument("--path", default="data/candidate_profile.toml")
    pp.add_argument("--bank", default=None, help="path to answer_bank.toml")
    pp.set_defaults(func=cmd_profile)

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

    if args.command not in ("companies", "sources") and not cfg.sources:
        print("error: no sources configured in config file", file=sys.stderr)
        return 2

    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
