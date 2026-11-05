"""Загрузка и структурная валидация JSON-сценариев."""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

from . import conditions, effects, paths

NODE_KEYS = {
    "text", "extra", "on_enter", "on_first_enter", "choices", "item_uses",
    "examine", "router", "input", "secret", "note",
}
CHOICE_KEYS = {
    "text", "if", "goto", "effects", "result", "once", "id", "check",
    "success", "fail", "success_effects", "fail_effects", "success_text",
    "fail_text", "note",
}
ENDING_KEYS = {"title", "type", "text", "extra", "secret", "on_enter", "note"}
ENDING_TYPES = {"good", "bad", "strange", "secret", "death", "defeat", "neutral"}
ENDING_TYPE_NAMES = {
    "good": "хорошая", "bad": "плохая", "strange": "странная",
    "secret": "секретная", "death": "смерть", "defeat": "поражение",
    "neutral": "нейтральная",
}
STAT_KEYS = {"value", "min", "max", "hidden", "kind", "label", "zero_ending",
             "format", "offset", "mod"}
TIMER_KEYS = {"id", "every", "if", "effects", "texts", "max", "goto", "style", "note"}


class ScenarioError(Exception):
    def __init__(self, scenario: str, problems: list[str]):
        self.scenario = scenario
        self.problems = problems
        super().__init__(f"{scenario}: " + "; ".join(problems[:5]))


class Scenario:
    """Обёртка над словарём сценария с удобными методами."""

    def __init__(self, data: dict[str, Any], path: str | None = None):
        self.data = data
        self.path = path
        self.id: str = data["id"]
        self.title: str = data.get("title", self.id)
        self.genre: str = data.get("genre", "")
        self.teaser: str = data.get("teaser", "")
        self.start: str = data["start"]
        self.items: dict[str, dict] = data.get("items", {})
        self.nodes: dict[str, dict] = data.get("nodes", {})
        self.endings: dict[str, dict] = data.get("endings", {})
        self.characters: dict[str, dict] = data.get("characters", {})
        self.timers: list[dict] = data.get("timers", [])
        self.start_items: list[str] = data.get("start_items", [])
        self.stats_def: dict[str, dict] = normalize_stats(data.get("stats", {}))

    # --- узлы ---
    def is_ending(self, node_id: str) -> bool:
        return node_id in self.endings

    def exists(self, node_id: str) -> bool:
        return node_id in self.nodes or node_id in self.endings

    def get(self, node_id: str) -> dict:
        if node_id in self.nodes:
            return self.nodes[node_id]
        return self.endings[node_id]

    def ending_number(self, ending_id: str) -> int:
        return list(self.endings).index(ending_id) + 1

    # --- предметы и статы ---
    def item_name(self, item_id: str) -> str:
        item = self.items.get(item_id)
        return item.get("name", item_id) if item else item_id

    def stat_label(self, name: str) -> str:
        return self.stats_def.get(name, {}).get("label", name)

    def format_stat(self, name: str, value: int) -> str:
        sdef = self.stats_def.get(name, {})
        v = int(value) + int(sdef.get("offset", 0))
        if sdef.get("mod"):
            v %= int(sdef["mod"])
        fmt = sdef.get("format")
        if fmt:
            try:
                return fmt.replace("{v}", str(v)).replace("{v2}", f"{v:02d}")
            except Exception:  # noqa: BLE001
                return str(v)
        return str(v)

    def initial_stats(self) -> dict[str, int]:
        return {k: int(v["value"]) for k, v in self.stats_def.items()}


