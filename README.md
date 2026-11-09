# automatejob

A personal, AI-assisted job hunting system. It collects job postings from
**permitted public job-board feeds** (Greenhouse, Lever and Ashby APIs),
applies a **hard location/eligibility filter**, stores them deduplicated in
SQLite with change/closure tracking, and scores how well each role matches a
configured target profile — with human-readable reasons for every score.

This tool only **reads** public, documented APIs. It never scrapes gated pages,
bypasses authentication, sends emails, applies to jobs, or connects to personal
accounts.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m automatejob sources        # configured boards
python -m automatejob companies      # company registry
python -m automatejob collect        # fetch, filter, score, store
python -m automatejob stats          # aggregate counts
python -m automatejob list --qualified --all --limit 20
python -m automatejob export --format csv -o qualified.csv
```

The SQLite database is created at `data/jobs.db` (configurable, git-ignored).

## Requirements

- Python ≥ 3.10 (standard library plus `requests`).

## CLI

```
python -m automatejob <command> [options]

  collect             fetch all sources, hard-filter, score, store, track
    --min-score N     only show new jobs scoring at least N
    --limit N         cap how many new jobs are shown

  list                list stored jobs (new ones by default)
    --all             show all jobs, not just unseen ones
    --qualified       only confirmed-eligible jobs
    --eligibility S   filter by confirmed/excluded/needs_verification
    --min-score N
    --source TYPE     greenhouse / lever / ashby
    --mark-seen       clear the "new" flag
    --no-reasons      hide scoring reasons

  rescore             re-score every stored row in place (no refetch)

  export              write jobs to CSV/JSON
    --format csv|json
    --all             export every job (default: confirmed-eligible only)
    -o/--output PATH

  stats               aggregate DB statistics
  sources             list configured sources
  companies           list the company registry

Global: -c/--config PATH, -v/-vv for logging
```

## Eligibility (hard filter, applied BEFORE scoring)

Every job is classified into one of three buckets by `eligibility.py`:

- **confirmed** — preferred on-site/hybrid cities (Mumbai metro: Mumbai,
  Thane, Navi Mumbai), remote roles explicitly open to India, or genuinely
  worldwide remote ("Remote - Global", "Anywhere").
- **excluded** — remote roles restricted to a region that excludes India
  (US/EU/EMEA/AMER/…), or on-site roles in a named foreign location.
- **needs_verification** — ambiguous: bare "Remote" with no region, regional
  remote (APAC/Asia), or unresolvable locations. These are stored separately
  and are **never** labelled confirmed eligible.

`[eligibility]` in config.toml makes the location and relocation preferences
configurable (`cities`, `india_wide`, `relocation`). The `companies` registry
can upgrade an ambiguous remote role to confirmed when the company is known to
hire world-wide/India remote.

## Seniority

`seniority.py` classifies each role into `internship`, `junior` (incl.
graduate/fresher/trainee/entry-level and credible 0–2 years), `intermediate`,
`senior` (incl. staff/principal/architect), `lead` (manager/director/head/VP),
`review` (borderline — never auto-accepted/rejected), or `unknown`.
Junior/internship roles are prioritised; intermediate/senior/lead are
identified and ranked lower rather than silently dropped.

## Sources

- **Greenhouse** — `?content=true` retrieves full descriptions; HTML is
  converted to searchable text (`html.py`).
- **Lever** — includes `descriptionPlain`.
- **Ashby** — the public posting API
  (`https://api.ashbyhq.com/posting-api/job-board/{board}`), verified against
  the live API. Shipped boards: Kong (Mumbai/Bangalore/Delhi offices),
  Supabase (worldwide remote), Notion (Hyderabad).

All board tokens are real and were checked against the live API — no company
boards are fabricated.

## Companies registry

`data/companies.toml` records, per company: name, website, careers URL, ATS
type, Mumbai-office status, remote hiring eligibility, last-checked timestamp,
and the source of every claim. Unverified facts are stored as null/"unknown",
never guessed.

## Deduplication & tracking

- **Per-source dedup key** = SHA-256(normalized company + title + URL).
- **Cross-board key** = SHA-256(company + title + location), linking the same
  role on two boards (reported via `stats`, collapsed in the qualified list).
- **first_seen / last_seen / changed_at** — `content_hash` hashes
  title+description+location; a re-seen posting with a different hash stamps
  `changed_at`.
- **Closure** — a role is only marked `closed` after it has been absent from
  `close_after_scans` successive *successful* scans of its source (default 2).
  A reappearing role is reopened.

## Scoring model (0–100, fully explained)

Role (max 40), Location (max 35, eligibility-driven), Seniority (±), Internship
(+10). Every stored row keeps `score_reasons` as JSON. A role with no
target-role match and no internship signal is capped at `irrelevant_cap`.

## Tests

```bash
python -m unittest discover -s tests
```

Covers normalization, dedup, scoring, eligibility (Mumbai vs Bengaluru,
India-remote, ambiguous remote), seniority (junior/graduate/intermediate/
senior/lead/review), HTML→text (incl. malformed input), source failures,
Ashby/GH/Lever parsing, closure/change tracking, and cross-board duplicates.

## Limitations

- **Keyword matching is heuristic.** It is a filter/ranker, not a recruiter —
  always read the actual posting before applying.
- **"Remote" with no region is deliberately treated as needs_verification.**
  Many employers mean US-only; we do not guess. Verify with the employer.
- **Remote region detection is pattern-based.** Some boards phrase regions
  unconventionally and may be mis-bucketed (rarely confirmed when ambiguous).
- **Only Greenhouse, Lever and Ashby** are implemented. Workday/Jobvite/
  LinkedIn/Indeed are not public APIs and are out of scope.
- **Cross-board dedup collapses on company+title+location**, so two genuinely
  different roles with identical title at the same company/location are linked
  — acceptable for a personal tracker, noted for transparency.
- **Closure needs ≥2 successful scans**, so a board that is down for two runs
  does not (correctly) close roles — failed scans never count against a role.
- Feed availability changes over time: a failed source is logged and skipped
  without aborting the run.

## Data & privacy

Job data is stored locally in `data/jobs.db` only. No data leaves your machine
except the outbound HTTPS requests to the public job-board APIs.
