"""Referrers from a shared Google Sheet (company, contact name, note, link to request the contact).

Community referral sheets are common in the Israeli tech scene. Set JOBAGENT_REFERRER_SHEET to the sheet's id to turn
this on. Contact names are usually links, so the export has to be the xlsx (CSV drops links). `refresh` stores the
entries in work/referrers.json; an optional committed snapshot (config/referrers.json) is used when the live sheet
can't be reached.
"""

from __future__ import annotations

import html
import io
import os
import re
from typing import Callable

import requests

from jobagent.models import _COMPANY_SUFFIXES

SHEET_ENV = "JOBAGENT_REFERRER_SHEET"
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{}/export?format=xlsx"
MAX_SHOWN = 3
_HYPERLINK = re.compile(r'^=HYPERLINK\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)$', re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def http_get_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=40, headers={"User-Agent": "job-hunt-agent/1.0"})
    response.raise_for_status()
    return response.content


def _text_and_link(cell) -> tuple[str, str]:
    """A cell's text and link: either a real hyperlink or a =HYPERLINK("url","label") formula."""
    value = str(cell.value or "").strip()
    if formula := _HYPERLINK.match(value):
        return formula.group(2).strip(), formula.group(1)
    return value, cell.hyperlink.target if cell.hyperlink and cell.hyperlink.target else ""


def parse_sheet(data: bytes) -> list[dict]:
    import openpyxl

    sheet = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    entries = []
    for company, contact, note in sheet.iter_rows(min_col=1, max_col=3):
        name, _ = _text_and_link(company)
        who, link = _text_and_link(contact)
        if not name or not who or (name, who) == ("Company", "Contact") or name.startswith("Request to add"):
            continue
        entries.append({
            "company": name,
            "contact": who,
            "note": (note.value or "").strip(),
            "url": link,
        })
    return entries


def sheet_id() -> str:
    return os.environ.get(SHEET_ENV, "").strip()


def refresh(get_bytes: Callable[[str], bytes] = http_get_bytes, sheet: str | None = None) -> list[dict] | None:
    """Today's entries from the sheet, or None when no sheet is configured."""
    sheet = sheet or sheet_id()
    if not sheet:
        return None
    entries = parse_sheet(get_bytes(EXPORT_URL.format(sheet)))
    if not entries:
        raise ValueError("no referrers found in the sheet")
    return entries


def _norm(name: str) -> str:
    name = _COMPANY_SUFFIXES.sub(" ", name.lower())
    return re.sub(r"[\W_]+", "", name)


def aliases(company: str) -> set[str]:
    """'Alice (formerly Activefence)' -> alice, activefence; '888 / Evoke' -> 888, evoke."""
    parts = re.split(r"[/()]|\bformerly\b|\baka\b", company, flags=re.IGNORECASE)
    return {n for p in parts if (n := _norm(p))}


def matches(job_company: str, sheet_company: str) -> bool:
    job = _norm(job_company)
    if not job:
        return False
    for alias in aliases(sheet_company):
        if alias == job:
            return True
        # "Check Point" vs "Check Point Software": one name starts with the other (long enough not to be a coincidence)
        short, long_ = sorted((alias, job), key=len)
        if len(short) >= 5 and long_.startswith(short):
            return True
    return False


def find(entries: list[dict], company: str) -> list[dict]:
    return [e for e in entries if matches(company, e["company"])]


def line(found: list[dict] | None) -> str:
    """The caption line. found=None means the sheet could not be read at all."""
    e = html.escape
    if found is None:
        return "🤝 Referrer: couldn't check the referrer list this run"
    if not found:
        return "🤝 Referrer: none found for this company"
    shown = []
    for r in found[:MAX_SHOWN]:
        email = _EMAIL.search(r["note"])
        if email:
            shown.append(f"{e(r['contact'])} (email {e(email.group())} with the role link + CV)")
        elif r["url"]:
            shown.append(f'<a href="{e(r["url"], quote=True)}">{e(r["contact"])}</a>')
        else:
            shown.append(e(r["contact"]))
    more = f" +{len(found) - MAX_SHOWN} more" if len(found) > MAX_SHOWN else ""
    return f"🤝 Referrer: {', '.join(shown)}{more}"
