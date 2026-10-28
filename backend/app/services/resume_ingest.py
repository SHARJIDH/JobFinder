"""Master-resume upload: validation, storage and text extraction."""
from __future__ import annotations

import hashlib
import io
import secrets
import zipfile
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import MasterResume

ALLOWED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           ".tex": "application/x-tex"}


class UploadError(ValueError):
    pass


def validate_upload(filename: str, data: bytes, max_mb: int) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise UploadError("Only PDF, DOCX or LaTeX (.tex) files are accepted")
    if not data:
        raise UploadError("Empty file")
    if len(data) > max_mb * 1024 * 1024:
        raise UploadError(f"File exceeds {max_mb} MB")
    if ext == ".tex":
        from .latex import unsafe_reasons

        try:
            src = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UploadError("LaTeX file must be UTF-8 text") from e
        if "\\documentclass" not in src or "\\begin{document}" not in src:
            raise UploadError("LaTeX file must be a complete document (\\documentclass … \\begin{document})")
        bad = unsafe_reasons(src)
        if bad:
            raise UploadError("LaTeX file uses forbidden commands: " + "; ".join(bad))
        return ext
    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadError("File content is not a valid PDF")
    else:
        if not data.startswith(b"PK"):
            raise UploadError("File content is not a valid DOCX")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if "word/document.xml" not in names:
                    raise UploadError("DOCX is missing word/document.xml")
                if any(n.lower().endswith(("vbaproject.bin",)) for n in names):
                    raise UploadError("Macro-enabled documents are not accepted")
                total = sum(i.file_size for i in z.infolist())
                if total > 50 * 1024 * 1024:
                    raise UploadError("DOCX expands too large (possible zip bomb)")
        except zipfile.BadZipFile as e:
            raise UploadError("Corrupt DOCX file") from e
    return ext


def extract_text(ext: str, data: bytes) -> str:
    if ext == ".tex":
        from .latex import tex_to_text

        return tex_to_text(data.decode("utf-8")).strip()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UploadError("Encrypted PDFs are not supported")
        return "\n".join((p.extract_text() or "") for p in reader.pages[:10]).strip()
    from docx import Document as Docx

    d = Docx(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(x for x in parts if x is not None).strip()


MAX_RESUMES = 10


def store_resume(db: Session, filename: str, data: bytes, label: str = "") -> MasterResume:
    """Add a resume. Several resumes can be included at once (e.g. one per specialisation)."""
    s = get_settings()
    ext = validate_upload(filename, data, s.max_upload_mb)
    digest = hashlib.sha256(data).hexdigest()
    if db.scalars(select(MasterResume).where(MasterResume.sha256 == digest)).first():
        raise UploadError("This exact file is already uploaded")
    if db.scalar(select(func.count(MasterResume.id))) >= MAX_RESUMES:
        raise UploadError(f"At most {MAX_RESUMES} resumes; delete one first")
    text = extract_text(ext, data)
    if len(text) < 50:
        raise UploadError("Could not extract text from the resume (is it a scanned image?)")
    path = s.uploads_dir / f"resume-{secrets.token_hex(8)}{ext}"
    path.write_bytes(data)
    rec = MasterResume(label=(label or Path(filename).stem).strip()[:200], original_filename=Path(filename).name[:255],
                       storage_path=str(path), sha256=digest, content_type=ALLOWED[ext],
                       size_bytes=len(data), text=text[:100000], is_active=True)
    db.add(rec)
    db.commit()
    return rec
