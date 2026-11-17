"""Tool Gateway: the only place external side effects happen.

Execution path for an approved request:
  confirm the job lease → re-check request status, spend a valid grant (scope, uses, expiry) and mark the
  idempotency ledger row started, in one short transaction → confirm the lease again → connector → ledger result.
A connector that raises ConnectorError stopped before any external I/O, and a holder whose job was reclaimed
during the spend, or whose confirm after it fails, sends nothing: either way the grant is refunded, the row says
not_sent, and a later call (the console's retry of the failed job) may try again. Any other failure may have
sent, so the gateway never re-sends: a later call for that request is refused until the owner asks again with a
new request.
"""
from __future__ import annotations

import json
import re
import smtplib
import ssl
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from sqlalchemy import select

from justshipit import i18n
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import ActionExecution, AgentRequest, CrmContact, CrmInteraction, Grant
from justshipit.db.session import session_scope
from justshipit.services import app_settings, queue, requests
from justshipit.settings import settings


class ConnectorError(RuntimeError):
    """Raised only before any external I/O (payload, consent or config checks): nothing was sent."""


def _lang() -> i18n.Lang:
    """The owner's language for what this send says, English when the settings can't be read."""
    try:
        return app_settings.language()
    except Exception:  # noqa: BLE001 - the language only words the message
        return i18n.DEFAULT


def _mode(name: str) -> str:
    return app_settings.effective().connectors.modes().get(name, "outbox")


_resolved: ContextVar[str | None] = ContextVar("gateway_send_mode", default=None)


def _send_mode(action_kind: str | None, pinned: str | None) -> str | None:
    """The connector mode this send runs under, resolved once before the connector is called and handed to it
    through `_resolved`, or None for an
    action with no connector setting. It refuses (ConnectorError, so the row ends not_sent and the grant is
    refunded) when the connector is off, when the settings cannot be read, or when the connector is now more
    permissive than it was at approval (outbox, then live): the owner approved a different send. A stricter
    connector than at approval wins."""
    name = app_settings.ACTION_CONNECTOR.get(action_kind or "")
    if name is None:
        return None
    lang = _lang()
    try:
        mode = _mode(name)
    except Exception as exc:
        raise ConnectorError(i18n.msg("operations.unread_settings", lang, error=type(exc).__name__)) from exc
    connector = i18n.msg(f"operations.connector.{name}", lang)
    if mode == "off":
        raise ConnectorError(i18n.msg("operations.connector_off", lang, connector=connector))
    if pinned is not None and app_settings.MODE_RANK[mode] > app_settings.MODE_RANK[pinned]:
        raise ConnectorError(i18n.msg("operations.connector_opened", lang, connector=connector))
    return mode


def _outbox(kind: str) -> Path:
    d = settings().var_dir / "outbox" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


_ADDRESS = re.compile(r"""[^\s,;:<>@"'()\[\]\\]+@[^\s,;:<>@"'()\[\]\\]+""")


def _address(v: Any) -> str:
    """One bare address (local@domain), or ConnectorError. Consent is recorded for an address and matched as text,
    so an entry a mail server would read as two recipients, or as a name with another address, is never one."""
    text = str(v).strip()
    if not _ADDRESS.fullmatch(text):
        raise ConnectorError(f"not a single email address: {text[:80]!r}")
    return text


def _addresses(v: Any) -> list[str]:
    """A list as given, or a string split on commas; every entry is one address."""
    if v is None:
        return []
    return [_address(x) for x in (v if isinstance(v, list) else str(v).split(",")) if str(x).strip()]


# ----------------------------------------------------------------------------- connectors

