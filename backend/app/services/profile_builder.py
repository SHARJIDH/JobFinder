"""Merge an AI-extracted profile draft into the current profile without losing anything you wrote.

Rules (deterministic):
  * contact details, application facts, extra answers, interests and context notes are never touched;
  * experience is matched by (company, title), projects by name, education by (degree, institution):
    matches get the union of bullets/technologies, new entries are appended;
  * bullets are de-duplicated on normalised text; existing bullet ids are kept so earlier documents stay traceable;
  * skills are unioned; your `verified` flags are preserved and new skills start unverified.
"""
from __future__ import annotations

from ..core.skills import canonical_skill
from ..core.text import norm
from ..core.yaml_config import Profile
from ..llm.tasks import ProfileDraft


def _key(*parts: str) -> str:
    return "|".join(norm(p) for p in parts)


def _merge_bullets(existing: list[dict], new: list[str]) -> list[dict]:
    seen = {norm(b["text"]) for b in existing}
    out = list(existing)
    for t in new:
        t = (t or "").strip()
        if t and norm(t) not in seen:
            out.append({"text": t})
            seen.add(norm(t))
    return out


def _merge_list(existing: list[str], new: list[str]) -> list[str]:
    seen = {canonical_skill(x).lower() for x in existing}
    out = list(existing)
    for x in new:
        if x and canonical_skill(x).lower() not in seen:
            out.append(x)
            seen.add(canonical_skill(x).lower())
    return out


def _merge_source(a: str, b: str) -> str:
    parts = [p.strip() for p in f"{a};{b}".split(";") if p.strip()]
    return "; ".join(dict.fromkeys(parts))


def merge_draft(current: Profile, draft: ProfileDraft) -> Profile:
    data = current.model_dump()
    if not data["headline"] and draft.headline:
        data["headline"] = draft.headline
    if not data["summary"] and draft.summary:
        data["summary"] = draft.summary

    exp = {_key(x["company"], x["title"]): x for x in data["experience"]}
    for d in draft.experience:
        k = _key(d.company, d.title)
        if k in exp:
            x = exp[k]
            x["bullets"] = _merge_bullets(x["bullets"], d.bullets)
            x["technologies"] = _merge_list(x["technologies"], d.technologies)
            for f in ("location", "start", "end"):
                x[f] = x[f] or getattr(d, f)
            x["source"] = _merge_source(x.get("source", ""), d.source)
        else:
            new = {"title": d.title, "company": d.company, "location": d.location, "start": d.start, "end": d.end,
                   "technologies": d.technologies, "bullets": _merge_bullets([], d.bullets), "source": d.source}
            data["experience"].append(new)
            exp[k] = new

    proj = {_key(p["name"]): p for p in data["projects"]}
    for d in draft.projects:
        k = _key(d.name)
        if k in proj:
            p = proj[k]
            p["bullets"] = _merge_bullets(p["bullets"], d.bullets)
            p["technologies"] = _merge_list(p["technologies"], d.technologies)
            p["url"] = p["url"] or d.url
            p["source"] = _merge_source(p.get("source", ""), d.source)
        else:
            new = {"name": d.name, "url": d.url, "technologies": d.technologies,
                   "bullets": _merge_bullets([], d.bullets), "source": d.source}
            data["projects"].append(new)
            proj[k] = new

    edu = {_key(e["degree"], e["institution"]) for e in data["education"]}
    for e in draft.education:
        if _key(e.degree, e.institution) not in edu:
            data["education"].append(e.model_dump())
            edu.add(_key(e.degree, e.institution))

    known = {canonical_skill(s["name"]).lower() for s in data["skills"]}
    for s in draft.skills:
        if s and canonical_skill(s).lower() not in known:
            data["skills"].append({"name": s, "category": "", "proficiency": "", "verified": False})
            known.add(canonical_skill(s).lower())

    certs = {norm(c["name"]) for c in data["certifications"]}
    for c in draft.certifications:
        if c and norm(c) not in certs:
            data["certifications"].append({"name": c})
            certs.add(norm(c))
    data["achievements"] = _merge_bullets(data["achievements"], draft.achievements)
    return Profile.model_validate(data)
