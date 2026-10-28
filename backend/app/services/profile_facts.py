"""Verified-fact helpers: which skills the candidate can truthfully claim."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.skills import canonical_skill, extract_skills, text_mentions_skill
from ..core.yaml_config import Profile
from ..models import MasterResume


@dataclass
class SkillEvidence:
    name: str
    canonical: str
    declared: bool
    confirmed: bool
    in_resume: bool
    in_experience: list[str]
    usable: bool  # may be claimed in generated documents
    in_notes: bool = False
    resumes: list[str] | None = None  # labels of resumes that mention it


def included_resumes(db: Session) -> list[MasterResume]:
    return list(db.scalars(select(MasterResume).where(MasterResume.is_active.is_(True)).order_by(MasterResume.id)))


def active_resume(db: Session) -> MasterResume | None:
    """Most recently uploaded included resume (kept for callers that need a single file)."""
    rs = included_resumes(db)
    return rs[-1] if rs else None


def resume_label(r: MasterResume) -> str:
    return r.label or r.original_filename


@dataclass
class Evidence:
    """Everything the candidate has provided: all included resumes plus their own context notes."""
    resumes: list[MasterResume]
    notes_text: str

    @property
    def resume_text(self) -> str:
        return "\n\n".join(r.text for r in self.resumes)

    @property
    def fingerprint(self) -> str:
        from ..core.text import sha256_text

        return sha256_text(*sorted(r.sha256 for r in self.resumes)) if self.resumes else ""

    def sources(self) -> list[dict]:
        return [{"id": r.id, "label": resume_label(r), "sha256": r.sha256} for r in self.resumes]


def evidence(db: Session, profile: Profile) -> Evidence:
    return Evidence(included_resumes(db), profile.notes_text())


def skill_evidence(profile: Profile, resume_text: str, resumes: list[MasterResume] | None = None) -> list[SkillEvidence]:
    notes = profile.notes_text()
    out: dict[str, SkillEvidence] = {}
    tech_sources: dict[str, list[str]] = {}
    for x in profile.experience:
        for t in x.technologies:
            tech_sources.setdefault(canonical_skill(t), []).append(x.id)
        for b in x.bullets:
            for s in extract_skills(b.text):
                tech_sources.setdefault(s, []).append(b.id)
    for p in profile.projects:
        for t in p.technologies:
            tech_sources.setdefault(canonical_skill(t), []).append(p.id)
        for b in p.bullets:
            for s in extract_skills(b.text):
                tech_sources.setdefault(s, []).append(b.id)

    def which(name: str) -> list[str]:
        return [resume_label(r) for r in resumes or [] if text_mentions_skill(r.text, name)]

    for sk in profile.skills:
        canon = canonical_skill(sk.name)
        in_res = bool(resume_text) and text_mentions_skill(resume_text, sk.name)
        in_notes = bool(notes) and text_mentions_skill(notes, sk.name)
        srcs = tech_sources.get(canon, [])
        out[canon] = SkillEvidence(sk.name, canon, True, sk.verified, in_res, srcs,
                                   sk.verified or in_res or in_notes or bool(srcs), in_notes, which(sk.name))
    for canon, srcs in tech_sources.items():
        if canon not in out:
            in_res = bool(resume_text) and text_mentions_skill(resume_text, canon)
            out[canon] = SkillEvidence(canon, canon, False, False, in_res, srcs, True,
                                       bool(notes) and text_mentions_skill(notes, canon), which(canon))
    return list(out.values())


def usable_skills(profile: Profile, resume_text: str) -> list[str]:
    return [e.canonical for e in skill_evidence(profile, resume_text) if e.usable]


def resume_skill_suggestions(profile: Profile, resume_text: str) -> list[str]:
    """Taxonomy skills found in the resumes or your notes that are not yet in the profile."""
    declared = {canonical_skill(s.name) for s in profile.skills}
    found = extract_skills((resume_text or "") + "\n" + profile.notes_text())
    return [s for s in found if s not in declared]
