"""End-to-end flow through the App, with fake boards and the dry-run Telegram client."""

import json
import shutil
from datetime import datetime, timezone

import pytest

from jobagent import cli, cv
from jobagent.models import Job

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def board_job(title, location="Tel Aviv", posted_at="2026-10-20T00:00:00+00:00"):
    return Job(source="greenhouse:acme", company="Acme", title=title, location=location, url=f"https://acme/{title}",
               posted_at=posted_at)


@pytest.fixture
def app(tmp_path, monkeypatch):
    companies = tmp_path / "companies.yaml"
    companies.write_text("- {name: Acme, ats: greenhouse, board: acme}\n- {name: Broken, ats: lever, board: gone}\n")
    monkeypatch.setattr(cli, "COMPANIES", companies)
    monkeypatch.setattr(cli, "discover", lambda names: [])  # never probe real ATSs from tests

    def fake_fetch(entry):
        if entry["board"] == "gone":
            raise RuntimeError("404 Not Found")
        return [board_job("DevOps Engineer"), board_job("Senior Software Engineer"), board_job("Product Manager"),
                board_job("SRE", location="Haifa"), board_job("Platform Engineer", posted_at="2026-07-01T00:00:00+00:00")]

    monkeypatch.setattr(cli.sources, "fetch_company", fake_fetch)
    monkeypatch.setattr(cli.referrers, "refresh", lambda: [
        {"company": "Acme", "contact": "Dana", "note": "", "url": "https://referrals.example.com/e/?s=dana"}])
    monkeypatch.setattr(cli, "REFERRERS_SNAPSHOT", tmp_path / "no-snapshot.json")
    monkeypatch.setenv("JOBAGENT_REFERRER_SHEET", "test-sheet")
    return cli.App(tmp_path / "state", tmp_path / "work", tmp_path / "dry", now=NOW)


@pytest.fixture
def tailored_cv():
    # Typst can only read files inside the repo, so the tailored CV lives under work/.
    path = cv.ROOT / "work" / "test-tailored" / "devops.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(cv.VARIANTS_DIR / "devops.yaml", path)
    yield path
    shutil.rmtree(path.parent)


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_fetch_filters_dedups_and_alerts_once_per_day(app, tmp_path):
    summary = app.fetch()
    assert summary["added"] == 3  # the months-old Platform Engineer job is still listed, so it counts
    assert summary["dropped"] == {"title is not a target family": 1, "location outside the allowed areas": 1}
    assert "lever:gone" in summary["errors"]
    assert "Some job boards failed" in (tmp_path / "dry" / "001.md").read_text(encoding="utf-8")

    again = app.fetch()
    assert again["added"] == 0
    assert again["dropped"]["already seen"] == 3
    assert len(list((tmp_path / "dry").glob("*.md"))) == 1  # no second alert today


def test_fetch_can_still_apply_an_age_limit(app):
    assert app.fetch(max_age_days=14)["dropped"]["posted more than 14 days ago"] == 1


REASON = "Kubernetes and GitLab CI ownership matches the ad"


def test_next_hands_out_a_prioritised_batch_with_full_ads(app):
    app.fetch()
    batch = app.next_batch(limit=2)
    # DevOps family first (newest first), then software; the senior software job waits.
    assert [j["title"] for j in batch["jobs"]] == ["DevOps Engineer", "Platform Engineer"]
    assert batch["left_after_this_batch"] == 1
    saved = json.loads(app.batch_path.read_text(encoding="utf-8"))
    assert [c["title"] for c in saved] == ["DevOps Engineer", "Platform Engineer"]
    assert "description" in saved[0]


def test_looping_next_and_decide_covers_every_candidate_in_one_run(app, tmp_path):
    app.fetch()
    decided = []
    while (batch := app.next_batch(limit=2))["jobs"]:
        decisions = [{"id": j["id"], "send": False, "match_rate": 40, "reason": REASON} for j in batch["jobs"]]
        app.decide(write(tmp_path / "d.json", decisions))
        decided += [j["title"] for j in batch["jobs"]]
    assert sorted(decided) == ["DevOps Engineer", "Platform Engineer", "Senior Software Engineer"]
    assert json.loads(app.candidates_path.read_text(encoding="utf-8")) == []


def test_decide_refuses_jobs_outside_the_batch_and_vague_reasons(app, tmp_path):
    app.fetch()
    app.next_batch(limit=1)
    candidates = {c["title"]: c for c in json.loads(app.candidates_path.read_text(encoding="utf-8"))}
    decisions = write(tmp_path / "d.json", [
        {"id": candidates["Senior Software Engineer"]["id"], "send": False, "match_rate": 40, "reason": REASON},
        {"id": candidates["DevOps Engineer"]["id"], "send": False, "match_rate": 40, "reason": "too senior"},
    ])
    results = app.decide(decisions)["results"]
    assert "not in the current batch" in results[0]["detail"]
    assert "specific 'reason'" in results[1]["detail"]
    assert app.state.seen == {}  # nothing was decided; both stay pending
    assert len(json.loads(app.candidates_path.read_text(encoding="utf-8"))) == 3


