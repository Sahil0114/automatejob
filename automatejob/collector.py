"""Orchestrates source adapters, eligibility filtering, scoring, and storage."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .companies import CompanyRegistry
from .config import AppConfig
from .eligibility import EXCLUDED, classify_eligibility
from .models import Job
from .scoring import Scorer
from .seniority import classify_seniority
from .sources import build_source
from .sources.base import SourceError
from .storage import Storage

log = logging.getLogger("automatejob")


class Collector:
    def __init__(self, config: AppConfig, storage: Storage, registry: Optional[CompanyRegistry] = None):
        self.config = config
        self.storage = storage
        self.registry = registry
        self.scorer = Scorer(config)

    def build_sources(self):
        sources = []
        for sc in self.config.sources:
            try:
                sources.append(build_source(sc.type, sc.token, sc.name, http=None))
            except ValueError as exc:
                log.warning("skipping source %r: %s", sc.token, exc)
        return sources

    def collect(self) -> Tuple[List[Job], List[dict]]:
        """Fetch, hard-filter, score and store jobs from every source.

        Returns (newly_discovered_jobs, errors). A per-source failure is
        recorded and skipped; it never aborts the whole run.
        """
        all_new: List[Job] = []
        errors: List[dict] = []
        for source in self.build_sources():
            label = f"{source.source_type}:{source.token}"
            try:
                jobs = source.fetch()
                seen_dedup_keys: set = set()
                scored = 0
                excluded = 0
                for job in jobs:
                    res = classify_eligibility(job, self.config, self.registry)
                    job.eligibility = res.status
                    job.eligibility_reason = res.reason
                    job.seniority = classify_seniority(job)
                    if res.status == EXCLUDED:
                        job.score = 0.0
                        job.score_reasons = [{
                            "factor": "eligibility", "points": 0.0,
                            "detail": f"excluded: {res.reason}",
                        }]
                        excluded += 1
                    else:
                        self.scorer.score(job)
                        scored += 1
                    seen_dedup_keys.add(job.dedup_key)

                new_jobs = self.storage.upsert_jobs(jobs)
                # Closure detection runs only on a successful scan of this source.
                self.storage.mark_absent(
                    source.source_type, source.token,
                    seen_dedup_keys, self.config.close_after_scans,
                )
                log.info("%s: fetched %d (scored %d, excluded %d), %d newly discovered",
                         label, len(jobs), scored, excluded, len(new_jobs))
                all_new.extend(new_jobs)
            except SourceError as exc:
                log.error("%s: %s", label, exc)
                errors.append({"source": label, "error": str(exc)})
            except Exception as exc:  # unexpected: keep going, but report
                log.exception("%s: unexpected error", label)
                errors.append({"source": label, "error": f"unexpected: {exc!r}"})
        all_new.sort(key=lambda j: j.score, reverse=True)
        return all_new, errors

    def rescore(self) -> int:
        """Re-score every stored row against the current config, in place.

        Used when scoring weights/roles/eligibility change: updates existing
        records without re-fetching anything.
        """
        updated = 0
        for row in self.storage.iter_jobs():
            job = self._row_to_job(row)
            res = classify_eligibility(job, self.config, self.registry)
            job.eligibility = res.status
            job.eligibility_reason = res.reason
            job.seniority = classify_seniority(job)
            if res.status == EXCLUDED:
                job.score = 0.0
                job.score_reasons = [{"factor": "eligibility", "points": 0.0,
                                      "detail": f"excluded: {res.reason}"}]
            else:
                self.scorer.score(job)
            self.storage.update_score(row["id"], job)
            updated += 1
        self.storage.conn.commit()
        return updated

    @staticmethod
    def _row_to_job(row: dict) -> Job:
        import json as _json
        try:
            secondary = _json.loads(row.get("secondary_locations") or "[]")
        except (ValueError, TypeError):
            secondary = []
        return Job(
            source_type=row["source_type"],
            source_token=row["source_token"],
            external_id=row["external_id"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            url=row["url"],
            apply_url=row.get("apply_url") or "",
            workplace_type=row.get("workplace_type") or "",
            employment_type=row.get("employment_type") or "",
            posted_at=row.get("posted_at") or "",
            description=row.get("description") or "",
            secondary_locations=secondary,
        )