def email_send(s, payload: dict, key: str) -> tuple[str, str, str]:
    to = _addresses(payload.get("to"))
    subject, body = payload.get("subject"), payload.get("body")
    if not to or not subject or not body:
        raise ConnectorError("email payload needs to, subject, body")
    msg = EmailMessage()
    msg["To"], msg["Subject"] = ", ".join(to), subject
    msg["From"] = settings().smtp_from
    msg["Message-ID"] = f"<{key}@ship.local>"
    msg.set_content(body)
    if (_resolved.get() or _mode("email")) != "live":
        path = _outbox("email") / f"{key}.eml"
        path.write_bytes(bytes(msg))
        return "email", "outbox", i18n.msg("operations.sent.outbox_email", _lang(), path=path)
    consented = {c.email for c in s.scalars(select(CrmContact).where(CrmContact.email.in_(to),
                                                                     CrmContact.consent.is_(True)))}
    if missing := [x for x in to if x not in consented]:
        raise ConnectorError(f"no recorded contact consent for: {', '.join(missing)}")
    cfg = settings()
    if not cfg.smtp_host:
        raise ConnectorError("SHIP_SMTP_HOST not configured for live email")
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())  # without one the certificate is not verified
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)
    return "email", "live", i18n.msg("operations.sent.email", _lang(), n=len(to))


def calendar_create(s, payload: dict, key: str) -> tuple[str, str, str]:
    title, start = payload.get("title"), payload.get("start")
    if not title or not start:
        raise ConnectorError("calendar payload needs title, start")
    try:
        begin = datetime.fromisoformat(start)
        end = begin + timedelta(minutes=int(payload.get("duration_min", 60)))
    except (TypeError, ValueError) as exc:
        raise ConnectorError(f"calendar start/duration_min invalid: {exc}") from exc
    fmt = "%Y%m%dT%H%M%S"
    attendees = "".join(f"ATTENDEE:mailto:{a}\r\n" for a in _addresses(payload.get("attendees")))
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Just Ship It//KO\r\nBEGIN:VEVENT\r\n"
           f"UID:{key}@ship.local\r\nDTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}Z\r\n"
           f"DTSTART:{begin.strftime(fmt)}\r\nDTEND:{end.strftime(fmt)}\r\nSUMMARY:{title}\r\n"
           f"DESCRIPTION:{(payload.get('description') or '').replace(chr(10), ' ')}\r\n{attendees}"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    path = _outbox("calendar") / f"{key}.ics"
    path.write_text(ics, encoding="utf-8")
    return "calendar", "outbox", i18n.msg("operations.sent.calendar", _lang(), path=path)


def survey_distribute(s, payload: dict, key: str) -> tuple[str, str, str]:
    path = _outbox("survey") / f"{key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return "survey", "outbox", i18n.msg("operations.sent.survey", _lang(), path=path)


def crm_contact_upsert(s, payload: dict, key: str) -> tuple[str, str, str]:
    if not payload.get("name"):
        raise ConnectorError("contact needs name")
    c = None
    if payload.get("email"):
        payload = {**payload, "email": _address(payload["email"])}
        c = s.scalar(select(CrmContact).where(CrmContact.email == payload["email"]))
    if c is None:
        c = CrmContact(name=payload["name"])
        s.add(c)
    for f in ("organization", "role", "email", "phone", "notes"):
        if payload.get(f):
            setattr(c, f, payload[f])
    # Consent can only be recorded by a human through the API/CLI, never by an agent payload.
    s.flush()
    return "crm", "live", i18n.msg("operations.sent.contact", _lang(), id=c.id)


def crm_interaction_log(s, payload: dict, key: str) -> tuple[str, str, str]:
    cid = payload.get("contact_id")
    if not cid or not s.get(CrmContact, int(cid)):
        raise ConnectorError("interaction needs an existing contact_id")
    it = CrmInteraction(contact_id=int(cid), project_id=payload.get("project_id"), kind=payload.get("kind", "note"),
                        summary=payload.get("summary", ""))
    s.add(it)
    s.flush()
    return "crm", "live", i18n.msg("operations.sent.interaction", _lang(), id=it.id)


