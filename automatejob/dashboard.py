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

    # -- auth ----------------------------------------------------------------
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

    # -- JSON API ------------------------------------------------------------
    def route(self, handler):
        path = urlparse(handler.path).path
        method = handler.command

        # auth bypass for login only
        if path == "/api/login" and method == "POST":
            body = self._body(handler)
            if self.token and not hmac.compare_digest(str(body.get("token", "")), self.token):
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
            self._send(handler, self._companies())
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
            self._send(handler, self.outreach.list_due_followups())
        elif path == "/api/activity":
            self._send(handler, self._activity())
        elif path == "/api/applications":
            self._send(handler, self.outreach.list_applications())
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
            self._send(handler, self.outreach.list_profile_confirmations())
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
        self._send(handler, {"contact_id": result.contact_id, "draft": result.draft,
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
        try:
            draft_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self._send(handler, {"error": "bad id"}, 400)
            return
        action = path.rsplit("/", 2)[-2] if "/" in path[:-1] else ""
        # action is the second-to-last segment e.g. /api/drafts/12/approve
        action = path.split("/")[-1]
        body = self._body(handler)

        if action == "approve":
            ok = self.outreach.approve_draft(draft_id)
            self._send(handler, {"ok": ok}, 200 if ok else 404)
        elif action == "reject":
            self.outreach.conn.execute(
                "UPDATE outreach_drafts SET state='rejected' WHERE id=?", (draft_id,))
            self.outreach.conn.commit()
            self._send(handler, {"ok": True})
        elif action == "save_gmail":
            row = self.outreach.conn.execute(
                "SELECT * FROM outreach_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                self._send(handler, {"error": "not found"}, 404)
                return
            if self.gmail is None:
                self._send(handler, {"status": "gmail_unavailable"}, 200)
                return
            contact = self.outreach.conn.execute(
                "SELECT email FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
            to_addr = contact["email"] if contact else ""
            res = self.gmail.save_draft(row["subject"], row["body"], to_addr)
            self.outreach.conn.execute(
                "UPDATE outreach_drafts SET state='gmail_draft' WHERE id=?", (draft_id,))
            self.outreach.conn.commit()
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
<button data-t="followups">Follow-ups</button><button data-t="linkedin">LinkedIn</button>
<button data-t="activity">Activity</button><button data-t="analytics">Analytics</button>
<button onclick="logout()">Log out</button></div>
<div id="view"></div></div>
<script>
const $=s=>document.querySelector(s);
async function get(u){{const r=await fetch(u);if(r.status===401){{location.href='/login';return null}}return r.json()}}
async function post(u,b){{const r=await fetch(u,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b||{{}})}});return r.json()}}
function esc(s){{return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}}
function tag(s){{const m={{'verified':'ok','sent':'ok','replied':'ok','active':'ok','high_signal_speculative':'ok','pending':'warn','unverified':'warn','awaiting_approval':'warn','rejected':'bad','bounced':'bad','opted_out':'bad','closed':'bad'}};return '<span class="tag '+(m[s]||'')+'">'+esc(s)+'</span>'}}
function metric(o){{return Object.entries(o||{{}}).map(([k,v])=>'<div class="metric"><b>'+esc(v)+'</b>'+esc(k.replace(/_/g,' '))+'</div>').join('')}}
async function show(t){{
document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
const v=$('#view');
if(t==='jobs'){{const d=await get('/api/overview');v.innerHTML='<h2>Overview</h2>'+metric(d)}}
else if(t==='companies'){{const d=await get('/api/companies');v.innerHTML='<h2>Companies ('+(d.length)+')</h2>'+d.map(c=>'<div class="card">'+tag(c.qualification)+'<b>'+esc(c.name)+'</b> <span class="small">'+esc(c.domain)+'</span><div class="small">score '+Math.round(c.score||0)+'</div></div>').join('')}}
else if(t==='contacts'){{const d=await get('/api/contacts');v.innerHTML='<h2>Contacts ('+(d.length)+')</h2>'+d.map(c=>'<div class="card">'+tag(c.email_status)+'<b>'+esc(c.full_name)+'</b> <span class="small">'+esc(c.title)+'</span><div class="small">'+esc(c.company_name||'')+' · '+esc(c.email)+'</div></div>').join('')+addContactForm()}}
else if(t==='drafts'){{const d=await get('/api/drafts');v.innerHTML='<h2>Drafts ('+(d.length)+')</h2>'+d.map(x=>'<div class="card"><span class="small">#'+x.id+'</span> '+tag(x.state)+' '+tag(x.draft_type)+'<b>'+esc(x.subject)+'</b><pre>'+esc(x.body)+'</pre><button onclick="act('+x.id+',\'approve\')">Approve</button><button class="dim" onclick="act('+x.id+',\'save_gmail\')">Save Gmail draft</button><button class="dim" onclick="act('+x.id+',\'send\')">Send…</button><button class="dim" onclick="act('+x.id+',\'mark_sent\')">Mark sent manually</button><button class="danger" onclick="act('+x.id+',\'reject\')">Reject</button></div>').join('')}}
else if(t==='followups'){{const d=await get('/api/followups');v.innerHTML='<h2>Follow-ups due ('+(d.length)+')</h2>'+d.map(f=>'<div class="card">'+tag('pending')+'#<b>'+f.followup_number+'</b> due '+esc(f.due_on)+'<div class="small">'+esc(f.reason)+'</div></div>').join('')}}
else if(t==='linkedin'){{const d=await get('/api/linkedin');v.innerHTML='<h2>LinkedIn queue ('+(d.length)+')</h2>'+d.map(t2=>'<div class="card">'+tag(t2.status)+'<b>'+esc(t2.profile_url)+'</b><pre>'+esc(t2.connection_note)+'</pre><div class="small">'+esc(t2.next_action)+'</div></div>').join('')}}
else if(t==='activity'){{const d=await get('/api/activity');v.innerHTML='<h2>Recent activity</h2>'+d.map(a=>'<div class="card"><span class="small">'+esc(a.at)+'</span> '+tag(a.entity)+' '+esc(a.entity_id)+' <b>'+esc(a.from_state)+'→'+esc(a.to_state)+'</b></div>').join('')}}
else if(t==='analytics'){{const d=await get('/api/overview');v.innerHTML='<h2>Analytics</h2>'+metric(d)}}
}}
function addContactForm(){{return '<div class="card"><h2>Add contact (paste email from Apollo)</h2>'+
'<div class="row"><div class="col"><label>Company id</label><input id="c_company"></div>'+
'<div class="col"><label>Name</label><input id="c_name"></div></div>'+
'<div class="row"><div class="col"><label>Title</label><input id="c_title"></div>'+
'<div class="col"><label>Role</label><select id="c_role"><option value="cto">CTO</option><option value="founder">Founder/CEO</option><option value="engineering_manager">Engineering Manager</option><option value="recruiter">Recruiter</option><option value="engineer">Engineer</option></select></div></div>'+
'<div class="row"><div class="col"><label>Email (unverified until confirmed)</label><input id="c_email" type="email"></div>'+
'<div class="col"><label>LinkedIn URL</label><input id="c_linkedin"></div></div>'+
'<button onclick="saveContact()">Save & generate draft</button></div>'}}
async function saveContact(){{const r=await post('/api/contacts',{{company_id:$('#c_company').value,full_name:$('#c_name').value,title:$('#c_title').value,role_kind:$('#c_role').value,email:$('#c_email').value,linkedin_url:$('#c_linkedin').value}});
if(r.warnings&&r.warnings.length)alert('Warnings:\\n'+r.warnings.join('\\n'));
show('contacts')}}
async function act(id,a){{if(a==='send'){{if(!confirm('Send this message? This sends real email.'))return;const r=await post('/api/drafts/'+id+'/send',{{confirm:true}});alert(JSON.stringify(r))}}
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
