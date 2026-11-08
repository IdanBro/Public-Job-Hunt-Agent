"""Telegram delivery and feedback.

One message per job: the tailored CV as a document, the details as an HTML
caption, and 👍 / 👎 / ✅ buttons. Taps are read with getUpdates at the start of
each run. Only TELEGRAM_CHAT_ID is ever written to, and updates from any other
chat are ignored.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from urllib.parse import urlparse
from pathlib import Path

import requests

from jobagent.models import Job
from jobagent.state import State

CAPTION_LIMIT = 1024  # Telegram's limit for document captions
ACTIONS = {"up": "interested", "down": "skipped", "applied": "applied"}
VARIANT_NAMES = {"devops": "DevOps", "software": "Software"}
CONFIRMATIONS = {"interested": "Saved: interested 👍", "skipped": "Saved: skip 👎", "applied": "Saved: applied ✅"}


def job_keyboard(job_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "👍 Interested", "callback_data": f"up:{job_id}"},
                {"text": "👎 Skip", "callback_data": f"down:{job_id}"},
                {"text": "✅ Applied", "callback_data": f"applied:{job_id}"},
            ]
        ]
    }


JOB_BOARD_HOSTS = ("linkedin.com", "glassdoor.", "indeed.", "alljobs.co.il", "drushim.co.il", "jobmaster.co.il", "ziprecruiter.")


def is_job_board(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in JOB_BOARD_HOSTS)


def job_link(job: Job, decision: dict) -> str | None:
    """The job's link on the company's own site or official ATS, or None.

    Prefers the decision's `company_url`, then the job's URL when it isn't a job board.
    A job-board link (LinkedIn etc.) is never returned.
    """
    company_url = (decision.get("company_url") or "").strip()
    if company_url.startswith("http") and not is_job_board(company_url):
        return company_url
    if job.url.startswith("http") and not is_job_board(job.url):
        return job.url
    return None


def seen_on(job: Job) -> str:
    """Plain-text name of the job board the job was found on ('' for the company's own board)."""
    if job.source.startswith("gmail:"):
        return job.source.split(":", 1)[1].capitalize()
    host = urlparse(job.url).netloc.lower()
    for h in JOB_BOARD_HOSTS:
        if h in host:
            return h.strip(".").split(".")[0].capitalize()
    return ""


def visible_length(caption: str) -> int:
    """Telegram's caption limit counts the text a reader sees, not the tags or link targets."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", caption)))


def job_caption(job: Job, decision: dict, referrer_line: str = "") -> str:
    """HTML caption, trimmed to fit Telegram's caption limit."""
    e = html.escape

    def build(reason: str, gaps: str) -> str:
        lines = [
            f"<b>{e(job.title)}</b> · {e(job.company)}",
            f"📍 {e(job.location or 'location not stated')}",
            f"🎯 Match: <b>{decision['match_rate']}%</b> · {e(reason)}",
        ]
        if gaps:
            lines.append(f"⚠️ Gaps: {e(gaps)}")
        variant = VARIANT_NAMES.get(decision["variant"], decision["variant"].capitalize())
        lines.append(f"📄 Based on: {e(variant)} CV")
        url = job_link(job, decision)
        board = seen_on(job)
        via = f" (found via {e(board)})" if board else ""
        if url:
            link = f'🔗 <a href="{e(url, quote=True)}">Open the job</a>{via}'
        else:
            link = f"🔗 No official company link found{via}: search the company's careers page"
        if url and not decision.get("verified", True):
            link += " (unverified: couldn't confirm it's still open)"
        lines.append(link)
        if referrer_line:
            lines.append(referrer_line)
        return "\n".join(lines)

    # Trim the free-text parts (gaps first, then reason) so the tags and link survive.
    reason, gaps = decision["reason"], "; ".join(decision.get("gaps") or [])
    caption = build(reason, gaps)
    while visible_length(caption) > CAPTION_LIMIT and (gaps or reason):
        overflow = visible_length(caption) - CAPTION_LIMIT + 1
        if gaps:
            gaps = gaps[:-overflow].rstrip() + "…" if len(gaps) > overflow + 1 else ""
        else:
            reason = reason[:-overflow].rstrip() + "…" if len(reason) > overflow + 1 else ""
        caption = build(reason, gaps)
    return caption


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)

    def _call(self, method: str, files=None, **params) -> dict:
        if files:
            data = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}
            response = requests.post(f"{self.base}/{method}", data=data, files=files, timeout=60)
        else:
            response = requests.post(f"{self.base}/{method}", json=params, timeout=60)
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description', response.status_code)}")
        return body["result"]

    def send_document(self, pdf: Path, caption: str, reply_markup: dict) -> int:
        with open(pdf, "rb") as f:
            result = self._call(
                "sendDocument",
                files={"document": (pdf.name, f, "application/pdf")},
                chat_id=self.chat_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        return result["message_id"]

    def send_text(self, text: str, force_reply: bool = False) -> int:
        params = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if force_reply:
            params["reply_markup"] = {"force_reply": True}
        return self._call("sendMessage", **params)["message_id"]

    def get_updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])

    def get_me(self) -> dict:
        return self._call("getMe")

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)


class DryRunClient:
    """Writes what would be sent into a folder instead of calling Telegram."""

    def __init__(self, out_dir: Path, updates: list[dict] | None = None):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.chat_id = "dry-run"
        self.updates = updates or []
        self._count = len(list(self.out_dir.glob("*.md")))

    def _write(self, text: str) -> int:
        self._count += 1
        (self.out_dir / f"{self._count:03d}.md").write_text(text, encoding="utf-8")
        return self._count

    def send_document(self, pdf: Path, caption: str, reply_markup: dict) -> int:
        shutil.copy(pdf, self.out_dir / pdf.name)
        buttons = " | ".join(b["text"] for b in reply_markup["inline_keyboard"][0])
        return self._write(f"{caption}\n\n[attachment: {pdf.name}]\n[buttons: {buttons}]\n")

    def send_text(self, text: str, force_reply: bool = False) -> int:
        return self._write(text + ("\n\n[reply requested]" if force_reply else "") + "\n")

    def get_updates(self, offset: int) -> list[dict]:
        return [u for u in self.updates if u["update_id"] >= offset]

    def answer_callback(self, callback_id: str, text: str) -> None:
        pass


def process_updates(client, state: State) -> list[dict]:
    """Record button taps and "why?" replies from our chat. Returns the new feedback events."""
    events = []
    updates = client.get_updates(state.telegram["offset"])
    for update in updates:
        state.telegram["offset"] = update["update_id"] + 1

        callback = update.get("callback_query")
        if callback:
            if str(callback["message"]["chat"]["id"]) != client.chat_id:
                continue
            action_code, _, job_id = callback.get("data", "").partition(":")
            action = ACTIONS.get(action_code)
            if not action:
                continue
            events.append(state.add_feedback(job_id, action))
            client.answer_callback(callback["id"], CONFIRMATIONS[action])
            if action == "skipped":
                found = state.find_by_id(job_id)
                title = html.escape(found[1]["title"]) if found else "this job"
                prompt_id = client.send_text(f"Why skip <b>{title}</b>? Reply to this message (optional).", True)
                state.telegram["why_prompts"][str(prompt_id)] = job_id
            continue

        message = update.get("message")
        if not message or str(message["chat"]["id"]) != client.chat_id:
            continue
        replied_to = str((message.get("reply_to_message") or {}).get("message_id", ""))
        job_id = state.telegram["why_prompts"].pop(replied_to, None)
        if job_id and message.get("text"):
            events.append(state.add_feedback(job_id, "reason", reason=message["text"]))
    return events
