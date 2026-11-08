from jobagent.models import Job, dedup_key, html_to_text, normalize, short_id


def job(**overrides):
    fields = dict(source="test", company="Riskified", title="DevOps Engineer", location="Tel Aviv", url="https://x")
    return Job(**{**fields, **overrides})


def test_same_job_from_different_sources_gets_the_same_key():
    a = job(company="Riskified Ltd.", title="Senior DevOps Engineer")
    b = job(company="riskified", title="Senior  DevOps Engineer ", source="gmail:linkedin")
    assert a.key == b.key
    assert a.id == b.id


def test_different_titles_get_different_keys():
    assert job(title="DevOps Engineer").key != job(title="Senior DevOps Engineer").key


def test_short_id_is_stable_and_fits_telegram_callback_data():
    assert short_id("a|b") == short_id("a|b")
    assert len(f"applied:{short_id('a|b')}".encode()) <= 64


def test_normalize_keeps_hebrew_letters():
    assert normalize("מהנדס/ת DevOps!") == "מהנדס ת devops"


def test_dedup_key_drops_company_suffixes():
    assert dedup_key("Wiz, Inc.", "SRE") == dedup_key("Wiz", "SRE")


def test_html_to_text_keeps_paragraphs_and_unescapes():
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p><ul><li>K8s &amp; Helm</li></ul>") == "Hello world\nK8s & Helm"


def test_round_trip_through_dict():
    original = job(remote=True, employment="Full-time")
    data = original.to_dict()
    assert data["id"] == original.id
    assert Job.from_dict(data) == original


def test_posting_ids_are_read_from_each_kind_of_url():
    from jobagent.models import job_ids
    assert "workday:jr101491" in job_ids("https://bitsight.wd1.myworkdayjobs.com/Bitsight/job/Tel-Aviv-Israel/DevOps-Engineer_JR101491-1")
    assert job_ids("https://www.linkedin.com/jobs/view/4483570331/") == {"linkedin:4483570331"}
    assert job_ids("https://www.linkedin.com/jobs/view/devops-at-x-4483570331?trk=a") == {"linkedin:4483570331"}
    assert "greenhouse:4567" in job_ids("https://job-boards.greenhouse.io/wiz/jobs/4567")
    assert "greenhouse:99" in job_ids("https://careers.acme.com/job?gh_jid=99")
    assert job_ids("https://x.com/careers/a/?utm=1") == job_ids("https://www.x.com/careers/a")
    assert job_ids("not a url") == set()


def test_job_board_pages_never_match_by_page_url():
    from jobagent.models import job_ids
    assert not any(i.startswith("url:") for i in job_ids("https://www.indeed.com/viewjob?jk=1"))


def test_same_job_matches_title_variants_but_never_different_seniority():
    from jobagent.models import same_job
    def m(c1, t1, c2, t2):
        return same_job(c1, t1, set(), c2, t2, set())
    assert m("Bitsight", "DevOps Engineer", "BitSight Technologies", "DevOps Engineer (m/f) - Tel Aviv, Israel")
    assert m("Wiz", "Sr. DevOps Engineers", "Wiz", "Senior DevOps Engineer")
    assert not m("Bitsight", "SR DevOps Engineer", "Bitsight", "DevOps Engineer")  # might be a different role: don't hide it
    assert not m("Wiz", "Backend Engineer", "Wiz", "Frontend Engineer")
    assert not m("Wiz", "DevOps Engineer", "Wizzard", "DevOps Engineer")
    assert same_job("A", "x", {"workday:jr1"}, "B", "y", {"workday:jr1"})  # a shared posting id is decisive
