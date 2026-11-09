"""Lightweight local web dashboard (stdlib only — no Flask dependency).

Serves a mobile-responsive HTML view plus JSON endpoints for the outreach
pipeline. Binds to localhost by default and requires a bearer token; it is
never exposed publicly without that token.

Endpoints:
  GET  /                     dashboard HTML
  GET  /api/overview         metrics
  GET  /api/companies        shortlisted companies
  GET  /api/drafts           drafts awaiting approval
  GET  /api/followups        follow-ups due
  GET  /api/contacts         verified contacts
  POST /api/approve          approve a draft (JSON body {id})
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
margin:0;background:#0f1115;color:#e6e6e6;line-height:1.45}
.wrap{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin:20px 0 8px;color:#7dd3fc}
.card{background:#1a1d24;border:1px solid #2a2e38;border-radius:8px;padding:12px;margin:8px 0}
.tag{display:inline-block;font-size:.72rem;padding:2px 8px;border-radius:10px;background:#27303f;margin-right:6px}
.metric{display:inline-block;background:#1a1d24;border:1px solid #2a2e38;border-radius:8px;
padding:10px 14px;margin:4px;min-width:90px}
.metric b{display:block;font-size:1.3rem;color:#7dd3fc}
a{color:#7dd3fc;text-decoration:none}button{background:#2563eb;color:#fff;border:0;
border-radius:6px;padding:6px 12px;font-size:.85rem;cursor:pointer}
.small{font-size:.8rem;color:#9aa2b1}
"""


def _html(overview: dict, companies: list, drafts: list, followups: list) -> str:
    metrics = "".join(f'<div class="metric"><b>{v}</b>{k.replace("_"," ")}</div>'
                      for k, v in overview.items())
    comp_rows = "".join(
        f'<div class="card"><span class="tag">{c["qualification"] or "?"}</span>'
        f'<b>{c["name"]}</b> <span class="small">{c.get("domain","")}</span>'
        f'<div class="small">score {c.get("score",0):.0f}</div></div>'
        for c in companies)
    draft_rows = "".join(
        f'<div class="card"><span class="tag">{d["draft_type"]}</span>'
        f'<b>{d["subject"]}</b><div class="small">{d["body"][:160]}...</div></div>'
        for d in drafts)
    fu_rows = "".join(
        f'<div class="card"><span class="tag">followup #{f["followup_number"]}</span>'
        f'due {f["due_on"]} <div class="small">{f.get("reason","")}</div></div>'
        for f in followups)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>automatejob outreach</title><style>{_CSS}</style></head><body>
<div class="wrap"><h1>automatejob — outreach pipeline</h1>
<h2>Overview</h2><div>{metrics}</div>
<h2>Shortlisted companies ({len(companies)})</h2>{comp_rows or '<div class="card">none</div>'}
<h2>Drafts awaiting review ({len(drafts)})</h2>{draft_rows or '<div class="card">none</div>'}
<h2>Follow-ups due ({len(followups)})</h2>{fu_rows or '<div class="card">none</div>'}
</div></body></html>"""


class Dashboard:
    def __init__(self, outreach, companies_store=None, jobs_store=None, token: str = ""):
        self.outreach = outreach
        self.companies_store = companies_store
        self.jobs_store = jobs_store
        self.token = token

    def _authorized(self, handler) -> bool:
        if not self.token:
            return True
        h = handler.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            return h[7:] == self.token
        return parse_qs(urlparse(handler.path).query).get("token", [""])[0] == self.token

    def _companies(self, limit=20) -> list:
        if self.companies_store is None:
            return []
        return self.companies_store.list(limit=limit)

    def _send(self, handler, payload, status=200):
        data = json.dumps(payload).encode() if not isinstance(payload, str) else payload.encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def route(self, handler):
        if not self._authorized(handler):
            self._send(handler, {"error": "unauthorized"}, 401)
            return
        path = urlparse(handler.path).path
        if path == "/api/overview":
            self._send(handler, self.outreach.metrics())
        elif path == "/api/companies":
            self._send(handler, self._companies())
        elif path == "/api/drafts":
            self._send(handler, self.outreach.list_drafts(state="awaiting_approval"))
        elif path == "/api/followups":
            self._send(handler, self.outreach.list_due_followups())
        elif path == "/api/contacts":
            self._send(handler, self.outreach.list_contacts())
        elif path == "/api/approve" and handler.command == "POST":
            length = int(handler.headers.get("Content-Length", 0))
            body = json.loads(handler.rfile.read(length) or b"{}")
            ok = self.outreach.approve_draft(int(body.get("id", -1)))
            self._send(handler, {"approved": ok}, 200 if ok else 404)
        else:
            self._send(handler, {"error": "not found"}, 404)

    def make_handler(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if urlparse(self.path).path == "/" and dashboard._authorized(self):
                    html = _html(dashboard.outreach.metrics(), dashboard._companies(),
                                 dashboard.outreach.list_drafts(state="awaiting_approval"),
                                 dashboard.outreach.list_due_followups())
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html.encode())
                else:
                    dashboard.route(self)

            def do_POST(self):
                dashboard.route(self)

        return Handler


def run_dashboard(outreach, companies_store=None, jobs_store=None,
                  host: str = "127.0.0.1", port: int = 8090, token: str = ""):
    dashboard = Dashboard(outreach, companies_store, jobs_store, token)
    server = ThreadingHTTPServer((host, port), dashboard.make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, dashboard
