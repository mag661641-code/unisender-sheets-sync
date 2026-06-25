"""
Ядро парсера Unisender -> Google Sheets.
Используется и из app.py (Streamlit) и напрямую из unisender_parser.py.

run_parser(account)  генератор, выдаёт строки лога.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import gspread
from google.oauth2.service_account import Credentials
from datetime import date as Date
import time
import re
import os

GOOGLE_JSON = "credentials.json"


def _setup_proxy():
    """Читает прокси из Streamlit Secrets или из локальных констант."""
    try:
        import streamlit as st
        p = st.secrets["proxy"]
        user, pwd, host, port = p["user"], p["password"], p["host"], p["port"]
    except Exception:
        user     = "2B36LLBTi"
        pwd      = "PbiFG8X5r"
        host     = "5.42.209.148"
        port     = "64328"

    proxy = f"http://{user}:{pwd}@{host}:{port}"
    os.environ["HTTP_PROXY"]  = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["http_proxy"]  = proxy
    os.environ["https_proxy"] = proxy
    os.environ["NO_PROXY"]    = "localhost,127.0.0.1"
    os.environ["no_proxy"]    = "localhost,127.0.0.1"


def _get_google_creds(scopes):
    """Читает Google credentials из Streamlit Secrets или из credentials.json."""
    try:
        import streamlit as st
        info = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(info, scopes=scopes)
    except Exception:
        return Credentials.from_service_account_file(GOOGLE_JSON, scopes=scopes)


def get_service_email():
    """Email сервисного аккаунта для показа в UI."""
    try:
        import streamlit as st
        return st.secrets["gcp_service_account"]["client_email"]
    except Exception:
        pass
    try:
        import json
        with open(GOOGLE_JSON, encoding="utf-8") as f:
            return json.load(f).get("client_email", "")
    except Exception:
        return ""

LIST_TO_SEGMENT = {
    "СМУ":         "Россия",
    "Алматы СМУ":  "Казахстан",
    "Минск":       "Беларусь",
    "ТашкентСМУ":  "Узбекистан",
    "Азербайджан": "Азербайджан",
    "Армения":     "Армения",
}
LIST_NAMES_BY_LEN = sorted(LIST_TO_SEGMENT.keys(), key=len, reverse=True)
DAYS_RU       = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
COUNTRY_ORDER = ["Россия", "Казахстан", "Беларусь", "Узбекистан", "Азербайджан", "Армения"]


#  Утилиты 

def to_int(s):
    if s is None or s == "":
        return ""
    try:
        return int(re.sub(r"[^\d]", "", str(s)))
    except:
        return ""


def get_weekday(date_str):
    try:
        parts = date_str.split(".")
        d = Date(2000 + int(parts[2]), int(parts[1]), int(parts[0]))
        return DAYS_RU[d.weekday()]
    except:
        return ""


#  Парсинг страниц 

def parse_stat_lines(page_text):
    lines = [l.strip() for l in page_text.split("\n")]

    def get_value(label_pattern):
        for i, line in enumerate(lines):
            if not re.fullmatch(label_pattern, line, re.IGNORECASE):
                continue
            if i >= 2:
                mid  = lines[i - 1]
                cand = lines[i - 2]
                if re.search(r"\d+[.,]?\d*\s*%", mid) and re.search(r"^\d", cand):
                    return cand
            if i >= 1:
                cand = lines[i - 1]
                if re.search(r"^\d", cand):
                    return cand
        return ""

    def before_slash(s):
        parts = [p.strip() for p in s.split("/")]
        return parts[0] if parts else ""

    def after_slash(s):
        parts = [p.strip() for p in s.split("/")]
        return parts[1] if len(parts) > 1 else ""

    sent_raw      = get_value(r"отправлено")
    delivered_raw = get_value(r"доставлено")
    opened_raw    = get_value(r"прочитано")
    clicks_raw    = get_value(r"переходы")
    unsub_raw     = get_value(r"отписок")
    spam_raw      = get_value(r"жалобы на спам")

    m = re.search(r"(\d[\d ]*)\s*недоставленных", page_text, re.IGNORECASE)
    undelivered_raw = m.group(1).strip() if m else "0"

    ctr_unique   = to_int(before_slash(clicks_raw) or clicks_raw)
    clicks_total = to_int(after_slash(clicks_raw))
    if clicks_total == "" and ctr_unique != "":
        clicks_total = ctr_unique

    return {
        "sent":         to_int(sent_raw),
        "delivered":    to_int(delivered_raw),
        "opened":       to_int(before_slash(opened_raw) or opened_raw),
        "ctr_unique":   ctr_unique,
        "clicks_total": clicks_total,
        "unsub":        to_int(unsub_raw),
        "spam":         to_int(spam_raw),
        "undelivered":  to_int(undelivered_raw),
    }


def parse_device_lines(page_text):
    lines = [l.strip() for l in page_text.split("\n")]

    def get_value(label_pattern):
        for i, line in enumerate(lines):
            if not re.search(label_pattern, line, re.IGNORECASE):
                continue
            if i >= 2:
                mid  = lines[i - 1]
                cand = lines[i - 2]
                if re.search(r"\d+[.,]?\d*\s*%", mid) and re.search(r"^\d", cand):
                    return cand
            if i >= 1:
                cand = lines[i - 1]
                if re.search(r"^\d", cand):
                    return cand
        return ""

    return {
        "desktop": to_int(get_value(r"десктоп")),
        "tablet":  to_int(get_value(r"планшет")),
        "mobile":  to_int(get_value(r"мобильн")),
    }


def validate_stats(stats):
    warnings = []
    s  = stats.get("sent")      or 0
    d  = stats.get("delivered") or 0
    o  = stats.get("opened")    or 0
    cu = stats.get("ctr_unique")   or 0
    ct = stats.get("clicks_total") or 0
    if not s:
        warnings.append("Отправлено = пусто  статистика не найдена")
    if s and d and d > s:
        warnings.append(f"Доставлено ({d}) > Отправлено ({s})")
    if s and d and d < s * 0.5:
        warnings.append(f"Доставлено ({d}) < 50% от Отправлено ({s})")
    if d and o and o > d:
        warnings.append(f"Прочитано ({o}) > Доставлено ({d})")
    if cu and ct and cu > ct:
        warnings.append(f"Уник. переходов ({cu}) > Всего переходов ({ct})")
    return warnings


#  Selenium 

def make_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # На Streamlit Cloud Chrome запускается headless
    if os.environ.get("STREAMLIT_CLOUD") or not os.environ.get("DISPLAY", True):
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    return webdriver.Chrome(options=options)


def login(driver, email, password):
    driver.get("https://app.unisender.com/ru/v5/spa/login")
    time.sleep(3)

    wait = WebDriverWait(driver, 20)
    ef = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[placeholder='Введите email']")
    ))
    ef.clear()
    ef.send_keys(email)
    time.sleep(0.5)

    pf = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Введите пароль']")
    pf.clear()
    pf.send_keys(password)
    time.sleep(0.5)

    # Пробуем кликнуть кнопку "Войти" автоматически
    try:
        btn = driver.find_element(
            By.XPATH,
            "//button[@type='submit' or contains(.,'Войти') or contains(.,'войти')]"
        )
        btn.click()
        yield "Нажата кнопка Войти..."
    except:
        yield "Кнопка Войти не найдена — нажмите вручную в браузере (60 сек)"

    try:
        WebDriverWait(driver, 60).until(lambda d: "login" not in d.current_url)
        yield f"Вход выполнен: {driver.current_url}"
    except:
        yield "Не удалось войти за 60 секунд"
        raise RuntimeError("login_timeout")


def get_all_campaigns(driver):
    driver.get("https://app.unisender.com/ru/v5/spa/campaigns")
    time.sleep(5)

    result = {}
    page   = 1

    while True:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/campaigns/']"))
            )
        except:
            break

        for card_link in driver.find_elements(By.CSS_SELECTOR, "a[href*='/campaigns/']"):
            try:
                url = card_link.get_attribute("href") or ""
                if not re.search(r"/campaigns/\d+", url):
                    continue
                url = re.sub(r"\?.*$", "", url)

                subject = card_link.text.strip()
                if not subject:
                    continue

                wrapper_lines = []
                for levels_up in ["..", "../..", "../../..", "../../../.."]:
                    try:
                        wrapper = card_link.find_element(By.XPATH, levels_up)
                        wt = wrapper.text
                        if re.search(r"\d{1,2}:\d{2}", wt):
                            wrapper_lines = [l.strip() for l in wt.split("\n") if l.strip()]
                            break
                    except:
                        continue

                segment_ru = ""
                for line in wrapper_lines:
                    for list_name in LIST_NAMES_BY_LEN:
                        if list_name in line:
                            segment_ru = LIST_TO_SEGMENT[list_name]
                            break
                    if segment_ru:
                        break

                if subject and segment_ru:
                    result[(subject, segment_ru)] = url

            except:
                continue

        try:
            nb = driver.find_element(
                By.XPATH,
                "//button[contains(@aria-label,'след') or contains(@class,'next')][not(@disabled)]"
            )
            nb.click()
            time.sleep(3)
            page += 1
        except:
            break

    return result


def parse_campaign_page(driver, url, subject):
    driver.get(url + "?tab=review")
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(text(),'отправлено') or contains(text(),'Отправлено')]")
            )
        )
    except:
        time.sleep(5)

    text  = driver.find_element(By.TAG_NAME, "body").text
    stats = parse_stat_lines(text)

    # Вкладка "Поведение получателей" (?tab=behavior)
    driver.get(url + "?tab=behavior")
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH,
                 "//*[contains(text(),'десктоп') or contains(text(),'Десктоп')"
                 " or contains(text(),'мобильный') or contains(text(),'Мобильный')]")
            )
        )
    except:
        time.sleep(4)

    t2 = driver.find_element(By.TAG_NAME, "body").text
    stats.update(parse_device_lines(t2))

    return stats


#  Google Sheets 

DEFAULT_COLS = {
    "sent":          "G", "delivered":     "H", "delivered_pct": "I",
    "opened":        "J", "opened_pct":    "K",
    "ctr_unique":    "L", "ctr_pct":       "M",
    "clicks_total":  "N", "clicks_pct":    "O",
    "unsub":         "P", "unsub_pct":     "Q",
    "spam":          "R", "spam_pct":      "S",
    "undelivered":   "T",
    "desktop":       "U", "tablet":        "V", "mobile":  "W",
    "weekday":       "X", "time":          "Y",
}


def write_stats(sheet, row_num, stats, weekday, cols=None):
    """Записывает статистику. cols — словарь {ключ: буква_столбца}."""
    r  = row_num
    c  = {**DEFAULT_COLS, **(cols or {})}

    cG = c["sent"];        cH = c["delivered"];    cI = c["delivered_pct"]
    cJ = c["opened"];      cK = c["opened_pct"]
    cL = c["ctr_unique"];  cM = c["ctr_pct"]
    cN = c["clicks_total"]; cO = c["clicks_pct"]
    cP = c["unsub"];       cQ = c["unsub_pct"]
    cR = c["spam"];        cS = c["spam_pct"]
    cT = c["undelivered"]
    cU = c["desktop"];     cV = c["tablet"];       cW = c["mobile"]
    cX = c["weekday"];     cY = c["time"]

    def div(a, b):
        return f"={a}{r}/{b}{r}"

    def v(key):
        val = stats.get(key, "")
        return val if isinstance(val, int) else ""

    updates = [
        {"range": f"{cG}{r}", "values": [[v("sent")        ]]},
        {"range": f"{cH}{r}", "values": [[v("delivered")   ]]},
        {"range": f"{cI}{r}", "values": [[div(cH, cG)      ]]},
        {"range": f"{cJ}{r}", "values": [[v("opened")      ]]},
        {"range": f"{cK}{r}", "values": [[div(cJ, cH)      ]]},
        {"range": f"{cL}{r}", "values": [[v("ctr_unique")  ]]},
        {"range": f"{cM}{r}", "values": [[div(cL, cH)      ]]},
        {"range": f"{cN}{r}", "values": [[v("clicks_total")]]},
        {"range": f"{cO}{r}", "values": [[div(cN, cH)      ]]},
        {"range": f"{cP}{r}", "values": [[v("unsub")       ]]},
        {"range": f"{cQ}{r}", "values": [[div(cP, cH)      ]]},
        {"range": f"{cR}{r}", "values": [[v("spam")        ]]},
        {"range": f"{cS}{r}", "values": [[div(cR, cH)      ]]},
        {"range": f"{cT}{r}", "values": [[v("undelivered") ]]},
        {"range": f"{cU}{r}", "values": [[v("desktop")     ]]},
        {"range": f"{cV}{r}", "values": [[v("tablet")      ]]},
        {"range": f"{cW}{r}", "values": [[v("mobile")      ]]},
        {"range": f"{cX}{r}", "values": [[weekday           ]]},
        {"range": f"{cY}{r}", "values": [["10:00"           ]]},
    ]
    sheet.batch_update(updates, value_input_option="USER_ENTERED")

    # Форматируем столбцы с процентами
    try:
        requests = []
        for pct_col in [cI, cK, cM, cO, cQ, cS]:
            col_idx = ord(pct_col) - ord("A")
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex":    r - 1,
                        "endRowIndex":      r,
                        "startColumnIndex": col_idx,
                        "endColumnIndex":   col_idx + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "PERCENT", "pattern": "0.00%"}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat"
                }
            })
        sheet.spreadsheet.batch_update({"requests": requests})
    except:
        pass


#  Главный генератор 

def run_parser(account):
    """
    Генератор: выдаёт строки лога.
    Использование:
        for msg in run_parser(account):
            print(msg)
    """
    email      = account["unisender_email"]
    password   = account["unisender_password"]
    sheet_name = account["sheet_name"]
    worksheet  = account["worksheet"]
    cols       = account.get("cols", None)
    # Обратная совместимость: старые аккаунты могли хранить start_col
    if cols is None and "start_col" in account:
        sc = account["start_col"]
        cols = {k: chr(ord(sc) + i) for i, k in enumerate(DEFAULT_COLS.keys())}
    g_col = (cols or DEFAULT_COLS).get("sent", "G")

    _setup_proxy()

    # Google Sheets
    yield "Подключаюсь к Google Sheets..."
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = _get_google_creds(scopes)
    client = gspread.authorize(creds)

    spreadsheet = client.open(sheet_name)
    try:
        sheet = spreadsheet.worksheet(worksheet)
    except gspread.WorksheetNotFound:
        available = [ws.title for ws in spreadsheet.worksheets()]
        yield f"Лист '{worksheet}' не найден. Доступные: {available}"
        return

    rows = sheet.get_all_values()
    # Индекс столбца "Отправлено" (G=6, H=7, ...)
    g_idx = ord(g_col.upper()) - ord("A")

    pending = []
    for i, row in enumerate(rows[1:], start=2):
        a = row[0].strip() if len(row) > 0 else ""
        c = row[2].strip() if len(row) > 2 else ""
        d = row[3].strip() if len(row) > 3 else ""
        g = row[g_idx].strip() if len(row) > g_idx else ""
        if a and c and d and not g:
            pending.append({"row_num": i, "date": a, "segment": c, "subject": d})

    if not pending:
        yield "Нечего заполнять — все строки уже заполнены!"
        return

    yield f"Найдено строк для заполнения: {len(pending)}"
    for p in pending:
        yield f"   [{p['row_num']}] {p['date']} | {p['segment']:<12} | {p['subject'][:45]}"

    # Selenium
    yield "\nОткрываю браузер..."
    driver = make_driver()

    try:
        yield "Вхожу в Unisender..."
        for msg in login(driver, email, password):
            yield msg

        yield "\nЗагружаю список рассылок из Unisender..."
        campaigns = get_all_campaigns(driver)
        yield f"   Найдено кампаний: {len(campaigns)}"

        filled    = 0
        not_found = []

        for item in pending:
            subject  = item["subject"]
            segment  = item["segment"]
            row_num  = item["row_num"]
            date_str = item["date"]

            key = (subject, segment)
            url = campaigns.get(key)

            if not url:
                yield f"[{row_num}] НЕ НАЙДЕНО: '{subject[:45]}' | {segment}"
                not_found.append(f"{subject[:45]} | {segment}")
                continue

            yield f"\n[{row_num}] {subject[:45]} | {segment}"
            stats   = parse_campaign_page(driver, url, subject)
            weekday = get_weekday(date_str)

            yield (f"   отпр={stats['sent']}, дост={stats['delivered']}, "
                   f"прочит={stats['opened']}, клики={stats['ctr_unique']}/{stats['clicks_total']}, "
                   f"дскт={stats.get('desktop')}, план={stats.get('tablet')}, моб={stats.get('mobile')}")

            warnings = validate_stats(stats)
            for w in warnings:
                yield f"   ! ПРОВЕРЬТЕ: {w}"

            write_stats(sheet, row_num, stats, weekday, cols)
            yield f"   Записано в строку {row_num}"
            filled += 1
            time.sleep(1)

    except RuntimeError as e:
        yield f"Прерван: {e}"
    except Exception as e:
        import traceback
        yield f"Ошибка: {e}"
        yield traceback.format_exc()
    finally:
        driver.quit()
        yield "\nБраузер закрыт"

    yield f"\n{'='*50}"
    yield f"Заполнено строк: {filled}"
    if not_found:
        yield f"Не найдено в Unisender ({len(not_found)}):"
        for nf in not_found:
            yield f"   - {nf}"


