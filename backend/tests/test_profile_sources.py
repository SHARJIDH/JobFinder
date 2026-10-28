import io
from pathlib import Path

from docx import Document as Docx

from app.core.yaml_config import Profile, load_profile
from app.llm.service import LLMService, ProviderResponse
from app.llm.tasks import ProfileDraft, candidate_facts
from app.services.profile_builder import merge_draft

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx(text: str) -> bytes:
    d = Docx()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


AI_RESUME = "Test Candidate\nProjects: RAG Notes Assistant built with Python, LangChain and PostgreSQL.\nSkills: Python, LangChain, Docker\n" * 2
WEB_RESUME = "Test Candidate\nProjects: Portfolio site built with Next.js, TypeScript and Tailwind.\nSkills: React, TypeScript\n" * 2


def _upload(client, text, name, label="", data=None):
    return client.post("/api/profile/resume", files={"file": (name, data or _docx(text), DOCX)}, data={"label": label})


def test_multiple_resumes_all_count_as_evidence(client, test_profile):
    ai_bytes = _docx(AI_RESUME)
    assert _upload(client, AI_RESUME, "ai.docx", "AI resume", ai_bytes).status_code == 200
    r = _upload(client, WEB_RESUME, "web.docx", "Web resume")
    assert r.status_code == 200
    data = r.json()
    assert [x["label"] for x in data["resumes"]] == ["AI resume", "Web resume"] and all(x["included"] for x in data["resumes"])
    ev = {e["canonical"]: e for e in data["skill_evidence"]}
    assert ev["React"]["usable"] and ev["React"]["resumes"] == ["Web resume"]
    assert "LangChain" in data["resume_suggestions"] and "TypeScript" in data["resume_suggestions"]

    # duplicate file rejected
    assert _upload(client, AI_RESUME, "ai-copy.docx", data=ai_bytes).status_code == 400

    # excluding a resume removes its evidence
    web_id = data["resumes"][1]["id"]
    data = client.patch(f"/api/profile/resumes/{web_id}", json={"included": False, "label": "Frontend"}).json()
    ev = {e["canonical"]: e for e in data["skill_evidence"]}
    assert not ev["React"]["usable"] and data["resumes"][1]["label"] == "Frontend"

    # deleting removes the file too
    path_count = len(list(Path(load_profile.__globals__["get_settings"]().uploads_dir).glob("resume-*")))
    data = client.delete(f"/api/profile/resumes/{web_id}").json()
    assert len(data["resumes"]) == 1
    assert len(list(Path(load_profile.__globals__["get_settings"]().uploads_dir).glob("resume-*"))) == path_count - 1


def test_context_notes_are_evidence_and_facts(client, test_profile):
    r = client.put("/api/profile/notes", json={"notes": [
        {"title": "Hackathon", "text": "Built a Kubernetes operator in Go with two teammates; won 2nd place at college fest."},
        {"title": "", "text": "   "},
    ]})
    assert r.status_code == 200
    data = r.json()
    assert len(data["context_notes"]) == 1 and data["context_notes"][0]["id"] == "note-1"
    ev = {e["canonical"]: e for e in data["skill_evidence"]}
    assert ev["Kubernetes"]["usable"] and ev["Kubernetes"]["in_notes"]  # declared + described in notes
    facts = candidate_facts(load_profile(), ["Python"])
    assert "Kubernetes operator" in facts and "[note-1] Hackathon" in facts


def test_merge_draft_unions_without_losing_user_data(test_profile):
    draft = ProfileDraft(
        headline="x", summary="y",
        education=[{"degree": "B.Tech in Electrical Engineering", "institution": "IIT Tirupati", "start": "", "end": "", "grade": ""}],
        experience=[{"title": "Associate Software Engineer", "company": "Kyndryl India", "location": "Hyderabad", "start": "2025-07",
                     "end": "", "technologies": ["Python", "Docker"], "source": "resume: AI resume",
                     "bullets": ["Built internal REST APIs in Python and FastAPI for an automation dashboard",  # duplicate
                                 "Containerised two internal services with Docker"]}],
        projects=[{"name": "RAG Notes Assistant", "url": "", "technologies": ["LangChain"], "source": "resume: AI resume",
                   "bullets": ["Added citation display to answers"]},
                  {"name": "Portfolio Site", "url": "https://x.dev", "technologies": ["Next.js"], "source": "resume: Web resume",
                   "bullets": ["Built a personal portfolio with Next.js"]}],
        skills=["Python", "Docker", "LangChain"], certifications=[], achievements=[])
    merged = merge_draft(test_profile, draft)
    assert merged.contact.full_name == "Test Candidate" and merged.headline == test_profile.headline  # not overwritten
    kyn = merged.experience[0]
    assert len(merged.experience) == 1 and len(kyn.bullets) == 3 and kyn.bullets[0].id == test_profile.experience[0].bullets[0].id
    assert "Docker" in kyn.technologies and kyn.location == "Hyderabad"
    assert [p.name for p in merged.projects] == ["RAG Notes Assistant", "Portfolio Site"]
    assert merged.projects[1].source == "resume: Web resume"
    assert len(merged.education) == 1
    sk = {s.name: s.verified for s in merged.skills}
    assert sk["Python"] is True and sk["Docker"] is False and sk["LangChain"] is False


class DraftFake:
    name = "fake"

    def model_for(self, tier):
        return "fake"

    def generate(self, *, system, prompt, schema, tier, max_output_tokens):
        assert "DOCUMENT: resume: AI resume" in prompt and "DOCUMENT: note: Hackathon" in prompt
        assert "test@example.com" not in prompt
        d = ProfileDraft(headline="", summary="", education=[], experience=[], skills=["LangChain"], certifications=[],
                         achievements=[], projects=[{"name": "Go Operator", "url": "", "technologies": ["Go", "Kubernetes"],
                                                     "bullets": ["Built a Kubernetes operator in Go"], "source": "note: Hackathon"}])
        return ProviderResponse(text=d.model_dump_json(), input_tokens=10, output_tokens=10, model="fake")


def test_draft_from_all_resumes_and_notes(client, test_profile, monkeypatch):
    _upload(client, AI_RESUME + "\ncontact: test@example.com", "ai.docx", "AI resume")
    client.put("/api/profile/notes", json={"notes": [{"title": "Hackathon", "text": "Built a Kubernetes operator in Go."}]})
    import app.api.profile as prof

    monkeypatch.setattr(prof, "LLMService", lambda db: LLMService(db, provider=DraftFake()))
    r = client.post("/api/profile/resume/draft")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added_projects"] == 1 and "Go Operator" in body["yaml"] and "RAG Notes Assistant" in body["yaml"]
    assert Profile.model_validate(__import__("yaml").safe_load(body["yaml"])).context_notes  # notes kept
    # draft is not saved until the user saves it
    assert not any(p.name == "Go Operator" for p in load_profile().projects)
