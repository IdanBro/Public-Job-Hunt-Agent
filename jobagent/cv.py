"""Render CVs to PDF and guard tailored CVs against untrue edits.

The validator is the deterministic half of the truthfulness rules in DESIGN.md:
a tailored CV must keep its variant's identity, titles, employers, dates and
education; may only list skills that already appear in the master CV or a
variant; and may not contain numbers that appear nowhere in the source CVs.
"""

from __future__ import annotations

import re
from pathlib import Path

import typst
import yaml

ROOT = Path(__file__).resolve().parent.parent
CV_DIR = ROOT / "cv"
TEMPLATE = CV_DIR / "template.typ"
FONTS = CV_DIR / "fonts"
MASTER = CV_DIR / "master.yaml"
VARIANTS_DIR = CV_DIR / "variants"
SKILL_POLICY = CV_DIR / "skill-policy.yaml"

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_LINK_TARGET = re.compile(r"#link\(\"[^\"]*\"\)")
# Typst markup characters that break rendering unless escaped (see cv/template.typ).
_UNSAFE_MARKUP = re.compile(r"(?<!\\)(\$|@|<|`|#(?!link\())")


def load(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def variant_names() -> list[str]:
    return sorted(p.stem for p in VARIANTS_DIR.glob("*.yaml"))


def render(cv_yaml: Path, out_pdf: Path) -> int:
    """Render one CV YAML (which must live inside the repo) to PDF. Returns the page count."""
    rel = Path(cv_yaml).resolve().relative_to(ROOT).as_posix()
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    typst.compile(
        str(TEMPLATE), output=str(out_pdf), root=str(ROOT), sys_inputs={"cv": "/" + rel}, font_paths=[str(FONTS)]
    )
    return len(re.findall(rb"/Type\s*/Page(?!s)", out_pdf.read_bytes()))


def pdf_name(company: str, title: str) -> str:
    """Company_Role_CV.pdf, safe for any filesystem."""
    clean = lambda s: re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-") or "Job"
    return f"{clean(company)}_{clean(title)}_CV.pdf"


def _texts(cv: dict) -> list[str]:
    texts = [cv.get("summary", "")]
    for job in cv.get("experience", []):
        texts += job.get("bullets", [])
    return texts


def _sources() -> list[dict]:
    return [load(MASTER)] + [load(p) for p in sorted(VARIANTS_DIR.glob("*.yaml"))]


def skill_policy() -> dict:
    return load(SKILL_POLICY)


def never_claimed() -> set[str]:
    """Skills the candidate doesn't have and must never be listed, even if an ad asks for them."""
    return {s.lower() for s in skill_policy().get("never_claim", [])}


def forbidden_names() -> re.Pattern | None:
    """Words that must never appear in CV text: unnamed personal projects, and spelled-out unclaimed skills."""
    words = skill_policy().get("never_name", [])
    return re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b", re.IGNORECASE) if words else None


def required_skills(variant: str) -> list[str]:
    """Skills every CV of this variant must list: the 'always' tier plus the variant's own tier."""
    policy = skill_policy()
    return policy["always"] + policy["variant"].get(variant, [])


def allowed_skills() -> set[str]:
    """Anything true about the candidate: the CVs plus every tier of the skill policy (job-only skills live only there)."""
    policy = skill_policy()
    tiers = policy["always"] + policy["job"] + [s for names in policy["variant"].values() for s in names]
    return {item.lower() for cv in _sources() for group in cv["skills"] for item in group["items"]} | {
        s.lower() for s in tiers
    }


def allowed_numbers() -> set[str]:
    return {n for cv in _sources() for text in _texts(cv) + [str(cv)] for n in _NUMBER.findall(text)}


def validate_tailored(tailored: dict, variant: str) -> list[str]:
    """Return a list of problems; an empty list means the tailored CV may be sent."""
    if variant not in variant_names():
        return [f"unknown variant '{variant}' (have: {', '.join(variant_names())})"]
    base = load(VARIANTS_DIR / f"{variant}.yaml")
    errors = []

    for field in ("name", "headline", "contact", "education"):
        if tailored.get(field) != base.get(field):
            errors.append(f"'{field}' must stay exactly as in the {variant} variant")

    # Links in the variant's summary (the article) must survive tailoring, wherever the sentence moves.
    for target in _LINK_TARGET.findall(base.get("summary", "")):
        if target not in tailored.get("summary", ""):
            errors.append(f"summary must keep the link {target}[...]")

    jobs, base_jobs = tailored.get("experience", []), base["experience"]
    if [(j.get("title"), j.get("org"), j.get("dates")) for j in jobs] != [
        (j["title"], j.get("org"), j["dates"]) for j in base_jobs
    ]:
        errors.append("experience entries (titles, employers, dates and order) must match the variant exactly")
    for job, base_job in zip(jobs, base_jobs):
        # Gap entries (title and dates only) have no bullets in the variant and must stay that way.
        if not base_job.get("bullets"):
            if job.get("bullets"):
                errors.append(f"'{job.get('title')}' must stay without bullets, as in the {variant} variant")
        elif not job.get("bullets"):
            errors.append(f"'{job.get('title')}' has no bullets")

    skills, never = allowed_skills(), never_claimed()
    for group in tailored.get("skills", []):
        for item in group.get("items", []):
            if item.lower() in never:
                errors.append(f"skill '{item}' is never claimed")
            elif item.lower() not in skills:
                errors.append(f"skill '{item}' isn't in the master CV or any variant")

    listed = {item.lower() for group in tailored.get("skills", []) for item in group.get("items", [])}
    for name in required_skills(variant):
        if name.lower() not in listed:
            errors.append(f"required skill '{name}' is missing (see cv/skill-policy.yaml)")

    numbers, forbidden = allowed_numbers(), forbidden_names()
    for text in _texts(tailored):
        visible = _LINK_TARGET.sub("#link(", text)  # URLs inside #link("...") are neither claims nor markup
        if forbidden and (hit := forbidden.search(visible)):
            errors.append(f"names a personal project or unclaimed skill: '{hit.group()}'")
        for n in _NUMBER.findall(visible):
            if n not in numbers:
                errors.append(f"number '{n}' doesn't appear in any source CV: '{text[:60]}…'")
        if _UNSAFE_MARKUP.search(visible):
            errors.append(f"unescaped Typst character '{_UNSAFE_MARKUP.search(visible).group()}' in: '{text[:60]}…'")
    return errors
