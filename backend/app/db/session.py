"""SQLAlchemy engine/session setup — see docs/backend.md and docs/db.md."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

Base = declarative_base()

engine = create_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None


def get_db():
    """FastAPI dependency — yields a session. Raises clearly if DATABASE_URL isn't set,
    rather than failing at import time (which would break tests that only need Base/models)."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL not configured — set it in backend/.env")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_session():
    """Non-FastAPI context manager for scripts/scheduler jobs — commits on success, rolls back on error."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL not configured — set it in backend/.env")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
