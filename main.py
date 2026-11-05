"""Точка входа: текстовая приключенческая игра."""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _run() -> None:
    from engine.menu import main

    main()


if __name__ == "__main__":
    try:
        _run()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 — последняя линия обороны: не закрывать окно молча
        traceback.print_exc()
        try:
            input("\nПроизошла непредвиденная ошибка. Нажми Enter, чтобы закрыть окно...")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
