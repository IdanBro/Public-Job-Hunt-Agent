from datetime import datetime, timedelta, timezone

import pytest

from jobagent.filters import location_status, prefilter, title_family
from jobagent.models import Job

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def job(title="DevOps Engineer", location="Tel Aviv, Israel", **overrides):
    return Job(source="test", company="Acme", title=title, location=location, url="https://x", **overrides)


@pytest.mark.parametrize(
    "title, family",
    [
        ("DevOps Engineer", "devops"),
        ("Senior Dev-Ops Engineer", "devops"),
        ("Site Reliability Engineer (SRE)", "devops"),
        ("Platform Engineer", "devops"),
        ("Cloud Infrastructure Engineer", "devops"),
        ("Software Engineer", "software"),
        ("Backend Developer", "software"),
        ("Back-End Engineer (Node.js)", "software"),
        ("Data Engineer", "software"),
        ("Python Developer", "software"),
        ("מהנדס/ת תוכנה", "software"),
        ("SW Engineer - Enterprise Browser team", "software"),
        ("Backend Engineer - ML Platform", "software"),
        ("Machine Learning Infrastructure Engineer", "devops"),
        ("Production Operations Engineer", "devops"),
        ("Software Engineer – CTO Office", "software"),
        ("Product Manager", None),
        ("Account Executive", None),
    ],
)
def test_title_family(title, family):
    assert title_family(title) == family


@pytest.mark.parametrize(
    "title",
    [
        "Staff Software Engineer",
        "Principal DevOps Engineer",
        "DevOps Team Lead",
        "Engineering Manager, Platform",
        "Head of DevOps",
        "Software Engineer Intern",
        "Student - Data Engineer",
        "Frontend Software Engineer",
        "Full Stack Developer",
        "QA Automation Software Engineer",
        "Embedded Software Engineer",
        "IT Infrastructure Engineer",
        "Solutions Engineer, Cloud",
        "Senior Backend Engineer (AI Fluent) - Temporary",
    ],
)
def test_excluded_titles_are_dropped(title):
    assert not prefilter(job(title), NOW).keep


@pytest.mark.parametrize(
    "location, remote, status",
    [
        ("Tel Aviv-Yafo, Israel", False, "ok"),
        ("Petah Tikva", False, "ok"),
        ("Herzliya; New York", False, "ok"),
        ("Ra'anana", False, "ok"),
        ("רמת גן", False, "ok"),
        ("Israel", False, "unclear"),
        ("", False, "unclear"),
        ("Remote - Israel", False, "unclear"),
        ("Remote (EMEA)", False, "unclear"),
        ("Remote", False, "unclear"),
        ("Remote - United States", False, "reject"),
        ("Haifa, Israel", False, "reject"),
        ("Jerusalem", False, "reject"),
        ("London", True, "reject"),
    ],
)
def test_location_status(location, remote, status):
    assert location_status(location, remote) == status


def test_kept_jobs_are_flagged_for_senior_and_unclear_location():
    verdict = prefilter(job("Senior Backend Engineer", location="Israel"), NOW)
    assert verdict.keep
    assert verdict.family == "software"
    assert set(verdict.flags) == {"senior", "location_unclear"}


def test_old_jobs_are_dropped_but_undated_jobs_are_kept():
    old = (NOW - timedelta(days=20)).isoformat()
    recent = (NOW - timedelta(days=3)).isoformat()
    assert not prefilter(job(posted_at=old), NOW, max_age_days=14).keep
    assert prefilter(job(posted_at=recent), NOW, max_age_days=14).keep
    assert prefilter(job(posted_at=None), NOW).keep
    # No limit at all for jobs still listed on a company's own board.
    assert prefilter(job(posted_at=(NOW - timedelta(days=200)).isoformat()), NOW, max_age_days=None).keep


@pytest.mark.parametrize("employment, keep", [("Full-time", True), ("FullTime", True), ("Part-time", False), ("Contract", False)])
def test_only_full_time(employment, keep):
    assert prefilter(job(employment=employment), NOW).keep is keep
