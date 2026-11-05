"""Бот-тестировщик: проходит сценарии случайными действиями.

Запуск:  python tools/autoplay.py [-n 1000] [--seed 1] [id_сценария ...]
Проверяет, что игра не падает, не зацикливается и что все концовки
достигаются хотя бы раз. Код выхода 1 при проблемах.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
import traceback
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Session  # noqa: E402
from engine.loader import ScenarioError, load_file, scenario_files  # noqa: E402
from engine.state import GameState  # noqa: E402
from engine.textutil import word_count  # noqa: E402

MAX_STEPS = 1500
GARBAGE = ["", "   ", "asdf", "123456", "-1", "0", "999", "и", "статус", "журнал",
           "осмотреть", "использовать", "использовать ничто", "осмотреть небо",
           "!!!???", "ё" * 300, "x" * 5000, "1.5", "помощь", "осмотреться",
           "использовать 99", "осмотреть 1", "\x00\x01", "SELECT * FROM", "привет"]


class RunResult:
    def __init__(self):
        self.ending: str | None = None
        self.steps = 0
        self.words = 0
        self.decisions = 0
        self.error: str | None = None
        self.stuck = False
        self.timeout = False


def _maybe(rng: random.Random, p: float) -> bool:
    return rng.random() < p


def play_once(scen, seed: int, global_counts: Counter) -> RunResult:
    rng = random.Random(seed)
    res = RunResult()
    sess = Session(scen, rng=random.Random(seed * 7 + 1))
    local = Counter()
    try:
        for step in range(MAX_STEPS):
            if sess.need_describe:
                for m in sess.describe():
                    res.words += word_count(m.text)
                sess.pop_enter_msgs()
            if sess.ended:
                res.ending = sess.ended
                res.steps = step
                return res
            node = sess.node
            choices = sess.available_choices()
            if not choices and not sess.is_input_node():
                res.stuck = True
                res.error = f"нет доступных вариантов в сцене «{sess.node_id}»"
                return res

            # сохранение/загрузка посреди игры
            if _maybe(rng, 0.003):
                st = GameState.from_dict(sess.state.to_dict())
                sess = Session(scen, st, rng=random.Random(seed + step))
                continue
            # мусорный ввод
            if _maybe(rng, 0.02):
                sess.handle(rng.choice(GARBAGE))
                continue
            # ответ на головоломку
            inp = node.get("input") if sess.is_input_node() else None
            if inp and _maybe(rng, 0.6):
                answers = [a for a in inp.get("answers", [])]
                if answers and _maybe(rng, 0.5):
                    ans = rng.choice(answers)
                    match = ans.get("match")
                    word = rng.choice(match) if isinstance(match, list) else match
                    sess.handle(str(word))
                else:
                    sess.handle(rng.choice(["пароль", "12345", "не знаю", "qwerty"]))
                res.decisions += 1
                continue
            inv = list(sess.state.inventory)
            uses = [i for i in (node.get("item_uses") or {}) if i in inv]
            if uses and _maybe(rng, 0.25):
                sess.use_item(rng.choice(uses))
                res.decisions += 1
                continue
            if inv and _maybe(rng, 0.06):
                sess.use_item(rng.choice(inv))
                res.decisions += 1
                continue
            if _maybe(rng, 0.06):
                objs = list((node.get("examine") or {}).keys())
                pool = [scen.item_name(i) for i in inv] + objs
                if pool:
                    sess.handle("осмотреть " + rng.choice(pool))
                    continue
            if not choices:
                sess.handle(rng.choice(["пароль", "1"]))
                continue
            # выбор варианта с небольшим уклоном в сторону неизведанного
            weights = []
            for c in choices:
                key = (sess.node_id, c["_idx"])
                weights.append(1.0 / (1 + local[key]) / (1 + global_counts[key]) ** 0.5)
            c = rng.choices(choices, weights=weights, k=1)[0]
            key = (sess.node_id, c["_idx"])
            local[key] += 1
            global_counts[key] += 1
            sess.apply_choice(c)
            res.decisions += 1
        res.timeout = True
        res.error = f"превышено {MAX_STEPS} шагов (последняя сцена «{sess.node_id}»)"
    except Exception:  # noqa: BLE001
        res.error = traceback.format_exc()
    return res


def run_scenario(path: str, runs: int, seed: int) -> bool:
    try:
        scen = load_file(path)
    except ScenarioError as exc:
        print(f"\n=== {os.path.basename(path)}: НЕ ЗАГРУЖЕН ===\n  {exc}")
        return False
    t0 = time.time()
    endings = Counter()
    errors: list[tuple[int, str]] = []
    steps, words, decisions = [], [], []
    gcounts: Counter = Counter()
    for i in range(runs):
        r = play_once(scen, seed + i, gcounts)
        if r.error:
            errors.append((seed + i, r.error))
        if r.ending:
            endings[r.ending] += 1
            steps.append(r.steps)
            words.append(r.words)
            decisions.append(r.decisions)
    dt = time.time() - t0
    missing = [e for e in scen.endings if endings[e] == 0]
    ok = not errors and not missing
    print(f"\n=== {scen.id} «{scen.title}»: {'OK' if ok else 'ПРОБЛЕМЫ'} ({runs} прогонов, {dt:.1f} с) ===")
    for eid, end in scen.endings.items():
        n = endings[eid]
        pct = 100.0 * n / runs
        print(f"  {end.get('title', eid):<40} {end.get('type', ''):<8} {n:>5}  ({pct:5.1f}%)")
    if steps:
        avg_words = sum(words) / len(words)
        avg_dec = sum(decisions) / len(decisions)
        minutes = avg_words / 150 + avg_dec * 8 / 60
        print(f"  ходов до концовки: среднее {sum(steps) / len(steps):.1f}, мин {min(steps)}, макс {max(steps)}")
        print(f"  оценка времени прохождения (случайный игрок): ~{minutes:.0f} мин "
              f"({avg_words:.0f} слов, {avg_dec:.0f} решений)")
    if missing:
        print(f"  [ПРОБЛЕМА] не достигнуты концовки: {', '.join(missing)}")
    if errors:
        print(f"  [ПРОБЛЕМА] ошибок: {len(errors)}. Первая (seed {errors[0][0]}):")
        print("    " + errors[0][1].strip().replace("\n", "\n    "))
    return ok


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Бот-тестировщик сценариев")
    ap.add_argument("-n", "--runs", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args(argv)
    files = scenario_files(include_hidden=True)
    if args.ids:
        files = [f for f in files if os.path.basename(f)[:-5] in args.ids]
    all_ok = True
    for f in files:
        all_ok &= run_scenario(f, args.runs, args.seed)
    print("\nИтог:", "все сценарии в порядке" if all_ok else "есть проблемы")
    return 0 if all_ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main(sys.argv[1:]))
