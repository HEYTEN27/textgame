"""Сохранения (3 слота), достижения (открытые концовки) и настройки.

Любой повреждённый файл не роняет игру: он просто считается пустым,
а игроку показывается понятное сообщение.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from . import paths
from .effects import Msg
from .state import GameState

SLOTS = 3
SAVE_VERSION = 1
DEFAULT_SETTINGS = {"speed": "нормально", "colors": True, "music": True, "volume": 60, "clear_screen": True}
VOLUME_STEPS = [25, 50, 75, 100]


class SaveError(Exception):
    pass


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Store:
    def __init__(self, directory: str | None = None):
        self.dir = directory or paths.saves_dir()

    def ensure_dir(self) -> bool:
        try:
            os.makedirs(self.dir, exist_ok=True)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------ слоты
    def slot_path(self, slot: int) -> str:
        return os.path.join(self.dir, f"slot{slot}.json")

    def _session_data(self, session) -> dict:
        return {
            "version": SAVE_VERSION,
            "scenario_id": session.scenario.id,
            "scenario_title": session.scenario.title,
            "saved_at": time.strftime("%d.%m.%Y %H:%M"),
            "turns": session.state.turns,
            "last_journal": session.state.journal[-1] if session.state.journal else "",
            "state": session.state.to_dict(),
        }

    def save(self, slot: int, session) -> None:
        data = self._session_data(session)
        try:
            _write_json(self.slot_path(slot), data)
        except OSError as exc:
            raise SaveError(f"не удалось записать файл: {exc}") from exc

    def read_slot(self, slot: int | str) -> dict:
        path = self.autosave_path() if slot == "auto" else self.slot_path(slot)
        if not os.path.exists(path):
            raise SaveError("слот пуст")
        try:
            data = _read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SaveError(f"файл повреждён ({exc.__class__.__name__})") from exc
        if not isinstance(data, dict) or "state" not in data or "scenario_id" not in data:
            raise SaveError("файл повреждён (нет нужных полей)")
        try:
            data["state_obj"] = GameState.from_dict(data["state"])
        except ValueError as exc:
            raise SaveError(str(exc)) from exc
        return data

    def slot_summary(self, slot: int) -> str:
        try:
            d = self.read_slot(slot)
        except SaveError as exc:
            return "пусто" if str(exc) == "слот пуст" else f"повреждён: {exc}"
        return f"{d.get('scenario_title', d['scenario_id'])} - ход {d.get('turns', '?')} - {d.get('saved_at', '')}"

    # ------------------------------------------------------------ автосохранение
    def autosave_path(self) -> str:
        return os.path.join(self.dir, "autosave.json")

    def autosave(self, session) -> None:
        try:
            _write_json(self.autosave_path(), self._session_data(session))
        except OSError:
            pass

    def clear_autosave(self) -> None:
        try:
            os.remove(self.autosave_path())
        except OSError:
            pass

    def autosave_summary(self) -> str | None:
        try:
            d = self.read_slot("auto")
        except SaveError:
            return None
        return f"{d.get('scenario_title', d['scenario_id'])} - ход {d.get('turns', '?')} - {d.get('saved_at', '')}"

    # ------------------------------------------------------------ статистика
    def stats_path(self) -> str:
        return os.path.join(self.dir, "stats.json")

    def load_stats(self) -> dict:
        stats = {"play_seconds": 0, "games": 0, "finished": 0}
        try:
            data = _read_json(self.stats_path())
            if isinstance(data, dict):
                for k in stats:
                    v = data.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                        stats[k] = int(v)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        return stats

    def update_stats(self, play_seconds: float = 0, games: int = 0, finished: int = 0) -> None:
        stats = self.load_stats()
        stats["play_seconds"] += int(max(0, play_seconds))
        stats["games"] += games
        stats["finished"] += finished
        try:
            _write_json(self.stats_path(), stats)
        except OSError:
            pass

    def load_session(self, slot: int | str, scenarios: dict):
        from .game import Session

        d = self.read_slot(slot)
        scen = scenarios.get(d["scenario_id"])
        if scen is None:
            raise SaveError(f"сценарий «{d['scenario_id']}» не найден")
        try:
            return Session(scen, d["state_obj"])
        except (ValueError, KeyError) as exc:
            raise SaveError(f"сохранение не подходит к сценарию: {exc}") from exc

    # ------------------------------------------------------------ диалоги
    def _pick_slot(self, display, title: str) -> int | None:
        display.header(title)
        for s in range(1, SLOTS + 1):
            display.plain(f"  {s}. {self.slot_summary(s)}")
        display.plain("  0. Отмена")
        ans = display.ask("Слот: ")
        if ans is None:
            return None
        ans = ans.strip()
        if ans.isdigit() and 1 <= int(ans) <= SLOTS:
            return int(ans)
        if ans not in ("0", ""):
            display.show([Msg("system", "Нет такого слота.")])
        return None

    def save_dialog(self, session, display) -> None:
        slot = self._pick_slot(display, "Сохранение")
        if slot is None:
            display.show([Msg("system", "Сохранение отменено.")])
            return
        if os.path.exists(self.slot_path(slot)) and not display.confirm(
                f"Перезаписать слот {slot}? (д/н): "):
            display.show([Msg("system", "Сохранение отменено.")])
            return
        try:
            self.save(slot, session)
            display.show([Msg("good", f"Игра сохранена в слот {slot}.")])
        except SaveError as exc:
            display.show([Msg("danger", f"Не удалось сохранить: {exc}")])

    def load_dialog(self, display, scenarios: dict):
        slot = self._pick_slot(display, "Загрузка")
        if slot is None:
            return None
        try:
            sess = self.load_session(slot, scenarios)
        except SaveError as exc:
            display.show([Msg("danger", f"Не удалось загрузить: {exc}")])
            return None
        display.show([Msg("good", f"Загружено из слота {slot}.")])
        sess.need_describe = True
        return sess

    # ------------------------------------------------------------ достижения
    def achievements_path(self) -> str:
        return os.path.join(self.dir, "achievements.json")

    def load_achievements(self) -> dict[str, list[str]]:
        try:
            data = _read_json(self.achievements_path())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v]
        return out

    def add_ending(self, scenario_id: str, ending_id: str) -> bool:
        ach = self.load_achievements()
        lst = ach.setdefault(scenario_id, [])
        if ending_id in lst:
            return False
        lst.append(ending_id)
        try:
            _write_json(self.achievements_path(), ach)
        except OSError:
            pass
        return True

    # ------------------------------------------------------------ настройки
    def settings_path(self) -> str:
        return os.path.join(self.dir, "settings.json")

    def load_settings(self) -> dict:
        settings = dict(DEFAULT_SETTINGS)
        try:
            data = _read_json(self.settings_path())
            if isinstance(data, dict):
                if data.get("speed") in ("нормально", "быстро", "мгновенно"):
                    settings["speed"] = data["speed"]
                for key in ("colors", "music", "clear_screen"):
                    if isinstance(data.get(key), bool):
                        settings[key] = data[key]
                vol = data.get("volume")
                if isinstance(vol, int) and not isinstance(vol, bool) and 0 <= vol <= 100:
                    settings["volume"] = vol
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        return settings

    def save_settings(self, settings: dict) -> None:
        try:
            _write_json(self.settings_path(), settings)
        except OSError:
            pass
