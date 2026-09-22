"""Uncapped sweep of every expired /tools/* output (tool_files rows past
their expires_at, plus the underlying Supabase Storage object).

tools_common.cleanup_expired_tool_files() already runs a capped (50-row)
version of this on every /tools/* request, which is enough to keep the table
bounded during normal traffic. This script exists for when there's no traffic
to trigger that lazily (e.g. an idle weekend) — run it manually, or point an
external scheduler (cron, a Render Cron Job on a paid plan, GitHub Actions
schedule, etc.) at it. See CLAUDE.md's Document tools section.

Usage (from backend/, with the venv active):
    python scripts/cleanup_tool_files.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools_common import cleanup_expired_tool_files
from database import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        total = 0
        while True:
            deleted = cleanup_expired_tool_files(db, limit=200)
            total += deleted
            if deleted == 0:
                break
        print(f"Deleted {total} expired tool file(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
