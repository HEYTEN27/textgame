"""Unit-тесты движка. Запуск: python -m unittest discover -s tests"""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import conditions, dice, effects, parser  # noqa: E402
from engine.display import Display, strip_markup, wrap  # noqa: E402
from engine.game import Session  # noqa: E402
from engine.loader import Scenario, ScenarioError, load_all, load_file, structural_problems  # noqa: E402
from engine.save import SaveError, Store  # noqa: E402
from engine.state import GameState  # noqa: E402

TEST_SCENARIO = os.path.join(ROOT, "data", "scenarios", "_test.json")


def load_test() -> Scenario:
    return load_file(TEST_SCENARIO)


def new_session(seed: int = 1) -> Session:
    return Session(load_test(), rng=random.Random(seed))


class TestConditions(unittest.TestCase):
    def setUp(self):
        self.st = GameState("x", "a", inventory=["key"], flags={"f1"}, stats={"hp": 5})

    def test_basic(self):
        c = conditions.check
        self.assertTrue(c(None, self.st))
        self.assertTrue(c({"has_item": "key"}, self.st))
        self.assertFalse(c({"has_item": "coin"}, self.st))
        self.assertTrue(c({"not_item": "coin"}, self.st))
        self.assertTrue(c({"flag": "f1"}, self.st))
        self.assertFalse(c({"not_flag": "f1"}, self.st))
        self.assertTrue(c({"stat_gte": {"hp": 5}}, self.st))
        self.assertFalse(c({"stat_gte": {"hp": 6}}, self.st))
        self.assertTrue(c({"stat_lte": {"hp": 5}}, self.st))
        self.assertTrue(c([{"flag": "f1"}, {"has_item": "key"}], self.st))
        self.assertFalse(c([{"flag": "f1"}, {"has_item": "coin"}], self.st))

    def test_logic(self):
        c = conditions.check
        self.assertTrue(c({"any": [{"flag": "zzz"}, {"flag": "f1"}]}, self.st))
        self.assertFalse(c({"not": {"flag": "f1"}}, self.st))
        self.assertTrue(c({"any_flag": ["q", "f1"]}, self.st))

    def test_unknown_raises(self):
        with self.assertRaises(conditions.ConditionError):
            conditions.check({"magic": 1}, self.st)


class TestEffectsAndDice(unittest.TestCase):
    def test_effects(self):
        sc = load_test()
        st = GameState("_test", "hall", stats=sc.initial_stats())
        out = []
        effects.apply([{"give_item": "coin"}, {"set_flag": "a"}, {"stat": {"здоровье": -2}},
                       {"journal": "запись"}], st, sc, out)
        self.assertIn("coin", st.inventory)
        self.assertIn("a", st.flags)
        self.assertEqual(st.stats["здоровье"], 3)
        self.assertEqual(st.journal, ["запись"])
        effects.apply({"stat": {"здоровье": 100}}, st, sc, out)
        self.assertEqual(st.stats["здоровье"], 5, "стат ограничен максимумом")
        effects.apply({"stat": {"здоровье": -100}}, st, sc, out)
        self.assertEqual(st.stats["здоровье"], 0, "стат не уходит ниже минимума")
        effects.apply({"if": {"flag": "a"}, "then": [{"clear_flag": "a"}], "else": [{"set_flag": "b"}]},
                      st, sc, out)
        self.assertNotIn("a", st.flags)
        self.assertNotIn("b", st.flags)

    def test_hidden_stat_silent(self):
        sc = load_test()
        st = GameState("_test", "hall", stats=sc.initial_stats())
        out = []
        effects.apply({"stat": {"удача": 1}}, st, sc, out)
        self.assertEqual(out, [])

    def test_dice(self):
        self.assertEqual(dice.modifier(5), 0)
        self.assertEqual(dice.modifier(10), 2)
        self.assertEqual(dice.modifier(1), -2)
        self.assertAlmostEqual(dice.success_chance(11, 0), 0.5)
        self.assertEqual(dice.success_chance(40, 0), 0.05)
        self.assertEqual(dice.success_chance(-5, 0), 0.95)
        self.assertEqual(dice.odds_words(0.1), "шансы невелики")
        self.assertEqual(dice.odds_words(0.9), "почти наверняка")
        rng = random.Random(3)
        for _ in range(200):
            r = dice.roll(10, 1, rng)
            self.assertTrue(1 <= r.die <= 20)
            if r.die == 20:
                self.assertTrue(r.success)
            if r.die == 1:
                self.assertFalse(r.success)


