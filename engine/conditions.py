"""Проверка условий.

Условие — это словарь (все ключи должны выполниться), список словарей
(логическое И) или None (всегда истинно).

Поддерживаемые ключи:
  has_item, not_item         — предмет есть / нет (строка или список)
  flag, not_flag             — флаг поднят / не поднят (строка или список)
  any_flag                   — поднят хотя бы один из флагов
  stat_gte, stat_lte         — {"стат": N}: стат >= N / <= N
  stat_gt, stat_lt           — строгие сравнения
  visited, not_visited       — сцена посещалась / нет
  revisit                    — true: игрок уже бывал в текущей сцене раньше
  turns_gte, turns_lte       — число сделанных ходов
  timer_gte                  — {"таймер": N}: таймер сработал не менее N раз
  any                        — список условий, достаточно одного (ИЛИ)
  all                        — список условий, нужны все (И)
  not                        — отрицание условия
"""
from __future__ import annotations

from typing import Any, Iterator

from .state import GameState

KNOWN_CONDITIONS = {
    "has_item", "not_item", "flag", "not_flag", "any_flag",
    "stat_gte", "stat_lte", "stat_gt", "stat_lt",
    "visited", "not_visited", "revisit", "turns_gte", "turns_lte", "timer_gte",
    "any", "all", "not",
}


class ConditionError(ValueError):
    pass


def as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def check(cond: Any, state: GameState) -> bool:
    if cond is None:
        return True
    if isinstance(cond, list):
        return all(check(c, state) for c in cond)
    if not isinstance(cond, dict):
        raise ConditionError(f"условие должно быть объектом: {cond!r}")
    for key, val in cond.items():
        if not _check_one(key, val, state):
            return False
    return True


def _stat_cmp(val: Any, state: GameState, op) -> bool:
    if not isinstance(val, dict):
        raise ConditionError(f"ожидался словарь стат->число: {val!r}")
    return all(op(state.stats.get(name, 0), int(n)) for name, n in val.items())


def _check_one(key: str, val: Any, state: GameState) -> bool:
    if key == "has_item":
        return all(state.has(i) for i in as_list(val))
    if key == "not_item":
        return not any(state.has(i) for i in as_list(val))
    if key == "flag":
        return all(f in state.flags for f in as_list(val))
    if key == "not_flag":
        return not any(f in state.flags for f in as_list(val))
    if key == "any_flag":
        return any(f in state.flags for f in as_list(val))
    if key == "stat_gte":
        return _stat_cmp(val, state, lambda a, b: a >= b)
    if key == "stat_lte":
        return _stat_cmp(val, state, lambda a, b: a <= b)
    if key == "stat_gt":
        return _stat_cmp(val, state, lambda a, b: a > b)
    if key == "stat_lt":
        return _stat_cmp(val, state, lambda a, b: a < b)
    if key == "visited":
        return all(state.visited.get(n, 0) > 0 for n in as_list(val))
    if key == "not_visited":
        return not any(state.visited.get(n, 0) > 0 for n in as_list(val))
    if key == "revisit":
        return (state.visited.get(state.node, 0) > 1) == bool(val)
    if key == "turns_gte":
        return state.turns >= int(val)
    if key == "turns_lte":
        return state.turns <= int(val)
    if key == "timer_gte":
        if not isinstance(val, dict):
            raise ConditionError("timer_gte ожидает словарь таймер->число")
        return all(state.timer(t)["fires"] >= int(n) for t, n in val.items())
    if key == "any":
        return any(check(c, state) for c in as_list(val))
    if key == "all":
        return all(check(c, state) for c in as_list(val))
    if key == "not":
        return not check(val, state)
    raise ConditionError(f"неизвестное условие: {key}")


def iter_refs(cond: Any) -> Iterator[tuple[str, str]]:
    """Перечисляет ссылки из условия: ("item"|"flag"|"stat"|"node"|"timer"|"unknown", имя)."""
    if cond is None:
        return
    if isinstance(cond, list):
        for c in cond:
            yield from iter_refs(c)
        return
    if not isinstance(cond, dict):
        yield ("unknown", repr(cond))
        return
    for key, val in cond.items():
        if key in ("has_item", "not_item"):
            for i in as_list(val):
                yield ("item", i)
        elif key in ("flag", "not_flag", "any_flag"):
            for f in as_list(val):
                yield ("flag", f)
        elif key in ("stat_gte", "stat_lte", "stat_gt", "stat_lt"):
            if isinstance(val, dict):
                for s in val:
                    yield ("stat", s)
        elif key in ("visited", "not_visited"):
            for n in as_list(val):
                yield ("node", n)
        elif key == "timer_gte":
            if isinstance(val, dict):
                for t in val:
                    yield ("timer", t)
        elif key in ("any", "all"):
            for c in as_list(val):
                yield from iter_refs(c)
        elif key == "not":
            yield from iter_refs(val)
        elif key in ("turns_gte", "turns_lte", "revisit"):
            continue
        else:
            yield ("unknown", key)
