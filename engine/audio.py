"""Фоновая музыка.

Windows: встроенный проигрыватель MCI (winmm.dll) через ctypes — без сторонних пакетов.
Linux/macOS: внешний проигрыватель, если он установлен (ffplay, mpg123, afplay).
Если звук недоступен — игра молча работает без музыки. Любая ошибка звука
никогда не роняет игру.

Отключить полностью: переменная окружения TA_NO_AUDIO=1.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import threading

from . import paths

ALIAS = "ta_bgm"


def music_dir() -> str:
    return os.path.join(paths.data_dir(), "music")


def track_path(slug: str | None) -> str | None:
    if not slug:
        return None
    path = os.path.join(music_dir(), f"{slug}.mp3")
    return path if os.path.isfile(path) else None


def load_credits() -> dict:
    try:
        with open(os.path.join(music_dir(), "credits.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def credit_line(slug: str | None) -> str:
    info = load_credits().get("tracks", {}).get(slug or "", {})
    if not info:
        return ""
    return f"«{info.get('title', slug)}» — {info.get('author', '')} ({info.get('license', '')})"


class _Backend:
    def play(self, path: str, volume: int) -> bool:  # pragma: no cover - интерфейс
        return False

    def stop(self) -> None:  # pragma: no cover
        pass

    def set_volume(self, volume: int) -> None:  # pragma: no cover
        pass


class _MciBackend(_Backend):
    """Windows MCI: mpegvideo играет MP3 и умеет зацикливание (repeat)."""

    def __init__(self):
        import ctypes

        self._ctypes = ctypes
        winmm = ctypes.WinDLL("winmm")
        self._send = winmm.mciSendStringW
        self._send.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self._send.restype = ctypes.c_uint
        self._open = False

    def _cmd(self, command: str) -> tuple[int, str]:
        buf = self._ctypes.create_unicode_buffer(256)
        code = self._send(command, buf, 255, None)
        return int(code), buf.value

    def play(self, path: str, volume: int) -> bool:
        self.stop()
        code, _ = self._cmd(f'open "{path}" type mpegvideo alias {ALIAS}')
        if code != 0:
            return False
        self._open = True
        self.set_volume(volume)
        code, _ = self._cmd(f"play {ALIAS} repeat")
        if code != 0:
            code, _ = self._cmd(f"play {ALIAS}")
        return code == 0

    def stop(self) -> None:
        if self._open:
            self._cmd(f"stop {ALIAS}")
            self._cmd(f"close {ALIAS}")
            self._open = False

    def set_volume(self, volume: int) -> None:
        if self._open:
            self._cmd(f"setaudio {ALIAS} volume to {max(0, min(100, volume)) * 10}")

    def status(self) -> str:
        return self._cmd(f"status {ALIAS} mode")[1] if self._open else "closed"


class _ProcessBackend(_Backend):
    """Внешний проигрыватель. Зацикливание — перезапуском в фоновом потоке."""

    def __init__(self, exe: str):
        self.exe = exe
        self.name = os.path.basename(exe)
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._path = ""
        self._volume = 70
        self.failed = False

    def _argv(self) -> list[str]:
        if self.name.startswith("ffplay"):
            return [self.exe, "-nodisp", "-autoexit", "-loglevel", "quiet",
                    "-volume", str(self._volume), self._path]
        if self.name.startswith("mpg123"):
            return [self.exe, "-q", "-f", str(int(32768 * self._volume / 100)), self._path]
        return [self.exe, "-v", f"{self._volume / 100:.2f}", self._path]  # afplay

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                proc = subprocess.Popen(self._argv(), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._proc = proc
                while proc.poll() is None:
                    if self._stop.wait(0.5):
                        break
                if proc.returncode not in (None, 0) and not self._stop.is_set():
                    self.failed = True  # проигрыватель не смог играть (нет звука и т. п.)
                    return
            except OSError:
                self.failed = True
                return
            finally:
                self._kill()

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def play(self, path: str, volume: int) -> bool:
        self.stop()
        self._path, self._volume = path, volume
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._kill()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None

    def set_volume(self, volume: int) -> None:
        if self._thread and self._path:
            self.play(self._path, volume)


def _make_backend() -> _Backend | None:
    if os.environ.get("TA_NO_AUDIO") == "1":
        return None
    try:
        if os.name == "nt":
            return _MciBackend()
        for name in ("ffplay", "mpg123", "afplay"):
            exe = shutil.which(name)
            if exe:
                return _ProcessBackend(exe)
    except Exception:  # noqa: BLE001
        return None
    return None


class Music:
    """Фоновая музыка с учётом настроек игрока."""

    def __init__(self, settings: dict):
        self.settings = settings
        self._backend: _Backend | None = None
        self._backend_ready = False
        self.current: str | None = None
        self.failed = False
        atexit.register(self.stop)

    @property
    def backend(self) -> _Backend | None:
        if not self._backend_ready:
            self._backend_ready = True
            self._backend = _make_backend()
        return self._backend

    @property
    def available(self) -> bool:
        return self.backend is not None

    def backend_name(self) -> str:
        b = self.backend
        if b is None:
            return "нет"
        if isinstance(b, _MciBackend):
            return "Windows MCI"
        return getattr(b, "name", "внешний проигрыватель")

    def play(self, slug: str | None) -> None:
        if slug == self.current and slug is not None:
            return
        self.current = slug
        self._restart()

    def _restart(self) -> None:
        b = self.backend
        if b is None:
            return
        try:
            b.stop()
            path = track_path(self.current)
            if not path or not self.settings.get("music", True):
                return
            if not b.play(path, int(self.settings.get("volume", 60))):
                self.failed = True
        except Exception:  # noqa: BLE001 — звук никогда не должен ронять игру
            self.failed = True

    def apply_settings(self) -> None:
        """Вызвать после изменения настроек музыки/громкости."""
        b = self.backend
        if b is None:
            return
        try:
            if not self.settings.get("music", True):
                b.stop()
            else:
                self._restart()
        except Exception:  # noqa: BLE001
            self.failed = True

    def stop(self) -> None:
        try:
            if self._backend is not None:
                self._backend.stop()
        except Exception:  # noqa: BLE001
            pass


def diagnose() -> int:
    """Проверка звука: python -m engine.audio. Код 0, если трек запустился."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    music = Music({"music": True, "volume": 50})
    print("Проигрыватель:", music.backend_name())
    path = track_path("menu")
    print("Трек меню:", path)
    if not music.available or not path:
        return 1
    music.play("menu")
    b = music.backend
    if isinstance(b, _MciBackend):
        import time

        time.sleep(1.5)
        print("Состояние MCI:", b.status())
        code, _ = b._cmd(f"status {ALIAS} position")
        print("Позиция воспроизведения, мс:", b._cmd(f"status {ALIAS} position")[1], "код:", code)
    music.stop()
    return 1 if music.failed else 0


if __name__ == "__main__":
    sys.exit(diagnose())
