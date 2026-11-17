"""The work path on the console: the owner's decisions in the order of what each frees with the outline of every project,
and one project's path view (docs/design/work-path.md 5 and 6). Reads only."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from justshipit.contracts.your_turn import PathView, YourTurn
from justshipit.db.models import Business
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import app_settings, path_view, requests, your_turn
from justshipit.services.control import NotFound

router = APIRouter()


@router.get("/businesses/{business_id}/your-turn", response_model=YourTurn)
def business_your_turn(business_id: int) -> YourTurn:
    """Every pending decision of the business in one order (the To do and Decisions pages share it), each with what
    answering it frees, and the outline: cycle, stage and project with their progress."""
    with session_scope() as s:
        if s.get(Business, business_id) is None:
            raise HTTPException(404, msg("today.not_found", app_settings.language(), id=business_id))
        return your_turn.your_turn(s, business_id)


@router.get("/projects/{project_id}/path", response_model=PathView)
def project_path(project_id: int, focus: str | None = None, neighbourhood: bool = False,
                 unfold: str | None = Query(None, description="Comma-separated group ids to open, or `all`")) -> PathView:
    """One project's nodes and edges, opened on `focus` (`REQ-6` or `6`). Past the size limit the graph is `too_big`
    and the outline stands in; `neighbourhood=true` with a `focus` draws just that card's neighbourhood."""
    try:
        focus_id = requests.parse_id(focus) if focus else None
    except ValueError as exc:
        raise HTTPException(422, msg("console.no_request", app_settings.language(), id=focus)) from exc
    with session_scope() as s:
        try:
            return path_view.path_view(s, project_id, focus=focus_id, neighbourhood=neighbourhood,
                                       unfold=[x for x in (unfold or "").split(",") if x])
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
