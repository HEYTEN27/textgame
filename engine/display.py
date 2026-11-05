"""Вывод текста: перенос по словам, эффект печатной машинки, цвета.

Разметка в текстах сценариев:
  [текст]  — предмет (жёлтым)
  <текст>  — опасность (красным)
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
import time

try:  # colorama необязательна: без неё просто не будет цветов в старых консолях
    import colorama  # type: ignore

    _HAS_COLORAMA = True
except Exception:  # noqa: BLE001
    colorama = None
    _HAS_COLORAMA = False

WIDTH = 80
SPEEDS = {"мгновенно": 0.0, "быстро": 0.004, "нормально": 0.014}
SPEED_ORDER = ["нормально", "быстро", "мгновенно"]

RESET = "\033[0m"
COLORS = {
    "text": "",
    "plain": "",
    "system": "\033[90m",   # серый
    "danger": "\033[91m",   # красный
    "good": "\033[92m",     # зелёный
    "title": "\033[96m\033[1m",
    "item": "\033[93m",     # жёлтый
    "choice": "",
}
ITEM_COLOR = "\033[93m"
DANGER_COLOR = "\033[91m"
TYPED_KINDS = {"text", "danger", "good"}

_MARKUP = re.compile(r"\[([^\[\]]*)\]|<([^<>]*)>")
_ANSI_TOKEN = re.compile(r"\033\[[0-9;]*m|.", re.S)


def setup_console() -> None:
    """Включает UTF-8 и ANSI-цвета в консоли Windows."""
    if os.name == "nt":
        os.system("chcp 65001 > nul")
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    if _HAS_COLORAMA:
        try:
            colorama.just_fix_windows_console()
        except AttributeError:
            colorama.init()


def strip_markup(text: str) -> str:
    return _MARKUP.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), text)


def wrap(text: str, width: int = WIDTH, indent: str = "", subsequent: str | None = None) -> str:
    """Перенос по словам с сохранением абзацев."""
    sub = indent if subsequent is None else subsequent
    paras = text.split("\n")
    out = []
    for para in paras:
        if not para.strip():
            out.append("")
            continue
        out.append(textwrap.fill(para.strip(), width=width, initial_indent=indent,
                                 subsequent_indent=sub, break_long_words=True,
                                 break_on_hyphens=False))
    return "\n".join(out)


class Display:
    def __init__(self, settings: dict | None = None, out=None, inp=None):
        self.settings = settings or {}
        self.out = out or sys.stdout
        self.inp = inp
        self._skip = False
        self._tail = "\n\n"

    # --- настройки
    @property
    def colors(self) -> bool:
        return bool(self.settings.get("colors", True))

    @property
    def delay(self) -> float:
        return SPEEDS.get(self.settings.get("speed", "нормально"), SPEEDS["нормально"])

    # --- низкий уровень
    def _write(self, s: str) -> None:
        if s:
            self._tail = (self._tail + s)[-2:]
        try:
            self.out.write(s)
        except UnicodeEncodeError:
            self.out.write(s.encode("ascii", "replace").decode("ascii"))

    def _colorize(self, text: str, kind: str, markup: bool) -> str:
        base = COLORS.get(kind, "") if self.colors else ""
        if markup:
            if self.colors:
                def repl(m: re.Match) -> str:
                    if m.group(1) is not None:
                        return f"{ITEM_COLOR}{m.group(1)}{RESET}{base}"
                    return f"{DANGER_COLOR}{m.group(2)}{RESET}{base}"
                text = _MARKUP.sub(repl, text)
            else:
                text = strip_markup(text)
        if base:
            return f"{base}{text}{RESET}"
        return text

    def _key_pressed(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore

                hit = False
                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\r", "\n", " "):
                        hit = True
                return hit
            if not sys.stdin or not sys.stdin.isatty():
                return False
            import select

            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                sys.stdin.readline()
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _type(self, s: str) -> None:
        delay = self.delay
        interactive = hasattr(self.out, "isatty") and self.out.isatty()
        if delay <= 0 or self._skip or not interactive:
            self._write(s)
            self.out.flush()
            return
        for n, tok in enumerate(_ANSI_TOKEN.findall(s)):
            self._write(tok)
            if tok.startswith("\033"):
                continue
            if n % 3 == 0:
                self.out.flush()
                if self._key_pressed():
                    self._skip = True
            if self._skip:
                continue
            time.sleep(delay if tok not in ".!?…" else delay * 6)
        self.out.flush()

    # --- высокий уровень
    def show(self, msgs, markup: bool = True) -> None:
        for m in msgs or []:
            kind, text = m.kind, m.text
            if not text:
                continue
            if kind == "plain":
                body = "\n".join(
                    wrap(line, WIDTH, "", "      ") if len(strip_markup(line)) > WIDTH else line
                    for line in text.split("\n"))
            elif kind == "title":
                body = text
            else:
                body = wrap(text)
            rendered = self._colorize(body, kind, markup)
            if kind in TYPED_KINDS:
                self._type(rendered + "\n")
                if kind == "text":
                    self._write("\n")
            else:
                self._write(rendered + "\n")
        self.out.flush()

    def plain(self, text: str = "") -> None:
        self._write(text + "\n")

    def blank(self) -> None:
        if not self._tail.endswith("\n\n"):
            self._write("\n")

    def rule(self) -> None:
        line = "-" * WIDTH
        self._write(self._colorize(line, "system", False) + "\n")

    def header(self, text: str) -> None:
        self._write("\n" + self._colorize(text, "title", False) + "\n")
        self._write(self._colorize("=" * min(WIDTH, max(10, len(text))), "system", False) + "\n")

    def choice(self, n: int, text: str) -> None:
        prefix = f"  {n}. "
        body = wrap(text, WIDTH, prefix, " " * len(prefix))
        self._write(self._colorize(body, "choice", True) + "\n")

    def ask(self, prompt: str = "> ") -> str | None:
        self._skip = False
        try:
            self._write(prompt)
            self.out.flush()
            if self.inp is not None:
                line = self.inp.readline()
                if line == "":
                    return None
                return line.rstrip("\r\n")[:500]
            return input()[:500]
        except (EOFError, KeyboardInterrupt):
            self._write("\n")
            return None
        except UnicodeDecodeError:
            return ""

    def confirm(self, prompt: str) -> bool:
        for _ in range(5):
            ans = self.ask(self._colorize(prompt, "system", False))
            if ans is None:
                return True
            a = ans.strip().lower()
            if a in ("д", "да", "y", "yes", "l"):
                return True
            if a in ("н", "нет", "n", "no"):
                return False
            self.plain("Ответь «д» или «н».")
        return False

    def ending_banner(self, info: dict, new: bool) -> None:
        self.blank()
        self.rule()
        title = f"КОНЦОВКА: {info['title']}"
        self._write(self._colorize(title, "title", False) + "\n")
        line = f"Концовка {info['number']} из {info['total']} ({info['type_name']})"
        if new:
            line += " - открыта впервые!"
        self._write(self._colorize(line, "system", False) + "\n")
        self.rule()
        self.blank()
