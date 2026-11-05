"""Проверки навыков: d20 + модификатор стата против сложности (DC)."""
from __future__ import annotations

import random
from dataclasses import dataclass


def modifier(value: int) -> int:
    """Модификатор от значения стата: 5 -> 0, 10 -> +2, 1 -> -2."""
    return (int(value) - 5) // 2


def success_chance(dc: int, total_mod: int) -> float:
    """Вероятность успеха. Натуральная 20 всегда успех, натуральная 1 — провал."""
    need = int(dc) - int(total_mod)  # нужно выбросить на кубике не меньше need
    wins = 21 - need
    wins = max(1, min(19, wins))
    return wins / 20.0


def odds_words(p: float) -> str:
    if p <= 0.25:
        return "шансы невелики"
    if p <= 0.45:
        return "рискованно"
    if p <= 0.6:
        return "как повезёт"
    if p <= 0.8:
        return "скорее получится"
    return "почти наверняка"


@dataclass
class RollResult:
    die: int
    mod: int
    total: int
    dc: int
    success: bool

    def describe(self) -> str:
        sign = "+" if self.mod >= 0 else "-"
        verdict = "успех" if self.success else "провал"
        extra = ""
        if self.die == 20:
            extra = " (натуральная 20!)"
        elif self.die == 1:
            extra = " (натуральная 1...)"
        return (
            f"Бросок d20: {self.die} {sign} {abs(self.mod)} = {self.total} "
            f"против {self.dc} - {verdict}{extra}"
        )


def roll(dc: int, total_mod: int, rng: random.Random | None = None) -> RollResult:
    rng = rng or random
    die = rng.randint(1, 20)
    total = die + total_mod
    if die == 20:
        ok = True
    elif die == 1:
        ok = False
    else:
        ok = total >= dc
    return RollResult(die=die, mod=total_mod, total=total, dc=int(dc), success=ok)
