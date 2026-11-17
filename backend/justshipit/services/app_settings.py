"""Settings the owner edits in the console, stored in the database behind one typed accessor.

config/company.yaml supplies the defaults and a row in `app_settings` overrides it field by field, so a fresh
install behaves as the files say and every change the owner makes is one small row. `effective()` reads the
table on every call: it is one tiny query, and a change is seen by the next caller in any process without a
restart. If the read fails, the caller fails too. Falling back to the files could loosen what the owner tightened.

Floors that no setting can lower live here: a connector only takes the modes it implements, and an approval
override never goes below `floor()` of the action's mode in config/tool_policy.yaml. The model never writes these;
the only writer is the owner's authenticated console request.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from justshipit.contracts.notify import NotifyKind
from justshipit.contracts.requests import POLICY_ORDER, PolicyMode
from justshipit.db.models import AppSetting
from justshipit.db.session import session_scope
from justshipit.i18n import DEFAULT as DEFAULT_LANG
from justshipit.i18n import Lang, lang_of, msg
from justshipit.services import usage as usage_service
from justshipit.services.strengths import Strength, Strengths, known
from justshipit.settings import company_config, tool_policy

Mode = Literal["off", "outbox", "live"]

SUPPORTED_MODES: dict[str, tuple[Mode, ...]] = {
    "email": ("off", "outbox", "live"),
    "calendar": ("off", "outbox"),
    "survey": ("off", "outbox"),
    "slack": ("off", "outbox", "live"),
    "github": ("off", "outbox"),
    "cloudflare": ("off", "outbox"),
}

MODE_RANK = {"off": 0, "outbox": 1, "live": 2}
ACTION_CONNECTOR = {"external.email_send": "email", "external.calendar_create": "calendar",
                    "external.survey_distribute": "survey"}

_CONSOLE_URL = re.compile(r"^https?://[^\s<>|]+$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_FLOOR = {PolicyMode.DENY: PolicyMode.STRONG_APPROVE, PolicyMode.STRONG_APPROVE: PolicyMode.APPROVE}


def floor(base: PolicyMode) -> PolicyMode:
    """The loosest mode an override may give an action whose file mode is `base`."""
    return _FLOOR.get(base, PolicyMode.AUTO)


def _lang(info: ValidationInfo) -> Lang:
    """The language a write's refusals are said in: `save` passes the owner's as validation context."""
    return lang_of((info.context or {}).get("lang"))


def _hhmm(v: str, info: ValidationInfo) -> str:
    if not _HHMM.match(v):
        raise ValueError(msg("settings.time", _lang(info)))
    return v


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageSettings(_Section):
    subscription: str = Field(default="", max_length=80)
    day_usd_eq: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    week_usd_eq: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # The owner's own guess at what the subscription allows in a week. Nothing enforces it; the console shows every cap as a share of it.
    capacity_week_usd_eq: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    timezone: str = "Asia/Seoul"
    owner_reserve_pct: int = Field(default=20, ge=0, le=50)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str, info: ValidationInfo) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(msg("settings.timezone", _lang(info))) from None
        return v


class CheckedUsageSettings(UsageSettings):
    """What a write must satisfy: a cap the largest step could never fit under is refused. Reads use UsageSettings
    and list a stored cap that has since become too small in `problems`, so a larger step added to the code later
    does not break every read."""

    @model_validator(mode="after")
    def _admits_the_largest_step(self, info: ValidationInfo) -> UsageSettings:
        if refusals := usage_service.too_small(self, lang=_lang(info)):
            raise ValueError("; ".join(refusals))
        return self


class ConnectorEntry(_Section):
    mode: Mode = "outbox"


class ConnectorSettings(_Section):
    email: ConnectorEntry = Field(default_factory=ConnectorEntry)
    slack: ConnectorEntry = Field(default_factory=ConnectorEntry)
    github: ConnectorEntry = Field(default_factory=ConnectorEntry)
    cloudflare: ConnectorEntry = Field(default_factory=ConnectorEntry)
    calendar: ConnectorEntry = Field(default_factory=ConnectorEntry)
    survey: ConnectorEntry = Field(default_factory=ConnectorEntry)

    @model_validator(mode="after")
    def _implemented_modes_only(self, info: ValidationInfo) -> ConnectorSettings:
        for name, allowed in SUPPORTED_MODES.items():
            if (mode := getattr(self, name).mode) not in allowed:
                raise ValueError(msg("settings.mode", _lang(info), name=name, mode=mode, allowed=", ".join(allowed)))
        return self

    def modes(self) -> dict[str, str]:
        return {name: getattr(self, name).mode for name in SUPPORTED_MODES}


class PolicySettings(_Section):
    overrides: dict[str, PolicyMode] = Field(default_factory=dict)


class CheckedPolicySettings(PolicySettings):
    """What a write must satisfy. Reads use PolicySettings and policy.py clamps to the floor, so a floor raised
    later in tool_policy.yaml tightens a saved override instead of breaking every policy decision."""

    @model_validator(mode="after")
    def _above_the_floor(self, info: ValidationInfo) -> PolicySettings:
        base_modes, lang = tool_policy(), _lang(info)
        for kind, mode in self.overrides.items():
            if kind not in base_modes:
                raise ValueError(msg("settings.unknown_action", lang, kind=kind))
            lowest = floor(PolicyMode(base_modes[kind]))
            if POLICY_ORDER.index(mode) < POLICY_ORDER.index(lowest):
                raise ValueError(msg("settings.floor", lang, kind=kind, lowest=lowest.value))
        return self


class QuietHours(_Section):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v: str, info: ValidationInfo) -> str:
        return _hhmm(v, info)


class NotificationSettings(_Section):
    slack_channel: str = Field(default="", max_length=80)
    kinds: list[NotifyKind] = Field(default_factory=lambda: ["decision_needed", "failed"])
    briefing_time: str = "09:00"
    quiet_hours: QuietHours | None = None
    console_url: str = Field(default="http://127.0.0.1:8000", max_length=200)

    @field_validator("console_url")
    @classmethod
    def _address(cls, v: str, info: ValidationInfo) -> str:
        v = v.strip().rstrip("/")
        if not _CONSOLE_URL.match(v):
            raise ValueError(msg("settings.console_url", _lang(info)))
        return v

    @field_validator("briefing_time")
    @classmethod
    def _time(cls, v: str, info: ValidationInfo) -> str:
        return _hhmm(v, info)

    @field_validator("kinds")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    def quiet_wait_s(self, tz: str, now: datetime | None = None) -> int:
        """Seconds until quiet hours end, or 0 outside them. The window may run past midnight. The end is a wall
        clock time in the owner's zone, so it is placed on its date as a zoned moment and measured in UTC: on a
        night the clocks change the window is an hour longer or shorter than its wall clock span."""
        q = self.quiet_hours
        if q is None or q.start == q.end:
            return 0
        zone, now = ZoneInfo(tz), (now or datetime.now(timezone.utc))
        local = now.astimezone(zone)
        at = local.hour * 60 + local.minute
        start, end = (int(h) * 60 + int(m) for h, m in (q.start.split(":"), q.end.split(":")))
        inside = start <= at < end if start < end else at >= start or at < end
        if not inside:
            return 0
        day = local.date() + timedelta(days=1) if start > end and at >= start else local.date()
        ends = datetime(day.year, day.month, day.day, end // 60, end % 60, tzinfo=zone)
        return math.ceil((ends.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds())


class BetSettings(_Section):
    """How many bets run at once, how many posts the weekly promotion may ask the owner to make, how many routines a
    business may keep, how long a failed run may be retried before its bet goes to review, and how many days a bet
    promised none waits for market response."""
    max_running: int = Field(default=1, ge=1, le=3)
    weekly_post_cap: int = Field(default=2, ge=0, le=7)
    max_routines: int = Field(default=2, ge=0, le=10)
    failed_grace_minutes: int = Field(default=30, ge=0, le=1440)
    window_days: int = Field(default=14, ge=1, le=60)


class GeneralSettings(_Section):
    """The owner's language: the console's words and everything the company writes for the owner from now on. One
    for the whole company, since one owner reads every business. Text already written keeps its language."""
    language: Lang = DEFAULT_LANG


class OwnerSettings(_Section):
    """What the owner is good at (`services.strengths`), for the whole company like the language: one owner runs every
    business. None until the owner answers; an empty list is their answer "none of these yet"."""
    strengths: list[Strength] | None = None

    @field_validator("strengths")
    @classmethod
    def _ordered(cls, v: list[Strength] | None) -> list[Strength] | None:
        return None if v is None else known(v)


class AppSettings(_Section):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    owner: OwnerSettings = Field(default_factory=OwnerSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    bets: BetSettings = Field(default_factory=BetSettings)

    def notifies(self, kind: NotifyKind) -> bool:
        """Whether the owner hears about this kind: Slack is not off and the kind is one they chose."""
        return self.connectors.slack.mode != "off" and kind in self.notifications.kinds


SECTIONS: dict[str, type[_Section]] = {name: f.annotation for name, f in AppSettings.model_fields.items()}
_YAML_KEY = {"usage": "usage", "connectors": "connectors", "notifications": "notifications"}
_CHECKED = {"policy": CheckedPolicySettings, "usage": CheckedUsageSettings}


class UnknownSection(KeyError):
    pass


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _fields(exc: Exception, lang: Lang) -> str:
    whole = msg("settings.whole", lang)
    if isinstance(exc, ValidationError):
        return ", ".join(sorted({"/".join(str(p) for p in e["loc"]) or whole for e in exc.errors()}))
    return whole


def _yaml(section: str, lang: Lang = DEFAULT_LANG) -> tuple[dict[str, Any], list[str]]:
    """The file's defaults for a section, cut down to what the models accept, and what was cut. A key or mode the
    code does not know never stops a read: it is left out (or, for a mode a connector lacks, set to outbox)."""
    raw = company_config().get(_YAML_KEY.get(section, ""), {})
    raw = dict(raw) if isinstance(raw, dict) else {}
    cut: list[str] = []
    if section == "connectors":
        clean = {}
        for name, entry in raw.items():
            mode = entry.get("mode") if isinstance(entry, dict) else None
            if name not in SUPPORTED_MODES:
                cut.append(msg("settings.problem.unknown_connector", lang, name=name))
            elif mode in SUPPORTED_MODES[name]:
                clean[name] = {"mode": mode}
            else:
                cut.append(msg("settings.problem.unsupported_mode", lang, name=name))
        return clean, cut
    known = set(SECTIONS[section].model_fields)
    clean = {k: v for k, v in raw.items() if k in known}
    cut += [msg("settings.problem.unknown_field", lang, key=k) for k in raw if k not in known]
    try:
        SECTIONS[section].model_validate(clean)
    except ValueError as exc:
        return {}, [*cut, msg("settings.problem.bad_file", lang, fields=_fields(exc, lang))]
    return clean, cut


def _salvage_policy(row: Any) -> dict[str, Any]:
    """From an unreadable policy row keep only the entries that are valid and at least as strict as the file."""
    overrides = row.get("overrides") if isinstance(row, dict) else None
    if not isinstance(overrides, dict):
        return {}
    base_modes, kept = tool_policy(), {}
    for kind, mode in overrides.items():
        stricter = mode in PolicyMode._value2member_map_ and kind in base_modes and (
            POLICY_ORDER.index(PolicyMode(mode)) >= POLICY_ORDER.index(PolicyMode(base_modes[kind])))
        if stricter:
            kept[kind] = mode
    return {"overrides": kept}


def _load_section(section: str, row: Any, lang: Lang = DEFAULT_LANG) -> tuple[_Section, list[str]]:
    """One section read tolerantly: a stored row that fails validation is replaced by the file's defaults
    (for policy, by only its entries that are no looser than the file), and the reason is returned in `lang`."""
    base, problems = _yaml(section, lang)
    model = SECTIONS[section]
    try:
        loaded = model.model_validate(_merge(base, row))
    except (ValueError, TypeError, AttributeError) as exc:
        problems = [*problems, msg("settings.problem.bad_row", lang, fields=_fields(exc, lang))]
        return model.model_validate(_merge(base, _salvage_policy(row) if section == "policy" else {})), problems
    if section == "usage":
        problems += [msg("settings.problem.cap_too_small", lang, why=m) for m in usage_service.too_small(loaded, lang=lang)]
    return loaded, problems


def stored() -> dict[str, dict[str, Any]]:
    """The owner's overrides as saved, one dict per section that has a row. The visits row is not a setting."""
    with session_scope(lease_checked=False) as s:
        return {r.section: r.value for r in s.scalars(select(AppSetting).where(AppSetting.section.in_(list(SECTIONS))))}