CONNECTORS = {
    "external.email_send": email_send,
    "external.calendar_create": calendar_create,
    "external.survey_distribute": survey_distribute,
    "crm.contact_upsert": crm_contact_upsert,
    "crm.interaction_log": crm_interaction_log,
}


# ----------------------------------------------------------------------------- gateway

def execute_action(request_id: int) -> int:
    key = f"req-{request_id}"
    queue.confirm_lease()
    with session_scope() as s:
        row = s.scalar(select(ActionExecution).where(ActionExecution.idempotency_key == key).with_for_update())
        if row and row.status == "done":
            return row.id
        if row and row.status != "not_sent":
            raise PermissionError(i18n.msg("operations.maybe_sent", _lang(), ref=requests.display_id(request_id),
                                           id=row.id, status=row.status))
        req = s.get(AgentRequest, request_id)
        if req is None or req.status not in (RequestStatus.APPROVED.value, RequestStatus.AUTO_APPROVED.value):
            raise PermissionError(f"REQ-{request_id} is not approved")
        fn = CONNECTORS.get(req.action_kind or "")
        if fn is None:
            raise ConnectorError(f"no connector for {req.action_kind}")
        payload = dict(req.action_params.get("payload") or {})
        if not payload:
            raise ConnectorError("request has no action payload")
        amount = float(payload.get("amount_usd") or 0)
        grant = requests.consume_grant(s, req.action_kind, req.project_id, amount_usd=amount, request_id=req.id)
        row = row or ActionExecution(request_id=req.id, project_id=req.project_id, action_kind=req.action_kind,
                                     connector="?", mode="?", idempotency_key=key, payload=payload)
        row.grant_id, row.status = grant.id, "started"
        s.add(row)
        s.flush()
        row_id, grant_id, pinned, action_kind = row.id, grant.id, grant.connector_mode, req.action_kind
    try:
        mode = _send_mode(action_kind, pinned)
        queue.confirm_lease()  # the spend can wait on locks for longer than a lease; a reclaimed holder sends nothing
    except queue.LeaseLost:
        _refund(row_id, grant_id, amount, i18n.msg("operations.taken_back", _lang()))
        raise
    except ConnectorError as exc:
        _refund(row_id, grant_id, amount, f"ConnectorError: {exc}")
        raise
    except Exception as exc:  # a database error in the confirm: the connector was not called either
        _refund(row_id, grant_id, amount, i18n.msg("operations.check_failed", _lang(), error=type(exc).__name__))
        raise
    # From here on the row says "started" with its grant spent, so a crash mid-send leaves it blocking. What the
    # connector did is a fact whoever owns the job now, so its record lands even after the lease was lost.
    try:
        with session_scope(lease_checked=False) as s:
            # Every attempt hands the connector the same key (outbox file name, Message-ID, calendar UID).
            token = _resolved.set(mode)
            try:
                connector, sent_mode, summary = fn(s, payload, key)
            finally:
                _resolved.reset(token)
            row = s.get(ActionExecution, row_id)
            row.connector, row.mode, row.status, row.result_summary = connector, sent_mode, "done", summary
    except ConnectorError as exc:
        _refund(row_id, grant_id, amount, f"ConnectorError: {exc}")
        raise
    except Exception as exc:
        with session_scope(lease_checked=False) as s:
            row = s.get(ActionExecution, row_id)
            row.status, row.result_summary = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        raise
    return row_id


def _refund(row_id: int, grant_id: int, amount: float, why: str) -> None:
    """Nothing went out: give the grant its use back and mark the row not_sent. This records what happened, so it
    lands even after the lease was lost."""
    with session_scope(lease_checked=False) as s:
        grant = s.get(Grant, grant_id, with_for_update=True)
        grant.uses -= 1
        grant.amount_used_usd -= amount
        row = s.get(ActionExecution, row_id, with_for_update=True)
        row.grant_id, row.status, row.result_summary = None, "not_sent", why[:2000]