def test_decide_sends_rejects_and_enforces_thresholds(app, tmp_path, tailored_cv):
    app.fetch(max_age_days=14)
    app.next_batch()
    candidates = {c["title"]: c for c in json.loads(app.candidates_path.read_text(encoding="utf-8"))}
    devops, senior_sw = candidates["DevOps Engineer"], candidates["Senior Software Engineer"]
    assert senior_sw["flags"] == ["senior"]

    decisions = write(tmp_path / "decisions.json", [
        {"id": devops["id"], "send": True, "match_rate": 81, "reason": REASON, "gaps": [], "variant": "devops",
         "cv": str(tailored_cv), "verified": True},
        # Senior software jobs need 65%+, so 64% must not be sent.
        {"id": senior_sw["id"], "send": True, "match_rate": 64, "reason": REASON, "gaps": [], "variant": "software",
         "cv": str(tailored_cv)},
    ])
    results = {r["title"]: r for r in app.decide(decisions)["results"]}

    assert results["DevOps Engineer"]["result"] == "sent"
    assert results["Senior Software Engineer"]["result"] == "rejected"
    assert "65%" in results["Senior Software Engineer"]["detail"]
    assert (tmp_path / "dry" / "Acme_DevOps-Engineer_CV.pdf").exists()
    assert app.state.seen[Job.from_dict(devops).key]["status"] == "sent"
    assert json.loads(app.candidates_path.read_text(encoding="utf-8")) == []


def test_decide_keeps_job_pending_when_the_cv_fails_validation(app, tmp_path, tailored_cv):
    app.fetch(max_age_days=14)
    app.next_batch()
    [devops] = [c for c in json.loads(app.candidates_path.read_text(encoding="utf-8")) if c["title"] == "DevOps Engineer"]
    tailored_cv.write_text(tailored_cv.read_text(encoding="utf-8").replace("headline: DevOps / Platform Engineer", "headline: CTO"),
                           encoding="utf-8")
    decisions = write(tmp_path / "d.json", [{"id": devops["id"], "send": True, "match_rate": 90, "reason": REASON,
                                             "variant": "devops", "cv": str(tailored_cv), "verified": True}])
    [result] = app.decide(decisions)["results"]
    assert result["result"] == "error"
    assert "headline" in result["detail"]
    assert devops["id"] in app.candidates_path.read_text(encoding="utf-8")
    assert not app.state.has_seen(Job.from_dict(devops).key)


def test_ingest_uses_the_same_filters_and_dedup(app, tmp_path):
    app.fetch(max_age_days=14)
    found = write(tmp_path / "gmail.json", [
        {"source": "gmail:linkedin", "company": "Acme Ltd", "title": "DevOps Engineer", "location": "Tel Aviv", "url": "u1"},
        {"source": "gmail:linkedin", "company": "Other", "title": "Platform Engineer", "location": "Ramat Gan", "url": "u2"},
    ])
    summary = app.ingest(found, max_age_days=14)
    assert summary["added"] == 1
    assert summary["dropped"] == {"already seen": 1}


class FakeBot:
    sent = []

    def __init__(self, token, chat_id, updates=()):
        self.chat_id = chat_id
        self.updates = list(updates)

    def get_me(self):
        return {"username": "my_jobs_bot"}

    def get_updates(self, offset):
        return self.updates

    def send_text(self, text, force_reply=False):
        FakeBot.sent.append((self.chat_id, text))
        return 1


def test_setup_telegram_finds_the_only_private_chat_and_sends_a_test(monkeypatch, capsys):
    updates = [{"update_id": 1, "message": {"chat": {"id": 4242, "type": "private", "username": "candidate"}}}]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(cli.telegram, "TelegramClient", lambda token, chat: FakeBot(token, chat, updates))
    FakeBot.sent = []
    assert cli.setup_telegram() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["TELEGRAM_CHAT_ID"] == "4242"
    assert "123:abc" not in json.dumps(out)
    assert FakeBot.sent[0][0] == "4242"