# When the owner last opened a screen, by a key such as "activity:3". Not a setting: it is kept on the server so the
# owner's two PCs share it, in a row of its own that the settings sections never read.
VISITS = "visits"


def visit(s: Session, key: str) -> str | None:
    """Record that the owner opens `key` now, and return when they opened it before (None the first time)."""
    row = s.get(AppSetting, VISITS, with_for_update=True)
    before = (row.value or {}).get(key) if row else None
    now = {key: datetime.now(UTC).isoformat()}
    stmt = insert(AppSetting).values(section=VISITS, value=now, updated_by="owner")
    s.execute(stmt.on_conflict_do_update(index_elements=["section"],
                                         set_={"value": AppSetting.value.op("||")(stmt.excluded.value),
                                               "updated_by": "owner", "updated_at": func.now()}))
    return before


def load(lang: Lang = DEFAULT_LANG) -> tuple[AppSettings, list[dict[str, str]]]:
    """The effective settings and the problems met reading them, as (section, message in `lang`). Reading never
    raises for a bad row or file value; only a failed database read does, which fails the caller closed."""
    rows = stored()
    sections, problems = {}, []
    for name in SECTIONS:
        sections[name], found = _load_section(name, rows.get(name, {}), lang)
        problems += [{"section": name, "message": m} for m in found]
    return AppSettings(**sections), problems


