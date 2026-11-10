# automatejob

A personal, AI-assisted job hunting system. It collects job postings from
**permitted public job-board feeds** (Greenhouse, Lever and Ashby APIs),
classifies each role across four hard gates, stores them deduplicated in
SQLite with change/closure tracking, and produces a **shortlist of genuinely
actionable junior engineering roles** — never padded with irrelevant jobs.

This tool only **reads** public, documented APIs. It never scrapes gated pages,
bypasses authentication, sends emails, applies to jobs, or connects to personal
accounts.

---

## Quick start

```bash
pip install -r requirements.txt

python -m automatejob sources        # configured boards
python -m automatejob companies      # company registry
python -m automatejob collect        # fetch, classify, score, store, track
python -m automatejob list --actionable --all
python -m automatejob list --preference relocation --all
python -m automatejob daily          # concise daily report
python -m automatejob export --format csv -o actionable.csv
```

The SQLite database is created at `data/jobs.db` (configurable, git-ignored).
Daily reports land in `reports/` (git-ignored).

## Requirements

- Python ≥ 3.10 (standard library plus `requests`).

## CLI

```
python -m automatejob <command> [options]

  collect             fetch all sources, classify, score, store, track
    --min-score N     only show new jobs scoring at least N
    --limit N         cap how many new jobs are shown

  list                list stored jobs (new ones by default)
    --all             show all jobs, not just unseen ones
    --actionable      only roles that pass ALL gates (open + role family +
                      junior seniority + Mumbai/eligible-remote)
    --eligibility S   confirmed / excluded / needs_verification
    --preference S    mumbai / relocation / remote
    --min-score N
    --source TYPE     greenhouse / lever / ashby
    --mark-seen       clear the "new" flag
    --no-reasons      hide scoring reasons

  rescore             re-classify + re-score every stored row (no refetch)

  export              write jobs to CSV/JSON
    --format csv|json
    --all | --actionable
    -o/--output PATH

  daily               concise daily report (new actionable, changed, closed,
                      needs-verification, failed sources)
    --all             show all actionable jobs, not just new
    -o/--output PATH

  stats / sources / companies

  discover             run company discovery (YC directory + optional ATS)
    --yc-pages N       cap YC directory pages (default: all)
    --ats              detect + validate ATS boards for discovered companies
    --ats-limit N      max companies to ATS-probe (bounded, default 30)

  leads                show company shortlists from the discovery DB
    --mumbai / --remote <india_remote|worldwide_remote>
    --category <active_hiring|speculative_outreach|research_lead>

  ats <name> [domain]  detect + validate a company's ATS board (live)

  profile              show the verified candidate profile + unverified fields

Global: -c/--config PATH, -v/-vv for logging
```

## The actionable gate

A job is **actionable** only if it passes ALL four gates (plus open status):

1. **Engineering role family** (title-driven) — DevOps, SRE, Platform,
   Infrastructure, Cloud, Go/Python Backend, AI/Automation, QA, or a related
   engineering internship. HR, sales, support, recruiters, operations,
   finance, solutions-architects and other non-engineering roles are excluded.
   Genuinely ambiguous titles go to review.
2. **Junior seniority** — intern, new-grad, fresher, trainee, entry-level,
   junior, or a credible 0–2 years requirement. Senior/staff/principal/lead/
   manager and clearly intermediate roles never enter the shortlist. Uncertain
   experience ("Software Engineer" with no signal, "L3", title-vs-description
   conflicts) goes to review.
3. **Employment eligibility** — India-eligible: Mumbai metro, other India
   offices, remote-India, or world-wide remote. US/EU-only remote and foreign
   on-site roles are excluded.
4. **Location preference** (kept separate from eligibility) — Mumbai/Thane/
   Navi Mumbai on-site/hybrid, or India-eligible / world-wide remote. Other
   Indian cities (Bengaluru/Hyderabad/Gurugram) go to a **relocation queue**,
   not the default shortlist.

