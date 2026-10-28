"""LaTeX resume generation, safety, one-page fitting and the editor API."""
import io

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.llm.service import LLMService, ProviderResponse
from app.models import Document, Job
from app.services import latex as L
from app.services.latex_tailor import LatexOut, fit_builtin
from tests.helpers import ingest, posting

needs_tex = pytest.mark.skipif(L.compiler_path() is None, reason="Tectonic not installed")


def test_escaping_and_detex():
    assert L.tex_escape("R&D 100% $5 #1 a_b {x} ~ ^") == r"R\&D 100\% \$5 \#1 a\_b \{x\} \textasciitilde{} \textasciicircum{}"
    src = "\\documentclass{article}\\begin{document}\\section{Skills} Python \\& Go % comment 99\n\\\\ \\textbf{Kafka}\\end{document}"
    t = L.tex_to_text(src)
    assert "Python & Go" in t and "Kafka" in t and "99" not in t


def test_unsafe_latex_refused():
    for bad in [r"\immediate\write18{rm -rf ~}", r"\input{/etc/passwd}", r"\include{../secrets}", r"\usepackage{shellesc}",
                r"\openin\x=foo", r"\directlua{os.execute('x')}"]:
        assert L.unsafe_reasons(bad), bad
        assert not L.compile_tex(r"\documentclass{article}\begin{document}" + bad + r"\end{document}").ok
    assert not L.unsafe_reasons(r"\input{sections/experience}  % \write18 in a comment is fine")


def test_unsupported_changes_detects_new_facts():
    ch = L.unsupported_changes(r"\begin{document}Cut latency by 40\% using Kafka and Python\end{document}",
                               "Improved API performance using Python", ["Python"])
    assert ch["numbers"] == ["40%"] and ch["skills"] == ["Kafka"]


CONTENT = {"contact": {"full_name": "Test Candidate", "email": "t@example.com"}, "summary": "Engineer.", "skills": ["Python"],
           "experience": [{"id": "e", "title": "Associate Software Engineer", "company": "Kyndryl India", "location": "", "start": "2025-07",
                           "end": "", "bullets": [{"source_id": f"b{i}", "text": f"Built internal tool number {i} with Python and FastAPI for teams across the organisation", "rewritten": False} for i in range(12)]}],
           "projects": [{"id": f"p{j}", "name": f"Project {j}", "url": "", "technologies": ["Python"],
                         "bullets": [{"source_id": f"p{j}b{i}", "text": f"Implemented feature {i} of project {j} end to end including tests and documentation", "rewritten": False} for i in range(5)]}
                        for j in range(6)],
           "education": [{"degree": "B.Tech", "institution": "IIT Tirupati", "start": "", "end": "", "grade": ""}]}


def test_fit_builtin_tightens_then_trims(monkeypatch):
    calls = []

    def fake_compile(src, timeout=180):
        calls.append(src)
        pages = 1 if ("Project 2" not in src and "number 3" not in src) else 2  # fits only after trimming
        return L.CompileResult(True, b"%PDF-1.4", pages, "")

    monkeypatch.setattr("app.services.latex_tailor.compile_tex", fake_compile)
    build = fit_builtin(CONTENT)
    assert build.result.pages == 1 and "Project 2" not in build.tex
    assert len(build.content["projects"]) == 2 and len(build.content["experience"][0]["bullets"]) == 3
    assert any("Trimmed" in n for n in build.notes) and len(calls) > 3


def test_ai_edit_compile_error_gets_one_repair_round(monkeypatch):
    from app.services import latex_tailor as T

    instructions = []

    def fake_revise(llm, tex, instruction, usable, allowed, pdf=None, pages=None):
        instructions.append(instruction)
        return ("BROKEN" if len(instructions) == 1 else "FIXED"), ["change"]

    monkeypatch.setattr(T, "ai_revise", fake_revise)
    monkeypatch.setattr(T, "compile_tex", lambda src, timeout=180: L.CompileResult(src == "FIXED", b"%PDF" if src == "FIXED" else None, 1, "" if src == "FIXED" else "! Missing $ inserted."))
    tex, changes, res = T.revise_and_compile(None, "ORIG", "make name larger", [], "")
    assert tex == "FIXED" and res.ok and "repaired a LaTeX compile error" in changes
    assert len(instructions) == 2 and "Missing $ inserted" in instructions[1]


@needs_tex
def test_builtin_compiles_to_real_one_page_pdf():
    res = L.compile_tex(L.render_builtin(CONTENT))
    assert res.ok and res.pdf.startswith(b"%PDF") and res.pages >= 1
    build = fit_builtin(CONTENT)
    assert build.result.ok and build.result.pages == 1
    assert L.page_images(build.result.pdf)[0].startswith(b"\x89PNG")


class TexFake:
    """Fake Gemini that returns LaTeX for apply/revise tasks."""
    name = "fake"

    def __init__(self, tex):
        self.tex, self.calls, self.saw_images = tex, 0, False

    def model_for(self, tier):
        return "fake"

    def generate(self, *, system, prompt, schema, tier, max_output_tokens, images=None):
        self.calls += 1
        self.saw_images = self.saw_images or bool(images)
        return ProviderResponse(text=LatexOut(tex=self.tex, changes=["applied"]).model_dump_json(), input_tokens=1, output_tokens=1, model="fake")


