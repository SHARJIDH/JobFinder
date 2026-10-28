from sqlalchemy.orm import Session

from ..core.text import dumps
from ..models import Event


def log_event(db: Session, category: str, message: str, *, level: str = "info", run_id: int | None = None,
              job_id: int | None = None, data: dict | None = None, commit: bool = False) -> None:
    db.add(Event(level=level, category=category, message=message[:4000], run_id=run_id, job_id=job_id,
                 data_json=dumps(data or {})))
    if commit:
        db.commit()
