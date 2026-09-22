from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import SUPABASE_DB_URL


def _connect_args_for(db_url: str) -> Dict[str, Any]:
    """Security audit finding (see CLAUDE.md's Security audit section): the
    connection string had no explicit sslmode, relying entirely on libpq's
    own default (prefer) and Supabase's server-side enforcement. Made
    explicit here for defense-in-depth — skipped for the local/test
    Postgres container (always "localhost"/"127.0.0.1", see
    tests/conftest.py), which has no SSL configured at all and would
    otherwise fail to connect."""
    is_local_db = "localhost" in db_url or "127.0.0.1" in db_url
    return {} if is_local_db else {"sslmode": "require"}


engine = create_engine(SUPABASE_DB_URL, pool_pre_ping=True, connect_args=_connect_args_for(SUPABASE_DB_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