class TestParser(unittest.TestCase):
    ITEMS = {
        "brass_key": {"name": "латунный ключ", "aliases": ["ключ", "латунный"]},
        "lighter": {"name": "зажигалка", "aliases": ["огниво"]},
        "keycard": {"name": "ключ-карта", "aliases": ["карта", "пропуск"]},
    }

    def test_cases_and_typos(self):
        m = lambda q: parser.match_items(q, self.ITEMS)  # noqa: E731
        self.assertEqual(m("зажигалку"), ["lighter"])
        self.assertEqual(m("зажигалкой"), ["lighter"])
        self.assertEqual(m("зажыгалку"), ["lighter"])
        self.assertEqual(m("латунного ключа"), ["brass_key"])
        self.assertEqual(m("латунный"), ["brass_key"])
        self.assertEqual(m("пропуск"), ["keycard"])
        self.assertEqual(m("банан"), [])

    def test_commands(self):
        self.assertEqual(parser.parse("3").kind, "choice")
        self.assertEqual(parser.parse("  3 ").number, 3)
        self.assertEqual(parser.parse("и").kind, "inventory")
        self.assertEqual(parser.parse("Инвентарь").kind, "inventory")
        self.assertEqual(parser.parse("инвентрь").kind, "inventory")
        self.assertEqual(parser.parse("осмотреть ключ").kind, "examine")
        self.assertEqual(parser.parse("использовать ключ").arg, "ключ")
        self.assertEqual(parser.parse("исп зажигалку").kind, "use")
        self.assertEqual(parser.parse("").kind, "empty")
        self.assertEqual(parser.parse("ыыы").kind, "unknown")
        self.assertEqual(parser.parse("аврора", answer_mode=True).kind, "answer")
        self.assertEqual(parser.parse("x" * 10000).kind, "unknown")


class TestSession(unittest.TestCase):
    def test_start(self):
        s = new_session()
        self.assertEqual(s.node_id, "hall")
        self.assertIn("key", s.state.inventory)
        text = s.describe()[0].text
        self.assertIn("пустом зале", text)
        self.assertEqual(s.state.journal, ["Ты очнулся в зале."])

    def test_once_choice_and_extra(self):
        s = new_session()
        n_before = len(s.available_choices())
        s.choose(1)  # поднять монету
        self.assertIn("coin", s.state.inventory)
        self.assertEqual(len(s.available_choices()), n_before - 1)
        texts = [m.text for m in s.describe()]
        self.assertTrue(any("Монета" in t for t in texts))

    def test_conditional_choice_hidden(self):
        s = new_session()
        s.state.take("key")
        labels = [c["text"] for c in s.available_choices()]
        self.assertNotIn("Открыть сундук ключом", labels)

    def test_examine_secret_and_secret_ending(self):
        s = new_session()
        s.handle("осмотреть записку")
        self.assertNotIn("знает_пароль", s.state.flags)
        s.handle("осмотреть надпись")
        self.assertIn("видел_знак", s.state.flags)
        s.handle("осмотреть бумажку")
        self.assertIn("знает_пароль", s.state.flags)
        s.handle("использовать фонарь")
        self.assertEqual(s.node_id, "dark_corner")
        self.assertEqual(s.state.stats["заряд"], 2)
        s.handle("1")
        self.assertEqual(s.ended, "end_secret")
        info = s.ending_info()
        self.assertEqual((info["number"], info["total"]), (3, 4))

    def test_neutral_use(self):
        s = new_session()
        resp = s.handle("использовать ключ")
        self.assertTrue(any("бесполезно" in m.text or "поможет" in m.text for m in resp.msgs))
        resp = s.handle("использовать банан")
        self.assertTrue(resp.msgs)

    def test_zero_stat_ending(self):
        s = new_session()
        s.choose(4)  # люк
        self.assertEqual(s.node_id, "trap")
        s.choose(1)  # прыгнуть: здоровье -10
        self.assertEqual(s.ended, "end_dead")

    def test_check_both_outcomes(self):
        outcomes = set()
        for seed in range(40):
            s = new_session(seed)
            s.choose(2)
            s.choose(1)
            outcomes.add(s.node_id)
        self.assertEqual(outcomes, {"bridge", "pit"})

    def test_input_node(self):
        s = new_session()
        s.state.stats["здоровье"] = 5
        s._enter("lock_room", [])
        self.assertTrue(s.is_input_node())
        s.handle("неправильно")
        self.assertEqual(s.state.input_attempts["lock_room"], 1)
        s.handle("Аврора")
        self.assertEqual(s.ended, "end_bad")  # удачи мало -> маршрутизатор ведёт к плохой концовке

    def test_input_fail(self):
        s = new_session()
        s._enter("lock_room", [])
        for _ in range(3):
            s.handle("нет")
        self.assertEqual(s.node_id, "hall")

    def test_router_good(self):
        s = new_session()
        s.state.stats["удача"] = 9
        s._enter("lock_room", [])
        s.handle("aurora")
        self.assertEqual(s.ended, "end_good")

    def test_timer_goto(self):
        s = new_session()
        for _ in range(12):
            if s.ended:
                break
            s.handle("2") if s.node_id == "hall" else s.handle("2")
        self.assertTrue(s.state.timer("bell")["fires"] >= 3)
        self.assertTrue(s.state.visited.get("bell_hall", 0) >= 1)

    def test_garbage_input_never_crashes(self):
        rng = random.Random(5)
        s = new_session()
        junk = ["", " ", "999", "-5", "о", "осмотреть", "использовать", "\x00", "ё" * 1000,
                "использовать 99", "сохранить", "выход", "!!!"]
        for _ in range(300):
            if s.ended:
                s = new_session(rng.randint(0, 99))
            s.handle(rng.choice(junk + [str(rng.randint(1, 4))]))

    def test_commands_output(self):
        s = new_session()
        self.assertEqual(s.handle("статус").msgs[0].kind, "title")
        self.assertTrue(s.handle("журнал").msgs)
        self.assertTrue(s.handle("и").msgs)
        self.assertEqual(s.handle("помощь").action, "help")
        self.assertEqual(s.handle("сохранить").action, "save")
        self.assertEqual(s.handle("выход").action, "exit")
        # скрытый стат не показывается
        self.assertFalse(any("удача" in m.text for m in s.status_msgs()))


