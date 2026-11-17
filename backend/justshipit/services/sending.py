"""Whether a connector that can send for real could send right now. A connector set to live with a part missing (no
mail server, no Slack token, no Slack channel, a token Slack refused) sends nothing, so the console says what is missing
instead of claiming it sends. Only whether each part is set is read here, never its value.

Slack goes further: it is turned live only once nothing is missing and the owner's connection test has passed with the
saved token (`notify.send_test`), so a token never tested, or refused since, cannot be turned live. Email has no test
that sends nothing from Settings yet, so turning it live with a part missing still saves, with a warning."""
from __future__ import annotations

from collections.abc import Callable

from justshipit.i18n import Lang, msg
from justshipit.services import app_settings, notify_state, secrets
from justshipit.settings import settings


def _email(cfg: app_settings.AppSettings, lang: Lang) -> list[str]:
    return [] if settings().smtp_host.strip() else [msg("settings.gap.smtp", lang)]


def _slack(cfg: app_settings.AppSettings, lang: Lang) -> list[str]:
    out = []
    try:
        token = secrets.get("slack_bot_token")
    except secrets.SecretError as exc:
        out.append(exc.say(lang))
    else:
        if token is None:
            out.append(msg("settings.gap.slack_token", lang))
        elif problem := secrets.format_problem("slack_bot_token", token):
            out.append(problem.say(lang))
        elif notify_state.slack_refused() is not None:
            # held until a new token is saved or a test goes through (notify.reconnected)
            out.append(msg("settings.gap.slack_refused", lang))
    if not cfg.notifications.slack_channel.strip():
        out.append(msg("settings.gap.slack_channel", lang))
    return out


def _slack_untested(cfg: app_settings.AppSettings, lang: Lang) -> list[str]:
    """What the connection test still owes: a pass with the saved token, then with the channel saved now (a channel
    left empty is already said by `_slack`)."""
    try:
        token = secrets.get("slack_bot_token")
    except secrets.SecretError:
        token = None
    passed = notify_state.slack_tested(token) if token else None
    if passed is None:
        return [msg("settings.gap.slack_untested", lang)]
    channel = cfg.notifications.slack_channel.strip()
    return [] if not channel or passed == channel else [msg("settings.gap.slack_channel_untested", lang, channel=passed)]


# one entry per connector that has a live mode (app_settings.SUPPORTED_MODES)
_NEEDS: dict[str, Callable[[app_settings.AppSettings, Lang], list[str]]] = {"email": _email, "slack": _slack}
# the connectors a save turns live only after the owner's connection test passed with the saved credential and channel
_TESTED: dict[str, Callable[[app_settings.AppSettings, Lang], list[str]]] = {"slack": _slack_untested}


def missing(cfg: app_settings.AppSettings, lang: Lang) -> dict[str, list[str]]:
    """For each connector with a live mode, the sentences naming what it lacks to send for real; empty when nothing."""
    return {name: need(cfg, lang) for name, need in _NEEDS.items()}


def live_blockers(cfg: app_settings.AppSettings, lang: Lang) -> dict[str, list[str]]:
    """For each live-capable connector, what stops a save from turning it live now: for one that must pass its test,
    every missing part and the test; empty for the others, which go live with a warning instead."""
    gaps = missing(cfg, lang)
    return {name: [*gaps[name], *_TESTED[name](cfg, lang)] if name in _TESTED else [] for name in gaps}


def view(cfg: app_settings.AppSettings, lang: Lang) -> dict[str, dict]:
    """Each live-capable connector's mode, what it lacks, `sends`: live with nothing missing, so it really sends, and
    `blocks_live`: what stops it being turned live now."""
    modes, blockers = cfg.connectors.modes(), live_blockers(cfg, lang)
    return {name: {"mode": modes[name], "missing": gaps, "sends": modes[name] == "live" and not gaps,
                   "blocks_live": blockers[name]}
            for name, gaps in missing(cfg, lang).items()}


def refused_live(before: app_settings.AppSettings, after: app_settings.AppSettings, lang: Lang) -> str | None:
    """The sentence refusing a save that turns live a connector that is not ready to (`live_blockers`), or None."""
    was, now = before.connectors.modes(), after.connectors.modes()
    return " ".join(" ".join([msg(f"settings.refuse.live.{name}", lang), *needs])
                    for name, needs in live_blockers(after, lang).items()
                    if needs and now[name] == "live" and was[name] != "live") or None


def newly_blocked(before: app_settings.AppSettings, after: app_settings.AppSettings, lang: Lang) -> list[dict]:
    """The connectors a save turns live that could not send: each with the sentence the owner reads before saving."""
    was, now = before.connectors.modes(), after.connectors.modes()
    return [{"kind": "cannot_send", "connector": name,
             "text": " ".join([msg(f"settings.warn.cannot_send.{name}", lang), *gaps])}
            for name, gaps in missing(after, lang).items() if gaps and now[name] == "live" and was[name] != "live"]
