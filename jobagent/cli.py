"""Command line the routine drives each run. See ROUTINE.md for the run order.

    python -m jobagent poll                 record 👍/👎/✅ taps and "why?" replies
    python -m jobagent fetch                fetch ATS boards -> work/candidates.json
    python -m jobagent ingest jobs.json     add jobs found in Gmail alerts / web search
    python -m jobagent next                 next batch of candidates, full ads in work/batch.json
    python -m jobagent decide decisions.json  send (render + validate + Telegram) or reject batch jobs
    python -m jobagent alert SOURCE MESSAGE   error alert, at most once per source per day
    python -m jobagent render CV.yaml ...     render CV YAML files to PDF
    python -m jobagent validate CV.yaml VARIANT
    python -m jobagent discover NAME ...      find a company's Greenhouse/Lever/Ashby board
    python -m jobagent setup-telegram         find your chat ID and send a test message (asks for the token)

Every command prints a JSON summary to stdout. Set JOBAGENT_DRY_RUN=<dir> (or
pass --dry-run DIR) to write Telegram messages to a folder instead of sending.
"""

from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from jobagent import cv, referrers, sources, telegram
from jobagent.filters import location_status, prefilter
from jobagent.models import Job, job_ids, same_job, similar_titles
from jobagent.state import State

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "config" / "companies.yaml"
REFERRERS_SNAPSHOT = ROOT / "config" / "referrers.json"
SEND_THRESHOLD = 55
SENIOR_SOFTWARE_THRESHOLD = 65
BATCH_SIZE = 12  # jobs per batch; the routine loops batches until every candidate is decided
MIN_REASON_CHARS = 25
BOARD_RETRY_DAYS = 7  # how long a company with no guessable ATS board is left alone


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _read_json(path: Path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class App:
    def __init__(self, state_dir: Path, work_dir: Path, dry_run: Path | None, now: datetime | None = None):
        self.state = State(state_dir)
        self.work = Path(work_dir)
        self.dry_run = dry_run
        self.now = now or datetime.now(timezone.utc)
        self._client = None
        self._board_jobs: dict[str, list[Job]] = {}

    @property
    def candidates_path(self) -> Path:
        return self.work / "candidates.json"

    @property
    def referrers_path(self) -> Path:
        return self.work / "referrers.json"

    @property
    def client(self):
        if self._client is None:
            if self.dry_run:
                self._client = telegram.DryRunClient(self.dry_run)
            else:
                token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
                if not token or not chat_id:
                    raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (or use --dry-run DIR)")
                self._client = telegram.TelegramClient(token, chat_id)
        return self._client

    # --- intake ----------------------------------------------------------

    def _admit(self, jobs: list[Job], max_age_days: int | None) -> dict:
        """Pre-filter + dedup jobs and append the survivors to work/candidates.json."""
        candidates = _read_json(self.candidates_path, [])
        added, dropped = 0, {}
        for job in jobs:
            if _is_aggregator(job.source):
                job = self.resolve_official(job)
            if self.state.find_duplicate(job):
                dropped["already seen"] = dropped.get("already seen", 0) + 1
                continue
            twin = next((i for i, c in enumerate(candidates) if _same_candidate(job, c)), None)
            if twin is not None:
                # Same job found twice this run: keep the company's own board listing, not the email/web copy.
                if _is_aggregator(candidates[twin]["source"]) and not _is_aggregator(job.source):
                    verdict = prefilter(job, self.now, max_age_days)
                    if verdict.keep:
                        candidates[twin] = {**job.to_dict(), "family": verdict.family, "flags": verdict.flags}
                dropped["already seen"] = dropped.get("already seen", 0) + 1
                continue
            verdict = prefilter(job, self.now, max_age_days)
            if not verdict.keep:
                dropped[verdict.reason] = dropped.get(verdict.reason, 0) + 1
                continue
            candidates.append({**job.to_dict(), "family": verdict.family, "flags": verdict.flags})
            added += 1
        _write_json(self.candidates_path, candidates)
        return {"added": added, "dropped": dropped, "pending_total": len(candidates)}

    def fetch(self, max_age_days: int | None = None) -> dict:
        """Board jobs have no age limit by default: if the company still lists a job, it's open."""
        companies = yaml.safe_load(COMPANIES.read_text(encoding="utf-8")) or []
        known = {(c["ats"], c["board"]) for c in companies}
        companies += [b for b in self.state.boards.values() if b.get("board") and (b["ats"], b["board"]) not in known]
        jobs, errors = [], {}
        for entry in companies:
            try:
                jobs += sources.fetch_company(entry)
            except Exception as exc:  # one broken board must not stop the run
                errors[f"{entry['ats']}:{entry['board']}"] = f"{type(exc).__name__}: {exc}"[:200]
        try:
            entries = referrers.refresh()  # None when no referrer sheet is configured
            if entries is not None:
                _write_json(self.referrers_path, entries)
        except Exception as exc:  # the committed snapshot is used instead
            errors["referrers"] = f"{type(exc).__name__}: {exc}"[:200]
        summary = {"fetched": len(jobs), "boards": len(companies), **self._admit(jobs, max_age_days)}
        if errors:
            summary["errors"] = errors
            new = [f"{source}: {error}" for source, error in errors.items() if self._should_alert(source)]
            if new:
                self._send_alert("Some job boards failed to load:\n" + "\n".join(new))
        self.state.save()
        return summary

    def ingest(self, path: Path, max_age_days: int) -> dict:
        jobs = [Job.from_dict(item) for item in _read_json(path, [])]
        summary = self._admit(jobs, max_age_days)
        self.state.save()
        return summary

    # --- decisions -------------------------------------------------------

    @property
    def batch_path(self) -> Path:
        return self.work / "batch.json"

    def next_batch(self, limit: int = BATCH_SIZE) -> dict:
        """Hand out the next `limit` candidates, full ads included, in work/batch.json.

        Only jobs in the current batch may be decided, so every decision is made
        on an ad the routine was actually given. The routine calls this again
        until it returns no jobs. DevOps-family, non-senior and newest jobs come first.
        """
        candidates = _read_json(self.candidates_path, [])
        candidates.sort(key=lambda c: c.get("posted_at") or "", reverse=True)
        candidates.sort(key=lambda c: (c["family"] != "devops", "senior" in c["flags"]))
        batch = candidates[:limit]
        _write_json(self.batch_path, batch)
        return {
            "batch_file": str(self.batch_path),
            "jobs": [{"id": c["id"], "company": c["company"], "title": c["title"], "flags": c["flags"]} for c in batch],
            "left_after_this_batch": len(candidates) - len(batch),
        }

    def decide(self, path: Path) -> dict:
        candidates = {c["id"]: c for c in _read_json(self.candidates_path, [])}
        batch = {c["id"] for c in _read_json(self.batch_path, [])}
        results = []
        for decision in _read_json(path, []):
            problem = _decision_problem(decision, batch)
            if problem:
                results.append({"id": decision.get("id"), "result": "error", "detail": problem})
                continue
            candidate = candidates.pop(decision["id"], None)
            if not candidate:
                results.append({"id": decision["id"], "result": "error", "detail": "not in work/candidates.json"})
                continue
            job = Job.from_dict(candidate)
            try:
                results.append({"id": job.id, "title": job.title, **self._apply(job, candidate, decision)})
            except Exception as exc:
                candidates[job.id] = candidate  # keep it pending so the next run can retry
                results.append({"id": job.id, "title": job.title, "result": "error", "detail": str(exc)[:300]})
        _write_json(self.candidates_path, list(candidates.values()))
        self.state.save()
        return {"results": results, "pending_total": len(candidates)}

    def _apply(self, job: Job, candidate: dict, decision: dict) -> dict:
        rate = int(decision.get("match_rate", 0))
        variant = decision.get("variant", "")
        threshold = SENIOR_SOFTWARE_THRESHOLD if variant == "software" and "senior" in candidate["flags"] else SEND_THRESHOLD
        details = {"match_rate": rate, "variant": variant, "reason": decision.get("reason", "")}

        if not decision.get("send") or rate < threshold:
            note = "" if not decision.get("send") else f"match rate below the {threshold}% threshold"
            self.state.mark(job, "rejected", **details)
            return {"result": "rejected", **({"detail": note} if note else {})}

        company_url = (decision.get("company_url") or "").strip()
        if not isinstance(decision.get("verified"), bool):
            raise ValueError("a job to send needs an explicit 'verified': true/false (is the posting still open?)")
        if company_url and (not company_url.startswith("http") or telegram.is_job_board(company_url)):
            raise ValueError("'company_url' must be the job on the company's own site or its ATS (Greenhouse/Lever/Ashby/Comeet), not a job board")
        if not company_url and (_is_aggregator(job.source) or telegram.is_job_board(job.url)) \
                and decision.get("official_link_searched") is not True:
            raise ValueError("this job came from an email/web job board: web-search the company's careers page or ATS and set "
                             "'company_url' (or, if it truly isn't there, 'company_url': null with 'official_link_searched': true)")
        # The company-site page reveals the posting's real id: if it is a job we already handled, don't send it again.
        extra_ids = job_ids(company_url) if not telegram.is_job_board(company_url) else set()
        if extra_ids and (twin := self.state.find_duplicate(job, extra_ids, ids_only=True)):
            self.state.mark(job, "rejected", extra_ids=extra_ids, **{**details, "reason": f"duplicate of {twin[1].get('title')} ({twin[1].get('status')})"})
            return {"result": "duplicate", "detail": f"same posting as '{twin[1].get('title')}' already {twin[1].get('status')}"}

        tailored_path = Path(decision["cv"])
        errors = cv.validate_tailored(cv.load(tailored_path), variant)
        if errors:
            raise ValueError("tailored CV failed validation: " + "; ".join(errors))
        pdf = self.work / "out" / cv.pdf_name(job.company, job.title)
        if cv.render(tailored_path, pdf) > 1:
            raise ValueError("tailored CV is longer than one page")
        caption = telegram.job_caption(job, decision, self._referrer_line(job.company))
        self.client.send_document(pdf, caption, telegram.job_keyboard(job.id))
        self.state.mark(job, "sent", extra_ids=extra_ids, **details)
        return {"result": "sent", "pdf": str(pdf)}

    def resolve_official(self, job: Job) -> Job:
        """Swap an email/web job for the same job on the company's own ATS board, so it carries the official link.

        Boards are guessed from the company name (Greenhouse/Lever/Ashby) and remembered in company-boards.json,
        which `fetch` then reads every run. A miss is re-tried after BOARD_RETRY_DAYS.
        """
        key = re.sub(r"[^a-z0-9]", "", job.company.lower())
        if not key:
            return job
        entry = self.state.boards.get(key)
        if entry is None or (not entry.get("board") and not self._fresh(entry)):
            try:
                hits = discover([job.company])
            except Exception:
                return job
            hit = next((h for h in hits if h["central_israel"] > 0), None)
            entry = {"name": job.company, "ats": hit["ats"], "board": hit["board"]} if hit else \
                {"name": job.company, "checked": self.now.date().isoformat()}
            self.state.boards[key] = entry
        if not entry.get("board"):
            return job
        board = f"{entry['ats']}:{entry['board']}"
        if board not in self._board_jobs:
            try:
                self._board_jobs[board] = sources.fetch_company(entry)
            except Exception:
                self._board_jobs[board] = []
        for candidate in self._board_jobs[board]:
            if similar_titles(job.title, candidate.title):
                return candidate
        return job

    def _fresh(self, entry: dict) -> bool:
        checked = entry.get("checked")
        return bool(checked) and (self.now.date() - datetime.fromisoformat(checked).date()).days < BOARD_RETRY_DAYS

    def _referrer_line(self, company: str) -> str:
        """Today's sheet from this run, else the committed snapshot, else an honest "couldn't check".

        With no referrer sheet configured and no snapshot, the message simply has no referrer line."""
        for path in (self.referrers_path, REFERRERS_SNAPSHOT):
            entries = _read_json(path, None)
            if entries:
                return referrers.line(referrers.find(entries, company))
        return referrers.line(None) if referrers.sheet_id() else ""

    # --- feedback and alerts ---------------------------------------------

    def poll(self) -> dict:
        events = telegram.process_updates(self.client, self.state)
        self.state.save()
        return {"new_feedback": events}

    def _should_alert(self, source: str) -> bool:
        return self.state.should_alert(source, self.now.date().isoformat())

    def _send_alert(self, text: str) -> None:
        self.client.send_text("⚠️ <b>Job agent warning</b>\n" + html.escape(text))

    def alert(self, source: str, message: str) -> dict:
        sent = self._should_alert(source)
        if sent:
            self._send_alert(f"{source}: {message}")
        self.state.save()
        return {"sent": sent}


def _is_aggregator(source: str) -> bool:
    return source.startswith("gmail:") or source == "web"


def _same_candidate(job: Job, candidate: dict) -> bool:
    return same_job(job.company, job.title, job.ids, candidate["company"], candidate["title"], job_ids(candidate["url"]))


def _decision_problem(decision: dict, batch: set[str]) -> str | None:
    """Why a decision can't be accepted, or None. Rejected decisions leave the job pending."""
    if decision.get("id") not in batch:
        return "not in the current batch: run `python -m jobagent next` and decide only those jobs"
    if not isinstance(decision.get("send"), bool) or not isinstance(decision.get("match_rate"), int):
        return "needs 'send' (true/false) and an integer 'match_rate'"
    if len(decision.get("reason", "").strip()) < MIN_REASON_CHARS:
        return f"needs a specific 'reason' (at least {MIN_REASON_CHARS} characters) based on the full ad"
    return None


COMEET_URL = re.compile(r"comeet\.com/jobs/([^/?#\s]+/[0-9A-Za-z]{2}\.[0-9A-Za-z]{3})", re.IGNORECASE)


def discover(names: list[str], get_json=sources.http_get_json, get_text=sources.http_get_text) -> list[dict]:
    """Try likely board names on each ATS and report which ones exist, with central-Israel job counts.

    A Comeet career page URL (comeet.com/jobs/<slug>/<uid>/...) is read directly, since its uid can't be guessed.
    """

    def probe(name: str) -> list[dict]:
        if comeet := COMEET_URL.search(name):
            board = comeet.group(1)
            try:
                jobs = sources.fetch_comeet(board, board.split("/")[0], get_text)
            except Exception:
                return []
            central = sum(location_status(j.location, j.remote) == "ok" for j in jobs)
            return [{"name": board.split("/")[0], "ats": "comeet", "board": board, "jobs": len(jobs), "central_israel": central}]
        base = re.sub(r"[^a-z0-9]", "", name.lower())
        slugs = dict.fromkeys([base, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), base + "hq", base + "inc"])
        hits = []
        for ats, fetch in sources.FETCHERS.items():
            for slug in slugs:
                try:
                    jobs = fetch(slug, name, get_json)
                except Exception:
                    continue
                if jobs:
                    central = sum(location_status(j.location, j.remote) == "ok" for j in jobs)
                    hits.append({"name": name, "ats": ats, "board": slug, "jobs": len(jobs), "central_israel": central})
                    break
        return hits

    with ThreadPoolExecutor(max_workers=12) as pool:
        return [hit for hits in pool.map(probe, names) for hit in hits]


