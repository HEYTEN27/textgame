"""Игровая сессия: исполнение сценария и игровой цикл.

`Session` — чистая логика без ввода-вывода (её же использует бот-тестировщик).
`play()` — интерактивный цикл поверх `Session` и `Display`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from . import dice, effects, parser
from .conditions import as_list, check
from .effects import Msg
from .loader import ENDING_TYPE_NAMES, Scenario
from .state import GameState
from .textutil import resolve, resolve_extras

MAX_ROUTER_DEPTH = 32
UNDO_DEPTH = 30

NEUTRAL_USE = [
    "Здесь это бесполезно.",
    "Ты вертишь это в руках, но ничего не происходит. Здесь это бесполезно.",
    "Не похоже, что это чем-то поможет прямо сейчас.",
]


@dataclass
class Response:
    msgs: list[Msg] = field(default_factory=list)
    action: str | None = None  # save, load, exit, help


class Session:
    def __init__(self, scenario: Scenario, state: GameState | None = None,
                 rng: random.Random | None = None):
        self.scenario = scenario
        self.rng = rng or random.Random()
        self.ended: str | None = None
        self.need_describe = True
        self.history: list[dict] = []
        self._enter_msgs: list[Msg] = []
        if state is None:
            self.state = GameState(scenario_id=scenario.id, node=scenario.start)
            self.state.stats = scenario.initial_stats()
            for item in scenario.start_items:
                self.state.give(item)
            self._enter(scenario.start, self._enter_msgs)
        else:
            self.state = state
            if not scenario.exists(state.node):
                raise ValueError(f"сцена «{state.node}» не найдена в сценарии")
            for name, sdef in scenario.stats_def.items():
                self.state.stats.setdefault(name, int(sdef["value"]))
            self.state.inventory = [i for i in self.state.inventory if i in scenario.items]
            if scenario.is_ending(state.node):
                self.ended = state.node

    # ------------------------------------------------------------ сведения
    @property
    def node_id(self) -> str:
        return self.state.node

    @property
    def node(self) -> dict:
        return self.scenario.get(self.state.node)

    def is_input_node(self) -> bool:
        return not self.ended and isinstance(self.node.get("input"), dict)

    def _text(self, value: Any) -> str:
        return resolve(value, self.state, self.scenario, self.rng)

    def describe(self) -> list[Msg]:
        node = self.node
        out = [Msg("text", self._text(node.get("text", "")))]
        for extra in resolve_extras(node.get("extra"), self.state, self.scenario, self.rng):
            out.append(Msg("text", extra))
        self.need_describe = False
        return out

    def pop_enter_msgs(self) -> list[Msg]:
        msgs, self._enter_msgs = self._enter_msgs, []
        return msgs

    def _once_key(self, choice: dict, idx: int) -> str:
        return f"{self.state.node}:{choice.get('id', idx)}"

    def available_choices(self) -> list[dict]:
        if self.ended:
            return []
        out = []
        for idx, c in enumerate(self.node.get("choices", []) or []):
            if c.get("once") and self._once_key(c, idx) in self.state.used_once:
                continue
            if not check(c.get("if"), self.state):
                continue
            out.append(dict(c, _idx=idx))
        return out

    def check_odds(self, chk: dict) -> tuple[float, int]:
        stat = chk["stat"]
        mod = dice.modifier(self.state.stats.get(stat, 0))
        for b in chk.get("bonus", []) or []:
            if check(b.get("if"), self.state):
                mod += int(b.get("value", 0))
        return dice.success_chance(int(chk["dc"]), mod), mod

    def choice_label(self, c: dict) -> str:
        text = self._text(c.get("text", ""))
        chk = c.get("check")
        if chk:
            p, _ = self.check_odds(chk)
            label = self.scenario.stat_label(chk["stat"])
            if self.scenario.stats_def.get(chk["stat"], {}).get("hidden"):
                text += f" (риск: {dice.odds_words(p)})"
            else:
                text += f" (проверка: {label} - {dice.odds_words(p)})"
        return text

    # ------------------------------------------------------------ переходы
    def _enter(self, node_id: str, msgs: list[Msg], depth: int = 0) -> None:
        if depth > MAX_ROUTER_DEPTH:
            raise RuntimeError(f"зацикливание маршрутизаторов около «{node_id}»")
        sc, st = self.scenario, self.state
        st.node = node_id
        first = st.visited.get(node_id, 0) == 0
        st.visited[node_id] = st.visited.get(node_id, 0) + 1
        node = sc.get(node_id)
        self.need_describe = True
        if sc.is_ending(node_id):
            self.ended = node_id
            effects.apply(node.get("on_enter"), st, sc, msgs, self.rng)
            return
        st.input_attempts.pop(node_id, None)
        if first:
            effects.apply(node.get("on_first_enter"), st, sc, msgs, self.rng)
        effects.apply(node.get("on_enter"), st, sc, msgs, self.rng)
        death = self._zero_ending()
        if death:
            self._enter(death, msgs, depth + 1)
            return
        router = node.get("router")
        if router:
            for r in router:
                if check(r.get("if"), st):
                    if r.get("text"):
                        msgs.append(Msg("text", self._text(r["text"])))
                    effects.apply(r.get("effects"), st, sc, msgs, self.rng)
                    self._enter(r["goto"], msgs, depth + 1)
                    return

    def _zero_ending(self) -> str | None:
        for name, sdef in self.scenario.stats_def.items():
            ze = sdef.get("zero_ending")
            if ze and self.state.stats.get(name, 0) <= 0:
                return ze
        return None

    def _tick(self, dest: str | None, msgs: list[Msg]) -> str | None:
        st, sc = self.state, self.scenario
        st.turns += 1
        for t in sc.timers:
            ts = st.timer(t["id"])
            if ts["pending_goto"]:
                if dest is None or not sc.is_ending(dest):
                    ts["pending_goto"] = False
                    dest = t["goto"]
                continue
            if ts["done"] or not check(t.get("if"), st):
                continue
            ts["ticks"] += 1
            if ts["ticks"] % int(t["every"]) != 0:
                continue
            ts["fires"] += 1
            texts = t.get("texts") or []
            if texts:
                txt = self._text(texts[min(ts["fires"], len(texts)) - 1])
                if txt:
                    msgs.append(Msg(t.get("style", "danger"), txt))
            effects.apply(t.get("effects"), st, sc, msgs, self.rng)
            mx = t.get("max")
            if mx and ts["fires"] >= int(mx):
                ts["done"] = True
                if t.get("goto"):
                    if dest is not None and sc.is_ending(dest):
                        ts["pending_goto"] = False
                    else:
                        dest = t["goto"]
        return dest

    def _advance(self, dest: str | None, msgs: list[Msg]) -> None:
        dest = self._tick(dest, msgs)
        if not (dest and self.scenario.is_ending(dest)):
            death = self._zero_ending()
            if death:
                dest = death
        if dest:
            self._enter(dest, self._enter_msgs)

    # ------------------------------------------------------------ действия
    def choose(self, number: int) -> list[Msg]:
        choices = self.available_choices()
        if not choices:
            return [Msg("system", "Здесь нечего выбирать.")]
        if not 1 <= number <= len(choices):
            return [Msg("system", f"Нет такого варианта. Выбери число от 1 до {len(choices)}.")]
        c = choices[number - 1]
        return self.apply_choice(c)

    def apply_choice(self, c: dict) -> list[Msg]:
        st, sc = self.state, self.scenario
        msgs: list[Msg] = []
        if c.get("once"):
            st.used_once.add(self._once_key(c, c["_idx"]))
        if c.get("result"):
            msgs.append(Msg("text", self._text(c["result"])))
        effects.apply(c.get("effects"), st, sc, msgs, self.rng)
        dest = c.get("goto")
        chk = c.get("check")
        if chk:
            _, mod = self.check_odds(chk)
            res = dice.roll(int(chk["dc"]), mod, self.rng)
            msgs.append(Msg("good" if res.success else "danger", res.describe()))
            if res.success:
                if c.get("success_text"):
                    msgs.append(Msg("text", self._text(c["success_text"])))
                effects.apply(c.get("success_effects"), st, sc, msgs, self.rng)
                dest = c["success"]
            else:
                if c.get("fail_text"):
                    msgs.append(Msg("text", self._text(c["fail_text"])))
                effects.apply(c.get("fail_effects"), st, sc, msgs, self.rng)
                dest = c["fail"]
        self._advance(dest, msgs)
        return msgs

    def use_item(self, item_id: str) -> list[Msg]:
        st, sc = self.state, self.scenario
        if not st.has(item_id):
            return [Msg("system", "У тебя этого нет.")]
        msgs: list[Msg] = []
        node = self.node
        use = (node.get("item_uses") or {}).get(item_id)
        key = f"use:{st.node}:{item_id}"
        if use and use.get("once") and key in st.used_once:
            use = None
        if use and not check(use.get("if"), st):
            if use.get("else_text"):
                return [Msg("text", self._text(use["else_text"]))]
            use = None
        if use is None:
            gen = sc.items.get(item_id, {}).get("use")
            gkey = f"guse:{item_id}"
            if gen and gen.get("once") and gkey in st.used_once:
                gen = None
            if gen and check(gen.get("if"), st):
                use, key = gen, gkey
            else:
                if gen and gen.get("else_text"):
                    return [Msg("text", self._text(gen["else_text"]))]
                return [Msg("system", self.rng.choice(NEUTRAL_USE))]
        if use.get("once"):
            st.used_once.add(key)
        if use.get("text"):
            msgs.append(Msg("text", self._text(use["text"])))
        effects.apply(use.get("effects"), st, sc, msgs, self.rng)
        if use.get("consume"):
            effects.apply({"take_item": item_id}, st, sc, msgs, self.rng)
        self._advance(use.get("goto"), msgs)
        return msgs

    def examine_item(self, item_id: str) -> list[Msg]:
        st, sc = self.state, self.scenario
        item = sc.items[item_id]
        msgs = [Msg("item", f"[{item.get('name', item_id)}]"),
                Msg("text", self._text(item.get("description", "Ничего особенного.")))]
        effects.apply(item.get("examine_effects"), st, sc, msgs, self.rng)
        for sec in as_list(item.get("examine_secret")):
            if not isinstance(sec, dict):
                continue
            cond = sec.get("if")
            if "if_flag" in sec and sec["if_flag"] not in st.flags:
                continue
            if not check(cond, st):
                continue
            msgs.append(Msg("text", self._text(sec.get("text", ""))))
            effects.apply(sec.get("effects"), st, sc, msgs, self.rng)
        return msgs

    def examine(self, query: str) -> list[Msg]:
        st, sc = self.state, self.scenario
        if not parser.words(query):
            self.need_describe = True
            return []
        inv = {i: sc.items[i] for i in st.inventory if i in sc.items}
        found = parser.match_items(query, inv)
        if len(found) == 1:
            return self.examine_item(found[0])
        objects = self.node.get("examine") or {}
        obj_index = {oid: {"name": oid, "aliases": o.get("aliases", [])}
                     for oid, o in objects.items() if check(o.get("if"), st)}
        ofound = parser.match_items(query, obj_index)
        if len(found) > 1:
            names = ", ".join(sc.item_name(i) for i in found)
            return [Msg("system", f"Уточни, что именно: {names}?")]
        if ofound:
            obj = objects[ofound[0]]
            msgs = [Msg("text", self._text(obj.get("text", "Ничего особенного.")))]
            okey = f"ex:{st.node}:{ofound[0]}"
            if not (obj.get("once") and okey in st.used_once):
                st.used_once.add(okey)
                effects.apply(obj.get("effects"), st, sc, msgs, self.rng)
                if obj.get("goto"):
                    self._advance(obj["goto"], msgs)
            return msgs
        if parser.match_items(query, sc.items):
            return [Msg("system", "У тебя этого нет, а здесь ничего такого не видно.")]
        return [Msg("system", "Ты присматриваешься, но не замечаешь ничего примечательного.")]

    def answer(self, text: str) -> list[Msg]:
        st, sc = self.state, self.scenario
        inp = self.node.get("input")
        if not isinstance(inp, dict):
            return [Msg("system", "Не понимаю. Введи номер варианта или «помощь».")]
        norm = parser.normalize(text).replace(" ", "")
        for ans in inp.get("answers", []):
            if not check(ans.get("if"), st):
                continue
            variants = [parser.normalize(m).replace(" ", "") for m in as_list(ans.get("match"))]
            if norm in variants:
                msgs: list[Msg] = []
                if ans.get("text"):
                    msgs.append(Msg("text", self._text(ans["text"])))
                effects.apply(ans.get("effects"), st, sc, msgs, self.rng)
                self._advance(ans.get("goto"), msgs)
                return msgs
        n = st.input_attempts.get(st.node, 0) + 1
        st.input_attempts[st.node] = n
        limit = int(inp.get("attempts", 0) or 0)
        wrong = as_list(inp.get("wrong_text")) or ["Неверно."]
        msgs = [Msg("danger", self._text(wrong[min(n, len(wrong)) - 1]))]
        if limit and n >= limit:
            if inp.get("fail_text"):
                msgs.append(Msg("text", self._text(inp["fail_text"])))
            effects.apply(inp.get("fail_effects"), st, sc, msgs, self.rng)
            self._advance(inp.get("on_fail"), msgs)
        elif limit:
            msgs.append(Msg("system", f"Осталось попыток: {limit - n}."))
        return msgs

    # ------------------------------------------------------------ команды
    def inventory_msgs(self) -> list[Msg]:
        sc = self.scenario
        if not self.state.inventory:
            return [Msg("system", "Инвентарь пуст.")]
        out = [Msg("title", "Инвентарь")]
        for n, iid in enumerate(self.state.inventory, 1):
            item = sc.items.get(iid, {})
            short = self._text(item.get("short") or item.get("description", ""))
            short = short.split(". ")[0].rstrip(".") + "." if short else ""
            out.append(Msg("plain", f"  {n}. [{item.get('name', iid)}] - {short}"))
        out.append(Msg("system", "Подробнее: осмотреть <предмет>. Применить: использовать <предмет>."))
        return out

    def status_msgs(self) -> list[Msg]:
        sc, st = self.scenario, self.state
        out = [Msg("title", f"Статус - {sc.title}")]
        for kind, header in (("stat", "Характеристики"), ("resource", "Ресурсы")):
            rows = [(n, d) for n, d in sc.stats_def.items()
                    if d.get("kind") == kind and not d.get("hidden")]
            if not rows:
                continue
            out.append(Msg("plain", f"  {header}:"))
            for name, d in rows:
                val = st.stats.get(name, 0)
                shown = sc.format_stat(name, val)
                bar = ""
                if kind == "stat" and d.get("max"):
                    filled = max(0, min(10, round(10 * val / max(1, d["max"]))))
                    bar = "  " + "#" * filled + "." * (10 - filled)
                    shown = f"{shown}/{d['max']}"
                out.append(Msg("plain", f"    {d.get('label', name):<16} {shown}{bar}"))
        out.append(Msg("plain", f"  Сделано ходов: {st.turns}"))
        return out

    def journal_msgs(self) -> list[Msg]:
        if not self.state.journal:
            return [Msg("system", "Журнал пока пуст.")]
        out = [Msg("title", "Журнал")]
        for n, entry in enumerate(self.state.journal, 1):
            out.append(Msg("plain", f"  {n}. {entry}"))
        return out

    def resolve_item(self, query: str) -> tuple[str | None, list[Msg]]:
        sc, st = self.scenario, self.state
        q = query.strip()
        if q.isdigit():
            n = int(q)
            if 1 <= n <= len(st.inventory):
                return st.inventory[n - 1], []
            return None, [Msg("system", "Нет предмета с таким номером в инвентаре.")]
        inv = {i: sc.items[i] for i in st.inventory if i in sc.items}
        found = parser.match_items(q, inv)
        if len(found) == 1:
            return found[0], []
        if len(found) > 1:
            names = ", ".join(sc.item_name(i) for i in found)
            return None, [Msg("system", f"Уточни, что именно: {names}?")]
        if parser.match_items(q, sc.items):
            return None, [Msg("system", "У тебя этого нет.")]
        return None, [Msg("system", f"У тебя нет ничего похожего на «{q}». Загляни в инвентарь: «и».")]

    def handle(self, line: str) -> Response:
        if self.ended:
            return Response([Msg("system", "История окончена.")])
        cmd = parser.parse(line, answer_mode=self.is_input_node())
        if cmd.kind == "undo":
            return Response(self.undo())
        before = self.state.to_dict()
        resp = self._dispatch(cmd)
        if self.state.to_dict() != before:
            self.history.append(before)
            del self.history[:-UNDO_DEPTH]
        return resp

    def undo(self) -> list[Msg]:
        if self.ended:
            return [Msg("system", "История окончена - отменять нечего.")]
        if not self.history:
            return [Msg("system", "Отменять нечего: это самое начало.")]
        self.state = GameState.from_dict(self.history.pop())
        self.need_describe = True
        self._enter_msgs = []
        return [Msg("system", f"Последнее действие отменено. Можно отменить ещё: {len(self.history)}.")]

    def hud_line(self, width: int = 80) -> str:
        sc, st = self.scenario, self.state
        parts = []
        for name, d in sc.stats_def.items():
            if d.get("hidden"):
                continue
            val = sc.format_stat(name, st.stats.get(name, 0))
            if d.get("kind") == "stat" and d.get("max"):
                val = f"{val}/{d['max']}"
            parts.append(f"{name} {val}")
        parts.append(f"ход {st.turns}")
        line = " · ".join(parts)
        full = f"{sc.title} | {line}"
        return full if len(full) <= width else line

    def _dispatch(self, cmd) -> Response:
        k = cmd.kind
        if k == "empty":
            return Response([Msg("system", "Введи номер варианта или команду («помощь» - список команд).")])
        if k == "choice":
            return Response(self.choose(cmd.number))
        if k == "inventory":
            return Response(self.inventory_msgs())
        if k == "status":
            return Response(self.status_msgs())
        if k == "journal":
            return Response(self.journal_msgs())
        if k == "look":
            self.need_describe = True
            return Response([])
        if k == "examine":
            if not cmd.arg:
                self.need_describe = True
                return Response([])
            return Response(self.examine(cmd.arg))
        if k == "use":
            if not cmd.arg:
                return Response([Msg("system", "Что использовать? Например: использовать ключ.")])
            iid, msgs = self.resolve_item(cmd.arg)
            if iid is None:
                return Response(msgs)
            return Response(self.use_item(iid))
        if k == "answer":
            return Response(self.answer(cmd.arg))
        if k in ("save", "load", "exit", "help"):
            return Response([], action=k)
        return Response([Msg("system", "Не понимаю. Введи номер варианта или «помощь», чтобы увидеть команды.")])

    # ------------------------------------------------------------ концовка
    def ending_info(self) -> dict:
        eid = self.ended
        end = self.scenario.endings[eid]
        return {
            "id": eid,
            "title": end.get("title", eid),
            "type": end.get("type", "neutral"),
            "type_name": ENDING_TYPE_NAMES.get(end.get("type", ""), ""),
            "number": self.scenario.ending_number(eid),
            "total": len(self.scenario.endings),
        }


HELP_TEXT = """Команды (работают всегда):
  <номер>                  выбрать вариант действия
  и, инвентарь             показать инвентарь
  осмотреть <предмет>      подробно рассмотреть предмет или деталь сцены
  осмотреться              ещё раз описать сцену
  использовать <предмет>   применить предмет в текущей сцене
  статус                   характеристики персонажа
  отменить                 отменить последнее действие (до 30 шагов назад)
  журнал                   хроника важных событий
  сохранить / загрузить    3 слота сохранения
  помощь                   эта справка
  выход                    выйти в главное меню

