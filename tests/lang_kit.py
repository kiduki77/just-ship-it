"""The owner's language for one test: `with owner_speaks("ko"): ...` sets Settings > Language and removes the row
after, so the next test reads the default (English)."""
from collections.abc import Iterator
from contextlib import contextmanager

from justshipit.db.models import AppSetting
from justshipit.db.session import session_scope
from justshipit.services import app_settings
from sqlalchemy import delete


@contextmanager
def owner_speaks(lang: str) -> Iterator[None]:
    with session_scope() as s:
        app_settings.save(s, "general", {"language": lang}, actor="test")
    try:
        yield
    finally:
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "general"))
