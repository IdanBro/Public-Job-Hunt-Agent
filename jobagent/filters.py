"""Cheap, deterministic pre-filter run before the routine spends tokens on a job.

It only drops jobs that clearly fail a hard filter from DESIGN.md (wrong title
family, excluded seniority or role type, wrong location, not full-time, too
old, for jobs from emails and web search). Anything ambiguous is kept and flagged, and the routine decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from jobagent.models import Job

# Allowed areas (DESIGN.md: Matching). English spellings seen on boards, plus Hebrew.
AREAS = [
    "tel aviv", "tel-aviv", "tlv", "jaffa", "yafo", "herzliya", "herzelia", "herzliyya",
    "petah tikva", "petach tikva", "petah tiqva", "petah-tikva", "petach-tikva", "ramat gan", "ramat-gan",
    "givatayim", "givataim", "bnei brak", "bnei-brak", "kiryat ono", "kiryat-ono", "or yehuda",
    "airport city", "rosh haayin", "rosh ha'ayin", "rosh ha-ayin", "ramat hasharon", "ramat ha'sharon",
    "raanana", "ra'anana", "kfar saba", "kfar-saba", "hod hasharon", "hod ha'sharon",
    "תל אביב", "יפו", "הרצליה", "פתח תקווה", "פתח תקוה", "רמת גן", "גבעתיים", "בני ברק", "קריית אונו",
    "קרית אונו", "אור יהודה", "איירפורט סיטי", "ראש העין", "רמת השרון", "רעננה", "כפר סבא", "הוד השרון",
]
# Remote jobs only count when Israel is explicitly allowed (DESIGN.md: Remote abroad).
REMOTE_OK = ["israel", "ישראל", "emea", "anywhere", "worldwide", "global"]

FAMILIES = {
    "devops": re.compile(
        r"dev\s*-?\s*ops|devsecops|\bsre\b|site reliability|reliability engineer|platform (engineer|developer)"
        r"|infrastructure|\binfra\b|cloud (engineer|infrastructure|ops|platform)|production (engineer|operations)"
        r"|developer experience|\bdevex\b|דבאופס",
        re.IGNORECASE,
    ),
    "software": re.compile(
        r"(software|\bsw) (engineer|developer|development engineer)|back[\s-]?end|server[\s-]side|data engineer"
        r"|big data|data platform engineer|(python|node(\.?js)?|java|typescript) (developer|engineer)"
        r"|(מהנדס|מפתח)\S{0,3} תוכנה|מפתח\S{0,3} backend|מהנדס\S{0,3} (נתונים|דאטה)",
        re.IGNORECASE,
    ),
}
EXCLUDED_SENIORITY = re.compile(
    r"\b(staff|principal|architect|lead|leader|manager|head|director|vp|vice president|chief"
    r"|intern|internship|student)\b|ראש צוות|מנהל|סטודנט",
    re.IGNORECASE,
)
# Hard-excluded role types (DESIGN.md), plus families that share keywords with ours.
EXCLUDED_ROLE_TYPES = re.compile(
    r"front[\s-]?end|full[\s-]?stack|mobile|\bios\b|android|\bqa\b|quality assurance|\bin test\b|\bsdet\b"
    r"|test automation|automation engineer|embedded|firmware|hardware|computer vision"
    r"|algorithm|data scien|analyst|\bit\b|helpdesk|help desk|\bnoc\b|network engineer|sysadmin"
    r"|system administrator|support engineer|solutions? engineer|sales|customer|pre[\s-]?sales|bi developer",
    re.IGNORECASE,
)
SENIOR = re.compile(r"\b(senior|sr\.?)\b|בכיר", re.IGNORECASE)
NOT_FULL_TIME = re.compile(r"part[\s-]?time|contract|freelance|temporary|intern|student", re.IGNORECASE)


@dataclass
class Verdict:
    keep: bool
    family: str | None = None
    reason: str = ""
    flags: list[str] = field(default_factory=list)


def title_family(title: str) -> str | None:
    for family, pattern in FAMILIES.items():
        if pattern.search(title):
            return family
    return None


def location_status(location: str, remote: bool) -> str:
    """'ok' (allowed area), 'unclear' (routine must check the ad) or 'reject'."""
    loc = location.lower()
    if any(area in loc for area in AREAS):
        return "ok"
    is_remote = remote or "remote" in loc
    leftover = re.sub(r"remote|hybrid|[\W_]", "", loc)
    if is_remote:
        return "unclear" if any(term in loc for term in REMOTE_OK) or not leftover else "reject"
    if leftover in ("", "israel", "il", "ישראל", "multiplelocations", "centerdistrict", "centraldistrict"):
        return "unclear"
    return "reject"


def is_too_old(posted_at: str | None, now: datetime, max_age_days: int | None) -> bool:
    if not posted_at or max_age_days is None:
        return False
    try:
        posted = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return now - posted > timedelta(days=max_age_days)


def prefilter(job: Job, now: datetime, max_age_days: int | None = 14) -> Verdict:
    """max_age_days=None means no age limit (used for jobs listed on a company's own board, which are open now)."""
    family = title_family(job.title)
    if not family:
        return Verdict(False, reason="title is not a target family")
    if EXCLUDED_ROLE_TYPES.search(job.title):
        return Verdict(False, family, "excluded role type")
    if EXCLUDED_SENIORITY.search(job.title):
        return Verdict(False, family, "excluded seniority")
    if NOT_FULL_TIME.search(f"{job.employment} {job.title}"):
        return Verdict(False, family, "not full-time")
    if is_too_old(job.posted_at, now, max_age_days):
        return Verdict(False, family, f"posted more than {max_age_days} days ago")
    location = location_status(job.location, job.remote)
    if location == "reject":
        return Verdict(False, family, "location outside the allowed areas")

    flags = []
    if location == "unclear":
        flags.append("location_unclear")
    if SENIOR.search(job.title):
        flags.append("senior")
    return Verdict(True, family, "passed", flags)
