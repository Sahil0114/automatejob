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
from .company_scoring import outreach_readiness
from .config import AppConfig, FirecrawlSettings
from .discovery import CompanyDiscovery
from .email_draft import DRAFT_TYPES, draft_email
from .gmail import GmailAdapter, GmailConfig
from .outreach import OutreachStorage
from .pipeline import build_shortlist
from .profile import AnswerBank, CandidateProfile, UnverifiedField
from .storage import Storage
from .workflow import add_contact_and_draft, import_contacts_csv, sync_gmail_replies

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
        only_new = not args.all
        # Actionable/category views are review buckets over stored jobs; default to
        # all matching records so counts align with `stats`/reports (not just "new").
        if (args.actionable or args.category) and not args.all:
            only_new = False
        rows = storage.list_jobs(
            only_new=only_new,
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

        # Outreach pipeline summary.
        try:
            outr = OutreachStorage(cfg.outreach_db_path)
            m = outr.metrics()
            drafts = outr.list_drafts(state="awaiting_approval")
            followups = outr.list_due_followups()
            out.append("")
            out.append("Outreach pipeline:")
            out.append(f"  companies {m['companies']} | contacts {m['contacts']} "
                       f"(verified {m['contacts_verified']}) | drafts {m['drafts']} "
                       f"| sent {m['sent']} | replies {m['replies']} | interviews {m['interviews']}")
            out.append(f"  drafts awaiting approval: {len(drafts)}")
            out.append(f"  follow-ups due: {len(followups)}")
            outr.close()
        except Exception as exc:  # outreach DB may not exist yet
            out.append("")
            out.append(f"Outreach pipeline: unavailable ({exc})")

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
        result = disc.discover_yc(max_pages=args.yc_pages)
        print(f"Discovered {result['fetched']} YC companies across {result['pages']} page(s) "
              f"(pagination complete={result['completed']}).")
        print(f"Distinct companies in DB: {result['distinct_stored']}.")
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
    if args.confirm:
        out = OutreachStorage(cfg.outreach_db_path)
        try:
            for item in args.confirm:
                field, _, value = item.partition("=")
                if not field or not value:
                    print(f"  invalid --confirm {item!r} (expected field=value)")
                    continue
                out.confirm_profile(field.strip(), value.strip(), source="cli")
                print(f"  confirmed {field.strip()}={value.strip()}")
            confirmed = out.list_profile_confirmations()
        finally:
            out.close()
        if confirmed:
            print("Confirmed fields:")
            for k, v in confirmed.items():
                print(f"  {k}: {v}")
        return 0

    out = OutreachStorage(cfg.outreach_db_path)
    try:
        confirmed = out.list_profile_confirmations()
        missing = prof.unverified_fields(out)
    finally:
        out.close()

    print("Candidate profile:")
    print(f"  name:       {prof.name}")
    print(f"  email:      {prof.email}")
    print(f"  portfolio:  {prof.portfolio or '(unset)'}")
    print(f"  github:     {prof.github or '(unset)'}")
    print(f"  linkedin:   {prof.linkedin or '(unset)'}")
    print(f"  education:  {prof.education_summary[:80]}...")
    print(f"  experience: {len(prof.experience)} roles (latest: "
          f"{prof.experience[0]['title']} @ {prof.experience[0]['company']})" if prof.experience else "  experience: (none)")
    print(f"  tech:       {', '.join(prof.technologies[:8])}...")
    if confirmed:
        print(f"\nConfirmed overrides ({len(confirmed)}):")
        for k, v in confirmed.items():
            print(f"  - {k}: {v}")
    print(f"\nUnverified fields needing confirmation ({len(missing)}):")
    for m in missing:
        print(f"  - {m}")
    if missing:
        print("\nConfirm locally with:")
        for m in missing:
            print(f"  python3 -m automatejob profile --confirm {m}=<value>")
    else:
        print("  (none — profile confirmations complete)")
    return 0


def cmd_pipeline(cfg: AppConfig, args) -> int:
    from .pipeline import build_shortlist
    cstore = CompanyStorage(cfg.companies_db_path)
    jobs = Storage(cfg.db_path)
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        n = build_shortlist(cstore, jobs, out, limit=args.limit)
        m = out.metrics()
        print(f"Qualified {n} companies into the outreach pipeline.")
        print(f"  by qualification:")
        for r in out.conn.execute(
            "SELECT qualification, COUNT(*) c FROM outreach_companies GROUP BY qualification"):
            print(f"    {r['qualification']:26s} {r['c']}")
        print(f"  metrics: {m}")
        return 0
    finally:
        out.close(); jobs.close(); cstore.close()


def cmd_outreach(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        m = out.metrics()
        for k, v in m.items():
            print(f"  {k:22s} {v}")
        print("\nTop companies:")
        for r in out.conn.execute(
            "SELECT name, domain, qualification, score FROM outreach_companies "
            "ORDER BY score DESC LIMIT 10"):
            print(f"  {r['score']:5.1f} [{r['qualification']:24s}] {r['name']} ({r['domain']})")
        return 0
    finally:
        out.close()


def cmd_draft(cfg: AppConfig, args) -> int:
    prof = CandidateProfile("data/candidate_profile.toml")
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        row = out.conn.execute(
            "SELECT * FROM outreach_companies WHERE domain=? OR name=?",
            (args.company, args.company)).fetchone()
        if row is None:
            print(f"No outreach company for {args.company!r}. Run `pipeline` first.")
            return 1
        evidence = out.list_evidence(row["company_id"])
        contact = None
        d = draft_email(prof, dict(row), contact, args.type,
                        evidence=evidence, job_title=args.job_title)
        print(f"Subject: {d['subject']}\n")
        print(d["body"])
        print("\n--- evidence map ---")
        for k, v in d["evidence_map"].items():
            print(f"  {k}: {v}")
        if args.save:
            existing = out.conn.execute(
                "SELECT id FROM outreach_drafts WHERE contact_id IS NULL AND company_id=? "
                "AND draft_type=? AND subject=? AND body=? AND state NOT IN ('rejected','sent') "
                "ORDER BY id DESC LIMIT 1",
                (row["company_id"], args.type, d["subject"], d["body"])).fetchone()
            if existing:
                did = existing["id"]
                print(f"\nReused existing draft #{did} (idempotent; state unchanged).")
            else:
                did = out.create_draft(row["company_id"], args.type, d["subject"], d["body"],
                                       d["evidence_map"])
                print(f"\nSaved as draft #{did} (state=draft_ready).")
        return 0
    finally:
        out.close()


def cmd_dashboard(cfg: AppConfig, args) -> int:
    from .dashboard import run_dashboard
    import os
    out = OutreachStorage(cfg.outreach_db_path)
    cstore = CompanyStorage(cfg.companies_db_path)
    jobs_store = Storage(cfg.db_path)
    token = args.token or os.environ.get("AUTOMATEJOB_DASHBOARD_TOKEN", "")
    profile = CandidateProfile("data/candidate_profile.toml")
    gmail = GmailAdapter(GmailConfig(
        credentials_file=os.environ.get("GMAIL_CREDENTIALS_FILE", ""),
        token_file=os.environ.get("GMAIL_TOKEN_FILE", "")),
        sending_enabled=args.enable_send)
    server, _ = run_dashboard(out, companies_store=cstore, jobs_store=jobs_store,
                              host=args.host,
                              port=args.port, token=token,
                              gmail=gmail, profile=profile,
                              sending_enabled=args.enable_send)
    url = f"http://{args.host}:{args.port}"
    print(f"Dashboard running at {url}")
    if not token:
        print("WARNING: no token set — open to any local process. "
              "Set AUTOMATEJOB_DASHBOARD_TOKEN or pass --token.")
    print("Sign in at /login with the token. Sending is DISABLED by default.")
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def _firecrawl_client(cfg: AppConfig):
    from .firecrawl import FirecrawlBudget, FirecrawlCLI, FirecrawlConfig
    settings = cfg.firecrawl
    budget = FirecrawlBudget(settings.db_path, FirecrawlConfig(
        enabled=settings.enabled, monthly_budget=settings.monthly_budget,
        warning_threshold=settings.warning_threshold,
        hard_stop_threshold=settings.hard_stop_threshold,
        absolute_account_limit=settings.absolute_account_limit,
        search_results_default=settings.search_results_default,
        allow_payg=settings.allow_payg,
        db_path=settings.db_path))
    return budget, FirecrawlCLI(budget)


def cmd_firecrawl(cfg: AppConfig, args) -> int:
    from .firecrawl import BudgetExceeded
    budget, client = _firecrawl_client(cfg)
    try:
        if args.action == "usage":
            account = client.account_usage()
            local = budget.usage()
            print("Firecrawl usage")
            print(f"  account source:      {account.get('source')}")
            if account.get("available"):
                print(f"  account remaining:   {account.get('remaining')} "
                      f"(plan {account.get('plan', '')})")
                print(f"  account used:        {account.get('used')}")
            else:
                print("  account remaining:   unknown (CLI usage API unavailable)")
            print(f"  local monthly usage: {local['monthly_consumed']:g} (estimated, not authoritative)")
            print(f"  local daily usage:   {local['daily_consumed']:g}")
            print(f"  internal budget limit:{local['budget_limit']:g} (hard_stop/absolute)")
            print(f"  internal remaining:  {local.get('internal_budget_remaining', local.get('budget_remaining', 0)):g}")
            print(f"  warning threshold:   {local.get('warning_threshold', local.get('soft_limit', 0)):g}")
            print(f"  hard stop threshold: {local.get('hard_stop_threshold', local.get('budget_limit', 0)):g}")
            print(f"  absolute acct limit: {local.get('absolute_account_limit', 'unknown')}")
            print(f"  allow_payg:          {local.get('allow_payg', False)}")
            print(f"  paid ops paused:     {local.get('paid_operations_paused', local.get('paused', False))}")
            print(f"  by endpoint:         {local['by_endpoint'] or '{}'}")
            print(f"  by provider:         {local['by_provider'] or '{}'}")
            print(f"  failed operations:   {local.get('failed_operations', 0)} (credits {local.get('failed_operations_credits', 0):g})")
            print(f"  unknown operations:  {local.get('unknown_operations', 0)}")
            return 0

        if args.action == "auth":
            print(f"authenticated: {client.auth_status()['authenticated']}")
            return 0

        if args.action == "search":
            data = client.search(args.query, limit=args.limit, sources=args.sources)
            urls = [r.get("url") for r in (data.get("data", {}).get("web") or [])]
            print(f"search returned {len(urls)} result(s) "
                  f"({'cached' if data.get('cached') else 'live'})")
            for url in urls:
                print(f"  {url}")
            return 0

        if args.action == "scrape":
            data = client.scrape(args.url, formats=args.formats)
            print(f"scrape {'cached' if data.get('cached') else 'completed'}")
            return 0

        if args.action == "map":
            data = client.map(args.url, search=args.search or "")
            print(f"map {'cached' if data.get('cached') else 'completed'}")
            return 0

        if args.action == "crawl":
            data = client.crawl(args.url, limit=args.limit, max_depth=args.max_depth,
                                path_pattern=args.path or "")
            print(f"crawl completed (limit={args.limit}, depth={args.max_depth})")
            return 0

        if args.action == "alexandria":
            if args.provider_cost is None:
                print("error: --provider-cost is required (read it from the provider contract)")
                return 2
            data = client.alexandria(args.provider, args.capability,
                                     json.loads(args.options or "{}"),
                                     provider_cost=float(args.provider_cost),
                                     cache_ttl_seconds=int(args.cache_ttl or 0))
            print(f"alexandria {args.provider}/{args.capability} completed "
                  f"({'cached' if data.get('cached') else 'live'})")
            return 0

        print(f"unknown firecrawl action {args.action!r}")
        return 2
    except BudgetExceeded as exc:
        print(f"Budget guard blocked the request: {exc}")
        return 3
    except RuntimeError as exc:
        print(f"Firecrawl request failed: {exc}")
        return 1
    finally:
        budget.close()


def cmd_registry(cfg: AppConfig, args) -> int:
    """Show the source registry (statuses are explicit; unsupported != active)."""
    from .source_registry import SourceRegistry, seed_defaults
    reg = SourceRegistry("data/source_registry.db")
    try:
        if args.refresh or reg.count() == 0:
            seed_defaults(reg)
        rows = reg.list_all()
        if not rows:
            seed_defaults(reg)
            rows = reg.list_all()
        if args.verbose:
            for r in rows:
                print(f"{r['status']:11s} {r['name']:20s} [{r['integration_status']}]")
                print(f"             {r['source_category']} | {r['access_method']} | auth={bool(r['auth_required'])} perm={r['permission']}")
                if r["website"]:
                    print(f"             site: {r['website']}")
                if r["source_url"]:
                    print(f"             url:  {r['source_url']}")
                if r["terms_url"]:
                    print(f"             terms:{r['terms_url']}")
                if r["provider_cost"] or r["firecrawl_credit_cost"]:
                    print(f"             cost: provider={r['provider_cost'] or 'n/a'} firecrawl={r['firecrawl_credit_cost'] or 'n/a'}")
                if r["reason"]:
                    print(f"             reason: {r['reason']}")
                if r["manual_workflow"]:
                    print(f"             manual: {r['manual_workflow']}")
                if r["last_successful_at"]:
                    print(f"             last ok {r['last_successful_at']} verified={r['last_verified_records']} records={r['record_count']} errors={r['error_count']}")
        else:
            for r in rows:
                print(f"{r['status']:11s} {r['name']:22s} {r['integration_status']:26s} "
                      f"auth={bool(r['auth_required'])} perm={r['permission']:18s} {r['source_category']}")
        return 0
    finally:
        reg.close()


def _seed_source_registry(reg) -> None:
    """Seed the canonical source catalog (honest statuses)."""
    from .source_registry import seed_defaults
    seed_defaults(reg)


def cmd_discovery_log(cfg: AppConfig, args) -> int:
    from .discovery_log import DiscoveryLog
    logdb = DiscoveryLog("data/discovery_log.db")
    try:
        summary = logdb.summary()
        print(f"Discovery operations today: {summary['total']}")
        for outcome, meta in summary["by_outcome"].items():
            print(f"  {outcome:10s} {meta['count']}  (credits {meta['credits']:g})")
        print("\nBy source:")
        for s in summary["by_source"]:
            print(f"  {s['source']:24s} {s['count']}  (credits {s['credits']:g})")
        print("\nRecent operations:")
        for op in logdb.list(limit=args.limit):
            print(f"  #{op['id']} {op['started_at']} {op['source']:20s} "
                  f"{op['outcome']:8s} records={op['returned_records']} "
                  f"new_jobs={op['new_jobs']} new_companies={op['new_companies']} "
                  f"credits={op['firecrawl_credits']:g}")
            if op["error_reason"]:
                print(f"      error: {op['error_reason']}")
        return 0
    finally:
        logdb.close()


def cmd_discovery_run(cfg: AppConfig, args) -> int:
    """Run the bounded daily discovery plan across permitted sources only.

    This is deterministic and conservative: it never exceeds Firecrawl budgets
    and never touches restricted sources. Each operation is recorded in the
    discovery log; a failure does not count as successful discovery.
    """
    from .discovery_log import DiscoveryLog
    logdb = DiscoveryLog("data/discovery_log.db")
    try:
        ops = _build_discovery_plan(cfg)
        print(f"Discovery plan: {len(ops)} operations.")
        if args.dry_run:
            for op in ops:
                print(f"  [dry] {op['category']:28s} {op['source']:20s} {op['query'][:60]}")
            return 0
        for op in ops:
            outcome, meta = _run_discovery_op(cfg, op)
            op_id = logdb.start(op["source"], access_method=op.get("access_method", ""),
                                query=op.get("query", ""),
                                provider_cost=op.get("provider_cost", 0.0))
            logdb.complete(op_id, outcome=outcome, **meta)
            print(f"  [{outcome:8s}] {op['source']:20s} {op['query'][:60]} "
                  f"-> records={meta.get('returned_records', 0)}")
        print(f"\n{logdb.summary()['total']} operations accounted for today.")
        return 0
    finally:
        logdb.close()


def _build_discovery_plan(cfg: AppConfig) -> list:
    """Return the bounded daily discovery plan (33 operations, permitted only).

    The plan favours deterministic/free operations (existing API/RSS adapters,
    source checks, company rechecks). Restricted portals are represented by
    ``manual`` or ``skipped`` entries, never by an automated scrape — so the
    accounting is honest about where the day's effort actually goes.
    """
    plan = []

    # 1) ATS / company career pages (6): existing free board APIs + company rechecks.
    for src in ("greenhouse", "lever", "ashby"):
        plan.append({"category": "ats_job_board", "source": src, "access_method": "api",
                     "query": f"collect {src}", "provider_cost": 0.0})
    plan.append({"category": "ats_job_board", "source": "companies",
                 "access_method": "public_scrape", "query": "recheck verified career pages",
                 "provider_cost": 0.0})
    plan.append({"category": "ats_job_board", "source": "companies",
                 "access_method": "ats_detect", "query": "detect/validate ATS for watchlist",
                 "provider_cost": 0.0})
    plan.append({"category": "ats_job_board", "source": "companies",
                 "access_method": "ats_detect", "query": "revalidate stale ATS tokens",
                 "provider_cost": 0.0})

    # 2) YC job/company discovery (5).
    plan.append({"category": "yc_discovery", "source": "yc_waas",
                 "access_method": "public_scrape", "query": "YC WaaS company jobs",
                 "provider_cost": 0.0})
    plan.append({"category": "yc_discovery", "source": "yc_directory",
                 "access_method": "api", "query": "YC directory page(s)",
                 "provider_cost": 0.0})
    plan.append({"category": "yc_discovery", "source": "yc_directory",
                 "access_method": "api", "query": "YC new-grad/junior scan",
                 "provider_cost": 0.0})
    plan.append({"category": "yc_discovery", "source": "yc_directory",
                 "access_method": "api", "query": "YC Mumbai companies recheck",
                 "provider_cost": 0.0})
    plan.append({"category": "yc_discovery", "source": "yc_waas",
                 "access_method": "public_scrape", "query": "YC remote-eligible companies",
                 "provider_cost": 0.0})

    # 3) Global remote-job sources (5).
    plan.append({"category": "remote_job_board", "source": "weworkremotely",
                 "access_method": "rss", "query": "remote programming RSS",
                 "provider_cost": 0.0})
    plan.append({"category": "remote_job_board", "source": "weworkremotely",
                 "access_method": "rss", "query": "remote devops/sysadmin RSS",
                 "provider_cost": 0.0})
    plan.append({"category": "remote_job_board", "source": "hn",
                 "access_method": "api", "query": "who is hiring (remote roles)",
                 "provider_cost": 0.0})
    plan.append({"category": "remote_job_board", "source": "firecrawl",
                 "access_method": "search", "query": "worldwide remote junior backend",
                 "provider_cost": 2.0})
    plan.append({"category": "remote_job_board", "source": "firecrawl",
                 "access_method": "search", "query": "india remote devops junior",
                 "provider_cost": 2.0})

    # 4) Recruiter posts from permitted sources (5).
    plan.append({"category": "recruiter_posts", "source": "hn",
                 "access_method": "api", "query": "hiring announcements with emails",
                 "provider_cost": 0.0})
    for i in range(4):
        plan.append({"category": "recruiter_posts", "source": "manual",
                     "access_method": "manual_import_only",
                     "query": f"manual recruiter-post import slot {i + 1}",
                     "provider_cost": 0.0})

    # 5) Mumbai startup intelligence (3).
    for src in ("yc_directory", "companies", "companies"):
        plan.append({"category": "mumbai_intel", "source": src,
                     "access_method": "api" if src == "yc_directory" else "db_recheck",
                     "query": "Mumbai engineering-office recheck", "provider_cost": 0.0})

    # 6) Previously discovered company verification (3).
    for i in range(3):
        plan.append({"category": "company_verify", "source": "companies",
                     "access_method": "db_recheck",
                     "query": f"reverify previously discovered company #{i + 1}",
                     "provider_cost": 0.0})

    # 7) Indian job portals and permitted feeds (6): all restricted/manual, honest.
    for src in ("internshala", "indeed", "naukri", "foundit", "cutshort", "hirist"):
        plan.append({"category": "india_portal", "source": src,
                     "access_method": "manual_import_only",
                     "query": "manual import / job-alert ingestion", "provider_cost": 0.0})

    return plan


def _run_discovery_op(cfg: AppConfig, op: dict):
    """Execute one permitted discovery op; returns (outcome, metadata)."""
    from .collector import Collector
    from .companies import CompanyRegistry
    from .company import CompanyStorage
    from .storage import Storage

    source = op["source"]
    # Restricted/manual sources are never executed automatically.
    if op.get("access_method") in ("manual_import_only", "requires_permission"):
        return "skipped", {"returned_records": 0, "new_jobs": 0,
                           "new_companies": 0, "duplicate_count": 0,
                           "firecrawl_credits": 0.0,
                           "error_reason": "manual/restricted source — not auto-run"}

    try:
        if source in ("greenhouse", "lever", "ashby", "hn", "yc_waas", "simplifyjobs"):
            storage = Storage(cfg.db_path)
            try:
                collector = Collector(cfg, storage, registry=_load_registry(cfg))
                new_jobs, errors = collector.collect()
                return ("success" if not errors else "failure",
                        {"returned_records": len(new_jobs), "new_jobs": len(new_jobs),
                         "new_companies": 0, "duplicate_count": 0,
                         "firecrawl_credits": 0.0,
                         "error_reason": "; ".join(e["error"] for e in errors)})
            finally:
                storage.close()
        if source == "weworkremotely":
            from .sources.weworkremotely import WeWorkRemotelySource
            src = WeWorkRemotelySource("remote-programming-jobs")
            jobs = src.fetch()
            return "success", {"returned_records": len(jobs), "new_jobs": 0,
                               "new_companies": 0, "duplicate_count": 0,
                               "firecrawl_credits": 0.0, "error_reason": ""}
        if source == "firecrawl" and op.get("access_method") == "search":
            from .firecrawl import FirecrawlBudget, FirecrawlCLI, FirecrawlConfig
            settings = cfg.firecrawl
            budget = FirecrawlBudget(settings.db_path, FirecrawlConfig(
                enabled=settings.enabled, monthly_budget=settings.monthly_budget,
                warning_threshold=settings.warning_threshold,
                hard_stop_threshold=settings.hard_stop_threshold,
                absolute_account_limit=settings.absolute_account_limit,
                search_results_default=settings.search_results_default,
                allow_payg=settings.allow_payg, db_path=settings.db_path))
            try:
                client = FirecrawlCLI(budget)
                data = client.search(op["query"], limit=10, sources="web")
                web = (data.get("data", {}) or {}).get("web") if isinstance(data, dict) else None
                n = len(web) if isinstance(web, list) else 0
                return "success", {"returned_records": n, "new_jobs": 0,
                                   "new_companies": 0, "duplicate_count": 0,
                                   "firecrawl_credits": 2.0, "error_reason": ""}
            finally:
                budget.close()
        if source == "companies":
            return "skipped", {"returned_records": 0, "new_jobs": 0, "new_companies": 0,
                               "duplicate_count": 0, "firecrawl_credits": 0.0,
                               "error_reason": "targeted recheck requires explicit selector (not auto-run)"}
        if source == "yc_directory":
            cstore = CompanyStorage(cfg.companies_db_path)
            try:
                from .discovery import CompanyDiscovery
                result = CompanyDiscovery(cstore).discover_yc(max_pages=2)
                return "success", {"returned_records": result["fetched"],
                                   "new_jobs": 0, "new_companies": result["fetched"],
                                   "duplicate_count": 0, "firecrawl_credits": 0.0,
                                   "error_reason": ""}
            finally:
                cstore.close()
        return "skipped", {"returned_records": 0, "new_jobs": 0, "new_companies": 0,
                           "duplicate_count": 0, "firecrawl_credits": 0.0,
                           "error_reason": f"no permitted runner for {source}"}
    except Exception as exc:
        return "failure", {"returned_records": 0, "new_jobs": 0, "new_companies": 0,
                           "duplicate_count": 0, "firecrawl_credits": 0.0,
                           "error_reason": str(exc)[:200]}


def cmd_recruiter(cfg: AppConfig, args) -> int:
    """Import a recruiter post from text/file and extract explicit roles/emails."""
    import json as _json
    from .recruiter import parse_recruiter_post
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
        if not text or not args.url:
            print("error: --url and (--text or --file) are required")
            return 2
        post = parse_recruiter_post(
            text, original_url=args.url, company=args.company or "",
            published_at=args.published_at or "", source_type=args.source_type)
        post.recruiter_name = args.recruiter_name or ""
        post.recruiter_title = args.recruiter_title or ""
        post.recruiter_linkedin_url = args.recruiter_linkedin or ""
        post_id = out.upsert_recruiter_post(post)
        print(f"recruiter post #{post_id} imported from {args.url}")
        print(f"  company: {post.company or '(unknown)'}")
        print(f"  published_at: {post.published_at or '(unknown)'}")
        print(f"  roles: {[r.title for r in post.roles]}")
        print(f"  published emails: {post.published_emails}")
        print(f"  confidence: {post.confidence}")
        return 0
    finally:
        out.close()


def cmd_gmail_auth(cfg: AppConfig, args) -> int:
    """Run the user-initiated local OAuth browser flow; never called by cron."""
    import os
    credentials = args.credentials_file or os.environ.get("GMAIL_CREDENTIALS_FILE", "")
    token = args.token_file or os.environ.get("GMAIL_TOKEN_FILE", "")
    adapter = GmailAdapter(GmailConfig(credentials_file=credentials, token_file=token))
    result = adapter.authorize_interactive(port=args.port)
    if result.get("ok"):
        print(f"Gmail authorized for {result.get('email') or '(account verified)'}.")
        print(f"Token saved locally: {token}")
        return 0
    print(f"Gmail authorization not completed: {result.get('error', 'unknown error')}")
    return 1


def cmd_gmail_sync(cfg: AppConfig, args) -> int:
    """Explicitly sync authorized Gmail reply state; never sends messages."""
    import os
    gmail = GmailAdapter(GmailConfig(
        credentials_file=os.environ.get("GMAIL_CREDENTIALS_FILE", ""),
        token_file=os.environ.get("GMAIL_TOKEN_FILE", "")))
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        result = sync_gmail_replies(out, gmail)
        if result.get("error"):
            print(f"Gmail sync not run: {result['error']}")
            return 1
        print(f"Gmail reply sync: {result['replies_recorded']} reply/replies recorded; "
              f"{result['unknown_threads']} thread(s) need mailbox review.")
        return 0
    finally:
        out.close()


def cmd_contact(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    profile = CandidateProfile("data/candidate_profile.toml")
    try:
        if args.import_csv:
            result = import_contacts_csv(out, profile, args.import_csv)
            print(f"imported {result['imported']}, skipped {result['skipped']}")
            for e in result["errors"]:
                print(f"  error: {e}")
            return 0
        if args.add:
            r = add_contact_and_draft(
                out, profile, args.company, args.name, args.title, args.role,
                args.email, email_status=args.email_status,
                linkedin_url=args.linkedin, source_url=args.source)
            print(f"contact #{r.contact_id}")
            for w in r.warnings:
                print(f"  warning: {w}")
            if r.draft.get("body"):
                print(f"\nSubject: {r.draft['subject']}\n\n{r.draft['body']}")
            return 0
        # default: list
        for c in out.list_contacts_joined():
            print(f"#{c['id']} [{c['email_status']:11s}] {c['full_name']:24s} "
                  f"{c['title'][:24]:24s} @ {c.get('company_name') or c['company_id']} "
                  f"{c['email']}")
        return 0
    finally:
        out.close()


def cmd_linkedin(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        if args.add:
            tid = out.add_linkedin_task(args.company, None, args.url,
                                        connection_note=args.note or "",
                                        post_accept_msg=args.message or "")
            print(f"linkedin task #{tid}")
            return 0
        if args.status and args.id:
            out.set_linkedin_status(args.id, args.status)
            print(f"task #{args.id} -> {args.status}")
            return 0
        for t in out.list_linkedin_tasks():
            print(f"#{t['id']} [{t['status']:12s}] {t['profile_url']} "
                  f"(company {t['company_id']})")
            if t["next_action"]:
                print(f"    next: {t['next_action']}")
        return 0
    finally:
        out.close()


def cmd_readiness(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        rows = out.conn.execute(
            "SELECT * FROM outreach_companies ORDER BY score DESC LIMIT ?",
            (args.limit,)).fetchall()
        for r in rows:
            evidence = out.list_evidence(r["company_id"])
            ready = outreach_readiness(dict(r), evidence)
            mark = "READY" if ready["ready"] else "     "
            print(f"{mark} {ready['score']:5.1f} {r['name']:24s} ({r['domain']})")
            for reason in ready["reasons"]:
                print(f"        {reason['factor']:16s} {reason['points']:+6.1f}  {reason['detail']}")
        return 0
    finally:
        out.close()


def cmd_apply(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        if args.update is not None:
            try:
                ok = out.update_application(args.update, status=args.status, notes=args.notes)
            except ValueError as exc:
                print(f"Error: {exc}")
                return 1
            if not ok:
                print(f"No application #{args.update}")
                return 1
            print(f"Updated application #{args.update}.")
            return 0
        if not args.role or not args.applied_at:
            print("error: --role and --applied-at are required when recording a new application")
            return 2
        app_id = out.record_application(
            args.company, args.role, args.applied_at,
            company_name=args.company_name or args.company,
            job_url=args.job_url or "", status=args.status, notes=args.notes)
        print(f"Recorded application #{app_id}: {args.role} @ {args.company_name or args.company} "
              f"(applied {args.applied_at})")
        return 0
    finally:
        out.close()


def cmd_applications(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        for a in out.list_applications():
            print(f"#{a['id']} [{a['status']:12s}] {a['role'][:40]:40} @ {a['company_name'][:24]:24} "
                  f"applied {a['applied_at']}")
            if a["notes"]:
                print(f"    notes: {a['notes']}")
        if not out.list_applications():
            print("(no applications recorded)")
        return 0
    finally:
        out.close()


def cmd_followups(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        if getattr(args, "approve", None) is not None:
            if out.approve_followup(args.approve):
                print(f"Approved follow-up #{args.approve}.")
                return 0
            print(f"Could not approve follow-up #{args.approve} (not found or not pending).")
            return 1
        if getattr(args, "skip", None) is not None:
            if out.skip_followup(args.skip, args.note or ""):
                print(f"Skipped follow-up #{args.skip}.")
                return 0
            print(f"Could not skip follow-up #{args.skip} (not found or already completed).")
            return 1
        if getattr(args, "mark_sent", None) is not None:
            from datetime import datetime, timezone
            sent_at = args.sent_at or datetime.now(timezone.utc).isoformat()
            try:
                out.mark_followup_sent(args.mark_sent, sent_at)
                print(f"Marked follow-up #{args.mark_sent} as sent at {sent_at}.")
                return 0
            except ValueError as exc:
                print(f"Error: {exc}")
                return 1
            except Exception:
                print(f"Could not mark follow-up #{args.mark_sent} as sent.")
                return 1
        rows = out.list_followups() if getattr(args, "all", False) else (
            out.list_due_followups() if getattr(args, "due", False) else out.list_followups("pending"))
        if not rows:
            print("(no follow-ups)")
            return 0
        for f in rows:
            contact = (f.get("contact_name") or "").strip()
            company = (f.get("company_name") or f.get("company_id") or "").strip()
            line = f"#{f['id']} [{f['state']}] FU{f['followup_number']} due {f['due_on']} — {company}"
            if contact:
                line += f" / {contact}"
            print(line)
            if f.get("reason"):
                print(f"    reason: {f['reason']}")
            if f.get("thread_id"):
                print(f"    thread: {f['thread_id']}")
        return 0
    finally:
        out.close()


def cmd_holiday(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        if getattr(args, "add", ""):
            try:
                added = out.add_holiday(args.add, getattr(args, "note", "") or "")
            except ValueError as exc:
                print(f"Error: {exc}")
                return 1
            print("Added holiday" if added else "Holiday already exists", args.add)
            return 0
        if getattr(args, "remove", ""):
            removed = out.remove_holiday(args.remove)
            print("Removed holiday" if removed else "Holiday not found", args.remove)
            return 0
        rows = out.list_holidays()
        if not rows:
            print("(no holidays configured)")
        else:
            for r in rows:
                print(f"{r['day']}  {r.get('note', '')}".rstrip())
        return 0
    finally:
        out.close()


def cmd_mark_sent(cfg: AppConfig, args) -> int:
    out = OutreachStorage(cfg.outreach_db_path)
    try:
        from datetime import datetime, timezone
        sent_at = args.sent_at or datetime.now(timezone.utc).isoformat()
        msg_id = out.mark_sent_manually(args.draft_id, sent_at, args.thread_id or "")
        print(f"Draft #{args.draft_id} marked sent manually (message #{msg_id}, at {sent_at}).")
        return 0
    finally:
        out.close()


def cmd_verify_job(cfg: AppConfig, args) -> int:
    storage = _open_storage(cfg)
    try:
        n = storage.set_freshness(args.url, args.status, args.note or "")
        if n == 0:
            print(f"No job matched {args.url!r}. Use the job URL or job id.")
            return 1
        print(f"Marked {n} job(s) as {args.status}: {args.url}")
        if args.note:
            print(f"  note: {args.note}")
        return 0
    finally:
        storage.close()


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

    pp = sub.add_parser("profile", help="show/confirm the verified candidate profile")
    pp.add_argument("--path", default="data/candidate_profile.toml")
    pp.add_argument("--confirm", action="append", metavar="FIELD=VALUE",
                    help="confirm a profile field locally (e.g. notice_period='2 weeks')")
    pp.set_defaults(func=cmd_profile)

    pl2 = sub.add_parser("pipeline", help="qualify companies into the outreach pipeline")
    pl2.add_argument("--limit", type=int, default=50)
    pl2.set_defaults(func=cmd_pipeline)

    po = sub.add_parser("outreach", help="show outreach pipeline overview")
    po.set_defaults(func=cmd_outreach)

    pdr = sub.add_parser("draft", help="generate a personalized email draft")
    pdr.add_argument("company", help="company domain or name")
    pdr.add_argument("--type", choices=DRAFT_TYPES, default="founder")
    pdr.add_argument("--job-title", default=None)
    pdr.add_argument("--save", action="store_true", help="save draft to the pipeline DB")
    pdr.set_defaults(func=cmd_draft)

    pda = sub.add_parser("dashboard", help="run the local web dashboard")
    pda.add_argument("--host", default="127.0.0.1")
    pda.add_argument("--port", type=int, default=8090)
    pda.add_argument("--token", default="", help="access token (or env AUTOMATEJOB_DASHBOARD_TOKEN)")
    pda.add_argument("--enable-send", action="store_true",
                     help="DANGER: allow actually sending email (default off)")
    pda.set_defaults(func=cmd_dashboard)

    pga = sub.add_parser("gmail-auth", help="authorize the standalone local Gmail OAuth client")
    pga.add_argument("--credentials-file", default="",
                     help="OAuth Desktop client JSON (or GMAIL_CREDENTIALS_FILE)")
    pga.add_argument("--token-file", default="",
                     help="local token JSON (or GMAIL_TOKEN_FILE)")
    pga.add_argument("--port", type=int, default=0,
                     help="local OAuth callback port (0 chooses one)")
    pga.set_defaults(func=cmd_gmail_auth)

    pgs = sub.add_parser("gmail-sync", help="sync replies from authorized Gmail threads; never sends")
    pgs.set_defaults(func=cmd_gmail_sync)

    pfc = sub.add_parser("firecrawl", help="Firecrawl CLI usage, search, scrape, map, crawl, Alexandria")
    fsub = pfc.add_subparsers(dest="action", required=True)
    pfcu = fsub.add_parser("usage", help="show account + local budget usage")
    pfcu.set_defaults(func=cmd_firecrawl)
    pfca = fsub.add_parser("auth", help="show CLI authentication status")
    pfca.set_defaults(func=cmd_firecrawl)
    pfcs = fsub.add_parser("search", help="run a bounded web search")
    pfcs.add_argument("query")
    pfcs.add_argument("--limit", type=int, default=10)
    pfcs.add_argument("--sources", default="web")
    pfcs.set_defaults(func=cmd_firecrawl)
    pfcc = fsub.add_parser("scrape", help="scrape one URL")
    pfcc.add_argument("url")
    pfcc.add_argument("--formats", default="markdown")
    pfcc.set_defaults(func=cmd_firecrawl)
    pfcm = fsub.add_parser("map", help="map URLs on a site")
    pfcm.add_argument("url")
    pfcm.add_argument("--search", default="")
    pfcm.set_defaults(func=cmd_firecrawl)
    pfcw = fsub.add_parser("crawl", help="bounded crawl (limit + depth + path)")
    pfcw.add_argument("url")
    pfcw.add_argument("--limit", type=int, default=10)
    pfcw.add_argument("--max-depth", type=int, default=1)
    pfcw.add_argument("--path", default="")
    pfcw.set_defaults(func=cmd_firecrawl)
    pfcx = fsub.add_parser("alexandria", help="execute an Alexandria provider capability")
    pfcx.add_argument("provider")
    pfcx.add_argument("capability")
    pfcx.add_argument("--options", default="{}")
    pfcx.add_argument("--provider-cost", type=float, default=None,
                      help="credits from the inspected provider contract (required)")
    pfcx.add_argument("--cache-ttl", type=int, default=0)
    pfcx.set_defaults(func=cmd_firecrawl)

    prp = sub.add_parser("recruiter", help="import a recruiter post and extract explicit roles/emails")
    prp.add_argument("--url", required=True, help="original post URL")
    prp.add_argument("--text", default="", help="post text")
    prp.add_argument("--file", default="", help="read post text from a file")
    prp.add_argument("--company", default="")
    prp.add_argument("--recruiter-name", default="")
    prp.add_argument("--recruiter-title", default="")
    prp.add_argument("--recruiter-linkedin", default="")
    prp.add_argument("--published-at", default="")
    prp.add_argument("--source-type", default="manual")
    prp.set_defaults(func=cmd_recruiter)

    prg = sub.add_parser("registry", help="show the source registry")
    prg.add_argument("--refresh", action="store_true", help="re-seed known sources")
    prg.set_defaults(func=cmd_registry)

    pdis = sub.add_parser("discovery-log", help="show daily discovery-operation accounting")
    pdis.add_argument("--limit", type=int, default=100)
    pdis.set_defaults(func=cmd_discovery_log)

    pdis2 = sub.add_parser("discovery-run", help="run the bounded daily discovery operations (permitted sources only)")
    pdis2.add_argument("--dry-run", action="store_true", help="list planned operations without executing")
    pdis2.set_defaults(func=cmd_discovery_run)

    pc = sub.add_parser("contact", help="add/list/import contacts (manual enrichment)")
    pc.add_argument("--add", action="store_true", help="add a contact")
    pc.add_argument("--company", help="company_id")
    pc.add_argument("--name", help="full name")
    pc.add_argument("--title", help="job title")
    pc.add_argument("--role", default="engineer",
                    help="role_kind: cto/founder/engineering_manager/recruiter/engineer")
    pc.add_argument("--email", help="email address")
    pc.add_argument("--email-status", default="unverified",
                    choices=["unverified", "verified", "unavailable"])
    pc.add_argument("--linkedin", default="", help="linkedin url")
    pc.add_argument("--source", default="", help="source url")
    pc.add_argument("--import-csv", help="import contacts from CSV")
    pc.set_defaults(func=cmd_contact)

    pli = sub.add_parser("linkedin", help="LinkedIn manual outreach queue")
    pli.add_argument("--add", action="store_true")
    pli.add_argument("--company", help="company_id")
    pli.add_argument("--url", help="profile url")
    pli.add_argument("--note", default="", help="connection note")
    pli.add_argument("--message", default="", help="post-acceptance message")
    pli.add_argument("--id", type=int, help="task id (for --status)")
    pli.add_argument("--status", help="new status")
    pli.set_defaults(func=cmd_linkedin)

    prd = sub.add_parser("readiness", help="outreach-readiness score for shortlisted companies")
    prd.add_argument("--limit", type=int, default=20)
    prd.set_defaults(func=cmd_readiness)

    pap = sub.add_parser("apply", help="record or update a job application")
    pap.add_argument("--company", required=True, help="company id/domain/name")
    pap.add_argument("--role", default="", help="role title (required when recording)")
    pap.add_argument("--applied-at", default="", help="ISO date (e.g. 2026-10-24)")
    pap.add_argument("--company-name", default="", help="display name")
    pap.add_argument("--job-url", default="")
    pap.add_argument("--status", default="applied",
                     choices=["applied", "in_review", "interview", "offer", "rejected", "withdrawn"])
    pap.add_argument("--notes", default="")
    pap.add_argument("--update", type=int, help="update an existing application id instead of creating")
    pap.set_defaults(func=cmd_apply)

    papps = sub.add_parser("applications", help="list recorded applications")
    papps.set_defaults(func=cmd_applications)

    pf = sub.add_parser("followups", help="list, approve, skip, mark follow-ups (max 2; never sent automatically)")
    pf.add_argument("--due", action="store_true", help="list only follow-ups due today or earlier")
    pf.add_argument("--all", action="store_true", help="list all follow-up states (not only pending)")
    pf.add_argument("--approve", type=int, metavar="ID", help="approve a pending follow-up")
    pf.add_argument("--skip", type=int, metavar="ID", help="skip/cancel a pending or approved follow-up")
    pf.add_argument("--mark-sent", type=int, metavar="ID", help="mark an APPROVED follow-up as sent manually")
    pf.add_argument("--sent-at", default="", help="actual send time for --mark-sent (ISO 8601; default: now)")
    pf.add_argument("--note", default="", help="reason for --skip")
    pf.set_defaults(func=cmd_followups)

    ph = sub.add_parser("holiday", help="add/remove/list approved holidays for follow-up scheduling")
    ph.add_argument("--add", default="", help="add holiday (YYYY-MM-DD)")
    ph.add_argument("--remove", default="", help="remove holiday (YYYY-MM-DD)")
    ph.add_argument("--note", default="", help="note for --add")
    ph.set_defaults(func=cmd_holiday)

    pms = sub.add_parser("mark-sent", help="mark an approved draft as sent manually (copy-email workflow)")
    pms.add_argument("--draft-id", type=int, required=True)
    pms.add_argument("--sent-at", default="", help="actual send timestamp (ISO)")
    pms.add_argument("--thread-id", default="")
    pms.set_defaults(func=cmd_mark_sent)

    pvj = sub.add_parser("verify-job",
                         help="flag a job's freshness (stale / needs_verification / fresh)")
    pvj.add_argument("url", help="job URL or job id")
    pvj.add_argument("--status", required=True,
                     choices=["fresh", "needs_verification", "stale"])
    pvj.add_argument("--note", default="", help="reason (e.g. employer page shows 0 jobs)")
    pvj.set_defaults(func=cmd_verify_job)

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