def effective() -> AppSettings:
    return load()[0]


def language() -> Lang:
    """The language the owner reads, for a caller about to write owner text or call an agent."""
    return effective().general.language


def reader() -> tuple[Lang, Strengths]:
    """The owner's language and strengths in one read, for a boundary about to show cards."""
    owner = effective()
    return owner.general.language, owner.owner.strengths


def defaults() -> AppSettings:
    return AppSettings(**{name: _load_section(name, {})[0] for name in SECTIONS})


def connector_mode_for(action_kind: str | None) -> str | None:
    """The mode of the connector that would send this action now, or None when it has no connector setting."""
    name = ACTION_CONNECTOR.get(action_kind or "")
    return effective().connectors.modes()[name] if name else None


def _pick(full: dict[str, Any], asked: dict[str, Any]) -> dict[str, Any]:
    """The validated values of only the fields `asked` named, so a row never holds a default it was not given."""
    return {k: _pick(full[k], v) if isinstance(v, dict) and isinstance(full.get(k), dict) else full[k]
            for k, v in asked.items()}


def _leaves(d: Any, path: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        return {k: v for key, val in d.items() for k, v in _leaves(val, f"{path}/{key}" if path else key).items()}
    return {path: d}


def merged(section: str, body: dict[str, Any], *, lang: Lang = DEFAULT_LANG) -> _Section:
    """The section as it would be with `body` as the owner's overrides, validated over the file defaults as `save`
    stores it; a floor or an unsupported mode raises ValidationError, its reasons in `lang`."""
    if section not in SECTIONS:
        raise UnknownSection(section)
    base, _ = _yaml(section)
    return _CHECKED.get(section, SECTIONS[section]).model_validate(_merge(base, body), context={"lang": lang})


def save(s: Session, section: str, body: dict[str, Any], *, actor: str,
         lang: Lang = DEFAULT_LANG) -> list[dict[str, Any]]:
    """Make `body` the owner's overrides of `section` and return what changed in the effective values, as
    (key, from, to). The section is validated as merged over the file defaults, so a floor or an unsupported
    mode raises ValidationError, its reasons in `lang`, before anything is stored."""
    before = _leaves(_load_section(section, stored().get(section, {}))[0].model_dump(mode="json"))
    normal = merged(section, body, lang=lang).model_dump(mode="json")
    value = _pick(normal, body)
    if value:
        stmt = insert(AppSetting).values(section=section, value=value, updated_by=actor)
        s.execute(stmt.on_conflict_do_update(
            index_elements=["section"],
            set_={"value": stmt.excluded.value, "updated_by": stmt.excluded.updated_by, "updated_at": func.now()}))
    else:
        s.execute(delete(AppSetting).where(AppSetting.section == section))
    after = _leaves(normal)
    return [{"key": f"{section}/{k}", "from": before.get(k), "to": after.get(k)}
            for k in sorted(before.keys() | after.keys()) if before.get(k) != after.get(k)]


_REASONS = ("extra_forbidden", "literal_error", "enum", "greater_than", "greater_than_equal", "less_than_equal",
            "string_too_long", "finite_number")


def describe(exc: ValidationError, lang: Lang) -> str:
    """The refusal in the owner's language, naming each field. Values are left out, so a bad entry never echoes
    back. A validator's own reason was already written in `lang` by `save`."""
    lines = []
    for e in exc.errors():
        where = "/".join(str(p) for p in e["loc"])
        reason = (str(e["ctx"]["error"]) if e["type"] == "value_error"
                  else msg(f"settings.reason.{e['type'] if e['type'] in _REASONS else 'other'}", lang))
        lines.append(f"{where}: {reason}" if where else reason)
    return msg("settings.refused", lang, reasons="; ".join(lines))
