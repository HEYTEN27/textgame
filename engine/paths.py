"""Пути к данным и к папке сохранений.

Данные (сценарии) лежат внутри exe (sys._MEIPASS) или рядом с исходниками.
Сохранения всегда создаются рядом с exe / main.py, а не во временной папке.
"""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    override = os.environ.get("TA_DATA_DIR")
    if override:
        return override
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "data")  # type: ignore[attr-defined]
    return os.path.join(project_root(), "data")


def scenarios_dir() -> str:
    return os.path.join(data_dir(), "scenarios")


def user_dir() -> str:
    """Папка, рядом с которой создаётся saves/."""
    override = os.environ.get("TA_SAVE_DIR")
    if override:
        return override
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return project_root()


def saves_dir() -> str:
    override = os.environ.get("TA_SAVE_DIR")
    if override:
        return override
    return os.path.join(user_dir(), "saves")
