"""Persistent agent state, stored as plain files on the repo's `state` branch.

    seen.json        every job the agent has decided on (sent or rejected), by dedup key
    feedback.jsonl   one line per 👍 / 👎 / ✅ tap or "why?" reply, in order
    company-boards.json  ATS board found for each company seen in email/web jobs (fetched every run)
    telegram.json    getUpdates offset, open "why?" prompts, last error-alert date per source

Files are written with sorted keys so the hourly commits diff cleanly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jobagent.models import Job, job_ids, same_job


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.seen: dict[str, dict] = self._load("seen.json", {})
        self.telegram: dict = self._load("telegram.json", {"offset": 0, "why_prompts": {}, "alerts": {}})
        self.boards: dict = self._load("company-boards.json", {})

    def _load(self, name: str, default):
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def _dump(self, name: str, data) -> None:
        text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (self.root / name).write_text(text, encoding="utf-8")

    def save(self) -> None:
        self._dump("seen.json", self.seen)
        self._dump("telegram.json", self.telegram)
        self._dump("company-boards.json", self.boards)

    @property
    def is_first_run(self) -> bool:
        return not self.seen

    # --- seen jobs -------------------------------------------------------

    def has_seen(self, key: str) -> bool:
        return key in self.seen

    def find_duplicate(self, job: Job, extra_ids: set[str] | None = None, ids_only: bool = False) -> tuple[str, dict] | None:
        """The seen record for this same job, found by posting id, else by exact key, else by close company + title.

        `extra_ids` are ids from other pages of the job (e.g. the company page of a LinkedIn job).
        `ids_only` skips the title matching, for checks that must never hide a different job.
        """
        ids = job.ids | (extra_ids or set())
        for key, record in self.seen.items():
            record_ids = set(record.get("ids") or []) | job_ids(record.get("url", ""))
            if ids & record_ids:
                return key, record
        if ids_only:
            return None
        if job.key in self.seen:
            return job.key, self.seen[job.key]
        for key, record in self.seen.items():
            if same_job(job.company, job.title, set(), record.get("company", ""), record.get("title", ""), set()):
                return key, record
        return None

    def mark(self, job: Job, status: str, extra_ids: set[str] | None = None, **details) -> None:
        """status: 'sent' or 'rejected'. Later feedback may set it to interested/skipped/applied."""
        record = self.seen.setdefault(job.key, {"first_seen": _now()})
        ids = set(record.get("ids") or []) | job.ids | (extra_ids or set())
        record.update(id=job.id, company=job.company, title=job.title, url=job.url, status=status, ids=sorted(ids), **details)

    def find_by_id(self, job_id: str) -> tuple[str, dict] | None:
        for key, record in self.seen.items():
            if record.get("id") == job_id:
                return key, record
        return None

    # --- feedback --------------------------------------------------------

    def add_feedback(self, job_id: str, action: str, reason: str | None = None) -> dict:
        """action: 'interested', 'skipped', 'applied' or 'reason' (a reply to "why?")."""
        event = {"ts": _now(), "id": job_id, "action": action}
        found = self.find_by_id(job_id)
        if found:
            key, record = found
            event.update(company=record["company"], title=record["title"])
            if action != "reason":
                record["status"] = action
        if reason:
            event["reason"] = reason
        with open(self.root / "feedback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    # --- error alerts ----------------------------------------------------

    def should_alert(self, source: str, today: str) -> bool:
        """At most one alert per source per day. Records the alert when it returns True."""
        if self.telegram["alerts"].get(source) == today:
            return False
        self.telegram["alerts"][source] = today
        return True
