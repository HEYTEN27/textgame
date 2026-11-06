"""Главное меню, выбор сценария, галерея концовок, настройки, титры."""
from __future__ import annotations

import os

from . import audio
from . import display as dsp
from .effects import Msg
from .game import Session, play
from .loader import ENDING_TYPE_NAMES, load_all
from .save import VOLUME_STEPS, SaveError, Store

GAME_TITLE = "Текстовые приключения"
VERSION = "1.2"

BANNER = r"""
  _____ _____ _  _____ _____    _   ___ _____ ___ _  _ _____ _   _ ___  ___
 |_   _| __\ \/ /_   _|  _  |  /_\ |   \_   _| __| \| |_   _| | | | _ \| __|
   | | | _| >  <  | | | |_| | / _ \| |) || | | _|| .` | | | | |_| |   /| _|
   |_| |___/_/\_\ |_| |_____|/_/ \_\___/ |_| |___|_|\_| |_|  \___/|_|_\|___|
"""

SPEED_ORDER = dsp.SPEED_ORDER


def _ask_number(display, prompt: str, maximum: int) -> int | None:
    ans = display.ask(prompt)
    if ans is None:
        return None
    ans = ans.strip()
    if ans.isdigit() and 0 <= int(ans) <= maximum:
        return int(ans)
    display.show([Msg("system", f"Введи число от 0 до {maximum}.")])
    return -1


def _hours(seconds: int) -> str:
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h} ч {m} мин" if h else f"{m} мин"


