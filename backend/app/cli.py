"""Command-line entry points (used locally and by the Cloud Run Job).

    python -m app.cli migrate
    python -m app.cli run-daily [--no-notify]
    python -m app.cli discover [--company NAME]
    python -m app.cli rank [--no-ai] [--force]
    python -m app.cli notify [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .core.yaml_config import load_profile, load_search_config
from .db import session_factory
from .llm.service import LLMService
from .migrations import upgrade_db
from .services.notify import send_daily_digest
from .services.pipeline import discover, rank_jobs, run_daily
from .sources.http import PoliteClient


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="jobhunter")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    d = sub.add_parser("run-daily")
    d.add_argument("--no-notify", action="store_true")
    di = sub.add_parser("discover")
    di.add_argument("--company")
    di.add_argument("--no-web", action="store_true", help="skip Gemini web search")
    r = sub.add_parser("rank")
    r.add_argument("--no-ai", action="store_true")
    r.add_argument("--force", action="store_true")
    n = sub.add_parser("notify")
    n.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    upgrade_db()
    if args.cmd == "migrate":
        print("database migrated")
        return 0
    db = session_factory()()
    try:
        if args.cmd == "run-daily":
            run = run_daily(db, notify=not args.no_notify)
            print(json.dumps({"run": run.id, "status": run.status, "stats": json.loads(run.stats_json)}, indent=2, default=str))
            return 0 if run.status in ("success", "partial") else 1
        if args.cmd == "discover":
            http = PoliteClient()
            st = discover(db, load_search_config(), http, only_company=args.company, llm=LLMService(db),
                          web_search=not args.no_web)
            st.pop("new_job_ids", None)
            print(json.dumps(st, indent=2))
            return 0
        if args.cmd == "rank":
            st = rank_jobs(db, load_search_config(), load_profile(), LLMService(db), use_ai=not args.no_ai, force=args.force)
            print(json.dumps(st, indent=2))
            return 0
        if args.cmd == "notify":
            res = send_daily_digest(db, load_search_config(), dry_run=args.dry_run)
            print(res["text"] if args.dry_run else json.dumps({k: res[k] for k in ("jobs", "sent")}))
            return 0
    finally:
        db.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
