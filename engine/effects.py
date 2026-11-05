"""Применение эффектов.

Эффект — словарь с одним (или несколькими) ключами:
  give_item / take_item   — дать / забрать предмет (строка или список)
  set_flag / clear_flag   — поднять / снять флаг (строка или список)
  stat                    — {"стат": +/-N} изменить стат
  stat_set                — {"стат": N} установить стат
  journal                 — "текст" запись в журнал
  text                    — "текст" показать игроку
  danger                  — "текст" показать красным (опасность)
  if / then / else        — условный эффект
  random                  — [{"weight": 1, "effects": [...]}, ...] случайный исход
"""
from __future__ import annotations

import random
from collections import namedtuple
from typing import TYPE_CHECKING, Any

from .conditions import as_list, check
from .textutil import resolve

if TYPE_CHECKING:  # pragma: no cover
    from .loader import Scenario
    from .state import GameState

Msg = namedtuple("Msg", "kind text")

KNOWN_EFFECTS = {
    "give_item", "take_item", "set_flag", "clear_flag", "stat", "stat_set",
    "journal", "text", "danger", "if", "then", "else", "random",
}


class EffectError(ValueError):
    pass


def change_stat(state: "GameState", scenario: "Scenario", name: str, value: int,
                out: list, absolute: bool = False) -> None:
    sdef = scenario.stats_def.get(name)
    if sdef is None:
        raise EffectError(f"неизвестный стат: {name}")
    old = state.stats.get(name, 0)
    new = int(value) if absolute else old + int(value)
    lo, hi = sdef.get("min", 0), sdef.get("max")
    new = max(lo, new)
    if hi is not None:
        new = min(hi, new)
    state.stats[name] = new
    if sdef.get("hidden") or new == old:
        return
    delta = new - old
    sign = "+" if delta > 0 else "-"
    label = sdef.get("label", name)
    shown = scenario.format_stat(name, new)
    out.append(Msg("system", f"{label.capitalize()}: {sign}{abs(delta)} (теперь {shown})"))


def apply(effects: Any, state: "GameState", scenario: "Scenario", out: list,
          rng: random.Random | None = None) -> None:
    rng = rng or random
    for eff in as_list(effects):
        if not isinstance(eff, dict):
            raise EffectError(f"эффект должен быть объектом: {eff!r}")
        if "if" in eff:
            branch = eff.get("then") if check(eff["if"], state) else eff.get("else")
            apply(branch, state, scenario, out, rng)
            continue
        for key, val in eff.items():
            _apply_one(key, val, state, scenario, out, rng)


def _apply_one(key: str, val: Any, state: "GameState", scenario: "Scenario",
               out: list, rng: random.Random) -> None:
    if key == "give_item":
        for item in as_list(val):
            if state.give(item):
                out.append(Msg("system", f"Получено: [{scenario.item_name(item)}]"))
    elif key == "take_item":
        for item in as_list(val):
            if state.take(item):
                out.append(Msg("system", f"Больше нет: [{scenario.item_name(item)}]"))
    elif key == "set_flag":
        for f in as_list(val):
            state.flags.add(f)
    elif key == "clear_flag":
        for f in as_list(val):
            state.flags.discard(f)
    elif key == "stat":
        if not isinstance(val, dict):
            raise EffectError("stat ожидает словарь стат->изменение")
        for name, delta in val.items():
            change_stat(state, scenario, name, int(delta), out)
    elif key == "stat_set":
        if not isinstance(val, dict):
            raise EffectError("stat_set ожидает словарь стат->значение")
        for name, value in val.items():
            change_stat(state, scenario, name, int(value), out, absolute=True)
    elif key == "journal":
        entry = resolve(val, state, scenario, rng)
        if entry and entry not in state.journal:
            state.journal.append(entry)
            out.append(Msg("system", "(В журнале новая запись.)"))
    elif key == "text":
        out.append(Msg("text", resolve(val, state, scenario, rng)))
    elif key == "danger":
        out.append(Msg("danger", resolve(val, state, scenario, rng)))
    elif key == "random":
        options = [o for o in as_list(val) if isinstance(o, dict)]
        if not options:
            return
        weights = [max(0.0, float(o.get("weight", 1))) for o in options]
        if sum(weights) <= 0:
            weights = [1.0] * len(options)
        chosen = rng.choices(options, weights=weights, k=1)[0]
        if "text" in chosen:
            out.append(Msg("text", resolve(chosen["text"], state, scenario, rng)))
        apply(chosen.get("effects"), state, scenario, out, rng)
    elif key in ("then", "else"):
        return
    else:
        raise EffectError(f"неизвестный эффект: {key}")


def iter_refs(effects: Any):
    """Ссылки из эффектов: ("give"|"take"|"set_flag"|"clear_flag"|"stat"|"cond"|"unknown", имя)."""
    from .conditions import iter_refs as cond_refs

    for eff in as_list(effects):
        if not isinstance(eff, dict):
            yield ("unknown", repr(eff))
            continue
        if "if" in eff:
            for r in cond_refs(eff["if"]):
                yield ("cond", r)
            yield from iter_refs(eff.get("then"))
            yield from iter_refs(eff.get("else"))
            continue
        for key, val in eff.items():
            if key == "give_item":
                for i in as_list(val):
                    yield ("give", i)
            elif key == "take_item":
                for i in as_list(val):
                    yield ("take", i)
            elif key == "set_flag":
                for f in as_list(val):
                    yield ("set_flag", f)
            elif key == "clear_flag":
                for f in as_list(val):
                    yield ("clear_flag", f)
            elif key in ("stat", "stat_set"):
                if isinstance(val, dict):
                    for s in val:
                        yield ("stat", s)
            elif key == "random":
                for o in as_list(val):
                    if isinstance(o, dict):
                        yield from iter_refs(o.get("effects"))
            elif key in ("journal", "text", "danger", "then", "else"):
                continue
            else:
                yield ("unknown", key)