Anything not actionable sits in review or relocation, and ambiguous remote
stays in needs_verification. If only one job genuinely passes, the report
shows one job.

## Configuration (`config.toml`)

- `[[sources]]` — Greenhouse / Lever / Ashby boards (real, verified tokens).
- `[[roles]]` / `[[locations]]` — scoring keyword lists and location points.
- `[eligibility]` — `cities` (Mumbai metro) and `relocation` (accept other
  India into a relocation queue). Location preference is configurable.
- `[tracking]` — `close_after_scans` (default 2): a role is marked closed only
  after it is absent from that many successive successful scans.
- `[companies]` — path to the company registry.

## Companies registry

`data/companies.toml` records, per company: name, website, careers URL, ATS
type, Mumbai-office status, remote hiring eligibility, last-checked timestamp,
and the source of every claim. Unverified facts are stored as null/"unknown",
never guessed. A company known to hire world-wide remote upgrades its bare
"Remote" roles from needs_verification to confirmed.

## Company discovery

A separate company layer (`company.py`, `sources/company_sources.py`,
`ats_detect.py`, `company_scoring.py`, `discovery.py`) discovers and enriches
companies independently of the job pipeline:

- **YC directory** (`api.ycombinator.com/v0.1/companies`, public, paginated) —
  name, website, description, team size, batch, tags, locations.
- **GitHub org** (`api.github.com/orgs/{org}`, public) — confirms engineering
  activity and detects technologies from repository languages.
- **ATS auto-detection** (`ats_detect.py`) — infers a Greenhouse/Lever/Ashby
  board identifier, then **validates it against the live public endpoint**
  (200 = verified, 404 = not this ATS). Inferred tokens are never trusted
  unvalidated. (Wellfound has no stable public API — recorded as unsupported.)
- **Company scoring** — three opportunity categories: `active_hiring`
  (relevant verified opening), `speculative_outreach` (credible engineering /
  funding / remote / Mumbai signals, no vacancy), `research_lead` (insufficient
  evidence). Every claim carries source URLs; speculative is never presented
  as a vacancy.

Discovered companies are stored in `data/companies.db` (separate from
`data/jobs.db`, both git-ignored) with provenance and unknown-value handling.

## Candidate profile & answer bank

`data/candidate_profile.toml` and `data/answer_bank.toml` hold the non-sensitive
schema (portfolio, technologies, target roles, location and employment
preferences) plus reusable answer templates. Verified personal facts (name,
contact, education, employment history, projects) live in `data/personal.toml`,
which is **git-ignored** — populate it from your resume and never commit it.
`profile.py` refuses to render an answer that depends on an unconfirmed fact
rather than inventing one; the `profile` command prints the remaining
confirmation checklist.

## Outreach pipeline

`discover` → `pipeline` → `draft` → `dashboard` converts company discovery into
a tracked, approval-gated outreach queue:

- `pipeline` qualifies discovered companies into the outreach database
  (`data/outreach.db`) with four tiers — `active_vacancy`,
  `high_signal_speculative`, `research_candidate`, `unqualified`.
- `outreach` shows metrics and the top shortlisted companies.
- `draft <domain> --type founder|vacancy|internship|referral|followup` renders a
  personalized email from verified facts + company evidence, with an evidence
  map linking every claim to its source. Use `--save` to store it.
- `dashboard` serves a mobile-friendly web UI (`http://127.0.0.1:8090`) with
  session-cookie auth; bind to a private address only and always pass `--token`.
- `verify-job <url> --status fresh|needs_verification|stale` records source
  freshness. A stale/unverified listing can never be actionable merely because
  its URL continues responding.

The pipeline database records contacts, enrichment history, company evidence,
drafts, outbound messages, Gmail threads, follow-ups, replies, interviews, and
audit events. Status changes append audit events rather than overwriting
history. **Nothing sends email or applies to jobs** — all sends remain disabled
unless individually approved after explicitly enabling the separate send path.
When local Gmail OAuth is authorized, `gmail-auth` creates a 0600 local token,
real Gmail drafts can be created, and `gmail-sync` can read tracked threads to
suppress follow-ups after replies. OAuth is never started by collection/cron;
see `docs/gmail-oauth.md`.

