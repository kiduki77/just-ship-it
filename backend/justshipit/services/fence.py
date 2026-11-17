"""Text the owner wrote (or a record built from it) enters a model prompt as data between fences. The fence's own
marker is neutralized inside the text, so the text cannot close the fence and pass for the prompt's instructions."""
from __future__ import annotations


def neutralize(text: str) -> str:
    return text.replace("<<<", "＜＜＜").replace(">>>", "＞＞＞")


def fenced(label: str, text: str) -> str:
    return f"<<<데이터 시작: {label}>>>\n{neutralize(text)}\n<<<데이터 끝: {label}>>>"
