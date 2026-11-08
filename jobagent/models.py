"""The job record every source produces, and the keys used to dedup it."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import asdict, dataclass, fields
from urllib.parse import parse_qs, urlparse

_COMPANY_SUFFIXES = re.compile(r"\b(inc|ltd|llc|corp|corporation|limited|gmbh|co)\b\.?", re.IGNORECASE)
_NON_WORD = re.compile(r"[^\w]+")


@dataclass
class Job:
    source: str  # e.g. "greenhouse:riskified", "gmail:linkedin", "web"
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    posted_at: str | None = None  # ISO 8601
    remote: bool = False
    employment: str = ""  # as reported by the source, e.g. "Full-time", "Contract"

    @property
    def key(self) -> str:
        return dedup_key(self.company, self.title)

    @property
    def id(self) -> str:
        return short_id(self.key)

    @property
    def ids(self) -> set[str]:
        return job_ids(self.url)

    def to_dict(self) -> dict:
        return {"id": self.id, "key": self.key, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Job:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and collapse whitespace. Hebrew letters survive."""
    return " ".join(_NON_WORD.sub(" ", text.lower()).replace("_", " ").split())


def dedup_key(company: str, title: str) -> str:
    """Same company + same normalized title = same job, whichever source found it."""
    return normalize(_COMPANY_SUFFIXES.sub(" ", company)) + "|" + normalize(title)


# --- same job, whichever source found it -----------------------------------
#
# Matching is deliberately conservative: a missed duplicate costs the candidate one extra message,
# a false match hides a real job. So (1) a shared posting id is decisive, (2) otherwise the
# company must match loosely and the titles must match closely, with seniority an exact match.

_COMPANY_FILLER = {"technologies", "technology", "tech", "security", "software", "systems", "group", "labs",
                   "israel", "solutions", "networks", "holdings", "global"}
_TITLE_NOISE = {"m", "f", "d", "x", "mf", "fm", "israel", "il", "tel", "aviv", "yafo", "jaffa", "herzliya", "remote",
                "hybrid", "onsite", "full", "time", "fulltime", "job", "position", "opening", "the", "a", "and", "of",
                "for", "in", "at"}
_TITLE_ALIASES = {"sr": "senior", "jr": "junior", "eng": "engineer", "engineers": "engineer", "engineering": "engineer",
                  "developer": "engineer", "swe": "software engineer"}
SENIORITY = {"senior", "junior", "lead", "staff", "principal", "head", "manager", "director", "intern", "student",
             "ii", "iii", "iv", "vp"}


def job_ids(url: str) -> set[str]:
    """Stable posting ids found in a job URL (LinkedIn, Workday, Greenhouse, Lever, Ashby, Comeet, or any exact page).

    The same posting keeps the same id on every page that shows it, unlike its title.
    """
    if not url.startswith("http"):
        return set()
    parsed = urlparse(url)
    host, path, query = parsed.netloc.lower().removeprefix("www."), parsed.path, parse_qs(parsed.query)
    ids = set()
    if m := re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", path):
        ids.add(f"linkedin:{m.group(1)}")
    for value in query.get("currentJobId", []):
        ids.add(f"linkedin:{value}")
    if m := re.search(r"_(R?-?\d{4,}|JR-?\d+)(?:-\d+)?/?$", path, re.IGNORECASE):  # Workday: ..._JR101491-1
        ids.add(f"workday:{m.group(1).lower().replace('-', '')}")
    for value in query.get("gh_jid", []):
        ids.add(f"greenhouse:{value}")
    if "greenhouse.io" in host and (m := re.search(r"/jobs/(\d+)", path)):
        ids.add(f"greenhouse:{m.group(1)}")
    if m := re.search(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", path, re.IGNORECASE):
        if "lever.co" in host or "ashbyhq.com" in host:
            ids.add(f"uuid:{m.group(1).lower()}")
    if m := re.search(r"comeet\.com/jobs/([^/]+)/([0-9A-Za-z]{2}\.[0-9A-Za-z]{3})/[^/]+/([0-9A-Za-z.]+)", url, re.IGNORECASE):
        ids.add(f"comeet:{m.group(1)}/{m.group(2)}/{m.group(3)}".lower())
    if not any(h in host for h in ("linkedin.com", "glassdoor.", "indeed.", "alljobs.co.il", "drushim.co.il",
                                   "jobmaster.co.il", "ziprecruiter.")):
        ids.add(f"url:{host}{path.rstrip('/').lower()}")
    return ids


def loose_company(company: str) -> str:
    tokens = normalize(_COMPANY_SUFFIXES.sub(" ", company)).split()
    return " ".join([t for t in tokens if t not in _COMPANY_FILLER] or tokens)


def title_tokens(title: str) -> tuple[frozenset, frozenset]:
    """(seniority words, the rest) of a title, with noise like '(m/f)' and 'Israel' dropped."""
    words = []
    for word in normalize(title).split():
        words += _TITLE_ALIASES.get(word, word).split()
    words = [w for w in words if w not in _TITLE_NOISE]
    return frozenset(w for w in words if w in SENIORITY), frozenset(w for w in words if w not in SENIORITY)


def similar_titles(a: str, b: str) -> bool:
    (seniority_a, core_a), (seniority_b, core_b) = title_tokens(a), title_tokens(b)
    if seniority_a != seniority_b or not core_a or not core_b:
        return False
    return len(core_a & core_b) / len(core_a | core_b) >= 0.8


def same_job(company_a: str, title_a: str, ids_a: set[str], company_b: str, title_b: str, ids_b: set[str]) -> bool:
    if ids_a & ids_b:
        return True
    return loose_company(company_a) == loose_company(company_b) and similar_titles(title_a, title_b)


def short_id(key: str) -> str:
    """Short stable id that fits in Telegram callback data (64 bytes max)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


def html_to_text(markup: str) -> str:
    """Good-enough HTML to plain text for job descriptions."""
    text = _BLOCK_TAGS.sub("\n", markup)
    text = html.unescape(_TAGS.sub("", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
