from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL


_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    # Managed Postgres drops idle connections; check before handing one out.
    pool_pre_ping=not _is_sqlite,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


# Any constant works; it only has to be the same for every process.
_LIMITS_LOCK_KEY = 4_812_001


def lock_limits(db: Session) -> None:
    """Serialize limit checks until this transaction ends, so concurrent requests cannot all
    pass the same check. SQLite already allows only one writer at a time."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LIMITS_LOCK_KEY})


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db
