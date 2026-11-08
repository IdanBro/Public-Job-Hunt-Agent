"""Fetchers for public ATS job boards (no auth needed).

Each fetcher takes the company's board name in that ATS and a `get_json(url)`
callable, so tests can feed recorded responses. Comeet's API token is
admin-only, but its public career pages (comeet.com/jobs/<slug>/<uid>) embed the
full job list as JSON, so `fetch_comeet` reads that from the page HTML instead.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Callable

import requests

from jobagent.models import Job, html_to_text

GetJson = Callable[[str], object]
GetText = Callable[[str], str]


def http_get_json(url: str) -> object:
    response = requests.get(url, timeout=25, headers={"User-Agent": "job-hunt-agent/1.0"})
    response.raise_for_status()
    return response.json()


def http_get_text(url: str) -> str:
    response = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 (job-hunt-agent/1.0)"})
    response.raise_for_status()
    return response.text


def fetch_greenhouse(board: str, company: str, get_json: GetJson) -> list[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    return [
        Job(
            source=f"greenhouse:{board}",
            company=company or j.get("company_name", board),
            title=j["title"],
            location=(j.get("location") or {}).get("name", ""),
            url=j["absolute_url"],
            # Greenhouse returns the description as escaped HTML.
            description=html_to_text(html.unescape(j.get("content") or "")),
            posted_at=j.get("first_published") or j.get("updated_at"),
        )
        for j in data["jobs"]
    ]


def fetch_lever(board: str, company: str, get_json: GetJson) -> list[Job]:
    jobs = []
    for p in get_json(f"https://api.lever.co/v0/postings/{board}?mode=json"):
        categories = p.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location", "")]
        sections = [p.get("descriptionPlain", "")]
        sections += [f"{item.get('text', '')}\n{html_to_text(item.get('content', ''))}" for item in p.get("lists") or []]
        sections.append(p.get("additionalPlain", ""))
        created = p.get("createdAt")
        jobs.append(
            Job(
                source=f"lever:{board}",
                company=company,
                title=p["text"],
                location="; ".join(loc for loc in locations if loc),
                url=p["hostedUrl"],
                description="\n".join(s for s in sections if s).strip(),
                posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
                remote=p.get("workplaceType") == "remote",
                employment=categories.get("commitment", ""),
            )
        )
    return jobs


def fetch_ashby(board: str, company: str, get_json: GetJson) -> list[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=false")
    jobs = []
    for j in data["jobs"]:
        if j.get("isListed") is False:
            continue
        locations = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        jobs.append(
            Job(
                source=f"ashby:{board}",
                company=company,
                title=j["title"],
                location="; ".join(loc for loc in locations if loc),
                url=j["jobUrl"],
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                posted_at=j.get("publishedAt"),
                remote=bool(j.get("isRemote")),
                employment=j.get("employmentType", ""),
            )
        )
    return jobs


COMEET_DATA = re.compile(r"COMPANY_POSITIONS_DATA\s*=\s*(\[.*?\]);\s*$", re.S | re.M)


def fetch_comeet(board: str, company: str, get_text: GetText) -> list[Job]:
    """board is "<slug>/<uid>" from the career page URL, e.g. "kaltura/E2.00D"."""
    match = COMEET_DATA.search(get_text(f"https://www.comeet.com/jobs/{board}"))
    if not match:
        raise ValueError("no COMPANY_POSITIONS_DATA on the Comeet page")
    jobs = []
    for p in json.loads(match.group(1)):
        if p.get("is_internal"):
            continue
        loc = p.get("location") or {}
        places = dict.fromkeys(x for x in (loc.get("name"), loc.get("city"), loc.get("country")) if x)
        details = (p.get("custom_fields") or {}).get("details") or []
        jobs.append(
            Job(
                source=f"comeet:{board}",
                company=company,
                title=p["name"],
                location="; ".join(places),
                url=p.get("url_comeet_hosted_page") or p.get("url_active_page") or "",
                description="\n".join(f"{d.get('name', '')}\n{html_to_text(d.get('value') or '')}" for d in details).strip(),
                posted_at=p.get("time_updated"),
                remote=bool(loc.get("is_remote")) or p.get("workplace_type") == "Remote",
                employment=p.get("employment_type") or "",
            )
        )
    return jobs


# ATSs whose boards `discover` can guess from a company name. Comeet needs the page's uid, so it isn't here.
FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def fetch_company(entry: dict, get_json: GetJson = http_get_json, get_text: GetText = http_get_text) -> list[Job]:
    """Fetch one entry of config/companies.yaml."""
    if entry["ats"] == "comeet":
        return fetch_comeet(entry["board"], entry["name"], get_text)
    return FETCHERS[entry["ats"]](entry["board"], entry["name"], get_json)
