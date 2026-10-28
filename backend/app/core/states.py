"""Application pipeline state machine. All state changes go through `transition`."""

STATES = [
    "discovered", "shortlisted", "resume_ready", "ready_for_review", "approved",
    "submitted", "interview", "offer", "rejected", "closed", "archived",
]

PRE_SUBMISSION = {"discovered", "shortlisted", "resume_ready", "ready_for_review", "approved"}

ALLOWED: dict[str, set[str]] = {
    "discovered": {"shortlisted", "archived", "closed"},
    "shortlisted": {"discovered", "resume_ready", "archived", "closed"},
    "resume_ready": {"shortlisted", "ready_for_review", "archived", "closed"},
    "ready_for_review": {"resume_ready", "approved", "archived", "closed"},
    "approved": {"ready_for_review", "submitted", "archived", "closed"},
    "submitted": {"interview", "offer", "rejected", "closed"},
    "interview": {"offer", "rejected", "closed"},
    "offer": {"closed"},
    "rejected": {"archived"},
    "closed": {"discovered", "archived"},
    "archived": {"discovered", "shortlisted"},
}

LABELS = {
    "discovered": "Discovered", "shortlisted": "Shortlisted", "resume_ready": "Resume Ready",
    "ready_for_review": "Ready for Review", "approved": "Approved", "submitted": "Submitted",
    "interview": "Interview", "offer": "Offer", "rejected": "Rejected", "closed": "Closed", "archived": "Archived",
}


class TransitionError(ValueError):
    pass


def check_transition(current: str, target: str) -> None:
    if target not in ALLOWED.get(current, set()):
        raise TransitionError(f"Cannot move from '{LABELS.get(current, current)}' to '{LABELS.get(target, target)}'")
