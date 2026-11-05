"""Главное меню, выбор сценария, галерея концовок, настройки."""
from __future__ import annotations

import os

from . import display as dsp
from .effects import Msg
from .game import Session, play
from .loader import ENDING_TYPE_NAMES, load_all
from .save import Store

BANNER = r"""
  _____ _____ _  _____ _____    _   ___ _____ ___ _  _ _____ _   _ ___  ___
 |_   _| __\ \/ /_   _|  _  |  /_\ |   \_   _| __| \| |_   _| | | | _ \| __|
   | | | _| >  <  | | | |_| | / _ \| |) || | | _|| .` | | | | |_| |   /| _|
   |_| |___/_/\_\ |_| |_____|/_/ \_\___/ |_| |___|_|\_| |_|  \___/|_|_\|___|
"""


def _ask_number(display, prompt: str, maximum: int) -> int | None:
    ans = display.ask(prompt)
    if ans is None:
        return None
    ans = ans.strip()
    if ans.isdigit() and 0 <= int(ans) <= maximum:
        return int(ans)
    display.show([Msg("system", f"Введи число от 0 до {maximum}.")])
    return -1


class App:
    def __init__(self):
        self.store = Store()
        self.store.ensure_dir()
        self.settings = self.store.load_settings()
        self.display = dsp.Display(self.settings)
        scenarios, broken = load_all(include_hidden=os.environ.get("TA_DEBUG") == "1")
        self.scenarios = scenarios
        self.by_id = {s.id: s for s in scenarios}
        self.broken = broken

    def run(self) -> None:
        d = self.display
        d.plain(d._colorize(BANNER, "title", False))
        d.plain(d._colorize("Текстовые приключения. Десять историй, которые ждут тебя в темноте.", "system", False))
        for err in self.broken:
            d.show([Msg("danger", f"Сценарий {err.scenario} не загружен: {err.problems[0]}")])
        while True:
            d.header("Главное меню")
            d.plain("  1. Новая игра")
            d.plain("  2. Продолжить (загрузка)")
            d.plain("  3. Концовки")
            d.plain("  4. Настройки")
            d.plain("  5. Выход")
            choice = _ask_number(d, "> ", 5)
            if choice is None or choice == 5:
                if choice is None or d.confirm("Точно выйти из игры? (д/н): "):
                    d.plain("До встречи.")
                    return
            elif choice == 1:
                self.new_game()
            elif choice == 2:
                sess = self.store.load_dialog(d, self.by_id)
                if sess is not None:
                    play(sess, d, self.store, self.by_id)
            elif choice == 3:
                self.gallery()
            elif choice == 4:
                self.settings_menu()

    def _found(self, scen_id: str) -> list[str]:
        return self.store.load_achievements().get(scen_id, [])

    def new_game(self) -> None:
        d = self.display
        if not self.scenarios:
            d.show([Msg("danger", "Не найдено ни одного сценария в папке data/scenarios.")])
            return
        d.header("Выбери историю")
        for n, s in enumerate(self.scenarios, 1):
            found = [e for e in self._found(s.id) if e in s.endings]
            d.plain(d._colorize(f"  {n}. {s.title}", "title", False)
                    + d._colorize(f"  [{s.genre}] открыто концовок: {len(found)}/{len(s.endings)}", "system", False))
            d.plain(dsp.wrap(s.teaser, dsp.WIDTH, "     ", "     "))
            d.blank()
        d.plain("  0. Назад")
        n = _ask_number(d, "Номер истории: ", len(self.scenarios))
        if not n:
            return
        scen = self.scenarios[n - 1]
        d.header(scen.title)
        d.show([Msg("system", "Подсказка: вводи номер варианта или команду. «помощь» - список команд.")])
        play(Session(scen), d, self.store, self.by_id)

    def gallery(self) -> None:
        d = self.display
        d.header("Галерея концовок")
        total_found = total = 0
        for s in self.scenarios:
            found = set(self._found(s.id))
            got = [e for e in s.endings if e in found]
            total_found += len(got)
            total += len(s.endings)
            d.plain(d._colorize(f"{s.title}: открыто {len(got)}/{len(s.endings)}", "title", False))
            for n, (eid, end) in enumerate(s.endings.items(), 1):
                if eid in found:
                    tname = ENDING_TYPE_NAMES.get(end.get("type", ""), "")
                    d.plain(f"   {n}. {end.get('title', eid)} ({tname})")
                else:
                    d.plain(d._colorize(f"   {n}. ??? (ещё не открыта)", "system", False))
        d.blank()
        d.plain(f"Всего открыто: {total_found}/{total}")
        d.ask(d._colorize("Enter - назад", "system", False))

    def settings_menu(self) -> None:
        d = self.display
        while True:
            d.header("Настройки")
            d.plain(f"  1. Скорость текста: {self.settings['speed']}")
            d.plain(f"  2. Цвета: {'включены' if self.settings['colors'] else 'выключены'}")
            d.plain("  0. Назад")
            n = _ask_number(d, "> ", 2)
            if n == 1:
                order = dsp.SPEED_ORDER
                i = order.index(self.settings["speed"]) if self.settings["speed"] in order else 0
                self.settings["speed"] = order[(i + 1) % len(order)]
            elif n == 2:
                self.settings["colors"] = not self.settings["colors"]
            elif n in (0, None):
                self.store.save_settings(self.settings)
                return
            self.store.save_settings(self.settings)


def main() -> None:
    dsp.setup_console()
    try:
        App().run()
    except KeyboardInterrupt:
        print("\nДо встречи.")