def normalize_stats(raw: dict[str, Any]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, val in raw.items():
        if isinstance(val, (int, float)):
            d = {"value": int(val)}
        elif isinstance(val, dict):
            d = dict(val)
        else:
            d = {"value": 0}
        d.setdefault("value", 0)
        d.setdefault("min", 0)
        d.setdefault("max", d["value"])
        d.setdefault("hidden", False)
        d.setdefault("kind", "stat")
        d.setdefault("label", name)
        out[name] = d
    return out


# ---------------------------------------------------------------- обход данных

def iter_choices(node: dict) -> Iterator[dict]:
    for c in node.get("choices", []) or []:
        if isinstance(c, dict):
            yield c


def node_targets(node: dict, scenario_data: dict | None = None) -> list[str]:
    """Все сцены, куда можно попасть из узла (без учёта условий)."""
    out: list[str] = []
    for c in iter_choices(node):
        for k in ("goto", "success", "fail"):
            if c.get(k):
                out.append(c[k])
    for use in (node.get("item_uses") or {}).values():
        if isinstance(use, dict) and use.get("goto"):
            out.append(use["goto"])
    for r in node.get("router") or []:
        if isinstance(r, dict) and r.get("goto"):
            out.append(r["goto"])
    inp = node.get("input")
    if isinstance(inp, dict):
        for a in inp.get("answers", []) or []:
            if isinstance(a, dict) and a.get("goto"):
                out.append(a["goto"])
        if inp.get("on_fail"):
            out.append(inp["on_fail"])
    for ex in (node.get("examine") or {}).values():
        if isinstance(ex, dict) and ex.get("goto"):
            out.append(ex["goto"])
    return out


def iter_all_conditions(data: dict) -> Iterator[tuple[str, Any]]:
    """(место, условие) для всех условий сценария."""
    for nid, node in list(data.get("nodes", {}).items()) + list(data.get("endings", {}).items()):
        if not isinstance(node, dict):
            continue
        for i, c in enumerate(iter_choices(node)):
            yield (f"{nid}/выбор {i + 1}", c.get("if"))
            chk = c.get("check")
            if isinstance(chk, dict):
                for b in chk.get("bonus", []) or []:
                    if isinstance(b, dict):
                        yield (f"{nid}/выбор {i + 1}/бонус", b.get("if"))
        for iid, use in (node.get("item_uses") or {}).items():
            if isinstance(use, dict):
                yield (f"{nid}/использование {iid}", use.get("if"))
        for r in node.get("router") or []:
            if isinstance(r, dict):
                yield (f"{nid}/маршрутизатор", r.get("if"))
        for key in ("extra",):
            for ex in node.get(key) or []:
                if isinstance(ex, dict):
                    yield (f"{nid}/extra", ex.get("if"))
        for t in _text_variants(node.get("text")):
            yield (f"{nid}/text", t)
        for oid, ex in (node.get("examine") or {}).items():
            if isinstance(ex, dict):
                yield (f"{nid}/осмотр {oid}", ex.get("if"))
        inp = node.get("input")
        if isinstance(inp, dict):
            for a in inp.get("answers", []) or []:
                if isinstance(a, dict):
                    yield (f"{nid}/ответ", a.get("if"))
    for iid, item in data.get("items", {}).items():
        if not isinstance(item, dict):
            continue
        for sec in conditions.as_list(item.get("examine_secret")):
            if isinstance(sec, dict):
                cond = sec.get("if")
                if "if_flag" in sec:
                    cond = [cond, {"flag": sec["if_flag"]}] if cond else {"flag": sec["if_flag"]}
                yield (f"предмет {iid}/секрет", cond)
        use = item.get("use")
        if isinstance(use, dict):
            yield (f"предмет {iid}/use", use.get("if"))
    for t in data.get("timers", []) or []:
        if isinstance(t, dict):
            yield (f"таймер {t.get('id')}", t.get("if"))


def _text_variants(value: Any) -> Iterator[Any]:
    if isinstance(value, list):
        for v in value:
            if isinstance(v, dict):
                yield v.get("if")


def iter_all_effects(data: dict) -> Iterator[tuple[str, Any]]:
    for nid, node in list(data.get("nodes", {}).items()) + list(data.get("endings", {}).items()):
        if not isinstance(node, dict):
            continue
        yield (f"{nid}/on_enter", node.get("on_enter"))
        yield (f"{nid}/on_first_enter", node.get("on_first_enter"))
        for i, c in enumerate(iter_choices(node)):
            for k in ("effects", "success_effects", "fail_effects"):
                yield (f"{nid}/выбор {i + 1}/{k}", c.get(k))
        for iid, use in (node.get("item_uses") or {}).items():
            if isinstance(use, dict):
                yield (f"{nid}/использование {iid}", use.get("effects"))
        for r in node.get("router") or []:
            if isinstance(r, dict):
                yield (f"{nid}/маршрутизатор", r.get("effects"))
        for oid, ex in (node.get("examine") or {}).items():
            if isinstance(ex, dict):
                yield (f"{nid}/осмотр {oid}", ex.get("effects"))
        inp = node.get("input")
        if isinstance(inp, dict):
            for a in inp.get("answers", []) or []:
                if isinstance(a, dict):
                    yield (f"{nid}/ответ", a.get("effects"))
            yield (f"{nid}/провал ввода", inp.get("fail_effects"))
    for iid, item in data.get("items", {}).items():
        if not isinstance(item, dict):
            continue
        for sec in conditions.as_list(item.get("examine_secret")):
            if isinstance(sec, dict):
                yield (f"предмет {iid}/секрет", sec.get("effects"))
        yield (f"предмет {iid}/examine_effects", item.get("examine_effects"))
        use = item.get("use")
        if isinstance(use, dict):
            yield (f"предмет {iid}/use", use.get("effects"))
    for t in data.get("timers", []) or []:
        if isinstance(t, dict):
            yield (f"таймер {t.get('id')}", t.get("effects"))


# ---------------------------------------------------------------- валидация

def structural_problems(data: Any) -> list[str]:
    """Ошибки, при которых сценарий нельзя запускать."""
    p: list[str] = []
    if not isinstance(data, dict):
        return ["корень сценария должен быть объектом"]
    for key in ("id", "title", "start", "nodes", "endings"):
        if key not in data:
            p.append(f"нет обязательного поля «{key}»")
    if p:
        return p
    nodes, endings = data["nodes"], data["endings"]
    if not isinstance(nodes, dict) or not nodes:
        return ["«nodes» должен быть непустым объектом"]
    if not isinstance(endings, dict) or not endings:
        return ["«endings» должен быть непустым объектом"]
    for dup in set(nodes) & set(endings):
        p.append(f"id «{dup}» одновременно сцена и концовка")
    all_ids = set(nodes) | set(endings)
    if data["start"] not in nodes:
        p.append(f"стартовая сцена «{data['start']}» не найдена")

    items = data.get("items", {})
    if not isinstance(items, dict):
        p.append("«items» должен быть объектом")
        items = {}
    for iid, item in items.items():
        if not isinstance(item, dict) or "name" not in item:
            p.append(f"предмет «{iid}»: нужно поле name")
    stats = data.get("stats", {})
    if not isinstance(stats, dict):
        p.append("«stats» должен быть объектом")
        stats = {}
    for sname, sval in stats.items():
        if isinstance(sval, dict):
            for k in sval:
                if k not in STAT_KEYS:
                    p.append(f"стат «{sname}»: неизвестное поле «{k}»")
            ze = sval.get("zero_ending")
            if ze and ze not in endings:
                p.append(f"стат «{sname}»: zero_ending «{ze}» не найдена")
        elif not isinstance(sval, (int, float)):
            p.append(f"стат «{sname}»: должно быть число или объект")
    for si in data.get("start_items", []):
        if si not in items:
            p.append(f"стартовый предмет «{si}» не описан")

    timer_ids = set()
    for t in data.get("timers", []) or []:
        if not isinstance(t, dict) or "id" not in t or "every" not in t:
            p.append("таймер: нужны поля id и every")
            continue
        timer_ids.add(t["id"])
        if int(t["every"]) < 1:
            p.append(f"таймер «{t['id']}»: every должно быть >= 1")
        if t.get("goto") and t["goto"] not in all_ids:
            p.append(f"таймер «{t['id']}»: goto «{t['goto']}» не найден")
        for k in t:
            if k not in TIMER_KEYS:
                p.append(f"таймер «{t['id']}»: неизвестное поле «{k}»")

    for nid, node in nodes.items():
        if not isinstance(node, dict):
            p.append(f"сцена «{nid}» должна быть объектом")
            continue
        for k in node:
            if k not in NODE_KEYS:
                p.append(f"сцена «{nid}»: неизвестное поле «{k}»")
        if "router" not in node and "text" not in node:
            p.append(f"сцена «{nid}»: нет текста")
        for i, c in enumerate(node.get("choices", []) or []):
            where = f"сцена «{nid}», выбор {i + 1}"
            if not isinstance(c, dict):
                p.append(f"{where}: должен быть объектом")
                continue
            if "text" not in c:
                p.append(f"{where}: нет текста")
            for k in c:
                if k not in CHOICE_KEYS:
                    p.append(f"{where}: неизвестное поле «{k}»")
            if "check" in c:
                chk = c["check"]
                if not isinstance(chk, dict) or "stat" not in chk or "dc" not in chk:
                    p.append(f"{where}: check требует stat и dc")
                elif chk["stat"] not in stats:
                    p.append(f"{where}: стат «{chk['stat']}» не определён")
                if "success" not in c or "fail" not in c:
                    p.append(f"{where}: у проверки нужны success и fail")
        for iid in (node.get("item_uses") or {}):
            if iid not in items:
                p.append(f"сцена «{nid}»: item_uses на неизвестный предмет «{iid}»")
        router = node.get("router")
        if router is not None:
            if not isinstance(router, list) or not router:
                p.append(f"сцена «{nid}»: router должен быть непустым списком")
            elif not isinstance(router[-1], dict) or "if" in router[-1]:
                p.append(f"сцена «{nid}»: последний пункт router должен быть без условия")
        inp = node.get("input")
        if inp is not None:
            if not isinstance(inp, dict) or not inp.get("answers"):
                p.append(f"сцена «{nid}»: input требует answers")
        for tgt in node_targets(node):
            if tgt not in all_ids:
                p.append(f"сцена «{nid}»: переход на несуществующую сцену «{tgt}»")

    for eid, end in endings.items():
        if not isinstance(end, dict):
            p.append(f"концовка «{eid}» должна быть объектом")
            continue
        for k in ("title", "type", "text"):
            if k not in end:
                p.append(f"концовка «{eid}»: нет поля {k}")
        if end.get("type") not in ENDING_TYPES:
            p.append(f"концовка «{eid}»: неизвестный тип «{end.get('type')}»")
        for k in end:
            if k not in ENDING_KEYS:
                p.append(f"концовка «{eid}»: неизвестное поле «{k}»")

    # ссылки в условиях и эффектах
    for where, cond in iter_all_conditions(data):
        for kind, name in conditions.iter_refs(cond):
            if kind == "unknown":
                p.append(f"{where}: неизвестное условие «{name}»")
            elif kind == "item" and name not in items:
                p.append(f"{where}: неизвестный предмет «{name}»")
            elif kind == "stat" and name not in stats:
                p.append(f"{where}: неизвестный стат «{name}»")
            elif kind == "node" and name not in all_ids:
                p.append(f"{where}: неизвестная сцена «{name}»")
            elif kind == "timer" and name not in timer_ids:
                p.append(f"{where}: неизвестный таймер «{name}»")
    for where, effs in iter_all_effects(data):
        for kind, name in effects.iter_refs(effs):
            if kind == "unknown":
                p.append(f"{where}: неизвестный эффект «{name}»")
            elif kind in ("give", "take") and name not in items:
                p.append(f"{where}: неизвестный предмет «{name}»")
            elif kind == "stat" and name not in stats:
                p.append(f"{where}: неизвестный стат «{name}»")
            elif kind == "cond":
                ck, cn = name
                if ck == "item" and cn not in items:
                    p.append(f"{where}: неизвестный предмет «{cn}»")
                elif ck == "stat" and cn not in stats:
                    p.append(f"{where}: неизвестный стат «{cn}»")
                elif ck == "unknown":
                    p.append(f"{where}: неизвестное условие «{cn}»")
    return p


def load_file(path: str) -> Scenario:
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        raise ScenarioError(name, [f"не удалось прочитать файл: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioError(name, [f"ошибка JSON: строка {exc.lineno}, столбец {exc.colno}: {exc.msg}"]) from exc
    problems = structural_problems(data)
    if problems:
        raise ScenarioError(name, problems)
    return Scenario(data, path)


def scenario_files(directory: str | None = None, include_hidden: bool = False) -> list[str]:
    directory = directory or paths.scenarios_dir()
    if not os.path.isdir(directory):
        return []
    files = sorted(f for f in os.listdir(directory) if f.endswith(".json"))
    if not include_hidden:
        files = [f for f in files if not f.startswith("_")]
    return [os.path.join(directory, f) for f in files]


def load_all(directory: str | None = None, include_hidden: bool = False
             ) -> tuple[list[Scenario], list[ScenarioError]]:
    good, bad = [], []
    for path in scenario_files(directory, include_hidden):
        try:
            good.append(load_file(path))
        except ScenarioError as exc:
            bad.append(exc)
    good.sort(key=lambda s: (s.data.get("order", 999), s.id))
    return good, bad