def test_setup_telegram_asks_for_start_when_nobody_messaged_the_bot(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(cli.telegram, "TelegramClient", lambda token, chat: FakeBot(token, chat))
    assert cli.setup_telegram() == 1
    assert "send /start" in json.loads(capsys.readouterr().out)["hint"]


def test_alert_command_is_rate_limited(app):
    assert app.alert("gmail", "connector disconnected") == {"sent": True}
    assert app.alert("gmail", "still disconnected") == {"sent": False}


def test_discover_reads_a_comeet_url_directly():
    from tests.test_sources import COMEET_PAGE

    calls = []

    def get_text(url):
        calls.append(url)
        return COMEET_PAGE

    [hit] = cli.discover(["https://www.comeet.com/jobs/acme/A1.002/devops-engineer/B2.003"], get_text=get_text)
    assert calls == ["https://www.comeet.com/jobs/acme/A1.002"]
    assert hit == {"name": "acme", "ats": "comeet", "board": "acme/A1.002", "jobs": 1, "central_israel": 1}


def test_sent_message_names_the_referrer_or_says_none_was_found(app, tmp_path, tailored_cv):
    app.fetch(max_age_days=14)
    app.next_batch()
    devops = next(c for c in json.loads(app.candidates_path.read_text(encoding="utf-8")) if c["title"] == "DevOps Engineer")
    decision = {"id": devops["id"], "send": True, "match_rate": 81, "reason": REASON, "gaps": [], "variant": "devops",
                "cv": str(tailored_cv), "verified": True}
    app.decide(write(tmp_path / "decisions.json", [decision]))
    # fetch() already wrote a "boards failed" warning (the fixture's broken board), so pick the job message itself.
    messages = [p.read_text(encoding="utf-8") for p in sorted((tmp_path / "dry").glob("*.md"))]
    [message] = [m for m in messages if "[attachment:" in m]
    assert '🤝 Referrer: <a href="https://referrals.example.com/e/?s=dana">Dana</a>' in message

    assert app._referrer_line("Unlisted Co") == "🤝 Referrer: none found for this company"
    app.referrers_path.unlink()
    assert "couldn't check" in app._referrer_line("Acme")


def test_fetch_alerts_when_the_referrer_sheet_cannot_be_read(app, monkeypatch):
    def boom():
        raise RuntimeError("403")

    monkeypatch.setattr(cli.referrers, "refresh", boom)
    assert "referrers" in app.fetch(max_age_days=14)["errors"]


def test_no_referrer_line_when_no_sheet_is_configured(app, monkeypatch):
    monkeypatch.delenv("JOBAGENT_REFERRER_SHEET")
    assert app._referrer_line("Acme") == ""


def test_email_copy_of_a_sent_board_job_is_dropped_even_with_a_different_title(app, tmp_path, tailored_cv):
    app.fetch(max_age_days=14)
    app.next_batch()
    [devops] = [c for c in json.loads(app.candidates_path.read_text(encoding="utf-8")) if c["title"] == "DevOps Engineer"]
    app.decide(write(tmp_path / "d.json", [{"id": devops["id"], "send": True, "match_rate": 81, "reason": REASON,
                                            "gaps": [], "variant": "devops", "cv": str(tailored_cv), "verified": True}]))
    alert = Job(source="gmail:linkedin", company="Acme Ltd", title="DevOps Engineer (m/f) - Israel", location="Tel Aviv",
                url="https://www.linkedin.com/jobs/view/1/", posted_at="2026-10-24T00:00:00+00:00")
    summary = app.ingest(write(tmp_path / "gmail.json", [alert.to_dict()]), max_age_days=14)
    assert summary["added"] == 0 and summary["dropped"] == {"already seen": 1}


def test_decide_refuses_to_resend_a_posting_whose_company_page_was_already_handled(app, tmp_path, tailored_cv):
    wd = "https://acme.wd1.myworkdayjobs.com/Acme/job/TLV/DevOps_JR101491-1"
    sent = Job(source="web", company="Acme", title="Sr DevOps Engineer", location="Tel Aviv", url="https://www.linkedin.com/jobs/view/9/",
               posted_at="2026-10-24T00:00:00+00:00")
    app.state.mark(sent, "sent", extra_ids={"workday:jr101491"})
    later = Job(source="gmail:linkedin", company="Acme", title="Platform Engineer", location="Tel Aviv",
                url="https://www.linkedin.com/jobs/view/10/", posted_at="2026-10-24T00:00:00+00:00")
    app.ingest(write(tmp_path / "gmail.json", [later.to_dict()]), max_age_days=14)
    app.next_batch()
    results = app.decide(write(tmp_path / "d.json", [{"id": later.id, "send": True, "match_rate": 85, "reason": REASON, "gaps": [],
                                                      "variant": "devops", "cv": str(tailored_cv), "company_url": wd, "verified": True}]))["results"]
    assert results[0]["result"] == "duplicate"
    assert not (tmp_path / "dry" / "Acme_Platform-Engineer_CV.pdf").exists()


def test_a_board_listing_replaces_the_email_copy_found_in_the_same_run(app, tmp_path):
    email = Job(source="gmail:linkedin", company="Acme", title="DevOps Engineer", location="Tel Aviv",
                url="https://www.linkedin.com/jobs/view/1/", posted_at="2026-10-24T00:00:00+00:00")
    app.ingest(write(tmp_path / "gmail.json", [email.to_dict()]), max_age_days=14)
    app.fetch()
    sources = {c["title"]: c["source"] for c in json.loads(app.candidates_path.read_text(encoding="utf-8"))}
    assert sources["DevOps Engineer"] == "greenhouse:acme"


def _email_job_pending(app, tmp_path):
    job = Job(source="gmail:linkedin", company="Acme", title="Platform Engineer", location="Tel Aviv",
              url="https://www.linkedin.com/jobs/view/10/", posted_at="2026-10-24T00:00:00+00:00")
    app.ingest(write(tmp_path / "gmail.json", [job.to_dict()]), max_age_days=14)
    app.next_batch()
    return job


def _send(app, tmp_path, tailored_cv, job, **extra):
    decision = {"id": job.id, "send": True, "match_rate": 85, "reason": REASON, "gaps": [], "variant": "devops",
                "cv": str(tailored_cv), **extra}
    return app.decide(write(tmp_path / "d.json", [decision]))["results"][0]


def test_sending_needs_an_explicit_verified_flag(app, tmp_path, tailored_cv):
    job = _email_job_pending(app, tmp_path)
    result = _send(app, tmp_path, tailored_cv, job, company_url="https://acme.com/careers/1")
    assert result["result"] == "error" and "verified" in result["detail"]


def test_email_jobs_must_search_for_the_official_link_before_sending(app, tmp_path, tailored_cv):
    job = _email_job_pending(app, tmp_path)
    result = _send(app, tmp_path, tailored_cv, job, verified=True)
    assert result["result"] == "error" and "company_url" in result["detail"]
    assert app.state.seen == {}  # still pending


def test_a_job_board_is_not_accepted_as_the_company_url(app, tmp_path, tailored_cv):
    job = _email_job_pending(app, tmp_path)
    result = _send(app, tmp_path, tailored_cv, job, verified=True, company_url="https://www.indeed.com/viewjob?jk=1")
    assert result["result"] == "error" and "job board" in result["detail"]


def test_official_link_is_saved_with_its_posting_id_when_sent(app, tmp_path, tailored_cv):
    job = _email_job_pending(app, tmp_path)
    url = "https://acme.wd1.myworkdayjobs.com/Acme/job/TLV/Platform_JR555-1"
    assert _send(app, tmp_path, tailored_cv, job, verified=True, company_url=url)["result"] == "sent"
    assert {"workday:jr555", "linkedin:10"} <= set(app.state.seen[job.key]["ids"])


def test_not_found_is_accepted_only_after_an_explicit_search(app, tmp_path, tailored_cv):
    job = _email_job_pending(app, tmp_path)
    assert _send(app, tmp_path, tailored_cv, job, verified=False, company_url=None, official_link_searched=True)["result"] == "sent"


def test_email_job_is_swapped_for_the_company_board_listing_and_board_is_remembered(app, monkeypatch, tmp_path):
    board = Job(source="ashby:zafran-security", company="Zafran Security", title="DevOps Engineer",
                location="Tel Aviv", url="https://jobs.ashbyhq.com/zafran-security/1", posted_at="2026-10-20T00:00:00+00:00")
    monkeypatch.setattr(cli, "discover", lambda names: [{"name": names[0], "ats": "ashby", "board": "zafran-security",
                                                        "jobs": 3, "central_israel": 2}])
    real = cli.sources.fetch_company
    monkeypatch.setattr(cli.sources, "fetch_company", lambda e: [board] if e["board"] == "zafran-security" else real(e))
    email = Job(source="gmail:linkedin", company="Zafran Security", title="DevOps Engineer",
                location="Tel Aviv", url="https://www.linkedin.com/jobs/view/123", posted_at="2026-10-24T00:00:00+00:00")
    path = tmp_path / "in.json"
    path.write_text(json.dumps([email.to_dict()]))
    assert app.ingest(path, 14)["added"] == 1
    pending = json.loads((app.work / "candidates.json").read_text())
    assert pending[0]["source"] == "ashby:zafran-security"
    assert json.loads((app.state.root / "company-boards.json").read_text())["zafransecurity"]["board"] == "zafran-security"


def test_company_without_a_board_is_not_re_probed_within_a_week(app, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "discover", lambda names: calls.append(names) or [])
    email = Job(source="gmail:linkedin", company="Nowhere Ltd", title="DevOps Engineer", location="Tel Aviv",
                url="https://www.linkedin.com/jobs/view/9", posted_at="2026-10-24T00:00:00+00:00")
    assert app.resolve_official(email) is email
    assert app.resolve_official(email) is email
    assert len(calls) == 1