class App:
    def __init__(self):
        self.store = Store()
        self.store.ensure_dir()
        self.settings = self.store.load_settings()
        self.display = dsp.Display(self.settings)
        self.music = audio.Music(self.settings)
        scenarios, broken = load_all(include_hidden=os.environ.get("TA_DEBUG") == "1")
        self.scenarios = scenarios
        self.by_id = {s.id: s for s in scenarios}
        self.broken = broken

    # ------------------------------------------------------------ главное меню
    def run(self) -> None:
        d = self.display
        d.set_title(GAME_TITLE)
        self.music.play("menu")
        d.clear()
        d.plain(d._colorize(BANNER, "title", False))
        d.plain(d._colorize(f"{GAME_TITLE}. Десять историй, которые ждут тебя в темноте.", "system", False))
        for err in self.broken:
            d.show([Msg("danger", f"Сценарий {err.scenario} не загружен: {err.problems[0]}")])
        try:
            while True:
                d.set_title(GAME_TITLE)
                self.music.play("menu")
                auto = self.store.autosave_summary()
                d.header("Главное меню")
                items = []
                if auto:
                    items.append(("Продолжить: " + auto, self.continue_auto))
                items += [
                    ("Новая игра", self.new_game),
                    ("Загрузить сохранение", self.load_game),
                    ("Концовки и статистика", self.gallery),
                    ("Настройки", self.settings_menu),
                    ("Об игре и авторы", self.credits),
                    ("Выход", None),
                ]
                for n, (label, _) in enumerate(items, 1):
                    d.plain(f"  {n}. {label}")
                choice = _ask_number(d, "> ", len(items))
                if choice is None:
                    break
                if not 1 <= choice <= len(items):
                    continue
                action = items[choice - 1][1]
                if action is None:
                    if d.confirm("Точно выйти из игры? (д/н): "):
                        break
                    continue
                action()
                d.clear()
        finally:
            self.music.stop()
        d.plain("До встречи.")

    def _play(self, session: Session) -> None:
        play(session, self.display, self.store, self.by_id, self.music)

    def continue_auto(self) -> None:
        try:
            sess = self.store.load_session("auto", self.by_id)
        except SaveError as exc:
            self.display.show([Msg("danger", f"Автосохранение не загрузилось: {exc}")])
            self.store.clear_autosave()
            return
        sess.need_describe = True
        self.display.clear()
        self.display.show([Msg("good", "Продолжаем с автосохранения.")])
        self._play(sess)

    def load_game(self) -> None:
        sess = self.store.load_dialog(self.display, self.by_id)
        if sess is not None:
            self.display.clear()
            self._play(sess)

    def _found(self, scen_id: str) -> list[str]:
        return self.store.load_achievements().get(scen_id, [])

    def new_game(self) -> None:
        d = self.display
        if not self.scenarios:
            d.show([Msg("danger", "Не найдено ни одного сценария в папке data/scenarios.")])
            return
        d.clear()
        d.header("Выбери историю")
        for n, s in enumerate(self.scenarios, 1):
            found = [e for e in self._found(s.id) if e in s.endings]
            mark = " *" if len(found) == len(s.endings) else ""
            d.plain(d._colorize(f"  {n}. {s.title}", "title", False)
                    + d._colorize(f"  [{s.genre}] концовок: {len(found)}/{len(s.endings)}{mark}", "system", False))
            d.plain(dsp.wrap(s.teaser, None, "     ", "     "))
            d.blank()
        d.plain("  0. Назад")
        n = _ask_number(d, "Номер истории: ", len(self.scenarios))
        if not n or n < 0:
            return
        scen = self.scenarios[n - 1]
        if self.store.autosave_summary() and not d.confirm(
                "Начать новую историю? Автосохранение текущей игры будет заменено (д/н): "):
            return
        self.intro(scen)
        self.store.update_stats(games=1)
        self._play(Session(scen))

    def intro(self, scen) -> None:
        d = self.display
        self.music.play(scen.data.get("music"))
        d.set_title(f"{GAME_TITLE} - {scen.title}")
        d.clear()
        d.header(scen.title)
        d.show([Msg("system", scen.genre)])
        d.show([Msg("text", scen.teaser)])
        credit = audio.credit_line(scen.data.get("music"))
        if credit and self.settings.get("music", True) and self.music.available:
            d.show([Msg("system", f"Музыка: {credit}")])
        d.show([Msg("system", "Вводи номер варианта или команду. «помощь» - список команд, "
                              "«отменить» - отменить последний ход, «подсказка» - что ещё не изведано.")])
        d.pause("Enter - начать")
        d.clear()

    # ------------------------------------------------------------ галерея
    def gallery(self) -> None:
        d = self.display
        d.clear()
        d.header("Концовки и статистика")
        total_found = total = 0
        for s in self.scenarios:
            found = set(self._found(s.id))
            got = [e for e in s.endings if e in found]
            total_found += len(got)
            total += len(s.endings)
            d.plain(d._colorize(f"{s.title}: открыто {len(got)}/{len(s.endings)}", "title", False))
            for n, (eid, end) in enumerate(s.endings.items(), 1):
                tname = ENDING_TYPE_NAMES.get(end.get("type", ""), "")
                if eid in found:
                    d.plain(f"   {n}. {end.get('title', eid)} ({tname})")
                else:
                    d.plain(d._colorize(f"   {n}. ??? ({tname})", "system", False))
        stats = self.store.load_stats()
        pct = round(100 * total_found / total) if total else 0
        d.blank()
        d.plain(f"Всего открыто концовок: {total_found}/{total} ({pct}%)")
        d.plain(f"Начато историй: {stats['games']}, доведено до концовки: {stats['finished']}")
        d.plain(f"Время в игре: {_hours(stats['play_seconds'])}")
        d.pause("Enter - назад")

    # ------------------------------------------------------------ настройки
    def settings_menu(self) -> None:
        d = self.display
        while True:
            st = self.settings
            d.clear()
            d.header("Настройки")
            d.plain(f"  1. Скорость текста: {st['speed']}")
            d.plain(f"  2. Цвета: {'включены' if st['colors'] else 'выключены'}")
            d.plain(f"  3. Музыка: {'включена' if st['music'] else 'выключена'}")
            d.plain(f"  4. Громкость музыки: {st['volume']}%")
            d.plain(f"  5. Очищать экран между сценами: {'да' if st['clear_screen'] else 'нет'}")
            if not self.music.available:
                d.show([Msg("system", "   (звук на этом компьютере недоступен - музыки не будет)")])
            d.plain("  0. Назад")
            n = _ask_number(d, "> ", 5)
            if n == 1:
                i = SPEED_ORDER.index(st["speed"]) if st["speed"] in SPEED_ORDER else 0
                st["speed"] = SPEED_ORDER[(i + 1) % len(SPEED_ORDER)]
            elif n == 2:
                st["colors"] = not st["colors"]
            elif n == 3:
                st["music"] = not st["music"]
                self.music.apply_settings()
            elif n == 4:
                cur = st["volume"]
                nxt = [v for v in VOLUME_STEPS if v > cur]
                st["volume"] = nxt[0] if nxt else VOLUME_STEPS[0]
                self.music.apply_settings()
            elif n == 5:
                st["clear_screen"] = not st["clear_screen"]
            elif n in (0, None):
                self.store.save_settings(st)
                return
            self.store.save_settings(st)

    # ------------------------------------------------------------ титры
    def credits(self) -> None:
        d = self.display
        d.clear()
        d.header(f"Об игре - версия {VERSION}")
        d.show([Msg("text", "Десять текстовых приключений в разных жанрах: мистика, научная фантастика, "
                            "детектив, хоррор, вестерн и другие. Движок, сценарии и тексты написаны для этой игры.")])
        d.plain(d._colorize("Музыка", "title", False))
        credits = audio.load_credits()
        names = {s.data.get("music"): s.title for s in self.scenarios}
        names.update({"menu": "Главное меню", "ending": "Концовки"})
        for slug, info in credits.get("tracks", {}).items():
            d.plain(f"  {names.get(slug, slug)}: «{info.get('title')}» - {info.get('author')}")
        d.blank()
        d.show([Msg("system", "Kevin MacLeod (incompetech.com). Licensed under Creative Commons: "
                              "By Attribution 4.0 License, http://creativecommons.org/licenses/by/4.0/")])
        d.show([Msg("system", f"Проигрыватель звука: {self.music.backend_name()}")])
        d.pause("Enter - назад")


def main() -> None:
    dsp.setup_console()
    try:
        App().run()
    except KeyboardInterrupt:
        print("\nДо встречи.")
