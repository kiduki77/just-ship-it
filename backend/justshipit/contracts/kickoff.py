"""What the CEO returns for one turn of the kickoff conversation."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from justshipit.contracts.goal import GoalTarget
from justshipit.services.roadmap import BRIEF_SECTIONS

SectionKey = Literal[tuple(spec.key for spec in BRIEF_SECTIONS)]  # type: ignore[valid-type]
_LANGUAGE = "in the language the Output language rule names"


class BriefPatchLine(BaseModel):
    section: SectionKey
    text: str = Field(description=f"One line that carries what the owner means, in plain words, {_LANGUAGE}. It "
                                  "speaks to the owner ('your building', never 'the owner's building'). It carries no "
                                  "tag such as '· a guess' or '· seen firsthand': provenance says where the line came "
                                  "from")
    provenance: Literal["said", "guess"] = Field(
        description="said: the owner said it. guess: you inferred or proposed it. When the owner answers "
                    "'That's right', the server marks it confirmed")
    owner_quote: str = Field(
        description="For said: the owner's words behind this line, copied character for character (at least 6 "
                    "characters without spaces, at least 2 meaningful words, at least one word shared with the "
                    "line). The server turns the line into a guess when the quote is not in the owner's answers, "
                    "pasted notes or files, or is not about the line. Any section's answer counts. For guess: an "
                    "empty string")
    quote_gloss: str = Field(
        default="",
        description="When owner_quote and text are in different languages: owner_quote rendered faithfully and whole "
                    "in the language of text, adding nothing (no number, no detail the owner did not say). The server "
                    "matches it against the line in place of the quote's own words. Otherwise an empty string")
    person: str = Field(
        default="",
        description="Only for a known_people line about one person the owner can reach: that person's name or handle "
                    "as a name reads ('Dani', 'Jake', '@mkim'), nothing else. An empty string for every other line, "
                    "and for a known_people line about a group, a place or a channel ('40 agency owners on LinkedIn') "
                    "or one that says the owner knows nobody")
    target: GoalTarget | None = Field(
        default=None,
        description="On the goal_deadline line that states the main number: that number typed, which the dashboard "
                    "counts. The number must be one the line's text says. null on every other line")
    replaces_line_id: str = Field(
        description="The id of the guess or unknown line in the same section that this line corrects. An empty string "
                    "for a new line")


class KickoffTurnOut(BaseModel):
    kind: Literal["question", "playback", "proposal"] = Field(
        description="question: one question. playback: show what you understood and ask whether it is right. "
                    "proposal: propose your own idea and ask whether it is right")
    section: SectionKey = Field(
        description="The plan section this turn is about. owner_words for a turn that plays back the whole plan")
    text: str = Field(description=f"What the owner reads, {_LANGUAGE}. A question turn asks one question only")
    chips: list[str] = Field(
        description=f"0 to 5 example answers, short and in the owner's voice, {_LANGUAGE}. Leave out 'I don't know' "
                    "and 'Later' in any language: the screen adds them")
    brief_patch: list[BriefPatchLine] = Field(description="The lines this turn adds to or corrects in the plan draft")
    holds_line: bool = Field(
        default=False,
        description="true only on the one proposal that holds the line (rule 16): the owner just turned down a test "
                    "with real people or a narrower who-it's-for, and this turn says the cost once and offers the "
                    "smallest step. false on every other turn")
    done: bool = Field(description="true when every required section is settled and this turn plays back the whole "
                                   "plan to close")
    done_reason: str = Field(description="One sentence on why, when done is true. Otherwise an empty string")