TEMPLATE = (r"\documentclass{article}\usepackage{xcolor}\newcommand{\role}[1]{\textbf{#1}}" "\n"
            r"\begin{document}\section{Experience}\role{Old Title}\begin{itemize}\item Old bullet about the previous job"
            r" and the work that was done there\end{itemize}\section{Education}B.Tech, IIT Tirupati\end{document}")


def _setup(db, client):
    ingest(db, posting())
    r = client.post("/api/profile/resume", files={"file": ("mine.tex", TEMPLATE.encode(), "application/x-tex")}, data={"label": "My LaTeX"})
    assert r.status_code == 200, r.text
    return db.scalars(select(Job)).first()


def test_tex_upload_validation(client, test_profile):
    bad = client.post("/api/profile/resume", files={"file": ("x.tex", rb"\documentclass{article}\begin{document}\write18{ls}\end{document}", "text/x-tex")})
    assert bad.status_code == 400 and "forbidden" in bad.json()["detail"]
    assert client.post("/api/profile/resume", files={"file": ("x.tex", b"just text", "text/x-tex")}).status_code == 400


def test_template_applied_and_fabrication_rejected(client, db, test_profile, monkeypatch):
    monkeypatch.setenv("RESUME_FORMAT", "latex")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.latex_tailor.compile_tex", lambda src, timeout=180: L.CompileResult(True, b"%PDF-1.4 x", 1, ""))
    job = _setup(db, client)
    from app.core.yaml_config import load_profile
    from app.services.tailoring import generate_documents

    good = TEMPLATE.replace("Old Title", "Associate Software Engineer").replace("Old bullet", "Built internal REST APIs in Python and FastAPI")
    resume, _ = generate_documents(db, job, load_profile(), LLMService(db, provider=TexFake(good)), use_llm=True, want_cover=False)
    assert resume.tex_origin == "template" and "\\role{Associate Software Engineer}" in resume.tex_source

    lie = good.replace("FastAPI", "FastAPI, cutting costs by 75\\% with Kafka")
    from app.models import LlmCache
    db.query(LlmCache).delete()  # identical prompt would otherwise be served from cache
    db.commit()
    resume2, _ = generate_documents(db, job, load_profile(), LLMService(db, provider=TexFake(lie)), use_llm=True, want_cover=False)
    assert resume2.tex_origin == "builtin"  # fabricated metric + skill -> custom output rejected, safe built-in used
    from app.core.text import loads
    assert any("unsupported content" in n for n in loads(resume2.validation_json)["latex_notes"])


def test_editor_api_save_flags_and_fit(client, db, test_profile, monkeypatch):
    monkeypatch.setenv("RESUME_FORMAT", "latex")
    get_settings.cache_clear()
    pages = {"n": 2}

    def fake_compile(src, timeout=180):
        if L.unsafe_reasons(src):
            return L.CompileResult(False, None, None, "Refused to compile: source uses file or shell I/O")
        return L.CompileResult(True, b"%PDF-1.4 fake", pages["n"], "")

    for mod in ("app.services.latex_tailor", "app.services.latex"):
        monkeypatch.setattr(f"{mod}.compile_tex", fake_compile)
    monkeypatch.setattr("app.services.latex_tailor.page_images", lambda pdf, scale=1.0, max_pages=3: [b"\x89PNGfake"])
    ingest(db, posting())
    job = db.scalars(select(Job)).first()
    r = client.post(f"/api/jobs/{job.id}/generate", json={"use_ai": False, "cover_letter": False})
    doc_id = r.json()["resume"]["id"]
    lx = client.get(f"/api/documents/{doc_id}/latex").json()
    assert lx["origin"] == "builtin" and "\\begin{document}" in lx["source"]

    # unsafe edits refused, honest edits saved, invented facts flagged
    assert client.put(f"/api/documents/{doc_id}/latex", json={"source": lx["source"].replace("\\end{document}", "\\input{/etc/passwd}\\end{document}")}).status_code == 422
    edited = lx["source"].replace("\\end{document}", "Led a team of 12 engineers using Rust\n\\end{document}")
    out = client.put(f"/api/documents/{doc_id}/latex", json={"source": edited}).json()
    assert out["origin"] == "edited" and "12" in out["validation"]["latex_unverified"]["numbers"]
    assert "Rust" in out["validation"]["latex_unverified"]["skills"]
    tex = client.get(f"/api/documents/{doc_id}/download?format=tex")
    assert tex.status_code == 200 and b"Led a team" in tex.content

    # AI fit on an edited doc: Gemini sees screenshots; result must not add facts
    shorter = edited.replace("Led a team of 12 engineers using Rust\n", "")
    fake = TexFake(shorter)
    import app.api.jobs as jobs_api
    monkeypatch.setattr(jobs_api, "LLMService", lambda db: LLMService(db, provider=fake))

    def one_page(src, timeout=180):
        return L.CompileResult(True, b"%PDF-1.4 fit", 1 if "Led a team" not in src else 2, "")

    for mod in ("app.services.latex_tailor", "app.services.latex"):
        monkeypatch.setattr(f"{mod}.compile_tex", one_page)
    fit = client.post(f"/api/documents/{doc_id}/latex/fit").json()
    assert fit["page_count"] == 1 and fake.saw_images and "Led a team" not in fit["source"]
    assert db.get(Document, doc_id) is not None
    # inline preview for the editor
    assert client.get(f"/api/documents/{doc_id}/download?format=pdf&inline=true").headers["content-disposition"].startswith("inline")