def setup_telegram() -> int:
    """Find the candidate's private chat with the bot and send a test message. Run it yourself in a terminal."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or getpass.getpass("Paste the bot token from @BotFather (hidden): ")
    client = telegram.TelegramClient(token.strip(), "")
    try:
        bot = client.get_me()
    except RuntimeError as exc:
        _print({"ok": False, "error": f"Telegram rejected the token: {exc}"})
        return 1
    chats = {}
    for update in client.get_updates(0):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            chats[str(chat["id"])] = chat.get("username") or chat.get("first_name", "")
    if len(chats) != 1:
        hint = f"Open t.me/{bot['username']}, send /start, then run this again." if not chats else (
            "More than one person messaged the bot. Pick your chat ID from the list.")
        _print({"ok": False, "bot": bot["username"], "chats": chats, "hint": hint})
        return 1
    [(chat_id, name)] = chats.items()
    telegram.TelegramClient(token.strip(), chat_id).send_text("✅ <b>Job agent connected.</b> Job matches will arrive here.")
    _print({"ok": True, "bot": bot["username"], "chat": name, "TELEGRAM_CHAT_ID": chat_id,
            "next": "A test message was sent to your Telegram. Keep this chat ID for the cloud environment."})
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="jobagent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("JOBAGENT_STATE_DIR", ROOT / "state")))
    parser.add_argument("--work-dir", type=Path, default=Path(os.environ.get("JOBAGENT_WORK_DIR", ROOT / "work")))
    parser.add_argument("--dry-run", type=Path, default=os.environ.get("JOBAGENT_DRY_RUN") or None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("poll")
    p = sub.add_parser("fetch")
    p.add_argument("--max-age-days", type=int, default=None, help="default: no limit (listed jobs are open)")
    p = sub.add_parser("ingest")
    p.add_argument("file", type=Path)
    p.add_argument("--max-age-days", type=int, default=14)
    p = sub.add_parser("next")
    p.add_argument("--limit", type=int, default=BATCH_SIZE)
    p = sub.add_parser("decide")
    p.add_argument("file", type=Path)
    p = sub.add_parser("alert")
    p.add_argument("source")
    p.add_argument("message")
    p = sub.add_parser("render")
    p.add_argument("cvs", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "cv" / "out")
    p = sub.add_parser("validate")
    p.add_argument("cv", type=Path)
    p.add_argument("variant")
    p = sub.add_parser("discover")
    p.add_argument("names", nargs="+")
    sub.add_parser("setup-telegram")
    args = parser.parse_args(argv)

    if args.command == "render":
        pages = {str(f): cv.render(f, args.out / (f.stem + ".pdf")) for f in args.cvs}
        _print({"pages": pages})
        return 1 if any(n > 1 for n in pages.values()) else 0
    if args.command == "validate":
        errors = cv.validate_tailored(cv.load(args.cv), args.variant)
        _print({"ok": not errors, "errors": errors})
        return 1 if errors else 0
    if args.command == "discover":
        _print(discover(args.names))
        return 0
    if args.command == "setup-telegram":
        return setup_telegram()

    app = App(args.state_dir, args.work_dir, args.dry_run)
    if args.command == "poll":
        _print(app.poll())
    elif args.command == "fetch":
        _print(app.fetch(args.max_age_days))
    elif args.command == "ingest":
        _print(app.ingest(args.file, args.max_age_days))
    elif args.command == "next":
        _print(app.next_batch(args.limit))
    elif args.command == "decide":
        result = app.decide(args.file)
        _print(result)
        return 1 if any(r["result"] == "error" for r in result["results"]) else 0
    elif args.command == "alert":
        _print(app.alert(args.source, args.message))
    return 0
