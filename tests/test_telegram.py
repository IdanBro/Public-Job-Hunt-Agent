from jobagent.models import Job
from jobagent.state import State
from jobagent.telegram import CAPTION_LIMIT, DryRunClient, job_caption, job_keyboard, process_updates, visible_length

JOB = Job(source="test", company="Acme & Co", title="DevOps <Engineer>", location="Tel Aviv", url="https://x/?a=1&b=2")
DECISION = {"match_rate": 82, "reason": "Strong K8s + GitLab CI fit", "gaps": ["Terraform depth"], "variant": "devops"}


def test_caption_has_every_field_and_escapes_html():
    caption = job_caption(JOB, DECISION)
    assert "<b>DevOps &lt;Engineer&gt;</b> · Acme &amp; Co" in caption
    assert "<b>82%</b>" in caption
    assert "Terraform depth" in caption
    assert "Based on: DevOps CV" in caption
    assert 'href="https://x/?a=1&amp;b=2"' in caption
    assert "unverified" not in caption


def test_caption_marks_unverified_jobs():
    assert "unverified" in job_caption(JOB, {**DECISION, "verified": False})


def test_caption_trims_long_gaps_but_keeps_the_link():
    caption = job_caption(JOB, {**DECISION, "gaps": ["x" * 400] * 5})
    assert visible_length(caption) <= CAPTION_LIMIT
    assert "Open the job</a>" in caption


def test_keyboard_callback_data_fits_telegram_limit():
    for button in job_keyboard(JOB.id)["inline_keyboard"][0]:
        assert len(button["callback_data"].encode()) <= 64


def callback(update_id, data, chat="dry-run"):
    return {"update_id": update_id, "callback_query": {"id": f"cb{update_id}", "data": data, "message": {"chat": {"id": chat}}}}


def test_taps_and_why_replies_are_recorded(tmp_path):
    state = State(tmp_path / "state")
    state.mark(JOB, "sent")
    client = DryRunClient(tmp_path / "out", updates=[callback(1, f"up:{JOB.id}"), callback(2, f"down:{JOB.id}")])

    events = process_updates(client, state)
    assert [e["action"] for e in events] == ["interested", "skipped"]
    assert state.telegram["offset"] == 3
    [(prompt_id, job_id)] = state.telegram["why_prompts"].items()
    assert job_id == JOB.id
    assert "Why skip" in (tmp_path / "out" / "001.md").read_text(encoding="utf-8")

    reply = {"update_id": 3, "message": {"chat": {"id": "dry-run"}, "text": "Too senior", "reply_to_message": {"message_id": int(prompt_id)}}}
    client.updates.append(reply)
    [event] = process_updates(client, state)
    assert event == {**event, "action": "reason", "reason": "Too senior", "id": JOB.id}
    assert state.telegram["why_prompts"] == {}
    assert state.find_by_id(JOB.id)[1]["status"] == "skipped"


def test_updates_from_other_chats_are_ignored(tmp_path):
    state = State(tmp_path / "state")
    client = DryRunClient(tmp_path / "out", updates=[callback(5, f"applied:{JOB.id}", chat="12345")])
    assert process_updates(client, state) == []
    assert state.telegram["offset"] == 6


def test_link_prefers_company_site_over_job_board():
    board = Job(source="gmail:linkedin", company="Acme", title="DevOps", location="TLV", url="https://www.linkedin.com/jobs/view/1")
    caption = job_caption(board, {**DECISION, "company_url": "https://acme.com/careers/devops"})
    assert 'href="https://acme.com/careers/devops"' in caption
    assert "job-board link" not in caption


def test_never_links_a_job_board_and_says_where_it_was_seen():
    board = Job(source="gmail:linkedin", company="Acme", title="DevOps", location="TLV", url="https://www.linkedin.com/jobs/view/1")
    caption = job_caption(board, {**DECISION, "company_url": "https://www.indeed.com/viewjob?jk=1"})
    assert "href" not in caption
    assert "linkedin.com" not in caption
    assert "No official company link found (found via Linkedin)" in caption


def test_company_link_notes_the_source_board_as_text():
    board = Job(source="gmail:linkedin", company="Acme", title="DevOps", location="TLV", url="https://www.linkedin.com/jobs/view/1")
    caption = job_caption(board, {**DECISION, "company_url": "https://acme.com/careers/1"})
    assert 'href="https://acme.com/careers/1"' in caption
    assert "found via Linkedin" in caption
