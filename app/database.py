from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rfp.db")

# SQLite needs `check_same_thread=False` so the FastAPI worker thread can
# share the connection with background tasks (BackgroundTasks + the
# intelligence-run threads). For Postgres / other engines this kwarg is
# ignored.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)


# Enable Write-Ahead Logging on SQLite so multiple capture leads can read
# while one is writing. WAL also drastically reduces "database is locked"
# errors during the parallel intelligence pipeline. Idempotent — SQLite
# stores this setting in the database file itself and only writes once.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_wal(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            # 5-second busy timeout is plenty for our short transactions and
            # keeps writers from immediately erroring under contention.
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()