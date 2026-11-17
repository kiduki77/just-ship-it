"""Quiet hours end at a wall clock time in the owner's zone, also on the nights the clocks change."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from justshipit.services import app_settings

NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.parametrize("quiet, at, release", [
    ({"start": "22:00", "end": "07:00"}, datetime(2026, 10, 31, 23, 0, tzinfo=NEW_YORK), datetime(2026, 11, 1, 7, 0, tzinfo=NEW_YORK)),
    ({"start": "22:00", "end": "07:00"}, datetime(2026, 11, 1, 0, 30, tzinfo=NEW_YORK), datetime(2026, 11, 1, 7, 0, tzinfo=NEW_YORK)),
    ({"start": "22:00", "end": "07:00"}, datetime(2026, 3, 7, 23, 0, tzinfo=NEW_YORK), datetime(2026, 3, 8, 7, 0, tzinfo=NEW_YORK)),
    ({"start": "22:00", "end": "07:00"}, datetime(2026, 3, 8, 0, 30, tzinfo=NEW_YORK), datetime(2026, 3, 8, 7, 0, tzinfo=NEW_YORK)),
    ({"start": "01:00", "end": "05:00"}, datetime(2026, 3, 8, 1, 30, tzinfo=NEW_YORK), datetime(2026, 3, 8, 5, 0, tzinfo=NEW_YORK)),
    ({"start": "00:00", "end": "05:00"}, datetime(2026, 11, 1, 0, 30, tzinfo=NEW_YORK), datetime(2026, 11, 1, 5, 0, tzinfo=NEW_YORK)),
])
def test_quiet_hours_end_at_the_wall_clock_time_on_the_nights_the_clocks_change(quiet, at, release):
    quiet_hours = app_settings.NotificationSettings(quiet_hours=quiet)
    now = at.astimezone(timezone.utc)

    waits = quiet_hours.quiet_wait_s("America/New_York", now)

    assert now + timedelta(seconds=waits) == release.astimezone(timezone.utc)
    assert (release.replace(tzinfo=None) - at.replace(tzinfo=None)).total_seconds() != waits,         "the wall clock difference is not the answer on these nights"
