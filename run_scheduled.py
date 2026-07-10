"""
Запуск сбора статистики без Streamlit — для автозапуска по расписанию
(например, через GitHub Actions). Проходит по всем аккаунтам и для
каждого вызывает run_parser(), печатая лог в консоль.

Аккаунты берутся из переменной окружения ACCOUNTS_JSON (JSON-список,
такой же формат, как accounts.json) либо из файла accounts.json, если
переменная не задана.

Google-креды ожидаются в файле credentials.json (см. .github/workflows).
"""

import json
import os
import sys

from parser_core import run_parser


def load_accounts():
    raw = os.environ.get("ACCOUNTS_JSON")
    if raw:
        return json.loads(raw)
    with open("accounts.json", encoding="utf-8") as f:
        return json.load(f)


def main():
    accounts = load_accounts()
    if not accounts:
        print("Нет ни одного аккаунта — нечего запускать.")
        return

    had_errors = False
    for account in accounts:
        print(f"\n{'=' * 60}")
        print(f"Аккаунт: {account['name']}")
        print(f"{'=' * 60}")
        try:
            for msg in run_parser(account):
                print(msg)
        except Exception as e:
            had_errors = True
            print(f"ОШИБКА в аккаунте '{account['name']}': {e}")

    sys.exit(1 if had_errors else 0)


if __name__ == "__main__":
    main()
