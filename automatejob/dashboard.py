"""Lightweight local web dashboard (stdlib only — no Flask dependency).

Mobile-responsive HTML + JSON API for the outreach pipeline. Auth is
session-cookie based (HMAC-signed) plus an optional Authorization bearer
header. Query-string tokens are NOT supported (never expose secrets in URLs).

Endpoints (mutating ones require a valid session AND a same-origin request):
  GET  /login, GET /                       HTML
  POST /api/login {token}                  set session cookie
  POST /api/logout                         clear session
  GET  /api/overview /companies /contacts /drafts /followups /activity
  POST /api/contacts                       add/edit contact (auto-drafts)
  POST /api/contacts/import                bulk rows [{company_id,name,...}]
  POST /api/drafts/generate                regenerate draft for a contact
  POST /api/drafts/<id>/approve|reject     local review (no email)
  POST /api/drafts/<id>/save_gmail         create a Gmail draft (never sends)
  POST /api/drafts/<id>/send               send (requires approval + explicit confirm + Gmail)
  POST /api/suppress /api/suppress/remove  company/email/contact suppression
  POST /api/profile/confirm                confirm candidate fields
  POST /api/linkedin/<id>/status           manual LinkedIn task status
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

_CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
margin:0;background:#0f1115;color:#e6e6e6;line-height:1.45;font-size:15px}
.wrap{max-width:900px;margin:0 auto;padding:14px}
h1{font-size:1.25rem}h2{font-size:1.0rem;margin:18px 0 8px;color:#7dd3fc}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.tabs button{background:#1a1d24;color:#cbd5e1;border:1px solid #2a2e38;border-radius:8px;
padding:8px 12px;font-size:.85rem;cursor:pointer}
.tabs button.on{background:#2563eb;color:#fff;border-color:#2563eb}
.card{background:#1a1d24;border:1px solid #2a2e38;border-radius:8px;padding:12px;margin:8px 0}
.tag{display:inline-block;font-size:.7rem;padding:2px 8px;border-radius:10px;background:#27303f;margin:0 6px 0 0}
.tag.ok{background:#123d2b;color:#6ee7b7}.tag.warn{background:#4a3411;color:#fcd34d}
.tag.bad{background:#4c1d1d;color:#fca5a5}
.metric{display:inline-block;background:#1a1d24;border:1px solid #2a2e38;border-radius:8px;
padding:10px 14px;margin:4px;min-width:88px}
.metric b{display:block;font-size:1.25rem;color:#7dd3fc}
a{color:#7dd3fc;text-decoration:none;word-break:break-all}
button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:7px 12px;font-size:.85rem;cursor:pointer;margin:2px}
button.dim{background:#27303f}button.danger{background:#b91c1c}
input,select,textarea{background:#0d1015;color:#e6e6e6;border:1px solid #2a2e38;border-radius:6px;
padding:8px;font-size:.9rem;width:100%;margin:3px 0}
label{font-size:.75rem;color:#9aa2b1}
.small{font-size:.8rem;color:#9aa2b1}pre{white-space:pre-wrap;font-family:inherit;margin:4px 0}
.hidden{display:none}.row{display:flex;gap:8px;flex-wrap:wrap}.col{flex:1;min-width:220px}
"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class Dashboard:
    def __init__(self, outreach, companies_store=None, jobs_store=None,
                 token: str = "", gmail=None, profile=None, sending_enabled: bool = False):
        self.outreach = outreach
        self.companies_store = companies_store
        self.jobs_store = jobs_store
        self.token = token or ""
        self.gmail = gmail
        self.profile = profile
        self.sending_enabled = sending_enabled
        # session signing secret (derived from token; random when no token)
        self.secret = token or hashlib.sha256(str(time.time()).encode()).hexdigest()
        self._login_failures = {}  # ip -> [timestamps]

    # -- auth ----------------------------------------------------------------
    def _login_throttled(self, handler) -> bool:
        """Simple in-memory throttle: max 10 failures per 5 minutes per IP."""
        ip = (handler.client_address[0] if handler.client_address else "unknown")
        now = time.time()
        window = [t for t in self._login_failures.get(ip, []) if now - t < 300]
        self._login_failures[ip] = window
        return len(window) >= 10

    def _record_login_failure(self, handler) -> None:
        ip = (handler.client_address[0] if handler.client_address else "unknown")
        self._login_failures.setdefault(ip, []).append(time.time())
    def _sign(self, value: str) -> str:
        return hmac.new(self.secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def _make_session(self) -> str:
        exp = str(int(time.time()) + 86400)  # 24h
        return f"{exp}.{self._sign(exp)}"

    def _valid_session(self, value: str) -> bool:
        try:
            exp, sig = value.split(".", 1)
            return int(exp) > time.time() and hmac.compare_digest(sig, self._sign(exp))
        except (ValueError, IndexError):
            return False

    def _session_cookie(self, handler) -> Optional[str]:
        cookies = SimpleCookie()
        raw = handler.headers.get("Cookie", "")
        cookies.load(raw)
        c = cookies.get("aj_session")
        return c.value if c else None

    def _authorized(self, handler) -> bool:
        if not self.token:
            return True  # no token configured => open (localhost-only by default)
        h = handler.headers.get("Authorization", "")
        if h.startswith("Bearer ") and hmac.compare_digest(h[7:], self.token):
            return True
        sess = self._session_cookie(handler)
        return bool(sess and self._valid_session(sess))

    def _csrf_ok(self, handler) -> bool:
        # SameSite=Strict cookie already blocks cross-site POSTs; additionally
        # reject cross-origin writes when an Origin header is present.
        origin = handler.headers.get("Origin")
        if origin:
            host = handler.headers.get("Host", "")
            return urlparse(origin).netloc == host
        return True

    def _set_session_cookie(self, handler):
        sess = self._make_session()
        handler.send_header("Set-Cookie",
                            f"aj_session={sess}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400")
        return sess

    # -- helpers -------------------------------------------------------------
    def _body(self, handler) -> dict:
        try:
            length = int(handler.headers.get("Content-Length", 0))
            return json.loads(handler.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def _send(self, handler, payload, status=200):
        data = json.dumps(payload).encode() if not isinstance(payload, str) else payload.encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _companies(self, limit=30) -> list:
        if self.companies_store is None:
            return []
        return self.companies_store.list(limit=limit)

    def _company_research(self, limit=30) -> list:
        """Research-ready company cards sourced only from stored evidence."""
        rows = self.outreach.conn.execute(
            "SELECT * FROM outreach_companies ORDER BY score DESC, name LIMIT ?", (limit,)).fetchall()
        contacts = self.outreach.list_contacts_joined()
        applications = self.outreach.list_applications()
        out = []
        for row in rows:
            company = dict(row)
            cid = company["company_id"]
            company["evidence"] = self.outreach.list_evidence(cid)
            company["contacts"] = [c for c in contacts if c["company_id"] == cid]
            company["jobs"] = []
            if self.jobs_store is not None:
                company["jobs"] = [dict(j) for j in self.jobs_store.conn.execute(
                    "SELECT id,title,location,url,seniority,actionable,freshness,freshness_note,status "
                    "FROM jobs WHERE lower(company)=lower(?) AND status='open' "
                    "ORDER BY actionable DESC, score DESC LIMIT 5", (company["name"],)).fetchall()]
            existing = next((a for a in applications if a["company_id"] == cid), None)
            if existing:
                company["suggested_next_action"] = (
                    f"Existing application ({existing['role']} on {existing['applied_at']}); "
                    "review a follow-up, do not reapply.")
            elif any(j["freshness"] != "fresh" for j in company["jobs"]):
                company["suggested_next_action"] = "Verify the employer career source before outreach about this role."
            elif company["contacts"]:
                company["suggested_next_action"] = "Add or verify a professional email, then generate a reviewable draft."
            else:
                company["suggested_next_action"] = "Identify a suitable public contact or add one manually; no email is assumed."
            out.append(company)
        return out

    def _jobs(self, limit=40) -> list:
        """Relevant open jobs with 48-hour freshness + application status."""
        if self.jobs_store is None:
            return []
        rows = self.jobs_store.conn.execute(
            "SELECT id,title,company,location,url,seniority,eligibility,actionable,"
            "freshness,freshness_note,location_preference,status,"
            "posted_at,first_seen_at,last_seen_at,last_verified_at,collected_at,"
            "published_at,source_type FROM jobs "
            "WHERE status='open' AND (actionable=1 "
            "OR freshness IN ('stale','needs_verification') "
            "OR (eligibility='confirmed' AND seniority IN ('junior','internship','review') "
            "AND role_family NOT IN ('non_engineering','review'))) "
            "ORDER BY actionable DESC, score DESC LIMIT ?", (limit,)).fetchall()
        apps = self.outreach.list_applications()
        def application_company_key(value: str) -> str:
            # HN displays e.g. "MixRank (YC S11)" while the official board
            # uses "MixRank". Treat this provenance suffix as the same employer.
            value = re.sub(r"\s*\(yc\s+[a-z]\d+\)\s*$", "", value or "", flags=re.I)
            return re.sub(r"[^a-z0-9]+", "", value.lower())
        out = []
        for r in rows:
            d = dict(r)
            d["applied"] = None
            d["freshness_48h"] = self._freshness_48h(d)
            job_key = application_company_key(d["company"])
            for a in apps:
                if (application_company_key(a["company_name"]) == job_key
                        or application_company_key(a["company_id"]) == job_key):
                    d["applied"] = a["status"]
                    break
            out.append(d)
        return out

    @staticmethod
    def _freshness_48h(row: dict) -> str:
        from .freshness import freshness_status
        return freshness_status(
            status=row.get("status", "open"),
            published_at=row.get("published_at") or row.get("posted_at") or "",
            last_verified_at=row.get("last_verified_at") or "",
            last_seen_at=row.get("last_seen_at") or "",
        )

    def _sources(self) -> list:
        from .source_registry import SourceRegistry, seed_defaults
        reg = SourceRegistry("data/source_registry.db")
        try:
            if reg.count() == 0:
                seed_defaults(reg)
            return reg.list_all()
        finally:
            reg.close()

    def _firecrawl_usage(self) -> dict:
        from .firecrawl import FirecrawlBudget, FirecrawlCLI, FirecrawlConfig
        from .config import AppConfig
        try:
            cfg = AppConfig.load("config.toml")
        except Exception:
            return {"error": "config unavailable"}
        s = cfg.firecrawl
        budget = FirecrawlBudget(s.db_path, FirecrawlConfig(
            enabled=s.enabled, monthly_budget=s.monthly_budget,
            warning_threshold=s.warning_threshold,
            hard_stop_threshold=s.hard_stop_threshold,
            absolute_account_limit=s.absolute_account_limit,
            search_results_default=s.search_results_default, allow_payg=s.allow_payg,
            db_path=s.db_path))
        try:
            account = FirecrawlCLI(budget).account_usage()
            local = budget.usage()
            return {"account": account, "local": local}
        finally:
            budget.close()

    def _discovery_activity(self) -> dict:
        from .discovery_log import DiscoveryLog
        logdb = DiscoveryLog("data/discovery_log.db")
        try:
            return {"summary": logdb.summary(), "recent": logdb.list(limit=50)}
        finally:
            logdb.close()

    def _verify_job(self, handler):
        body = self._body(handler)
        url = body.get("url", "")
        status = body.get("status", "")
        note = body.get("note", "")
        if not url or status not in ("fresh", "needs_verification", "stale"):
            self._send(handler, {"error": "bad request"}, 400)
            return
        n = self.jobs_store.set_freshness(url, status, note)
        self._send(handler, {"ok": n > 0, "updated": n})

    # -- JSON API ------------------------------------------------------------
    def route(self, handler):
        path = urlparse(handler.path).path
        method = handler.command

        # auth bypass for login only
        if path == "/api/login" and method == "POST":
            body = self._body(handler)
            if self.token and self._login_throttled(handler):
                self._send(handler, {"error": "too many failed attempts; try again later"}, 429)
                return
            if self.token and not hmac.compare_digest(str(body.get("token", "")), self.token):
                self._record_login_failure(handler)
                self._send(handler, {"error": "invalid token"}, 401)
                return
            self._set_session_cookie(handler)
            self._send(handler, {"ok": True})
            return
        if path == "/api/logout" and method == "POST":
            handler.send_header("Set-Cookie", "aj_session=; Path=/; HttpOnly; Max-Age=0")
            self._send(handler, {"ok": True})
            return

        if not self._authorized(handler):
            self._send(handler, {"error": "unauthorized"}, 401)
            return
        if method in ("POST",) and not self._csrf_ok(handler):
            self._send(handler, {"error": "cross-origin request blocked"}, 403)
            return

        if path == "/api/overview":
            self._send(handler, self.outreach.metrics())
        elif path == "/api/companies":
            self._send(handler, self._company_research())
        elif path == "/api/contacts" and method == "GET":
            self._send(handler, self.outreach.list_contacts_joined())
        elif path == "/api/contacts" and method == "POST":
            self._add_contact(handler)
        elif path == "/api/contacts/import" and method == "POST":
            self._import_contacts(handler)
        elif path == "/api/drafts" and method == "GET":
            state = urlparse(handler.path).query and \
                dict(p.split("=") for p in urlparse(handler.path).query.split("&")).get("state")
            self._send(handler, self.outreach.list_drafts(state=state or None))
        elif path == "/api/drafts/generate" and method == "POST":
            self._generate_draft(handler)
        elif path.startswith("/api/drafts/") and method == "POST":
            self._draft_action(handler, path)
        elif path == "/api/followups":
            if method == "POST":
                self._followup_action(handler)
            else:
                self._send(handler, self.outreach.list_followups("pending"))
        elif path.startswith("/api/followups/") and method == "POST":
            self._followup_action(handler, path)
        elif path == "/api/applications" and method == "GET":
            self._send(handler, self.outreach.list_applications())
        elif path == "/api/applications" and method == "POST":
            self._record_application(handler)
        elif path.startswith("/api/applications/") and method == "POST":
            self._update_application(handler, path)
        elif path == "/api/jobs":
            self._send(handler, self._jobs())
        elif path == "/api/jobs/verify" and method == "POST":
            self._verify_job(handler)
        elif path == "/api/sources":
            self._send(handler, self._sources())
        elif path == "/api/firecrawl-usage":
            self._send(handler, self._firecrawl_usage())
        elif path == "/api/discovery-activity":
            self._send(handler, self._discovery_activity())
        elif path == "/api/recruiter-posts":
            self._send(handler, self.outreach.list_recruiter_posts())
        elif path == "/api/recruiter-posts/import" and method == "POST":
            self._import_recruiter_post(handler)
        elif path.startswith("/api/recruiter-posts/") and method == "POST":
            self._recruiter_post_status(handler, path)
        elif path == "/api/linkedin":
            self._send(handler, self.outreach.list_linkedin_tasks())
        elif path.startswith("/api/linkedin/") and method == "POST":
            self._linkedin_status(handler, path)
        elif path == "/api/suppress" and method == "POST":
            body = self._body(handler)
            self.outreach.suppress(body.get("scope", ""), body.get("key", ""),
                                   body.get("reason", ""))
            self._send(handler, {"ok": True})
        elif path == "/api/suppress/remove" and method == "POST":
            body = self._body(handler)
            self.outreach.unsuppress(body.get("scope", ""), body.get("key", ""))
            self._send(handler, {"ok": True})
        elif path == "/api/profile/confirm" and method == "POST":
            body = self._body(handler)
            self.outreach.confirm_profile(body.get("field", ""), body.get("value", ""),
                                          source=body.get("source", "dashboard"))
            self._send(handler, {"ok": True})
        elif path == "/api/profile":
            confirmed = self.outreach.list_profile_confirmations()
            missing = []
            if self.profile is not None:
                try:
                    missing = self.profile.unverified_fields(self.outreach)
                except Exception:
                    missing = []
            self._send(handler, {"confirmed": confirmed, "missing": missing})
        else:
            self._send(handler, {"error": "not found"}, 404)

    def _add_contact(self, handler):
        from .workflow import add_contact_and_draft
        body = self._body(handler)
        if self.profile is None:
            self._send(handler, {"error": "profile not configured"}, 500)
            return
        result = add_contact_and_draft(
            self.outreach, self.profile,
            body.get("company_id", ""), body.get("full_name", ""), body.get("title", ""),
            body.get("role_kind", ""), body.get("email", ""),
            email_status=body.get("email_status", "unverified"),
            linkedin_url=body.get("linkedin_url", ""), source_url=body.get("source_url", ""))
        draft_id = None
        created = False
        if result.draft.get("subject") and result.draft.get("body"):
            contact = self.outreach.conn.execute(
                "SELECT company_id FROM contacts WHERE id=?", (result.contact_id,)).fetchone()
            if contact is not None:
                draft_id, created = self.outreach.ensure_draft(
                    contact["company_id"],
                    result.draft.get("evidence_map", {}).get("draft_type", "referral"),
                    result.draft["subject"], result.draft["body"],
                    result.draft.get("evidence_map", {}), contact_id=result.contact_id)
        self._send(handler, {"contact_id": result.contact_id, "draft": result.draft,
                             "draft_id": draft_id, "draft_created": created,
                             "draft_already_exists": draft_id is not None and not created,
                             "warnings": result.warnings})

    def _import_contacts(self, handler):
        from .workflow import add_contact_and_draft
        body = self._body(handler)
        rows = body.get("rows", [])
        imported, skipped, warnings = 0, 0, []
        for row in rows:
            email = (row.get("email") or "").strip()
            if not email:
                skipped += 1
                continue
            r = add_contact_and_draft(
                self.outreach, self.profile, row.get("company_id", ""),
                row.get("full_name", ""), row.get("title", ""), row.get("role_kind", ""),
                email, email_status=row.get("email_status", "unverified"),
                linkedin_url=row.get("linkedin_url", ""), source_url=row.get("source_url", ""))
            imported += 1
            warnings += r.warnings
        self._send(handler, {"imported": imported, "skipped": skipped, "warnings": warnings})

    def _generate_draft(self, handler):
        from .workflow import add_contact_and_draft
        body = self._body(handler)
        contact = self.outreach.conn.execute(
            "SELECT * FROM contacts WHERE id=?", (int(body.get("contact_id", -1)),)).fetchone()
        if contact is None:
            self._send(handler, {"error": "contact not found"}, 404)
            return
        r = add_contact_and_draft(
            self.outreach, self.profile, contact["company_id"], contact["full_name"],
            contact["title"], contact["role_kind"], contact["email"],
            email_status=contact["email_status"], linkedin_url=contact["linkedin_url"],
            source_url=contact["source_url"])
        self._send(handler, {"contact_id": r.contact_id, "draft": r.draft,
                             "warnings": r.warnings})

    def _draft_action(self, handler, path):
        parts = path.strip("/").split("/")
        # /api/drafts/<numeric-id>/<action>
        if len(parts) != 4 or parts[:2] != ["api", "drafts"]:
            self._send(handler, {"error": "bad draft action path"}, 400)
            return
        try:
            draft_id = int(parts[2])
        except ValueError:
            self._send(handler, {"error": "bad id"}, 400)
            return
        action = parts[3]
        body = self._body(handler)

        if action == "edit":
            subject = str(body.get("subject", "")).strip()
            draft_body = str(body.get("body", ""))
            if not subject or not draft_body:
                self._send(handler, {"ok": False, "error": "subject and body are required"}, 400)
                return
            ok = self.outreach.update_draft(draft_id, subject, draft_body)
            self._send(handler, {"ok": ok}, 200 if ok else 409)
        elif action == "approve":
            ok = self.outreach.approve_draft(draft_id)
            self._send(handler, {"ok": ok}, 200 if ok else 404)
        elif action == "reject":
            row = self.outreach.conn.execute(
                "SELECT state FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                self._send(handler, {"ok": False, "error": "not found"}, 404)
                return
            if row["state"] == "sent":
                self._send(handler, {"ok": False, "error": "sent drafts cannot be rejected"}, 409)
                return
            self.outreach.conn.execute(
                "UPDATE outreach_drafts SET state='rejected' WHERE id=?", (draft_id,))
            self.outreach.audit("draft", draft_id, row["state"], "rejected", "")
            self.outreach.conn.commit()
            self._send(handler, {"ok": True})
        elif action == "save_gmail":
            row = self.outreach.conn.execute(
                "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                self._send(handler, {"error": "not found"}, 404)
                return
            # Durable idempotency: never create a second Gmail draft after a
            # click/retry if one is already stored locally.
            if row["gmail_draft_id"]:
                self._send(handler, {"status": "gmail_draft", "draft_id": row["gmail_draft_id"],
                                     "message_id": row["gmail_message_id"],
                                     "thread_id": row["gmail_thread_id"],
                                     "already_exists": True})
                return
            if self.gmail is None:
                self._send(handler, {"status": "gmail_unavailable"}, 200)
                return
            contact = self.outreach.conn.execute(
                "SELECT email FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
            to_addr = contact["email"] if contact else ""
            res = self.gmail.save_draft(row["subject"], row["body"], to_addr,
                                        thread_id=row["gmail_thread_id"])
            if res.get("status") == "gmail_draft":
                saved = self.outreach.record_gmail_draft(
                    draft_id, res.get("draft_id", ""), res.get("message_id", ""),
                    res.get("thread_id", ""), to_addr)
                res["already_exists"] = False
                res["recipient"] = saved["recipient"]
            self._send(handler, res)
        elif action == "mark_sent":
            # Copy-email workflow: user sent manually, record actual timestamp.
            sent_at = body.get("sent_at") or _now_iso()
            try:
                msg_id = self.outreach.mark_sent_manually(
                    draft_id, sent_at, body.get("thread_id", ""))
                self._send(handler, {"ok": True, "message_id": msg_id,
                                     "sent_at": sent_at})
            except ValueError as e:
                self._send(handler, {"ok": False, "error": str(e)}, 400)
        elif action == "send":
            self._send_draft(handler, draft_id, body)
        else:
            self._send(handler, {"error": "unknown action"}, 404)

    def _send_draft(self, handler, draft_id, body):
        from .workflow import send_draft
        row = self.outreach.conn.execute(
            "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            self._send(handler, {"error": "not found"}, 404)
            return
        if not body.get("confirm"):
            self._send(handler, {"ok": False, "status": "not_confirmed",
                                 "detail": "explicit confirmation required"}, 200)
            return
        contact = self.outreach.conn.execute(
            "SELECT email FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
        to_addr = contact["email"] if contact else ""
        result = send_draft(self.outreach, self.gmail, draft_id, to_addr,
                            sending_enabled=self.sending_enabled)
        self._send(handler, result, 200 if result.get("ok") else 200)

    def _import_recruiter_post(self, handler):
        from .recruiter import parse_recruiter_post
        body = self._body(handler)
        text = body.get("text", "")
        url = body.get("original_url", "")
        if not text or not url:
            self._send(handler, {"error": "text and original_url are required"}, 400)
            return
        post = parse_recruiter_post(
            text, original_url=url, company=body.get("company", ""),
            published_at=body.get("published_at", ""),
            source_type=body.get("source_type", "manual"))
        post.recruiter_name = body.get("recruiter_name", "")
        post.recruiter_title = body.get("recruiter_title", "")
        post.recruiter_linkedin_url = body.get("recruiter_linkedin_url", "")
        post_id = self.outreach.upsert_recruiter_post(post)
        self._send(handler, {"post_id": post_id, "roles": len(post.roles),
                             "published_emails": post.published_emails,
                             "confidence": post.confidence})

    def _recruiter_post_status(self, handler, path):
        try:
            post_id = int(path.rsplit("/", 2)[-2])
        except (ValueError, IndexError):
            self._send(handler, {"error": "bad id"}, 400)
            return
        body = self._body(handler)
        status = body.get("status", "")
        try:
            ok = self.outreach.set_recruiter_post_status(post_id, status, body.get("note", ""))
        except ValueError as exc:
            self._send(handler, {"error": str(exc)}, 400)
            return
        self._send(handler, {"ok": ok}, 200 if ok else 404)

    def _record_application(self, handler):
        body = self._body(handler)
        company = body.get("company_id") or body.get("company") or ""
        role = body.get("role") or ""
        applied_at = body.get("applied_at") or ""
        if not company or not role or not applied_at:
            self._send(handler, {"error": "company_id, role and applied_at are required"}, 400)
            return
        app_id = self.outreach.record_application(
            company, role, applied_at,
            company_name=body.get("company_name", company),
            job_url=body.get("job_url", ""),
            status=body.get("status", "applied"),
            notes=body.get("notes", ""),
            source=body.get("source", "dashboard"))
        self._send(handler, {"ok": True, "application_id": app_id})

    def _update_application(self, handler, path):
        try:
            app_id = int(path.rstrip("/").split("/")[-1])
        except ValueError:
            self._send(handler, {"error": "bad application id"}, 400)
            return
        body = self._body(handler)
        try:
            ok = self.outreach.update_application(
                app_id, status=body.get("status"), notes=body.get("notes"))
        except ValueError as exc:
            self._send(handler, {"error": str(exc)}, 400)
            return
        self._send(handler, {"ok": ok}, 200 if ok else 404)

    def _followup_action(self, handler, path=""):
        body = self._body(handler)
        if path:
            try:
                followup_id = int(path.rstrip("/").split("/")[-2])
                action = path.rstrip("/").split("/")[-1]
            except (ValueError, IndexError):
                self._send(handler, {"error": "bad follow-up path"}, 400)
                return
        else:
            followup_id = int(body.get("followup_id") or 0)
            action = body.get("action", "")

        if action == "approve":
            ok = self.outreach.approve_followup(followup_id)
            self._send(handler, {"ok": ok}, 200 if ok else 400)
            return
        if action == "skip":
            ok = self.outreach.skip_followup(followup_id, body.get("note", ""))
            self._send(handler, {"ok": ok}, 200 if ok else 400)
            return
        if action == "draft":
            if self.profile is None:
                self._send(handler, {"error": "profile not configured"}, 500)
                return
            from .workflow import generate_followup_draft
            result = generate_followup_draft(self.outreach, self.profile, followup_id)
            self._send(handler, result, 200 if result.get("ok") else 400)
            return
        if action == "mark_sent":
            sent_at = body.get("sent_at") or _now_iso()
            try:
                ok = self.outreach.mark_followup_sent(followup_id, sent_at)
                self._send(handler, {"ok": ok, "sent_at": sent_at})
            except ValueError as exc:
                self._send(handler, {"error": str(exc)}, 400)
            return
        self._send(handler, {"error": "unknown follow-up action"}, 400)

    def _activity(self) -> list:
        return [dict(r) for r in self.outreach.conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT 50").fetchall()]

    def _linkedin_status(self, handler, path):
        try:
            task_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self._send(handler, {"error": "bad id"}, 400)
            return
        body = self._body(handler)
        self.outreach.set_linkedin_status(task_id, body.get("status", "pending"),
                                          body.get("note", ""))
        self._send(handler, {"ok": True})

    # -- HTML ----------------------------------------------------------------
    def _login_html(self) -> str:
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>automatejob login</title><style>{_CSS}</style></head><body>
<div class="wrap"><h1>automatejob</h1>
<div class="card"><label>Access token</label>
<input type="password" id="tok" placeholder="token">
<button onclick="login()">Sign in</button></div></div>
<script>
async function login(){{const t=document.getElementById('tok').value;
const r=await fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
body:JSON.stringify({{token:t}})}});
if(r.ok)location.href='/';else alert('invalid token');}}
</script></body></html>"""

    def _main_html(self) -> str:
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>automatejob outreach</title><style>{_CSS}</style></head><body>
<div class="wrap"><h1>automatejob — outreach</h1>
<div class="tabs" id="tabs">
<button class="on" data-t="jobs">Jobs</button><button data-t="companies">Companies</button>
<button data-t="contacts">Contacts</button><button data-t="drafts">Drafts</button>
<button data-t="applications">Applications</button><button data-t="followups">Follow-ups</button>
<button data-t="linkedin">LinkedIn</button><button data-t="recruiter">Recruiter Posts</button>
<button data-t="sources">Sources</button><button data-t="firecrawl">Firecrawl</button>
<button data-t="discovery">Discovery</button>
<button data-t="activity">Activity</button><button data-t="analytics">Analytics</button>
<button data-t="profile">Profile</button><button onclick="logout()">Log out</button></div>
<div id="view"></div></div>
<script>
const $=s=>document.querySelector(s);
async function get(u){{const r=await fetch(u);if(r.status===401){{location.href='/login';return null}}return r.json()}}
async function post(u,b){{const r=await fetch(u,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b||{{}})}});return r.json()}}
function esc(s){{return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}}
function tag(s){{const m={{'verified':'ok','sent':'ok','replied':'ok','active':'ok','high_signal_speculative':'ok','fresh':'ok','pending':'warn','unverified':'warn','awaiting_approval':'warn','needs_verification':'warn','approved':'warn','draft_ready':'','gmail_draft':'warn','rejected':'bad','bounced':'bad','opted_out':'bad','optout':'bad','closed':'bad','stale':'bad','skipped':'bad','cancelled':'bad','failed':'bad'}};return '<span class="tag '+(m[s]||'')+'">'+esc(s)+'</span>'}}
function metric(o){{return Object.entries(o||{{}}).map(([k,v])=>'<div class="metric"><b>'+esc(v)+'</b>'+esc(k.replace(/_/g,' '))+'</div>').join('')}}
async function show(t){{
document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
const v=$('#view');
if(t==='jobs'){{const d=await get('/api/jobs');v.innerHTML='<h2>Relevant jobs ('+(d.length)+')</h2><div class="small">48-hour freshness: newly posted / recently verified / verification overdue / uncertain / closed. Publication and collection times are kept separate.</div>'+d.map(j=>'<div class="card">'+tag(j.freshness||'active')+' '+tag(j.freshness_48h||'')+'<b>'+esc(j.title)+'</b> <span class="small">'+esc(j.company)+'</span><div class="small">'+esc(j.location)+' · '+esc(j.seniority)+' · '+esc(j.location_preference)+(j.applied?' · <b>applied: '+esc(j.applied)+'</b>':'')+'</div><div class="small">published '+esc(j.published_at||j.posted_at||'unknown')+' · first-seen '+esc(j.first_seen_at||'?')+' · last-seen '+esc(j.last_seen_at||'?')+' · last-verified '+esc(j.last_verified_at||'never')+'</div><a href="'+esc(j.url)+'" target="_blank">'+esc(j.url)+'</a>'+(j.freshness!=='fresh'?'<div class="small">⚠ '+esc(j.freshness)+': '+esc(j.freshness_note)+'</div>':'')+'<button class="dim" onclick="verifyJob(\\''+esc(j.url)+'\\',\\'fresh\\')">Mark fresh</button><button class="dim" onclick="verifyJob(\\''+esc(j.url)+'\\',\\'needs_verification\\')">Needs verification</button><button class="danger" onclick="verifyJob(\\''+esc(j.url)+'\\',\\'stale\\')">Stale</button></div>').join('')}}
else if(t==='companies'){{const d=await get('/api/companies');v.innerHTML='<h2>Companies ('+(d.length)+')</h2>'+d.map(c=>'<div class="card">'+tag(c.qualification)+'<b>'+esc(c.name)+'</b> <span class="small">'+esc(c.domain)+'</span><div class="small">'+esc(c.state)+' · score '+Math.round(c.score||0)+'</div><div class="small"><b>Next:</b> '+esc(c.suggested_next_action)+'</div><h3>Research evidence</h3>'+((c.evidence||[]).map(e=>'<div class="small">'+tag(e.kind)+' '+esc(e.fact)+' <a href="'+esc(e.url)+'" target="_blank">source</a> · '+esc(e.observed_at)+'</div>').join('')||'<div class="small">No sourced evidence yet.</div>')+'<h3>Current vacancies</h3>'+((c.jobs||[]).map(j=>'<div class="small">'+tag(j.freshness)+'<a href="'+esc(j.url)+'" target="_blank">'+esc(j.title)+'</a> · '+esc(j.location)+' · '+esc(j.seniority)+(j.actionable?' · actionable':'')+'</div>').join('')||'<div class="small">No matching stored open role.</div>')+'<h3>Contacts</h3>'+((c.contacts||[]).map(x=>'<div class="small">'+tag(x.email_status)+'<b>'+esc(x.full_name)+'</b> · '+esc(x.title)+' '+(x.linkedin_url?'<a href="'+esc(x.linkedin_url)+'" target="_blank">profile</a>':'')+(x.email?' · '+esc(x.email):' · email needed')+'</div>').join('')||'<div class="small">No verified public contact recorded.</div>')+'<button onclick="openContact(\\''+esc(c.company_id)+'\\')">Add contact / email</button></div>').join('')}}
else if(t==='contacts'){{const d=await get('/api/contacts');v.innerHTML='<h2>Contacts ('+(d.length)+')</h2>'+d.map(c=>'<div class="card">'+tag(c.email_status)+'<b>'+esc(c.full_name)+'</b> <span class="small">'+esc(c.title)+'</span><div class="small">'+esc(c.company_name||c.company_id||'')+' · '+(c.linkedin_url?'<a href="'+esc(c.linkedin_url)+'" target="_blank">profile</a>':'no profile')+' · '+(c.email?esc(c.email):'email needed')+'</div><div class="small">email source: '+esc(c.source_url||'manual / not recorded')+'</div><button class="dim" onclick="fillContact('+JSON.stringify(c)+')">Edit</button></div>').join('')+addContactForm()}}
else if(t==='drafts'){{const d=await get('/api/drafts');v.innerHTML='<h2>Drafts ('+(d.length)+')</h2><div class="small">Copy-email workflow: approve, copy, send from your own client, then "Mark sent manually". "Save Gmail draft" only creates a Gmail draft (never sends). Sending requires per-message approval and --enable-send.</div>'+d.map(x=>'<div class="card"><span class="small">#'+x.id+'</span> '+tag(x.state)+' '+tag(x.draft_type)+' <span class="small">'+esc(x.recipient||'')+'</span><label>Subject</label><input id="subj-'+x.id+'" value="'+esc(x.subject)+'"><label>Message</label><textarea id="body-'+x.id+'" rows="12">'+esc(x.body)+'</textarea><button class="dim" onclick="editDraft('+x.id+')">Save edits</button><button onclick="act('+x.id+',\\'approve\\')">Approve</button><button class="dim" onclick="act('+x.id+',\\'save_gmail\\')">Save Gmail draft</button><button class="dim" onclick="act('+x.id+',\\'send\\')">Send…</button><button class="dim" onclick="act('+x.id+',\\'mark_sent\\')">Mark sent manually</button><button class="danger" onclick="act('+x.id+',\\'reject\\')">Reject</button></div>').join('')}}
else if(t==='applications'){{const d=await get('/api/applications');v.innerHTML='<h2>Applications ('+(d.length)+')</h2>'+d.map(a=>'<div class="card">'+tag(a.status)+'<b>'+esc(a.role)+'</b> <span class="small">@ '+esc(a.company_name||a.company_id)+'</span><div class="small">applied '+esc(a.applied_at)+' · '+esc(a.source||'manual')+'</div>'+(a.job_url?'<a href="'+esc(a.job_url)+'" target="_blank">'+esc(a.job_url)+'</a>':'')+'<div class="small">'+esc(a.notes||'')+'</div><label>Update status</label><select id="ast-'+a.id+'">'+['applied','in_review','interview','offer','rejected','withdrawn'].map(s=>'<option value="'+s+'"'+(s===a.status?' selected':'')+'>'+s+'</option>').join('')+'</select><label>Notes</label><input id="an-'+a.id+'" value="'+esc(a.notes||'')+'"><button class="dim" onclick="updateApp('+a.id+')">Update</button></div>').join('')+addApplicationForm()}}
else if(t==='followups'){{const d=await get('/api/followups');v.innerHTML='<h2>Follow-ups (pending, '+d.length+')</h2><div class="small">First follow-up: ~4 business days after send. Final: ~6 business days later (max 2). Weekends/approved holidays skipped. Suppressed after reply/rejection/bounce/opt-out. Each follow-up requires individual approval before a draft can be generated/sent.</div>'+d.map(f=>'<div class="card">'+tag(f.state)+' FU#<b>'+esc(f.followup_number)+'</b> due '+esc(f.due_on)+'<div class="small">'+esc(f.company_name||f.company_id||'')+(f.contact_name?' · '+esc(f.contact_name):'')+(f.contact_email?' · '+esc(f.contact_email):'')+'</div><div class="small">'+esc(f.reason||'')+'</div><div class="small">thread: '+esc(f.thread_id||'n/a')+(f.draft_id?' · draft #'+esc(f.draft_id):'')+'</div><button onclick="fuAction('+f.id+',\\'approve\\')">Approve</button><button class="dim" onclick="fuAction('+f.id+',\\'draft\\')">Generate draft</button><button class="dim" onclick="fuAction('+f.id+',\\'skip\\')">Skip</button><button class="dim" onclick="fuAction('+f.id+',\\'mark_sent\\')">Mark sent manually</button></div>').join('')}}
else if(t==='linkedin'){{const d=await get('/api/linkedin');v.innerHTML='<h2>LinkedIn queue ('+(d.length)+')</h2>'+d.map(t2=>'<div class="card">'+tag(t2.status)+'<b>'+esc(t2.profile_url)+'</b><pre>'+esc(t2.connection_note)+'</pre><div class="small">'+esc(t2.last_action||'')+'</div></div>').join('')}}
else if(t==='recruiter'){{const d=await get('/api/recruiter-posts');v.innerHTML='<h2>Recruiter posts ('+(d.length)+')</h2>'+d.map(p=>'<div class="card">'+tag(p.status)+'<b>'+esc(p.company||'(company unknown)')+'</b> <span class="small">'+esc(p.recruiter_name||'recruiter not identified')+(p.recruiter_title?' · '+esc(p.recruiter_title):'')+'</span><div class="small">published '+esc(p.published_at||'unknown')+' · verified '+esc(p.last_verified_at||'never')+' · <a href="'+esc(p.original_url)+'" target="_blank">original post</a></div>'+((p.roles||[]).map(r=>'<div class="small">• '+esc(r.title)+(r.location?' — '+esc(r.location):'')+(r.experience?' · '+esc(r.experience):'')+'</div>').join('')||'<div class="small">No roles extracted.</div>')+(p.published_emails&&p.published_emails.length?'<div class="small">Published emails: '+esc(p.published_emails.join(', '))+'</div>':'')+'<button class="dim" onclick="postStatus('+p.id+',\\'verified\\')">Verify</button><button class="dim" onclick="postStatus('+p.id+',\\'applied\\')">Mark applied</button><button class="dim" onclick="postStatus('+p.id+',\\'expired\\')">Expired</button><button class="danger" onclick="postStatus('+p.id+',\\'irrelevant\\')">Irrelevant</button></div>').join('')+recruiterForm()}}
else if(t==='activity'){{const d=await get('/api/activity');v.innerHTML='<h2>Recent activity</h2>'+d.map(a=>'<div class="card"><span class="small">'+esc(a.at)+'</span> '+tag(a.entity)+' '+esc(a.entity_id)+' <b>'+esc(a.from_state)+'→'+esc(a.to_state)+'</b><div class="small">'+esc(a.note||'')+'</div></div>').join('')}}
else if(t==='sources'){{const d=await get('/api/sources');v.innerHTML='<h2>Source registry ('+(d.length)+')</h2><div class="small">Sources with an honest integration status. "paused"/"unsupported" are never treated as active; restricted portals are manual-import only.</div>'+d.map(s=>'<div class="card">'+tag(s.status)+' <b>'+esc(s.name)+'</b> <span class="small">['+esc(s.integration_status)+'] '+esc(s.source_category)+'</span><div class="small">'+esc(s.website||'')+' · auth='+s.auth_required+' · perm='+esc(s.permission)+'</div>'+(s.reason?'<div class="small">reason: '+esc(s.reason)+'</div>':'')+(s.manual_workflow?'<div class="small">manual: '+esc(s.manual_workflow)+'</div>':'')+'</div>').join('')}}
else if(t==='firecrawl'){{const d=await get('/api/firecrawl-usage');const a=(d&&d.account)||{{}};const l=(d&&d.local)||{{}};v.innerHTML='<h2>Firecrawl usage</h2><div class="card"><div class="small"><b>Account (authoritative):</b> source='+esc(a.source||'unknown')+' remaining='+esc(a.remaining!==undefined?a.remaining:'n/a')+' plan='+esc(a.plan||'n/a')+'</div><div class="small"><b>Local ledger (estimate):</b> monthly '+esc(l.monthly_consumed)+' daily '+esc(l.daily_consumed)+' · remaining '+esc(l.internal_budget_remaining)+' / limit '+esc(l.budget_limit)+'</div><div class="small">warning '+esc(l.warning_threshold)+' hard-stop '+esc(l.hard_stop_threshold)+' absolute '+esc(l.absolute_account_limit)+' · allow_payg '+esc(l.allow_payg)+' · paid ops paused '+esc(l.paid_operations_paused)+'</div><div class="small">failed ops '+esc(l.failed_operations)+' (credits '+esc(l.failed_operations_credits)+') · unknown '+esc(l.unknown_operations)+'</div><div class="small">by endpoint: '+JSON.stringify(l.by_endpoint||{{}})+'</div><div class="small">by provider: '+JSON.stringify(l.by_provider||{{}})+'</div></div>'}}
else if(t==='discovery'){{const d=await get('/api/discovery-activity');const s=(d&&d.summary)||{{}};v.innerHTML='<h2>Discovery operations ('+(s.total||0)+' today)</h2><div class="card">'+Object.entries(s.by_outcome||{{}}).map(([k,o])=>'<div class="small">'+tag(k)+' '+esc(o.count)+' ops · '+esc(o.credits)+' credits</div>').join('')+'</div>'+((d&&d.recent)||[]).map(o=>'<div class="card"><span class="small">'+esc(o.started_at)+'</span> '+tag(o.outcome)+' <b>'+esc(o.source)+'</b><div class="small">'+esc(o.query)+' · records '+esc(o.returned_records)+' new_jobs '+esc(o.new_jobs)+' credits '+esc(o.firecrawl_credits)+'</div>'+(o.error_reason?'<div class="small">'+esc(o.error_reason)+'</div>':'')+'</div>').join('')}}
else if(t==='analytics'){{const d=await get('/api/overview');v.innerHTML='<h2>Analytics</h2>'+metric(d)}}
else if(t==='profile'){{const d=await get('/api/profile');const missing=(d&&d.missing)||[];const confirmed=(d&&d.confirmed)||{{}};v.innerHTML='<h2>Candidate profile checklist</h2><div class="small">Personal details remain local and git-ignored. Confirm fields below (stored locally in outreach.db); never invented or inferred.</div>'+(missing.length?'<div class="card"><b>Unverified fields needing confirmation ('+missing.length+')</b><ul>'+missing.map(m=>'<li>'+esc(m)+' <button class="dim" onclick="confirmField(\\''+esc(m)+'\\')">Confirm</button></li>').join('')+'</ul></div>':'<div class="card tag ok">Profile confirmations complete.</div>')+'<div class="card"><h3>Confirmed overrides</h3>'+Object.entries(confirmed).map(([k,v])=>'<div class="small"><b>'+esc(k)+'</b>: '+esc(v)+'</div>').join('')+'<label>Field</label><input id="pf_field" placeholder="notice_period / availability / work_authorization"><label>Value</label><input id="pf_value"><button onclick="saveProfile()">Save confirmation</button></div>'}}
}}
function addContactForm(){{return '<div class="card"><h2>Add / edit contact (paste email from Apollo)</h2><div class="small">Entering an email never sends a message. Emails are marked unverified unless you set status explicitly; they are not auto-verified.</div>'+
'<div class="row"><div class="col"><label>Company id/name</label><input id="c_company"></div>'+
'<div class="col"><label>Name</label><input id="c_name"></div></div>'+
'<div class="row"><div class="col"><label>Title</label><input id="c_title"></div>'+
'<div class="col"><label>Role</label><select id="c_role"><option value="cto">CTO</option><option value="founder">Founder/CEO</option><option value="engineering_manager">Engineering Manager</option><option value="recruiter">Recruiter</option><option value="engineer">Engineer</option></select></div></div>'+
'<div class="row"><div class="col"><label>Email</label><input id="c_email" type="email"></div>'+
'<div class="col"><label>Email status</label><select id="c_status"><option value="unverified">unverified</option><option value="verified">verified</option><option value="unavailable">unavailable</option></select></div></div>'+
'<div class="row"><div class="col"><label>LinkedIn URL</label><input id="c_linkedin"></div>'+
'<div class="col"><label>Email / contact source URL</label><input id="c_source" placeholder="https://…"></div></div>'+
'<button onclick="saveContact()">Save & generate draft</button></div>'}}
function addApplicationForm(){{return '<div class="card"><h2>Record application</h2>'+
'<div class="row"><div class="col"><label>Company id/name</label><input id="a_company"></div><div class="col"><label>Company display name</label><input id="a_company_name"></div></div>'+
'<div class="row"><div class="col"><label>Role</label><input id="a_role"></div><div class="col"><label>Applied at (YYYY-MM-DD)</label><input id="a_applied"></div></div>'+
'<label>Job URL</label><input id="a_url"><label>Notes</label><input id="a_notes">'+
'<button onclick="saveApplication()">Record application</button></div>'}}
async function saveApplication(){{const r=await post('/api/applications',{{company_id:$('#a_company').value,company_name:$('#a_company_name').value,role:$('#a_role').value,applied_at:$('#a_applied').value,job_url:$('#a_url').value,notes:$('#a_notes').value}});alert(JSON.stringify(r));show('applications')}}
async function updateApp(id){{const r=await post('/api/applications/'+id,{{status:$('#ast-'+id).value,notes:$('#an-'+id).value}});alert(JSON.stringify(r));show('applications')}}
async function saveContact(){{const r=await post('/api/contacts',{{company_id:$('#c_company').value,full_name:$('#c_name').value,title:$('#c_title').value,role_kind:$('#c_role').value,email:$('#c_email').value,email_status:$('#c_status').value,linkedin_url:$('#c_linkedin').value,source_url:$('#c_source').value}});
if(r.warnings&&r.warnings.length)alert('Warnings:\\n'+r.warnings.join('\\n'));
if(r.draft_id){{alert((r.draft_already_exists?'Existing':'New')+' draft #'+r.draft_id+' is ready for review.');show('drafts')}}else show('contacts')}}
function fillContact(c){{$('#c_company').value=c.company_id||'';$('#c_name').value=c.full_name||'';$('#c_title').value=c.title||'';$('#c_role').value=c.role_kind||'engineer';$('#c_email').value=c.email||'';$('#c_status').value=c.email_status||'unverified';$('#c_linkedin').value=c.linkedin_url||'';$('#c_source').value=c.source_url||'';window.scrollTo(0,document.body.scrollHeight)}}
async function editDraft(id){{const r=await post('/api/drafts/'+id+'/edit',{{subject:$('#subj-'+id).value,body:$('#body-'+id).value}});if(!r.ok)alert(JSON.stringify(r));show('drafts')}}
async function openContact(companyId){{await show('contacts');$('#c_company').value=companyId;$('#c_name').focus()}}
function recruiterForm(){{return '<div class="card"><h2>Import recruiter post</h2><label>Original post URL</label><input id="r_url" placeholder="https://…"><label>Company (only if the post states it)</label><input id="r_company"><label>Recruiter name / title (only if published)</label><input id="r_name"><input id="r_title"><label>Published date (YYYY-MM-DD, only if shown)</label><input id="r_date"><label>Post text</label><textarea id="r_text" rows="10"></textarea><button onclick="saveRecruiterPost()">Import & extract</button></div>'}}
async function saveRecruiterPost(){{const r=await post('/api/recruiter-posts/import',{{original_url:$('#r_url').value,company:$('#r_company').value,recruiter_name:$('#r_name').value,recruiter_title:$('#r_title').value,published_at:$('#r_date').value,text:$('#r_text').value,source_type:'manual'}});alert(JSON.stringify(r));show('recruiter')}}
async function postStatus(id,status){{const r=await post('/api/recruiter-posts/'+id+'/status',{{status:status}});alert(JSON.stringify(r));show('recruiter')}}
async function verifyJob(url,status){{const note=status==='fresh'?'re-verified against employer source':(prompt('Reason for '+status+'? (optional)','')||'');const r=await post('/api/jobs/verify',{{url:url,status:status,note:note}});alert(JSON.stringify(r));show('jobs')}}
async function fuAction(id,a){{let body={{}};if(a==='mark_sent'){{const t=prompt('Sent at (ISO 8601, blank = now)','')||'';body.sent_at=t}}if(a==='skip'){{body.note=prompt('Reason (optional)','')||''}}const r=await post('/api/followups/'+id+'/'+a,body);alert(JSON.stringify(r));if(a==='draft'&&r.ok&&r.draft_id){{show('drafts')}}else{{show('followups')}}}}
async function confirmField(field){{const v=prompt('Value for '+field,'');if(v===null)return;const r=await post('/api/profile/confirm',{{field:field,value:v}});alert(JSON.stringify(r));show('profile')}}
async function saveProfile(){{const r=await post('/api/profile/confirm',{{field:$('#pf_field').value,value:$('#pf_value').value}});alert(JSON.stringify(r));show('profile')}}
async function act(id,a){{if(a==='send'){{if(!confirm('Send this message? This sends real email.'))return;const r=await post('/api/drafts/'+id+'/send',{{confirm:true}});alert(JSON.stringify(r))}}
else if(a==='mark_sent'){{const t=prompt('Sent at (ISO 8601, blank = now)','')||'';const r=await post('/api/drafts/'+id+'/mark_sent',{{sent_at:t}});alert(JSON.stringify(r))}}
else{{const r=await post('/api/drafts/'+id+'/'+a,{{}});alert(JSON.stringify(r))}}
show('drafts')}}
async function logout(){{await post('/api/logout',{{}});location.href='/login'}}
show('jobs');
</script></body></html>"""

    def make_handler(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/login":
                    html = dashboard._login_html()
                    self._send_html(html)
                elif path == "/":
                    if dashboard._authorized(self):
                        self._send_html(dashboard._main_html())
                    else:
                        self.send_response(302)
                        self.send_header("Location", "/login")
                        self.end_headers()
                else:
                    dashboard.route(self)

            def do_POST(self):
                dashboard.route(self)

            def _send_html(self, html):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html.encode())

        return Handler


def run_dashboard(outreach, companies_store=None, jobs_store=None,
                  host: str = "127.0.0.1", port: int = 8090, token: str = "",
                  gmail=None, profile=None, sending_enabled: bool = False):
    dashboard = Dashboard(outreach, companies_store, jobs_store, token,
                          gmail=gmail, profile=profile, sending_enabled=sending_enabled)
    server = ThreadingHTTPServer((host, port), dashboard.make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, dashboard
