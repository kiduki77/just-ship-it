"""Shared builder for tests that need a business with a customer description."""
from justshipit.services.profile import store
from justshipit.services.profile.store import NewValue


def describe(s, business_id, buyer=(), reach=(), has=(), provenance="confirmed") -> None:
    """Write the lines the way the owner (confirmed, said) or the CEO (guess) would. A line with no codes is left as it is."""
    owner = provenance != "guess"
    for line, codes in (("buyer", buyer), ("reach", reach), ("has", has)):
        if codes:
            store.set_line(s, business_id, line, [NewValue(c, None, provenance, "console" if owner else "backfill:0")
                                                  for c in codes],
                           cause="owner_change" if owner else "backfill", written_by="owner" if owner else "project_manager",
                           brief_id=None, if_live=None, lang="en")
    s.commit()


def reject(s, business_id, line) -> None:
    """The owner says a line is not right: it becomes unknown."""
    store.set_line(s, business_id, line, [], cause="owner_not_right", written_by="owner", brief_id=None, if_live=None,
                   lang="en")
    s.commit()
