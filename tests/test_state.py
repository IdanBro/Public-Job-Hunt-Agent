import json

from jobagent.models import Job
from jobagent.state import State


def job(title="DevOps Engineer"):
    return Job(source="test", company="Acme", title=title, location="Tel Aviv", url="https://x")


def test_marked_jobs_persist_across_loads(tmp_path):
    state = State(tmp_path)
    assert state.is_first_run
    state.mark(job(), "sent", match_rate=80)
    state.save()

    reloaded = State(tmp_path)
    assert reloaded.has_seen(job().key)
    assert not reloaded.is_first_run
    key, record = reloaded.find_by_id(job().id)
    assert record["status"] == "sent"
    assert record["match_rate"] == 80


def test_feedback_updates_status_and_appends_log(tmp_path):
    state = State(tmp_path)
    state.mark(job(), "sent")
    state.add_feedback(job().id, "applied")
    state.add_feedback(job().id, "reason", reason="too far")

    assert state.find_by_id(job().id)[1]["status"] == "applied"
    lines = [json.loads(line) for line in (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in lines] == ["applied", "reason"]
    assert lines[1]["reason"] == "too far"
    assert lines[0]["title"] == "DevOps Engineer"


def test_alerts_are_limited_to_one_per_source_per_day(tmp_path):
    state = State(tmp_path)
    assert state.should_alert("gmail", "2026-10-25")
    assert not state.should_alert("gmail", "2026-10-25")
    assert state.should_alert("greenhouse:acme", "2026-10-25")
    assert state.should_alert("gmail", "2026-10-26")


def test_find_duplicate_by_id_exact_key_and_close_title(tmp_path):
    state = State(tmp_path)
    state.mark(Job(source="gmail:linkedin", company="Acme", title="DevOps Engineer", location="TLV",
                   url="https://www.linkedin.com/jobs/view/42/"), "sent", extra_ids={"workday:jr7"})
    state.save()
    state = State(tmp_path)
    other = lambda **kw: Job(**{**dict(source="x", company="Acme", title="Totally Different", location="TLV", url="https://x"), **kw})
    assert state.find_duplicate(other(url="https://www.linkedin.com/jobs/view/42"))  # same LinkedIn id
    assert state.find_duplicate(other(url="https://acme.wd1.myworkdayjobs.com/a/b_JR7-1"))  # learned from the company page
    assert state.find_duplicate(other(title="DevOps Engineer (m/f)"))  # close title
    assert not state.find_duplicate(other(title="Senior DevOps Engineer"))
    assert not state.find_duplicate(other(url="https://acme.wd1.myworkdayjobs.com/a/b_JR8-1"), ids_only=True)


def test_old_records_without_ids_still_match_by_their_url(tmp_path):
    state = State(tmp_path)
    state.seen["acme|old"] = {"company": "Acme", "title": "Old", "url": "https://job-boards.greenhouse.io/acme/jobs/5", "status": "sent"}
    probe = Job(source="x", company="Acme", title="Renamed", location="TLV", url="https://careers.acme.com/j?gh_jid=5")
    assert state.find_duplicate(probe)
