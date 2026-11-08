import copy
import re

import pytest

from jobagent import cv


@pytest.fixture
def software():
    return cv.load(cv.VARIANTS_DIR / "software.yaml")


@pytest.mark.parametrize("variant", cv.variant_names())
def test_every_approved_variant_validates_against_itself(variant):
    assert cv.validate_tailored(cv.load(cv.VARIANTS_DIR / f"{variant}.yaml"), variant) == []


@pytest.mark.parametrize("path", [cv.MASTER] + sorted(cv.VARIANTS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_skill_items_are_not_split_by_yaml_flow_commas(path):
    # An unquoted "AWS (EC2, ECR)" inside [...] silently becomes two items: "AWS (EC2" and "ECR)".
    for group in cv.load(path)["skills"]:
        for item in group["items"]:
            assert item.count("(") == item.count(")"), f"{path.name}: split skill item {item!r}; quote it"


def test_reordering_and_rephrasing_is_allowed(software):
    tailored = copy.deepcopy(software)
    tailored["experience"][1]["bullets"].reverse()
    tailored["experience"][1]["bullets"][0] = "Shipped Node.js features end to end for 14 development teams."
    tailored["skills"].reverse()
    tailored["skills"][0]["items"].append("JUnit")  # job-tier skill: allowed when the ad mentions it
    assert cv.validate_tailored(tailored, "software") == []


@pytest.mark.parametrize(
    "edit, problem",
    [
        (lambda c: c["experience"][0].update(title="Staff Engineer"), "experience entries"),
        (lambda c: c["experience"][1].update(dates="Mar 2023 - Dec 2025"), "experience entries"),
        (lambda c: c["experience"].pop(), "experience entries"),
        (lambda c: c.update(headline="Backend Engineer"), "'headline'"),
        (lambda c: c["skills"][0]["items"].append("Rust"), "skill 'Rust'"),
        (lambda c: c["skills"][0]["items"].append("Go"), "skill 'Go' is never claimed"),
        (lambda c: c["skills"][0]["items"].remove("Java"), "required skill 'Java'"),
        (lambda c: c["skills"][0]["items"].remove("Spring"), "required skill 'Spring'"),
        (lambda c: c["experience"][1]["bullets"].append("Cut costs by 47%."), "number '47'"),
        (lambda c: c["experience"][1]["bullets"].append("Built ProjectNebula."), "personal project"),
        (lambda c: c["experience"][0]["bullets"].append("Wrote services in Golang."), "unclaimed skill"),
        (lambda c: c["experience"][1].pop("bullets"), "has no bullets"),
        (lambda c: c.update(summary=c["summary"] + " Saved $1."), "unescaped Typst character"),
        (lambda c: c.update(summary=cv._LINK_TARGET.sub("", c["summary"])), "summary must keep the link"),
    ],
)
def test_untrue_or_unsafe_edits_are_rejected(software, edit, problem):
    tailored = copy.deepcopy(software)
    edit(tailored)
    errors = cv.validate_tailored(tailored, "software")
    assert any(problem in e for e in errors), errors


@pytest.mark.parametrize("path", [cv.MASTER] + sorted(cv.VARIANTS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_cvs_start_with_the_unnamed_projects_entry(path):
    first = cv.load(path)["experience"][0]
    assert (first["title"], first["dates"]) == ("Independent DevOps & AI Projects", "Oct 2026 - Present")
    assert "org" not in first and len(first["bullets"]) == 2
    assert not cv.forbidden_names().search(" ".join(first["bullets"]))


@pytest.mark.parametrize("path", [cv.MASTER] + sorted(cv.VARIANTS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_summary_links_the_article(path):
    assert re.search(r'#link\("https://example\.com/blog/[^"]+"\)\[Wrote a technical article\]', cv.load(path)["summary"])


def test_unknown_variant(software):
    assert "unknown variant" in cv.validate_tailored(software, "backend")[0]


def test_pdf_name():
    assert cv.pdf_name("monday.com", "Senior DevOps Engineer (Infra)") == "monday-com_Senior-DevOps-Engineer-Infra_CV.pdf"


def test_variants_render_to_one_page_with_the_bundled_font(tmp_path):
    for variant in cv.variant_names():
        pdf = tmp_path / f"{variant}.pdf"
        assert cv.render(cv.VARIANTS_DIR / f"{variant}.yaml", pdf) == 1
        # The cloud has no Arial; the bundled Liberation Sans must be what gets embedded.
        assert b"LiberationSans" in pdf.read_bytes()


def test_skill_policy_is_consistent_with_the_cvs():
    policy = cv.skill_policy()
    allowed = {item.lower() for c in cv._sources() for g in c["skills"] for item in g["items"]}
    variant_skills = {s for names in policy["variant"].values() for s in names}
    tiers = [set(policy["always"]), variant_skills, set(policy["job"])]
    assert sum(len(t) for t in tiers) == len(set().union(*tiers)), "a skill is in two tiers"
    assert set(policy["variant"]) == set(cv.variant_names())
    for name in cv.variant_names():
        listed = {i.lower() for g in cv.load(cv.VARIANTS_DIR / f"{name}.yaml")["skills"] for i in g["items"]}
        assert {s.lower() for s in cv.required_skills(name)} <= listed
        # job-tier and other-variant skills are added per ad, never baked into a variant
        assert not listed & {s.lower() for s in policy["job"]}
        other = {s.lower() for v, names in policy["variant"].items() if v != name for s in names}
        assert not listed & (other - {s.lower() for s in policy["variant"][name]})
