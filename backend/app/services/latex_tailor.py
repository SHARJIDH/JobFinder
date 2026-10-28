"""Tailored LaTeX resumes: apply the validated tailoring plan to your own LaTeX template (or the built-in
one), compile, and keep it to one page — deterministically for the built-in template, or with Gemini
looking at screenshots of the rendered pages for custom templates. Every AI-produced LaTeX version is
checked so it cannot introduce numbers, skills or claims that are not in your facts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.safety import SYSTEM_GUARD, wrap_untrusted
from ..llm.service import LLMError, LLMService
from ..models import Job, MasterResume
from .latex import CompileResult, Layout, compile_tex, page_images, render_builtin, tex_to_text, unsafe_reasons, unsupported_changes

PROMPT_VERSION_LATEX = "x1"
TEX_TYPE = "application/x-tex"


class LatexOut(BaseModel):
    tex: str
    changes: list[str]


class LatexInvalid(ValueError):
    pass


@dataclass
class LatexBuild:
    tex: str
    origin: str  # builtin | template | edited
    result: CompileResult
    notes: list[str] = field(default_factory=list)
    content: dict | None = None  # built-in: the (possibly trimmed) content actually rendered


def template_resume(db: Session) -> MasterResume | None:
    return db.scalars(select(MasterResume).where(MasterResume.is_active.is_(True), MasterResume.content_type == TEX_TYPE)
                      .order_by(MasterResume.id.desc())).first()


def _content_text(content: dict) -> str:
    return json.dumps(content, ensure_ascii=False)


def validate_ai_tex(tex: str, allowed_text: str, usable: list[str]) -> None:
    if "\\begin{document}" not in tex or "\\end{document}" not in tex:
        raise LatexInvalid("model output is not a complete LaTeX document")
    bad = unsafe_reasons(tex)
    if bad:
        raise LatexInvalid("model output uses forbidden LaTeX: " + "; ".join(bad))
    ch = unsupported_changes(tex, allowed_text, usable)
    if ch["numbers"] or ch["skills"]:
        raise LatexInvalid(f"model added unsupported content: numbers={ch['numbers']} skills={ch['skills']}")


# --------------------------------------------------------------------------- applying a custom template

def apply_template(llm: LLMService, template_src: str, content: dict, job: Job, facts: str, usable: list[str]) -> tuple[str, list[str]]:
    system = SYSTEM_GUARD + (
        " You edit a LaTeX resume. Keep the template's preamble, macros, fonts and visual style exactly. Replace "
        "the body content with the TAILORED PLAN: its summary, skill order, chosen roles/projects and bullet wording. "
        "Use ONLY text from the TAILORED PLAN or the template itself; never add numbers, tools, skills, employers, "
        "dates or claims. Escape LaTeX special characters (& % $ # _ { } ~ ^). Do not use \\input, \\include, "
        "\\write18 or file/shell commands. Target exactly one page. Return the complete compilable document in `tex` "
        "and a short list of what you changed in `changes`."
    )
    prompt = (f"TARGET JOB: {job.title} at {job.company}\n\nTAILORED PLAN (validated facts, JSON):\n{_content_text(content)[:20000]}\n\n"
              f"TEMPLATE (LaTeX):\n{wrap_untrusted(template_src, 'latex_template', 40000)}")
    out, _ = llm.structured(task="latex_apply", system=system, prompt=prompt, schema=LatexOut, tier="quality",
                            prompt_version=PROMPT_VERSION_LATEX, max_output_tokens=12000)
    allowed = "\n".join([tex_to_text(template_src), facts, _content_text(content), job.title, job.company])
    validate_ai_tex(out.tex, allowed, usable)
    return out.tex, out.changes[:12]


def ai_revise(llm: LLMService, tex: str, instruction: str, usable: list[str], allowed_text: str,
              pdf: bytes | None = None, pages: int | None = None) -> tuple[str, list[str]]:
    """Ask Gemini to revise LaTeX (e.g. fit to one page), optionally showing screenshots of the rendered pages.
    The revision may only rephrase/shorten/remove/re-space — it is rejected if it adds new numbers or skills."""
    images = page_images(pdf, scale=1.0, max_pages=3) if pdf else None
    system = SYSTEM_GUARD + (
        " You revise a LaTeX resume. Allowed: shorten or remove the least relevant lines, merge bullets, adjust "
        "spacing (\\vspace, itemsep), margins (not below 0.4in) and base font size (not below 9.5pt). Not allowed: "
        "adding any new text, numbers, tools, skills or claims, or file/shell commands. Return the complete "
        "compilable document in `tex` and a short list of changes in `changes`."
    )
    prompt = (f"INSTRUCTION: {instruction[:1500]}\n"
              + (f"The current PDF has {pages} page(s); screenshots of the rendered pages are attached.\n" if images else "")
              + f"\nCURRENT LATEX:\n{wrap_untrusted(tex, 'latex', 40000)}")
    out, _ = llm.structured(task="latex_revise", system=system, prompt=prompt, schema=LatexOut, tier="quality",
                            prompt_version=PROMPT_VERSION_LATEX, max_output_tokens=12000, images=images)
    validate_ai_tex(out.tex, allowed_text + "\n" + tex_to_text(tex), usable)
    return out.tex, out.changes[:12]


def revise_and_compile(llm: LLMService, tex: str, instruction: str, usable: list[str], allowed_text: str,
                       pdf: bytes | None = None, pages: int | None = None) -> tuple[str, list[str], CompileResult]:
    """ai_revise + compile; if Gemini's LaTeX does not compile, give it the error log once to repair it."""
    new_tex, changes = ai_revise(llm, tex, instruction, usable, allowed_text, pdf, pages)
    res = compile_tex(new_tex)
    if not res.ok:
        fixed, more = ai_revise(llm, new_tex, "Fix this LaTeX compile error without changing any content. Remember a "
                                "line break with spacing is \\\\[2pt] (two backslashes), while \\[ starts math mode.\n"
                                "ERROR LOG:\n" + res.log[-1500:], usable, allowed_text + "\n" + tex_to_text(tex))
        fixed_res = compile_tex(fixed)
        if fixed_res.ok:
            return fixed, changes + ["repaired a LaTeX compile error"] + more[:3], fixed_res
    return new_tex, changes, res


