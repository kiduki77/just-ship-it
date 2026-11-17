from alembic import context

from justshipit.db.models import Base
from justshipit.db.session import engine, ensure_extensions

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    # Only manage tables declared in our models; LangGraph owns its checkpoint tables.
    if type_ == "table" and reflected and compare_to is None:
        return False
    return True


def run_migrations_online() -> None:
    ensure_extensions()
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