### Manual contact enrichment (no Apollo API required)

The primary contact workflow is manual: paste an email retrieved from Apollo
into the dashboard (or `contact --add`), and the system validates it, checks
duplicates and suppression lists, and auto-generates a role-tailored draft
(founder / CTO / engineering manager / recruiter / referral). `contact
--import-csv` bulk-loads a CSV. `readiness` scores companies on outreach
readiness (distinct from company fit). Manually-entered emails are marked
`unverified` until verification evidence is supplied.

### Dashboard security

The dashboard uses HMAC-signed session cookies (plus an optional bearer
header), never query-string tokens. It binds to localhost by default; access
from your iPhone over a private tunnel (Tailscale) or an authenticated reverse
proxy, always with `--token` set. Sending is disabled unless `--enable-send`
is explicitly passed AND Gmail OAuth is authorized.

### Sources

Configured job sources now include Greenhouse / Lever / Ashby boards, the
Hacker News "Who is hiring?" monthly thread (Algolia API), the SimplifyJobs
Summer-2027 internships + New-Grad lists (public GitHub README tables, original
employer links retained), and the YC Work-at-a-Startup job board (structured
JSON embedded in each company page, e.g. MixRank's "Junior Software Engineer —
Remote (Global)"). US/Canada-only roles are excluded by the eligibility gate,
not assumed open to India.

## Tracking

- **first_seen / last_seen / changed_at** — `content_hash` hashes
  title+description+location; a re-seen posting with a different hash stamps
  `changed_at`.
- **status (open/closed)** — a role closes only after `close_after_scans`
  successive successful scans without it; a reappearing role reopens.
- **cross_board_key** — company+title+location, linking the same role on two
  boards.

## Scheduling

A read-only collection runs three times a day (09:00 / 15:00 / 21:00
Asia/Kolkata) via Hermes cron in **script/no-agent mode** — no LLM runs on the
schedule. The entrypoint `scripts/collect.sh`:

- uses `flock` so overlapping runs are skipped,
- bounds each run with `timeout` (default 540s),
- uses stable absolute paths and the system Python,
- writes `reports/collect-*.log` and `reports/daily.txt`,
- exits non-zero on failure.

Run it manually at any time:

```bash
bash /home/sahil/Work/automatejob/scripts/collect.sh
```

## Tests

```bash
python -m unittest discover -s tests
```

85 tests cover normalization, dedup, scoring, eligibility (Mumbai vs Bengaluru,
India-remote, ambiguous remote, foreign cities), role-family classification
(20+ adversarial titles), seniority (junior/graduate/intermediate/senior/lead/
review, "Software Engineer 3", L-levels), the actionable gate, HTML→text,
source failures, Ashby/GH/Lever parsing, closure/change tracking, and
cross-board duplicates.

## Limitations

- **Keyword/title matching is heuristic.** It is a filter, not a recruiter —
  always read the actual posting before applying.
- **"Remote" with no region is deliberately needs_verification**, as are
  roles with no stated experience. We do not guess.
- **Foreign detection is city/country-list based.** Uncommon cities may leak
  into needs_verification (conservatively, never into the shortlist).
- **Only Greenhouse, Lever and Ashby** are implemented; Workday/Jobvite/
  LinkedIn/Indeed have no public API.
- **Cross-board dedup collapses on company+title+location**, so two genuinely
  different same-title roles at one company/location link together.
- The current boards skew senior and US/EU, so the genuinely actionable junior
  India-eligible list is short by design — that is expected, not a bug.

## Data & privacy

Job data is stored locally in `data/jobs.db` only. No data leaves your machine
except the outbound HTTPS requests to the public job-board APIs.
