"""Разбор ввода игрока.

Понимает номера вариантов, команды (с мелкими опечатками) и названия
предметов в разных падежах: «ключ», «ключа», «латунный ключ» -> один предмет.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_INPUT = 200

COMMANDS: dict[str, list[str]] = {
    "inventory": ["и", "инв", "инвентарь", "вещи", "рюкзак", "карманы"],
    "status": ["статус", "статы", "стат", "состояние"],
    "journal": ["журнал", "хроника", "ж"],
    "save": ["сохранить", "сохр", "сохраниться", "save"],
    "load": ["загрузить", "загр", "загрузиться", "load"],
    "help": ["помощь", "справка", "команды", "help", "?", "п"],
    "exit": ["выход", "выйти", "quit", "exit", "меню"],
    "hint": ["подсказка", "подскажи", "хинт", "hint", "что делать"],
    "undo": ["отменить", "отмена", "отменить ход", "откат", "undo"],
    "look": ["осмотреться", "оглядеться", "смотреть", "описание", "где", "л"],
}
VERBS: dict[str, list[str]] = {
    "examine": ["осмотреть", "осм", "изучить", "рассмотреть", "прочитать",
                "прочесть", "читать", "о", "x"],
    "use": ["использовать", "исп", "применить", "использую", "use", "юз"],
}
STOP_WORDS = {"на", "в", "во", "с", "со", "к", "ко", "у", "из", "по", "за", "от",
              "до", "о", "об", "обо", "мой", "мою", "моё", "мое", "мои", "свой",
              "свою", "этот", "эту", "это", "the", "a"}


@dataclass
class Command:
    kind: str          # choice, undo, inventory, status, journal, save, load, help, exit,
                       # look, examine, use, answer, empty, unknown
    arg: str = ""
    number: int = 0


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s\-]", " ", text)
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    return [w for w in normalize(text).split(" ") if w]


def levenshtein(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def word_match(q: str, a: str) -> int:
    """Сила совпадения слова запроса q со словом названия a (0 — нет)."""
    if q == a:
        return 4
    if len(q) >= 3 and a.startswith(q):
        return 3
    p = _common_prefix(q, a)
    if p >= 3 and p >= min(len(q), len(a)) - 2:
        return 2  # разные окончания: ключ/ключа, латунный/латунного
    if len(q) >= 4 and len(a) >= 4 and levenshtein(q, a, 1) <= 1:
        return 1  # опечатка
    if len(q) >= 6 and len(a) >= 6 and levenshtein(q[:len(a)], a, 2) <= 2 and p >= 2:
        return 1
    return 0


def match_items(query: str, items: dict[str, dict]) -> list[str]:
    """Возвращает id лучших совпадений (несколько — если неоднозначно)."""
    qwords = [w for w in words(query) if w not in STOP_WORDS] or words(query)
    if not qwords:
        return []
    scored: list[tuple[tuple[int, int], str]] = []
    for iid, item in items.items():
        names = [item.get("name", iid)] + list(item.get("aliases", []))
        best = (0, 0)
        for name in names:
            awords = words(name)
            if not awords:
                continue
            matched, strength = 0, 0
            for q in qwords:
                s = max(word_match(q, a) for a in awords)
                if s:
                    matched += 1
                    strength += s
            best = max(best, (matched, strength))
        if best[0] > 0:
            scored.append((best, iid))
    if not scored:
        return []
    scored.sort(reverse=True)
    top = scored[0][0]
    # все слова запроса должны найтись хотя бы у лучшего кандидата, иначе берём частичное
    return [iid for sc, iid in scored if sc == top]


def _match_command(word: str, table: dict[str, list[str]]) -> str | None:
    for kind, names in table.items():
        if word in names:
            return kind
    if len(word) >= 5:
        for kind, names in table.items():
            for n in names:
                if len(n) >= 5 and levenshtein(word, n, 1) <= 1:
                    return kind
    return None


def parse(line: str, *, answer_mode: bool = False) -> Command:
    raw = (line or "")[:MAX_INPUT]
    text = normalize(raw)
    if not text:
        return Command("empty")
    m = re.fullmatch(r"(\d{1,6})", text)
    if m:
        return Command("choice", number=int(m.group(1)))
    parts = text.split(" ", 1)
    head, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    if text in COMMANDS["undo"]:
        return Command("undo")
    if text in COMMANDS["hint"]:
        return Command("hint")
    if not rest:
        kind = _match_command(head, COMMANDS)
        if kind:
            return Command(kind)
    verb = _match_command(head, VERBS)
    if verb:
        if verb == "examine" and head in ("о", "x") and not rest:
            verb = None
        else:
            return Command(verb, arg=rest)
    if not rest and head in ("инвентарь",):
        return Command("inventory")
    if answer_mode:
        return Command("answer", arg=text)
    return Command("unknown", arg=text)
