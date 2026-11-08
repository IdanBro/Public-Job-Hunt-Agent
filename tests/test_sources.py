"""Response shapes recorded from the live APIs on 2026-10-25 (trimmed to the fields we read)."""

import json

from jobagent.sources import fetch_ashby, fetch_comeet, fetch_company, fetch_greenhouse, fetch_lever

GREENHOUSE = {
    "jobs": [
        {
            "absolute_url": "https://www.riskified.com/careers/job-description/?gh_jid=1",
            "location": {"name": "Tel Aviv"},
            "id": 1,
            "updated_at": "2026-10-20T14:01:50-04:00",
            "first_published": "2026-09-10T11:35:28-04:00",
            "title": "DevOps Engineer",
            "company_name": "Riskified",
            "content": "&lt;p&gt;Run &lt;b&gt;Kubernetes&lt;/b&gt; &amp;amp; Helm&lt;/p&gt;",
        }
    ]
}

LEVER = [
    {
        "id": "abc",
        "text": "Platform Engineer",
        "categories": {"commitment": "Full-time", "location": "Tel Aviv", "allLocations": ["Tel Aviv", "Remote"]},
        "createdAt": 1786469891368,
        "descriptionPlain": "About the role",
        "lists": [{"text": "Requirements", "content": "<li>3+ years DevOps</li>"}],
        "additionalPlain": "Benefits",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
        "workplaceType": "hybrid",
    }
]

ASHBY = {
    "jobs": [
        {
            "id": "x1",
            "title": "Backend Engineer",
            "employmentType": "FullTime",
            "location": "Tel Aviv",
            "secondaryLocations": [{"location": "Herzliya"}],
            "publishedAt": "2026-10-01T10:00:00.000+00:00",
            "isListed": True,
            "isRemote": False,
            "jobUrl": "https://jobs.ashbyhq.com/acme/x1",
            "descriptionPlain": "Build services",
        },
        {"id": "x2", "title": "Hidden", "isListed": False, "jobUrl": "u", "location": ""},
    ]
}


def fake(response, calls=None):
    def get_json(url):
        if calls is not None:
            calls.append(url)
        return response

    return get_json


def test_greenhouse():
    calls = []
    [job] = fetch_greenhouse("riskified", "Riskified", fake(GREENHOUSE, calls))
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/riskified/jobs?content=true"]
    assert (job.title, job.company, job.location) == ("DevOps Engineer", "Riskified", "Tel Aviv")
    assert job.description == "Run Kubernetes & Helm"
    assert job.posted_at == "2026-09-10T11:35:28-04:00"
    assert job.source == "greenhouse:riskified"


def test_lever():
    [job] = fetch_lever("acme", "Acme", fake(LEVER))
    assert job.location == "Tel Aviv; Remote"
    assert job.employment == "Full-time"
    assert job.posted_at.startswith("2026-08-11")
    assert "Requirements\n3+ years DevOps" in job.description
    assert job.url == "https://jobs.lever.co/acme/abc"


def test_ashby_skips_unlisted_jobs_and_joins_locations():
    [job] = fetch_ashby("acme", "Acme", fake(ASHBY))
    assert job.title == "Backend Engineer"
    assert job.location == "Tel Aviv; Herzliya"
    assert job.employment == "FullTime"


def test_fetch_company_dispatches_on_ats():
    [job] = fetch_company({"name": "Acme", "ats": "ashby", "board": "acme"}, fake(ASHBY))
    assert job.source == "ashby:acme"


COMEET = [
    {
        "name": "DevOps Engineer",
        "location": {"name": "Tel Aviv", "city": "Tel Aviv", "country": "IL", "is_remote": False},
        "url_comeet_hosted_page": "https://www.comeet.com/jobs/acme/A1.002/devops-engineer/B2.003",
        "employment_type": "Full-Time",
        "custom_fields": {"details": [{"name": "Requirements", "value": "<ul><li>Kubernetes</li></ul>"}]},
        "time_updated": "2026-10-25T17:15:17Z",
        "is_internal": False,
        "workplace_type": "Hybrid",
    },
    {"name": "Internal only", "location": {}, "is_internal": True},
]
COMEET_PAGE = "<script>\nCOMPANY_DATA = {};\nCOMPANY_POSITIONS_DATA = " + json.dumps(COMEET) + ";\n</script>"


def test_comeet_reads_positions_embedded_in_the_career_page():
    calls = []
    [job] = fetch_comeet("acme/A1.002", "Acme", fake(COMEET_PAGE, calls))
    assert calls == ["https://www.comeet.com/jobs/acme/A1.002"]
    assert (job.title, job.location, job.employment) == ("DevOps Engineer", "Tel Aviv; IL", "Full-Time")
    assert job.description == "Requirements\nKubernetes"
    assert job.url.endswith("/devops-engineer/B2.003")
    assert job.source == "comeet:acme/A1.002" and not job.remote


def test_comeet_page_without_data_is_an_error():
    import pytest

    with pytest.raises(ValueError):
        fetch_comeet("acme/A1.002", "Acme", fake("<html>moved</html>"))


def test_fetch_company_routes_comeet_to_the_page_fetcher():
    [job] = fetch_company({"name": "Acme", "ats": "comeet", "board": "acme/A1.002"}, get_text=fake(COMEET_PAGE))
    assert job.source == "comeet:acme/A1.002"
