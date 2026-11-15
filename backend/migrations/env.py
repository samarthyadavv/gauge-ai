from alembic import context
from app.db import engine, Base

with engine.connect() as c:
    context.configure(connection=c, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
