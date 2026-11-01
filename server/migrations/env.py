from alembic import context
from sqlalchemy import create_engine, pool
from app.config import settings
from app.db import Base
import app.models
config=context.config
target_metadata=Base.metadata

def offline():
    context.configure(url=settings().database_url,target_metadata=target_metadata,literal_binds=True)
    with context.begin_transaction():context.run_migrations()
def online():
    engine=create_engine(settings().database_url,poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
    engine.dispose()
if context.is_offline_mode():offline()
else:online()
