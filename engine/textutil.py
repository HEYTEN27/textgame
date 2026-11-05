"""Разрешение текстов: варианты по условиям и подстановки.

Текстовое поле в сценарии может быть:
  * строкой;
  * списком вариантов [{"if": условие, "text": "..."}, ..., {"text": "по умолчанию"}] —
    берётся первый подходящий.

Подстановки внутри строки:
  {stat:имя}   — значение стата (с форматом, если задан)
  {item:id}    — название предмета
  {turns}      — число сделанных ходов
  {rand:а|б|в} — случайный вариант
"""
from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING, Any

from .conditions import check

if TYPE_CHECKING:  # pragma: no cover
    from .loader import Scenario
    from .state import GameState

_PLACEHOLDER = re.compile(r"\{(stat|item|rand):([^{}]*)\}|\{turns\}")


def pick_variant(value: Any, state: "GameState") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for variant in value:
            if isinstance(variant, str):
                return variant
            if isinstance(variant, dict) and check(variant.get("if"), state):
                return str(variant.get("text", ""))
        return ""
    return str(value)


def substitute(text: str, state: "GameState", scenario: "Scenario",
               rng: random.Random | None = None) -> str:
    rng = rng or random

    def repl(m: re.Match) -> str:
        if m.group(0) == "{turns}":
            return str(state.turns)
        kind, arg = m.group(1), m.group(2)
        if kind == "stat":
            return scenario.format_stat(arg, state.stats.get(arg, 0))
        if kind == "item":
            return scenario.item_name(arg)
        if kind == "rand":
            opts = arg.split("|")
            return rng.choice(opts) if opts else ""
        return m.group(0)

    return _PLACEHOLDER.sub(repl, text)


def resolve(value: Any, state: "GameState", scenario: "Scenario",
            rng: random.Random | None = None) -> str:
    return substitute(pick_variant(value, state), state, scenario, rng)


def resolve_extras(extras: Any, state: "GameState", scenario: "Scenario",
                   rng: random.Random | None = None) -> list[str]:
    """Все подходящие дополнительные абзацы (в отличие от вариантов — берутся все)."""
    out = []
    for ex in extras or []:
        if isinstance(ex, str):
            out.append(substitute(ex, state, scenario, rng))
        elif isinstance(ex, dict) and check(ex.get("if"), state):
            out.append(resolve(ex.get("text", ""), state, scenario, rng))
    return out


def word_count(text: str) -> int:
    clean = re.sub(r"[\[\]<>{}]", " ", text)
    return len([w for w in clean.split() if any(ch.isalpha() for ch in w)])
