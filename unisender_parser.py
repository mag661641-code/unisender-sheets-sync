"""
Запуск парсера из командной строки.
Для интерфейса используйте: streamlit run app.py
"""

import json
import os
from parser_core import run_parser

ACCOUNTS_FILE = "accounts.json"

if __name__ == "__main__":
    if not os.path.exists(ACCOUNTS_FILE):
        print(f"Файл {ACCOUNTS_FILE} не найден")
        exit(1)

    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        accounts = json.load(f)

    if not accounts:
        print("В accounts.json нет аккаунтов")
        exit(1)

    if len(accounts) == 1:
        account = accounts[0]
    else:
        print("Выберите аккаунт:")
        for i, a in enumerate(accounts, 1):
            print(f"  {i}. {a['name']} ({a['unisender_email']})")
        choice = int(input("Номер: ")) - 1
        account = accounts[choice]

    print(f"\nАккаунт: {account['name']}")
    print(f"Таблица: {account['sheet_name']} / {account['worksheet']}\n")

    for msg in run_parser(account):
        print(msg)
