from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

class Base(DeclarativeBase):
    pass

engine = create_engine(settings().database_url, pool_pre_ping=True,
    pool_size=5, max_overflow=5, connect_args={'connect_timeout':10,
    'options':'-c statement_timeout=30000 -c lock_timeout=10000'})
Session = sessionmaker(engine, expire_on_commit=False)

def now():
    return datetime.now(timezone.utc)

@contextmanager
def transaction():
    with Session.begin() as s:
        yield s

def db_session():
    with transaction() as s:
        yield s