# --------------------------------------------------------------------------- one-page fitting

LAYOUTS = [Layout(11, 0.6, 1.0, 6), Layout(10, 0.55, 0.5, 5), Layout(10, 0.45, 0.0, 4)]


def _trim(content: dict, max_projects: int, max_bullets: int) -> dict:
    c = json.loads(json.dumps(content))
    c["projects"] = c.get("projects", [])[:max_projects]
    for sec in ("experience", "projects"):
        for x in c.get(sec, []):
            x["bullets"] = x.get("bullets", [])[:max_bullets]
    return c


def fit_builtin(content: dict) -> LatexBuild:
    """Try tighter layouts first, then drop the least relevant bullets/projects (already ordered by relevance)."""
    notes: list[str] = []
    attempts = [(lay, None, None) for lay in LAYOUTS] + [(LAYOUTS[-1], p, b) for p, b in ((3, 4), (3, 3), (2, 3), (2, 2), (1, 2))]
    last: LatexBuild | None = None
    for lay, maxp, maxb in attempts:
        c = _trim(content, maxp, maxb) if maxp else content
        tex = render_builtin(c, lay)
        res = compile_tex(tex)
        last = LatexBuild(tex, "builtin", res, list(notes), c)
        if not res.ok or (res.pages or 0) <= 1:
            if maxp:
                last.notes.append(f"Trimmed to the {maxp} most relevant project(s) and {maxb} bullet(s) per entry to fit one page.")
            if lay is not LAYOUTS[0]:
                last.notes.append(f"Used {lay.font_pt}pt font and {lay.margin_in}in margins to fit one page.")
            return last
    assert last is not None
    last.notes.append(f"Still {last.result.pages} pages after trimming; edit manually.")
    return last


def fit_with_ai(llm: LLMService, build: LatexBuild, usable: list[str], allowed_text: str, job: Job, rounds: int = 2) -> LatexBuild:
    tex, res = build.tex, build.result
    for _ in range(rounds):
        if not res.ok or (res.pages or 0) <= 1:
            break
        try:
            new_tex, changes = ai_revise(
                llm, tex, f"Make this resume fit on exactly one page for the role '{job.title}' at {job.company}. "
                          "Prefer removing the least relevant content over shrinking text.", usable, allowed_text, res.pdf, res.pages)
        except (LLMError, LatexInvalid) as e:
            build.notes.append(f"AI one-page fit stopped: {e}")
            break
        new_res = compile_tex(new_tex)
        if not new_res.ok or (new_res.pages or 99) >= (res.pages or 0):
            build.notes.append("AI one-page fit attempt did not reduce the page count; kept the previous version.")
            break
        tex, res = new_tex, new_res
        build.notes.append("AI fit: " + "; ".join(changes))
    build.tex, build.result = tex, res
    return build


# --------------------------------------------------------------------------- entry point

def build_tailored_latex(db: Session, llm: LLMService | None, job: Job, content: dict, facts: str, usable: list[str]) -> LatexBuild:
    notes: list[str] = []
    tpl = template_resume(db)
    allowed = "\n".join([facts, _content_text(content), job.title, job.company])
    if tpl and llm and llm.available:
        try:
            tex, changes = apply_template(llm, tpl.text_source(), content, job, facts, usable)
            res = compile_tex(tex)
            if res.ok:
                build = LatexBuild(tex, "template", res, [f"Applied to your LaTeX template '{tpl.label or tpl.original_filename}'"] + changes)
                if (res.pages or 0) > 1:
                    build = fit_with_ai(llm, build, usable, allowed + "\n" + tex_to_text(tpl.text_source()), job)
                return build
            notes.append("Your template did not compile after tailoring; used the built-in template. " + res.log[:300])
        except (LLMError, LatexInvalid) as e:
            notes.append(f"Could not apply your LaTeX template ({e}); used the built-in template.")
    elif tpl:
        notes.append("Applying your own LaTeX template needs Gemini; used the built-in template.")
    build = fit_builtin(content)
    build.notes = notes + build.notes
    return build
