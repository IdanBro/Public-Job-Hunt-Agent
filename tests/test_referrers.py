import io

import openpyxl

from jobagent import referrers
from jobagent.telegram import CAPTION_LIMIT, job_caption, visible_length
from tests.test_telegram import DECISION, JOB


def sheet_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Referrers:", None, "Notes"])
    ws.append(["Request to add company - click here!"])
    ws.append(["Company", "Contact"])
    ws.append(["Taboola", "Jonathan", "Be in touch with the contact before applying."])
    ws["B4"].hyperlink = "https://referrals.example.com/e/?s=jonathan&b=1"
    ws.append(["Taboola", '=HYPERLINK("https://referrals.example.com/e/?s=sharon&b=2","Sharon")', "Be in touch."])
    ws.append(["AdCore", "Maya Referrer", "Please email me the link to the role and your CV - maya@referrals.example.com"])
    ws.append(["Alice (formerly Activefence)", "Cecilia", ""])
    ws.append(["Check Point", "Yair", ""])
    ws.append(["Wix.com", "", ""])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_refresh_reads_names_links_formula_links_and_skips_headers():
    entries = referrers.refresh(lambda url: sheet_bytes(), sheet="test-sheet")
    assert [(e["company"], e["contact"]) for e in entries] == [
        ("Taboola", "Jonathan"), ("Taboola", "Sharon"), ("AdCore", "Maya Referrer"),
        ("Alice (formerly Activefence)", "Cecilia"), ("Check Point", "Yair"),
    ]
    assert entries[0]["url"] == "https://referrals.example.com/e/?s=jonathan&b=1"
    assert entries[1]["url"] == "https://referrals.example.com/e/?s=sharon&b=2"


def test_refresh_is_off_until_a_sheet_is_configured(monkeypatch):
    asked = []
    assert referrers.refresh(lambda url: asked.append(url) or sheet_bytes()) is None and asked == []
    monkeypatch.setenv(referrers.SHEET_ENV, "abc123")
    assert referrers.refresh(lambda url: asked.append(url) or sheet_bytes())
    assert asked == ["https://docs.google.com/spreadsheets/d/abc123/export?format=xlsx"]


def test_matching_handles_suffixes_aliases_and_longer_names_but_not_lookalikes():
    assert referrers.matches("Taboola Ltd.", "Taboola")
    assert referrers.matches("Activefence", "Alice (formerly Activefence)")
    assert referrers.matches("Check Point Software Technologies", "Check Point")
    assert not referrers.matches("Wiz", "Wix")
    assert not referrers.matches("Cato Networks", "Cato")  # 4 characters: too short to trust a prefix match


def test_line_shows_links_emails_none_and_unavailable():
    entries = referrers.refresh(lambda url: sheet_bytes(), sheet="test-sheet")
    assert 'href="https://referrals.example.com/e/?s=jonathan&amp;b=1">Jonathan</a>' in referrers.line(referrers.find(entries, "Taboola"))
    assert "email maya@referrals.example.com" in referrers.line(referrers.find(entries, "AdCore"))
    assert referrers.line(referrers.find(entries, "Wiz")) == "🤝 Referrer: none found for this company"
    assert "couldn't check" in referrers.line(None)


def test_caption_keeps_the_referrer_line_and_counts_only_visible_text():
    long_link = "https://referrals.example.com/e/?" + "x" * 300
    line = referrers.line([{"company": "A", "contact": c, "note": "", "url": long_link} for c in ("Ann", "Bob", "Cy", "Di")])
    caption = job_caption(JOB, {**DECISION, "gaps": ["y" * 300]}, line)
    assert "Referrer: " in caption and "+1 more" in caption
    assert visible_length(caption) <= CAPTION_LIMIT
