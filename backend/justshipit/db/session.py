from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from justshipit.settings import settings


# No app transaction holds a row or advisory lock across a model call, a git or docker command or a send, so a lock
# wait this long is a hang: one thread waiting on two connections (take_claim_lock), which PostgreSQL never breaks.
# Past it the statement fails and the job's error says so, instead of a worker slot blocking for good.
LOCK_TIMEOUT_MS = 120_000


@lru_cache
def engine() -> Engine:
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10,
                         connect_args={"options": f"-c lock_timeout={LOCK_TIMEOUT_MS}"})


@lru_cache
def _factory() -> sessionmaker[Session]:
    import justshipit.services.activity  # noqa: F401  (registers the activity-log hook)
    import justshipit.services.events  # noqa: F401  (registers change-event listeners)
    return sessionmaker(engine(), expire_on_commit=False)


@contextmanager
def session_scope(*, lease_checked: bool = True) -> Iterator[Session]:
    """A session that commits on exit. Under a job lease, a commit after the lease was lost would write rows
    that belong to the job's new holder, so it checks the lease first; the budget settlement, the model call
    record and the gateway's send result pass `lease_checked=False` because they describe what did happen. The owner's
    stops are not checked here: a stop ends a job at the start of its next step, so what a call already cost is kept."""
    s = _factory()()
    try:
        yield s
        if lease_checked:
            from justshipit.services.queue import check_commit
            check_commit()
        s.commit()
    except BaseException:  # a lost lease unwinds through here too, and its half-written rows must not land
        s.rollback()
        raise
    finally:
        s.close()


def ensure_extensions() -> None:
    with engine().begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
