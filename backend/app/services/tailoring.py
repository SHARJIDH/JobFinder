"""Job-specific resume and cover-letter generation with truthfulness validation."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.safety import detect_injection, unsupported_numbers
from ..core.skills import canonical_skill, extract_skills
from ..core.text import dumps, loads, norm
from ..core.yaml_config import Profile
from ..llm.service import LLMError, LLMService
from ..llm.tasks import PROMPT_VERSION_TAILOR, TailorOut, candidate_facts, tailor_prompt
from ..models import Document, Job
from . import render
from .profile_facts import evidence, usable_skills

TAILOR_DETERMINISTIC_VERSION = "d1"


@dataclass
class Validation:
    rejected_bullets: list[dict] = field(default_factory=list)
    dropped_skills: list[str] = field(default_factory=list)
    dropped_cover_paragraphs: list[dict] = field(default_factory=list)
    summary_fallback: str = ""
    warnings: list[str] = field(default_factory=list)
    injection_flags: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # resumes included when this was generated
    latex_notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _job_terms(job: Job) -> list[str]:
    return loads(job.required_skills_json, []) + loads(job.preferred_skills_json, [])


def _relevance(text: str, job: Job) -> float:
    skills = set(extract_skills(text))
    req, pref = set(loads(job.required_skills_json, [])), set(loads(job.preferred_skills_json, []))
    title_tokens = {t for t in norm(job.title).split() if len(t) > 3}
    tok = set(norm(text).split())
    return 2 * len(skills & req) + len(skills & pref) + 0.5 * len(tok & title_tokens)


def _ordered_skills(usable: list[str], job: Job) -> list[str]:
    req, pref = loads(job.required_skills_json, []), loads(job.preferred_skills_json, [])
    rank = {s.lower(): 0 for s in req} | {s.lower(): 1 for s in pref if s.lower() not in {r.lower() for r in req}}
    return sorted(usable, key=lambda s: (rank.get(s.lower(), 2), s.lower()))


def _deterministic_summary(profile: Profile, job: Job, usable: list[str]) -> str:
    parts = []
    if profile.headline:
        parts.append(profile.headline.rstrip(".") + ".")
    elif profile.experience:
        x = profile.experience[0]
        parts.append(f"{x.title} at {x.company}.")
    if profile.education:
        e = profile.education[0]
        parts.append(f"{e.degree}, {e.institution}.")
    matched = [s for s in _ordered_skills(usable, job) if s in set(_job_terms(job))][:5]
    if matched:
        parts.append("Skills include " + ", ".join(matched) + ".")
    if profile.summary and not profile.headline:
        parts = [profile.summary.strip()] + parts[1:]
    return " ".join(parts)


def _base_content(profile: Profile) -> dict:
    return {
        "contact": profile.contact.model_dump(),
        "headline": profile.headline,
        "education": [e.model_dump() for e in profile.education],
        "certifications": [c.model_dump() for c in profile.certifications],
        "achievements": [b.text for b in profile.achievements],
    }


def deterministic_tailor(profile: Profile, job: Job, usable: list[str]) -> dict:
    content = _base_content(profile)
    content["summary"] = _deterministic_summary(profile, job, usable)
    content["skills"] = _ordered_skills(usable, job)
    content["experience"] = [
        {"id": x.id, "title": x.title, "company": x.company, "location": x.location, "start": x.start, "end": x.end,
         "bullets": [{"source_id": b.id, "text": b.text, "rewritten": False}
                     for b in sorted(x.bullets, key=lambda b: -_relevance(b.text, job))]}
        for x in profile.experience
    ]
    projects = sorted(profile.projects, key=lambda p: -_relevance(" ".join([p.name, *p.technologies, *(b.text for b in p.bullets)]), job))
    content["projects"] = [
        {"id": p.id, "name": p.name, "url": p.url, "technologies": p.technologies,
         "bullets": [{"source_id": b.id, "text": b.text, "rewritten": False} for b in p.bullets]}
        for p in projects[:4]
    ]
    req = loads(job.required_skills_json, [])
    have = {s.lower() for s in usable}
    content["missing_qualifications"] = [f"No verified evidence for required skill: {s}" for s in req if s.lower() not in have]
    content["change_log"] = [
        "Reordered skills to put this job's required/preferred skills first.",
        "Reordered bullets within each role by relevance to the job description.",
        "Selected the most relevant projects." if profile.projects else "No projects in profile yet.",
        "Bullet wording unchanged (deterministic mode).",
    ]
    content["questions_for_candidate"] = []
    return content


def _validate_bullet(text: str, source: str, allowed_skills: set[str]) -> str | None:
    """Return a rejection reason or None if the rewritten bullet is supported by its source."""
    if not text.strip():
        return "empty"
    nums = unsupported_numbers(text, source)
    if nums:
        return f"introduces numbers not in source: {', '.join(sorted(nums))}"
    new_skills = set(extract_skills(text)) - set(extract_skills(source)) - allowed_skills
    if new_skills:
        return f"introduces tools/skills not in source: {', '.join(sorted(new_skills))}"
    if len(text) > max(2 * len(source), len(source) + 120):
        return "much longer than the source bullet"
    return None


def apply_llm_output(out: TailorOut, profile: Profile, job: Job, usable: list[str], facts: str) -> tuple[dict, Validation]:
    v = Validation()
    content = _base_content(profile)
    usable_l = {canonical_skill(s).lower(): canonical_skill(s) for s in usable}

    # skills
    skills = []
    for s in out.skills:
        c = canonical_skill(s)
        if c.lower() in usable_l and usable_l[c.lower()] not in skills:
            skills.append(usable_l[c.lower()])
        else:
            v.dropped_skills.append(s)
    for s in _ordered_skills(usable, job):
        if s not in skills:
            skills.append(s)
    content["skills"] = skills

    def sections(items, llm_sections, kind):
        by_id = {s.source_id: s for s in llm_sections}
        built = []
        for it in items:
            src = {b.id: b.text for b in it.bullets}
            tech = {canonical_skill(t) for t in getattr(it, "technologies", [])}
            sec = by_id.get(it.id)
            bullets = []
            if sec:
                for tb in sec.bullets:
                    if tb.source_id not in src:
                        v.rejected_bullets.append({"source_id": tb.source_id, "text": tb.text, "reason": "unknown source bullet id"})
                        continue
                    if any(b["source_id"] == tb.source_id for b in bullets):
                        continue
                    reason = _validate_bullet(tb.text, src[tb.source_id], tech)
                    if reason:
                        v.rejected_bullets.append({"source_id": tb.source_id, "text": tb.text, "reason": reason})
                        bullets.append({"source_id": tb.source_id, "text": src[tb.source_id], "rewritten": False})
                    else:
                        bullets.append({"source_id": tb.source_id, "text": tb.text.strip(), "rewritten": tb.text.strip() != src[tb.source_id]})
            if kind == "experience" and not bullets:
                bullets = [{"source_id": b.id, "text": b.text, "rewritten": False} for b in it.bullets]
            if kind == "projects" and not sec:
                continue
            built.append((it, bullets))
        return built

    content["experience"] = [
        {"id": x.id, "title": x.title, "company": x.company, "location": x.location, "start": x.start, "end": x.end, "bullets": b}
        for x, b in sections(profile.experience, out.experience, "experience")
    ]
    known_proj = {p.id for p in profile.projects}
    for s in out.projects:
        if s.source_id not in known_proj:
            v.warnings.append(f"model referenced unknown project id {s.source_id!r}; ignored")
    content["projects"] = [
        {"id": p.id, "name": p.name, "url": p.url, "technologies": p.technologies, "bullets": b}
        for p, b in sections(profile.projects, out.projects, "projects")
    ]

    # summary
    summary = out.summary.strip()
    bad_nums = unsupported_numbers(summary, facts)
    bad_skills = {s for s in extract_skills(summary) if s.lower() not in usable_l}
    years = re.findall(r"(\d+(?:\.\d+)?)\+?\s*years?", summary, re.I)
    too_many_years = any(float(y) > profile.years_of_experience() + 0.05 for y in years)
    if not summary or bad_nums or bad_skills or too_many_years:
        reason = ("numbers " + ",".join(sorted(bad_nums)) if bad_nums else "") + \
                 (" skills " + ",".join(sorted(bad_skills)) if bad_skills else "") + (" years claim" if too_many_years else "")
        v.summary_fallback = f"model summary rejected ({reason.strip() or 'empty'}); used deterministic summary"
        summary = _deterministic_summary(profile, job, usable)
    content["summary"] = summary

    # cover letter
    paras = []
    for p in out.cover_letter_paragraphs:
        nums = unsupported_numbers(p, facts + "\n" + job.title + "\n" + job.description)
        bad = {s for s in extract_skills(p) if s.lower() not in usable_l} - set(extract_skills(job.company))
        # a skill named in the cover letter must be one the candidate can claim
        if nums or bad:
            v.dropped_cover_paragraphs.append({"text": p, "reason": f"numbers={sorted(nums)} skills={sorted(bad)}"})
        else:
            paras.append(p.strip())
    content["cover_letter_paragraphs"] = paras
    content["change_log"] = [c[:300] for c in out.change_log][:10]
    content["missing_qualifications"] = [c[:300] for c in out.missing_qualifications][:10]
    content["questions_for_candidate"] = [c[:300] for c in out.questions_for_candidate][:8]
    return content, v


def deterministic_cover_letter(profile: Profile, job: Job, usable: list[str]) -> list[str]:
    matched = [s for s in _ordered_skills(usable, job) if s in set(_job_terms(job))][:4]
    role = profile.experience[0] if profile.experience else None
    p1 = f"I am applying for the {job.title} role at {job.company}."
    if role:
        p1 += f" I currently work as {role.title} at {role.company}."
    p2 = ""
    if matched:
        p2 = f"The role's focus on {', '.join(matched)} aligns with skills on my resume."
    best = None
    for x in profile.experience + profile.projects:
        for b in x.bullets:
            if best is None or _relevance(b.text, job) > _relevance(best, job):
                best = b.text
    if best:
        p2 += f" For example: {best.rstrip('.')}."
    p3 = "I would welcome the opportunity to discuss how I can contribute to your team. Thank you for your consideration."
    return [x for x in (p1, p2.strip(), p3) if x]


def generate_documents(db: Session, job: Job, profile: Profile, llm: LLMService | None, *,
                       want_cover: bool = True, use_llm: bool = True, latex: bool = True) -> tuple[Document, Document | None]:
    settings = get_settings()
    ev = evidence(db, profile)
    resume_text = ev.resume_text
    usable = usable_skills(profile, resume_text)
    facts = candidate_facts(profile, usable, include_bullet_ids=True)
    validation = Validation(injection_flags=detect_injection(job.description), sources=ev.sources())
    model, generator, prompt_version = "", "deterministic", TAILOR_DETERMINISTIC_VERSION

    content = None
    if use_llm and llm and llm.available:
        try:
            system, prompt = tailor_prompt(job, facts, want_cover)
            out, meta = llm.structured(task="tailor", system=system, prompt=prompt, schema=TailorOut, tier="quality",
                                       prompt_version=PROMPT_VERSION_TAILOR, max_output_tokens=4096)
            content, v2 = apply_llm_output(out, profile, job, usable, facts)
            v2.injection_flags = validation.injection_flags
            v2.sources = validation.sources
            validation = v2
            model, generator, prompt_version = meta["model"], "llm", PROMPT_VERSION_TAILOR
        except LLMError as e:
            validation.warnings.append(f"AI tailoring unavailable, used deterministic tailoring: {e}")
    if content is None:
        content = deterministic_tailor(profile, job, usable)
        content["cover_letter_paragraphs"] = deterministic_cover_letter(profile, job, usable) if want_cover else []
    if not profile.contact.full_name:
        validation.warnings.append("Profile has no name/contact details; add them before sending.")
    if not any(x.bullets for x in profile.experience) and not profile.projects:
        validation.warnings.append("Profile has no experience bullets or projects; the resume will be sparse. Add verified bullets in Profile.")

    gen_id = uuid.uuid4().hex[:12]
    out_dir = settings.documents_dir / f"job-{job.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rdocx, rpdf = out_dir / f"{gen_id}-resume.docx", out_dir / f"{gen_id}-resume.pdf"
    latex_fields: dict = {}
    if latex and settings.resume_format == "latex":
        from .latex_tailor import build_tailored_latex

        build = build_tailored_latex(db, llm if use_llm else None, job, content, facts, usable)
        validation.latex_notes = build.notes
        latex_fields = {"tex_source": build.tex, "tex_origin": build.origin, "compile_log": build.result.log[:8000],
                        "page_count": build.result.pages}
        if build.result.ok and build.result.pdf:
            rpdf.write_bytes(build.result.pdf)
            if build.origin == "builtin" and build.content is not None and build.content is not content:
                content = {**content, "experience": build.content["experience"], "projects": build.content["projects"]}
        else:
            validation.warnings.append("LaTeX could not be compiled here; PDF rendered with the classic template. "
                                       "Download the .tex or open it in Overleaf. " + build.result.log[:300])
            render.resume_pdf(content, rpdf)
    else:
        render.resume_pdf(content, rpdf)
    render.resume_docx(content, rdocx)
    common = dict(job_id=job.id, generation_id=gen_id, source_resume_id=ev.resumes[-1].id if ev.resumes else None,
                  source_resume_sha256=ev.fingerprint, profile_hash=profile.fingerprint(),
                  job_content_hash=job.content_hash, generator=generator, model=model, prompt_version=prompt_version,
                  validation_json=dumps(validation.as_dict()))
    resume_doc = Document(kind="resume", content_json=dumps(content), docx_path=str(rdocx), pdf_path=str(rpdf),
                          **latex_fields, **common)
    db.add(resume_doc)
    cover_doc = None
    if want_cover and content.get("cover_letter_paragraphs"):
        cdocx, cpdf = out_dir / f"{gen_id}-cover.docx", out_dir / f"{gen_id}-cover.pdf"
        letter = {"contact": content["contact"], "company": job.company, "title": job.title,
                  "paragraphs": content["cover_letter_paragraphs"]}
        render.cover_docx(letter, cdocx)
        render.cover_pdf(letter, cpdf)
        cover_doc = Document(kind="cover_letter", content_json=dumps(letter), docx_path=str(cdocx), pdf_path=str(cpdf), **common)
        db.add(cover_doc)
    db.flush()
    return resume_doc, cover_doc