class TestSaves(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip(self):
        s = new_session()
        s.choose(1)
        s.handle("осмотреть стену")
        self.store.save(2, s)
        loaded = self.store.load_session(2, {"_test": load_test()})
        self.assertEqual(loaded.state.to_dict(), s.state.to_dict())
        self.assertIn("Тестовая комната", self.store.slot_summary(2))
        self.assertEqual(self.store.slot_summary(1), "пусто")

    def test_corrupted(self):
        with open(self.store.slot_path(1), "w", encoding="utf-8") as fh:
            fh.write("{ это не json")
        with self.assertRaises(SaveError):
            self.store.load_session(1, {"_test": load_test()})
        self.assertIn("повреждён", self.store.slot_summary(1))
        with open(self.store.slot_path(3), "w", encoding="utf-8") as fh:
            json.dump({"scenario_id": "_test", "state": {"node": 5}}, fh)
        with self.assertRaises(SaveError):
            self.store.load_session(3, {"_test": load_test()})
        with open(self.store.slot_path(2), "wb") as fh:
            fh.write(b"\xff\xfe\x00garbage")
        with self.assertRaises(SaveError):
            self.store.load_session(2, {"_test": load_test()})

    def test_save_with_missing_node(self):
        s = new_session()
        self.store.save(1, s)
        with open(self.store.slot_path(1), encoding="utf-8") as fh:
            data = json.load(fh)
        data["state"]["node"] = "нет_такой_сцены"
        with open(self.store.slot_path(1), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with self.assertRaises(SaveError):
            self.store.load_session(1, {"_test": load_test()})

    def test_achievements_and_settings(self):
        self.assertTrue(self.store.add_ending("_test", "end_good"))
        self.assertFalse(self.store.add_ending("_test", "end_good"))
        self.assertEqual(self.store.load_achievements(), {"_test": ["end_good"]})
        with open(self.store.achievements_path(), "w", encoding="utf-8") as fh:
            fh.write("[[[")
        self.assertEqual(self.store.load_achievements(), {})
        self.store.save_settings({"speed": "быстро", "colors": False})
        self.assertEqual(self.store.load_settings(), {"speed": "быстро", "colors": False})
        with open(self.store.settings_path(), "w", encoding="utf-8") as fh:
            fh.write("oops")
        self.assertEqual(self.store.load_settings()["speed"], "нормально")


class TestLoaderAndDisplay(unittest.TestCase):
    def test_structural_errors(self):
        self.assertTrue(structural_problems({}))
        bad = {"id": "b", "title": "b", "start": "a",
               "nodes": {"a": {"text": "x", "choices": [{"text": "y", "goto": "nowhere"}]}},
               "endings": {"e": {"title": "t", "type": "good", "text": "z"}}}
        self.assertTrue(any("nowhere" in p for p in structural_problems(bad)))

    def test_bad_json_file(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "broken.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{,}")
            with self.assertRaises(ScenarioError):
                load_file(path)
            good, bad = load_all(d)
            self.assertEqual((len(good), len(bad)), (0, 1))
        finally:
            shutil.rmtree(d)

    def test_all_real_scenarios_load(self):
        good, bad = load_all()
        self.assertEqual(bad, [], [str(b) for b in bad])

    def test_wrap_and_markup(self):
        text = "слово " * 50
        for line in wrap(text).split("\n"):
            self.assertLessEqual(len(line), 80)
        self.assertEqual(strip_markup("взять [ключ] и <бежать>"), "взять ключ и бежать")
        out = io.StringIO()
        d = Display({"colors": False, "speed": "мгновенно"}, out=out)
        d.show([effects.Msg("text", "Это [ключ].")])
        self.assertIn("Это ключ.", out.getvalue())
        out2 = io.StringIO()
        Display({"colors": True, "speed": "мгновенно"}, out=out2).show([effects.Msg("text", "[ключ]")])
        self.assertIn("\033[93m", out2.getvalue())


if __name__ == "__main__":
    unittest.main()
