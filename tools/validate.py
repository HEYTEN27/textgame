"""Валидатор графа сюжета и требований к контенту.

Запуск:  python tools/validate.py [файл.json ...]
Без аргументов проверяет все сценарии в data/scenarios (включая _test).
Код выхода 1, если есть ошибки.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import conditions, effects  # noqa: E402
from engine.loader import (  # noqa: E402
    iter_all_conditions, iter_all_effects, iter_choices, node_targets,
    normalize_stats, scenario_files, structural_problems,
)
from engine.textutil import word_count  # noqa: E402

QUOTA = {
    "nodes": 35, "endings": 4, "items": 5, "forks": 3, "checks": 2,
    "characters": 1, "secrets": 1, "stats_min": 2, "stats_max": 4,
}
FORBIDDEN = re.compile(r"\b(TODO|FIXME|TBD|lorem ipsum|заглушка|placeholder)\b", re.I)
MIN_WORDS, MAX_WORDS = 60, 260


class Report:
    def __init__(self, name: str):
        self.name = name
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def build_graph(data: dict) -> dict[str, set[str]]:
    nodes, endings = data["nodes"], data["endings"]
    graph: dict[str, set[str]] = {n: set(node_targets(nodes[n])) for n in nodes}
    for e in endings:
        graph[e] = set()
    timer_gotos = [t["goto"] for t in data.get("timers", []) if t.get("goto")]
    zero = [s["zero_ending"] for s in normalize_stats(data.get("stats", {})).values()
            if s.get("zero_ending")]
    for n in nodes:
        graph[n].update(timer_gotos)
        graph[n].update(zero)
    for t in data.get("items", {}).values():
        use = t.get("use") if isinstance(t, dict) else None
        if isinstance(use, dict) and use.get("goto"):
            for n in nodes:
                graph[n].add(use["goto"])
    return graph


def reachable(graph: dict[str, set[str]], start: str, banned: str | None = None) -> set[str]:
    seen = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        for nxt in graph.get(cur, ()):
            if nxt == banned or nxt in seen:
                continue
            seen.add(nxt)
            q.append(nxt)
    return seen


def has_unconditional_exit(node: dict) -> bool:
    for c in iter_choices(node):
        if c.get("if") or c.get("once"):
            continue
        if c.get("goto") or c.get("check"):
            return True
    if node.get("router"):
        return True
    inp = node.get("input")
    if isinstance(inp, dict) and inp.get("on_fail") and inp.get("attempts"):
        return True
    return False


def longest_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts = [v.get("text", "") if isinstance(v, dict) else str(v) for v in value]
        return max(texts, key=len) if texts else ""
    return ""


def all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for v in obj:
            yield from all_strings(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from all_strings(v)


def validate_data(data: dict, name: str, quotas: bool = True) -> Report:
    rep = Report(name)
    for p in structural_problems(data):
        rep.err(p)
    if rep.errors:
        return rep

    nodes, endings, items = data["nodes"], data["endings"], data.get("items", {})
    stats = normalize_stats(data.get("stats", {}))
    graph = build_graph(data)

    # --- достижимость
    reach = reachable(graph, data["start"])
    for n in nodes:
        if n not in reach:
            rep.err(f"сцена «{n}» недостижима")
    for e in endings:
        if e not in reach:
            rep.err(f"концовка «{e}» недостижима")
    # --- тупики
    for nid, node in nodes.items():
        if not has_unconditional_exit(node):
            rep.err(f"сцена «{nid}» - тупик: нет ни одного безусловного выхода")
    # --- из каждой сцены должна быть достижима хотя бы одна концовка
    for nid in nodes:
        if nid in reach and not (reachable(graph, nid) & set(endings)):
            rep.err(f"из сцены «{nid}» не достижима ни одна концовка (ловушка)")

    # --- предметы
    used: set[str] = set()
    obtainable = set(data.get("start_items", []))
    for where, cond in iter_all_conditions(data):
        for kind, nm in conditions.iter_refs(cond):
            if kind == "item":
                used.add(nm)
    for where, effs in iter_all_effects(data):
        for kind, nm in effects.iter_refs(effs):
            if kind == "take":
                used.add(nm)
            elif kind == "give":
                obtainable.add(nm)
            elif kind == "cond" and nm[0] == "item":
                used.add(nm[1])
    for node in nodes.values():
        used.update((node.get("item_uses") or {}).keys())
    for iid, item in items.items():
        if isinstance(item.get("use"), dict):
            used.add(iid)
        for sec in conditions.as_list(item.get("examine_secret")):
            if isinstance(sec, dict) and sec.get("effects"):
                used.add(iid)
        if iid not in used:
            (rep.err if quotas else rep.warn)(f"у предмета «{iid}» нет применения")
        if iid not in obtainable:
            rep.warn(f"предмет «{iid}» нельзя получить")
        if not item.get("description"):
            rep.err(f"у предмета «{iid}» нет описания")

    # --- флаги
    set_flags, checked_flags = set(), set()
    for where, effs in iter_all_effects(data):
        for kind, nm in effects.iter_refs(effs):
            if kind == "set_flag":
                set_flags.add(nm)
            elif kind == "cond" and nm[0] == "flag":
                checked_flags.add(nm[1])
    for where, cond in iter_all_conditions(data):
        for kind, nm in conditions.iter_refs(cond):
            if kind == "flag":
                checked_flags.add(nm)
    for f in sorted(checked_flags - set_flags):
        rep.err(f"флаг «{f}» проверяется, но нигде не ставится")
    for f in sorted(set_flags - checked_flags):
        rep.warn(f"флаг «{f}» ставится, но нигде не проверяется")

    # --- тексты
    words = []
    for nid, node in list(nodes.items()) + list(endings.items()):
        if "router" in node:
            continue
        wc = word_count(longest_text(node.get("text")))
        words.append(wc)
        if quotas and wc < MIN_WORDS:
            rep.warn(f"«{nid}»: короткий текст ({wc} слов)")
        if wc > MAX_WORDS:
            rep.warn(f"«{nid}»: длинный текст ({wc} слов)")
    for s in all_strings(data):
        if FORBIDDEN.search(s):
            rep.err(f"заглушка в тексте: «{s[:60]}...»")
        if not s.strip() and s != "":
            rep.err("пустая строка из пробелов в тексте")
        if s.count("[") != s.count("]") or s.count("<") != s.count(">"):
            rep.warn(f"несбалансированная разметка: «{s[:50]}...»")

    # --- проверки, развилки, секреты
    checks = sum(1 for n in nodes.values() for c in iter_choices(n) if c.get("check"))
    forks = []
    end_set = set(endings)
    for nid, node in nodes.items():
        targets = sorted({t for c in iter_choices(node) for k in ("goto", "success", "fail")
                          if (t := c.get(k)) and t != nid})
        if len(targets) < 2:
            continue
        sets = {frozenset(reachable(graph, t, banned=nid) & end_set) for t in targets}
        if len(sets) >= 2:
            forks.append(nid)
    secrets = [n for n, d in list(nodes.items()) + list(endings.items()) if d.get("secret")]
    types = {e.get("type") for e in endings.values()}
    real_stats = [s for s, d in stats.items() if d.get("kind") == "stat"]

    rep.info.append(
        f"сцен: {len(nodes)}, концовок: {len(endings)}, предметов: {len(items)}, "
        f"проверок: {checks}, развилок: {len(forks)}, секретов: {len(secrets)}, "
        f"персонажей: {len(data.get('characters', {}))}, статов: {len(real_stats)}")
    if words:
        rep.info.append(f"слов в сценах: всего {sum(words)}, в среднем {sum(words) // len(words)}")

    if quotas:
        q = QUOTA
        if len(nodes) < q["nodes"]:
            rep.err(f"сцен {len(nodes)} < {q['nodes']}")
        if len(endings) < q["endings"]:
            rep.err(f"концовок {len(endings)} < {q['endings']}")
        if len(items) < q["items"]:
            rep.err(f"предметов {len(items)} < {q['items']}")
        if checks < q["checks"]:
            rep.err(f"проверок навыков {checks} < {q['checks']}")
        if len(forks) < q["forks"]:
            rep.err(f"значимых развилок {len(forks)} < {q['forks']}")
        if len(data.get("characters", {})) < q["characters"]:
            rep.err("нет ни одного персонажа в разделе characters")
        if len(secrets) < q["secrets"]:
            rep.err("нет ни одной секретной сцены/концовки (secret: true)")
        if not q["stats_min"] <= len(real_stats) <= q["stats_max"]:
            rep.err(f"статов {len(real_stats)}, нужно {q['stats_min']}-{q['stats_max']}")
        for s in real_stats:
            if not stats[s].get("zero_ending"):
                rep.err(f"у стата «{s}» нет zero_ending (особой концовки при нуле)")
        need = [("good",), ("bad",), ("strange", "secret"), ("death", "defeat")]
        for group in need:
            if not types & set(group):
                rep.err(f"нет концовки типа {' / '.join(group)}")
    return rep


def validate_file(path: str) -> Report:
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        rep = Report(name)
        rep.err(f"не удалось прочитать JSON: {exc}")
        return rep
    return validate_data(data, name, quotas=not name.startswith("_"))


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    files = [a for a in argv if not a.startswith("-")] or scenario_files(include_hidden=True)
    total_err = 0
    for path in files:
        rep = validate_file(path)
        status = "OK" if not rep.errors else "ОШИБКИ"
        print(f"\n=== {rep.name}: {status} ===")
        for line in rep.info:
            print(f"  {line}")
        for e in rep.errors:
            print(f"  [ОШИБКА] {e}")
        warn_list = rep.warnings if verbose else rep.warnings[:8]
        for w in warn_list:
            print(f"  [предупр.] {w}")
        if len(rep.warnings) > len(warn_list):
            print(f"  ... ещё предупреждений: {len(rep.warnings) - len(warn_list)} (ключ -v)")
        total_err += len(rep.errors)
    print(f"\nИтого: файлов {len(files)}, ошибок {total_err}.")
    return 1 if total_err else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main(sys.argv[1:]))