Предмет можно называть в любом падеже и не полностью: «ключ», «ключа»,
«латунный». Вместо названия можно указать номер из инвентаря:
«использовать 2». Нажми Enter во время вывода текста, чтобы допечатать его сразу."""


def dsp_width() -> int:
    from .display import text_width

    return text_width()


def _format_minutes(seconds: float) -> str:
    m = int(seconds // 60)
    return "меньше минуты" if m < 1 else f"{m} мин."


def play(session: Session, display, store, scenarios: dict[str, Scenario], music=None) -> None:
    """Интерактивный цикл. Возвращается в главное меню по выходу/концовке."""
    import time

    started = time.monotonic()
    first = True

    def enter_scenario(sess: Session) -> None:
        display.set_title(f"Текстовые приключения - {sess.scenario.title}")
        if music is not None:
            music.play(sess.scenario.data.get("music"))

    def flush_time() -> None:
        nonlocal started
        now = time.monotonic()
        store.update_stats(play_seconds=now - started)
        started = now

    enter_scenario(session)
    try:
        while True:
            if session.need_describe:
                if not first:
                    display.rule()
                first = False
                display.show(session.describe())
                display.show(session.pop_enter_msgs())
            if session.ended:
                info = session.ending_info()
                new = store.add_ending(session.scenario.id, info["id"])
                store.clear_autosave()
                store.update_stats(finished=1)
                if music is not None:
                    music.play("ending")
                display.ending_banner(info, new)
                found = len([e for e in store.load_achievements().get(session.scenario.id, [])
                             if e in session.scenario.endings])
                display.show([Msg("system",
                                  f"Ходов: {session.state.turns}. Время в этой истории: "
                                  f"{_format_minutes(time.monotonic() - started)} "
                                  f"Открыто концовок этой истории: {found}/{info['total']}.")])
                flush_time()
                if display.confirm("Начать этот сценарий заново? (д/н): "):
                    session = Session(session.scenario)
                    store.update_stats(games=1)
                    display.clear()
                    first = True
                    enter_scenario(session)
                    continue
                return
            store.autosave(session)
            choices = session.available_choices()
            display.blank()
            for n, c in enumerate(choices, 1):
                display.choice(n, session.choice_label(c))
            if session.is_input_node():
                prompt = session.node["input"].get("prompt", "Введи ответ")
                display.show([Msg("system", f"{prompt} (или номер варианта)")])
            line = display.ask("> ")
            if line is None:  # EOF / Ctrl+C
                if display.confirm("Выйти в главное меню? Прогресс сохранён автоматически (д/н): "):
                    return
                continue
            resp = session.handle(line)
            if session.need_describe or session.ended:
                display.clear()
                if display.settings.get("clear_screen", True) and display.interactive():
                    display.hud(session.hud_line(dsp_width()))
                    first = True
            display.show(resp.msgs)
            if resp.action == "help":
                display.show([Msg("plain", HELP_TEXT)], markup=False)
            elif resp.action == "save":
                store.save_dialog(session, display)
            elif resp.action == "load":
                loaded = store.load_dialog(display, scenarios)
                if loaded is not None:
                    session = loaded
                    enter_scenario(session)
            elif resp.action == "exit":
                if display.confirm("Выйти в главное меню? Прогресс сохранён автоматически (д/н): "):
                    return
                display.show([Msg("system", "Продолжаем.")])
    finally:
        flush_time()
