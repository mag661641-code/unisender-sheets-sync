"""
Ядро парсера Unisender -> Google Sheets.
Используется и из app.py (Streamlit) и напрямую из unisender_parser.py.

run_parser(account)  генератор, выдаёт строки лога.
"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.firefox.service import Service as FirefoxService
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
    """Читает прокси из Streamlit Secrets или из локальных констант.
    Возвращает (user, pwd, host, port) для передачи в make_driver()."""
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
    return user, pwd, host, str(port)


def _start_local_proxy_relay(user, pwd, host, port):
    """Поднимает локальный HTTP(S)-прокси без авторизации, который сам
    добавляет Proxy-Authorization и перенаправляет трафик на внешний
    прокси. Нужен, потому что Chrome не поддерживает логин/пароль
    в --proxy-server, а расширения с Manifest V2 (chrome.webRequest
    onAuthRequired) современные версии Chrome/Chromium больше не грузят.
    Возвращает адрес локального прокси "127.0.0.1:PORT"."""
    import asyncio
    import base64
    import socket
    import threading

    upstream_host = host
    upstream_port = int(port)
    auth_header = base64.b64encode(f"{user}:{pwd}".encode()).decode()

    async def _pipe(reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _handle(client_reader, client_writer):
        try:
            request_line = await client_reader.readline()
            if not request_line:
                client_writer.close()
                return
            headers = []
            while True:
                line = await client_reader.readline()
                if line in (b"\r\n", b""):
                    break
                headers.append(line)

            upstream_reader = upstream_writer = None
            last_err = None
            for attempt in range(3):
                try:
                    upstream_reader, upstream_writer = await asyncio.wait_for(
                        asyncio.open_connection(upstream_host, upstream_port),
                        timeout=10,
                    )
                    break
                except Exception as e:
                    last_err = e
                    await asyncio.sleep(0.5 * (attempt + 1))
            if upstream_writer is None:
                # Апстрим-прокси недоступен после нескольких попыток —
                # отвечаем клиенту явной ошибкой вместо тихого обрыва,
                # чтобы Chrome не завис в ожидании и корректно считал
                # ресурс недоступным.
                try:
                    client_writer.write(
                        b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"
                    )
                    await client_writer.drain()
                except Exception:
                    pass
                client_writer.close()
                return

            method = request_line.split(b" ", 1)[0]
            if method == b"CONNECT":
                upstream_writer.write(request_line)
                for h in headers:
                    upstream_writer.write(h)
                upstream_writer.write(f"Proxy-Authorization: Basic {auth_header}\r\n".encode())
                upstream_writer.write(b"\r\n")
                await upstream_writer.drain()

                resp = await upstream_reader.readuntil(b"\r\n\r\n")
                client_writer.write(resp)
                await client_writer.drain()
            else:
                upstream_writer.write(request_line)
                for h in headers:
                    upstream_writer.write(h)
                upstream_writer.write(f"Proxy-Authorization: Basic {auth_header}\r\n".encode())
                upstream_writer.write(b"\r\n")
                await upstream_writer.drain()

            await asyncio.gather(
                _pipe(client_reader, upstream_writer),
                _pipe(upstream_reader, client_writer),
            )
        except Exception:
            try:
                client_writer.close()
            except Exception:
                pass

    def _run_server(ready_event, port_holder):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _main():
            server = await asyncio.start_server(_handle, "127.0.0.1", 0)
            port_holder.append(server.sockets[0].getsockname()[1])
            ready_event.set()
            async with server:
                await server.serve_forever()

        loop.run_until_complete(_main())

    ready = threading.Event()
    port_holder = []
    t = threading.Thread(target=_run_server, args=(ready, port_holder), daemon=True)
    t.start()
    ready.wait(timeout=5)
    return f"127.0.0.1:{port_holder[0]}"


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
    "Кыргызстан":  "Кыргызстан",
}
LIST_NAMES_BY_LEN = sorted(LIST_TO_SEGMENT.keys(), key=len, reverse=True)
DAYS_RU       = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
COUNTRY_ORDER = ["Россия", "Казахстан", "Беларусь", "Узбекистан", "Азербайджан", "Армения", "Кыргызстан"]


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
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]

    def get_value(label_pattern):
        for i, line in enumerate(lines):
            if not re.search(label_pattern, line, re.IGNORECASE):
                continue
            # Верстка карточек устройств не всегда даёт строгий порядок
            # "число -> процент -> подпись" — ищем чистое число (без %)
            # в небольшом окне вокруг подписи в обе стороны.
            for offset in (-1, -2, 1, 2, -3, 3):
                j = i + offset
                if 0 <= j < len(lines) and re.fullmatch(r"\d[\d ]*", lines[j]):
                    return lines[j]
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

def make_driver(proxy=None):
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    options.add_experimental_option(
        "prefs", {"profile.managed_default_content_settings.images": 2}
    )
    # На Streamlit Cloud (и любом сервере без дисплея) Chrome запускается headless
    chromium_bin = "/usr/bin/chromium"
    chromedriver_bin = "/usr/bin/chromedriver"
    headless = os.environ.get("STREAMLIT_CLOUD") or not os.environ.get("DISPLAY") or os.path.exists(chromium_bin)
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--window-size=1920,1080")

    if proxy:
        user, pwd, host, port = proxy
        local_proxy_addr = _start_local_proxy_relay(user, pwd, host, port)
        options.add_argument(f"--proxy-server=http://{local_proxy_addr}")

    if os.path.exists(chromium_bin):
        options.binary_location = chromium_bin
    if os.path.exists(chromedriver_bin):
        return webdriver.Chrome(service=Service(chromedriver_bin), options=options)
    return webdriver.Chrome(options=options)


def _ensure_geckodriver():
    """Debian/apt не поставляет geckodriver отдельным пакетом, поэтому
    при первом запуске скачиваем бинарник с GitHub releases в /tmp."""
    cached = "/tmp/geckodriver"
    if os.path.exists(cached):
        return cached
    if os.path.exists("/usr/bin/geckodriver"):
        return "/usr/bin/geckodriver"
    try:
        import urllib.request
        import tarfile
        import io
        url = "https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux64.tar.gz"
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extract("geckodriver", path="/tmp")
        os.chmod(cached, 0o755)
        return cached
    except Exception:
        return None


def make_firefox_driver(proxy=None):
    """Запасной вариант, если Chrome не может стартовать (например,
    контейнеру не хватает памяти для Chrome, но хватает для Firefox)."""
    options = webdriver.FirefoxOptions()
    firefox_bin      = "/usr/bin/firefox-esr" if os.path.exists("/usr/bin/firefox-esr") else "/usr/bin/firefox"
    geckodriver_bin  = _ensure_geckodriver()

    headless = os.environ.get("STREAMLIT_CLOUD") or not os.environ.get("DISPLAY") or os.path.exists(firefox_bin)
    if headless:
        options.add_argument("--headless")
    options.set_preference("permissions.default.image", 2)

    if proxy:
        user, pwd, host, port = proxy
        local_proxy_addr = _start_local_proxy_relay(user, pwd, host, port)
        relay_host, relay_port = local_proxy_addr.split(":")
        options.set_preference("network.proxy.type", 1)
        options.set_preference("network.proxy.http", relay_host)
        options.set_preference("network.proxy.http_port", int(relay_port))
        options.set_preference("network.proxy.ssl", relay_host)
        options.set_preference("network.proxy.ssl_port", int(relay_port))
        options.set_preference("network.proxy.no_proxies_on", "localhost,127.0.0.1")

    if os.path.exists(firefox_bin):
        options.binary_location = firefox_bin
    if geckodriver_bin and os.path.exists(geckodriver_bin):
        return webdriver.Firefox(service=FirefoxService(geckodriver_bin), options=options)
    return webdriver.Firefox(options=options)


def login(driver, email, password):
    driver.get("https://app.unisender.com/ru/v5/spa/login")

    ef = None
    for reload_attempt in range(2):
        wait = WebDriverWait(driver, 20)
        try:
            ef = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[placeholder='Введите email']")
            ))
            break
        except Exception:
            if reload_attempt == 1:
                # Показываем подробности только если и вторая попытка не помогла
                yield f"   Не удалось открыть страницу входа. URL: {driver.current_url}"
                yield f"   Заголовок страницы: {driver.title!r}"
                raise
            # Первая загрузка часто не укладывается в 5с таймаут монтирования
            # SPA из-за задержек прокси; при перезагрузке ассеты уже в кэше
            # браузера и монтирование обычно укладывается в таймаут.
            yield "   Страница не загрузилась с первого раза, пробую ещё раз..."
            driver.get("https://app.unisender.com/ru/v5/spa/login")
    assert ef is not None
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
    """Генератор: yield-ит диагностические строки, в конце yield-ит
    финальный результат как {"result": dict}."""
    driver.get("https://app.unisender.com/ru/v5/spa/campaigns")

    result = {}
    page   = 1

    while True:
        def _has_campaign_link(d):
            for el in d.find_elements(By.CSS_SELECTOR, "a[href*='/campaigns/']"):
                href = el.get_attribute("href") or ""
                if re.search(r"/campaigns/\d+", href):
                    return True
            return False

        try:
            WebDriverWait(driver, 25).until(_has_campaign_link)
        except Exception:
            if page == 1:
                yield f"   Текущий URL: {driver.current_url}"
                yield f"   Заголовок страницы: {driver.title!r}"
                yield f"   Начало HTML: {driver.page_source[:500]!r}"
            break

        links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/campaigns/']")
        stats = {"links": len(links), "matched_id": 0, "has_subject": 0,
                 "has_wrapper": 0, "has_segment": 0}

        for card_link in links:
            try:
                url = card_link.get_attribute("href") or ""
                if not re.search(r"/campaigns/\d+", url):
                    continue
                stats["matched_id"] += 1
                url = re.sub(r"\?.*$", "", url)

                subject = card_link.text.strip()
                if not subject:
                    continue
                stats["has_subject"] += 1

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
                if wrapper_lines:
                    stats["has_wrapper"] += 1

                segment_ru = ""
                for line in wrapper_lines:
                    for list_name in LIST_NAMES_BY_LEN:
                        if list_name in line:
                            segment_ru = LIST_TO_SEGMENT[list_name]
                            break
                    if segment_ru:
                        break
                if segment_ru:
                    stats["has_segment"] += 1

                if subject and segment_ru:
                    result[(subject, segment_ru)] = url

            except:
                continue

        if page == 1:
            yield (f"   Диагностика стр.1: ссылок={stats['links']}, "
                   f"с ID кампании={stats['matched_id']}, с темой={stats['has_subject']}, "
                   f"с блоком времени={stats['has_wrapper']}, с сегментом={stats['has_segment']}")
            if stats["links"] and not stats["has_wrapper"]:
                try:
                    sample = links[0].find_element(By.XPATH, "../../..").text
                    yield f"   Пример текста обёртки (3 уровня вверх): {sample[:300]!r}"
                except Exception as ex:
                    yield f"   Не удалось получить пример обёртки: {ex}"

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

    yield f"   Кампаний в Unisender найдено: {len(result)}"

    yield {"result": result}


def parse_campaign_page(driver, url, subject):
    max_load_attempts = 4
    for load_attempt in range(max_load_attempts):
        driver.get(url + "?tab=review")
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(),'отправлено') or contains(text(),'Отправлено')]")
                )
            )
            # Блок "N недоставленных" на этой же вкладке подгружается отдельным
            # запросом и для больших рассылок появляется позже основных цифр
            # (отправлено/доставлено/прочитано). Даём ему время подгрузиться.
            time.sleep(2)
        except:
            time.sleep(5)

        text  = driver.find_element(By.TAG_NAME, "body").text
        stats = parse_stat_lines(text)
        stats["_review_text"] = text[:1200]

        # Иногда страница кампании вообще не открывается (доезжает только
        # левое меню, без самой карточки статистики, или вообще пусто) —
        # тогда "отправлено" не найдётся вовсе. Это похоже на разрыв
        # соединения через прокси при быстрой последовательной загрузке
        # страниц, поэтому пробуем перезагрузить несколько раз с паузой.
        if stats.get("sent") != "":
            break
        if load_attempt < max_load_attempts - 1:
            time.sleep(3 * (load_attempt + 1))

    # Если сошлось "доставлено < отправлено" (то есть недоставленные
    # реально есть), а блок ещё не подгрузился и дал 0 — ждём ещё и
    # перечитываем страницу.
    sent_val      = stats.get("sent")
    delivered_val = stats.get("delivered")
    sent_n      = sent_val if isinstance(sent_val, int) else 0
    delivered_n = delivered_val if isinstance(delivered_val, int) else 0
    if sent_n and delivered_n and sent_n > delivered_n and not stats.get("undelivered"):
        time.sleep(3)
        text  = driver.find_element(By.TAG_NAME, "body").text
        stats = parse_stat_lines(text)
        stats["_review_text"] = text[:1200]

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
        # Подписи ("десктоп"/"мобильный") появляются раньше самих цифр —
        # цифры подгружаются отдельно диаграммой, и для больших рассылок
        # это занимает заметно больше времени. Даём им время подгрузиться.
        time.sleep(2)
    except:
        time.sleep(5)

    t2 = driver.find_element(By.TAG_NAME, "body").text
    stats.update(parse_device_lines(t2))

    # Если после паузы цифры так и не появились (очень большая рассылка,
    # диаграмма ещё рендерится) — пробуем ещё раз чуть подождать.
    if stats.get("desktop") == "" and stats.get("tablet") == "" and stats.get("mobile") == "":
        time.sleep(3)
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


# Для каждого процентного поля — от какого поля считается (числитель, знаменатель)
PCT_SOURCE = {
    "delivered_pct": ("delivered",    "sent"),
    "opened_pct":    ("opened",       "delivered"),
    "ctr_pct":       ("ctr_unique",   "delivered"),
    "clicks_pct":    ("clicks_total", "delivered"),
    "unsub_pct":     ("unsub",        "delivered"),
    "spam_pct":      ("spam",         "delivered"),
}


def write_stats(sheet, row_num, stats, weekday, cols=None):
    """Записывает статистику. cols — словарь {ключ: буква_столбца}.
    Пустая буква (или отсутствие ключа) значит "не записывать это поле" —
    так можно отключить отдельные метрики для конкретного аккаунта."""
    r = row_num
    c = {**DEFAULT_COLS, **(cols or {})}

    def letter(key):
        val = (c.get(key) or "").strip().upper()
        return val or None

    def v(key):
        val = stats.get(key, "")
        return val if isinstance(val, int) else ""

    updates = []
    pct_cols_to_format = []

    for key in ("sent", "delivered", "opened", "ctr_unique", "clicks_total",
                "unsub", "spam", "undelivered", "desktop", "tablet", "mobile"):
        col = letter(key)
        if col:
            updates.append({"range": f"{col}{r}", "values": [[v(key)]]})

    for pct_key, (num_key, den_key) in PCT_SOURCE.items():
        pct_col = letter(pct_key)
        num_col = letter(num_key)
        den_col = letter(den_key)
        if pct_col and num_col and den_col:
            updates.append({"range": f"{pct_col}{r}", "values": [[f"={num_col}{r}/{den_col}{r}"]]})
            pct_cols_to_format.append(pct_col)

    wd_col = letter("weekday")
    if wd_col:
        updates.append({"range": f"{wd_col}{r}", "values": [[weekday]]})
    time_col = letter("time")
    if time_col:
        updates.append({"range": f"{time_col}{r}", "values": [["10:00"]]})

    if updates:
        sheet.batch_update(updates, value_input_option="USER_ENTERED")

    # Форматируем столбцы с процентами
    if pct_cols_to_format:
        try:
            requests = []
            for pct_col in pct_cols_to_format:
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
    g_col = ((cols or DEFAULT_COLS).get("sent") or "").strip().upper()
    if not g_col:
        yield ("Столбец 'Отправлено' не может быть пустым — по нему скрипт "
               "определяет, какие строки уже заполнены. Задайте букву столбца "
               "для этого поля в настройках аккаунта.")
        return

    proxy = _setup_proxy()

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
        b = row[1].strip() if len(row) > 1 else ""  # страна — сопоставляется с сегментом кампании в Unisender
        d = row[3].strip() if len(row) > 3 else ""
        g = row[g_idx].strip() if len(row) > g_idx else ""
        if a and b and d and not g:
            pending.append({"row_num": i, "date": a, "segment": b, "subject": d})

    if not pending:
        yield "Нечего заполнять — все строки уже заполнены!"
        return

    yield f"Найдено строк для заполнения: {len(pending)}"
    for p in pending:
        yield f"   [{p['row_num']}] {p['date']} | {p['segment']:<12} | {p['subject'][:45]}"

    # Убиваем зависшие chrome/chromedriver от предыдущих неудачных запусков —
    # на маленьком контейнере Streamlit Cloud они копятся и съедают память,
    # из-за чего новый Chrome падает с "session not created: Chrome instance exited".
    try:
        import subprocess
        for pattern in ["chromedriver", "chrome.*--headless", "geckodriver", "firefox.*headless"]:
            subprocess.run(["pkill", "-9", "-f", pattern], check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # Selenium
    # Первые попытки — Chrome; если он не может стартовать в принципе
    # (не хватает памяти на этом контейнере и т.п.), последняя попытка —
    # Firefox, у которого другой профиль потребления ресурсов.
    driver = None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        use_firefox = (attempt == max_attempts)
        browser_name = "Firefox" if use_firefox else "Chrome"
        yield f"\nОткрываю браузер {browser_name}... (попытка {attempt}/{max_attempts})"
        try:
            driver = make_firefox_driver(proxy=proxy) if use_firefox else make_driver(proxy=proxy)
            yield "Вхожу в Unisender..."
            for msg in login(driver, email, password):
                yield msg
            break
        except Exception as e:
            yield f"   Сбой при входе (вероятно, проблема с прокси/сетью): {e!r}"
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            driver = None
            if attempt == max_attempts:
                raise RuntimeError(
                    "Не удалось войти в Unisender после нескольких попыток. "
                    "Похоже, прокси нестабилен на этой сети — попробуйте ещё раз "
                    "чуть позже или запустите без прокси."
                ) from e
            time.sleep(3)

    filled    = 0
    not_found = []
    results   = []  # (row_num, segment, ok: bool, note: str)

    try:
        yield "\nЗагружаю список рассылок из Unisender..."
        campaigns = {}
        for msg in get_all_campaigns(driver):
            if isinstance(msg, dict):
                campaigns = msg["result"]
            else:
                yield msg

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
                results.append((row_num, segment, False, "рассылка не найдена в Unisender"))
                continue

            yield f"\n[{row_num}] {subject[:45]} | {segment}"
            stats   = parse_campaign_page(driver, url, subject)
            weekday = get_weekday(date_str)

            yield (f"   отпр={stats['sent']}, дост={stats['delivered']}, "
                   f"прочит={stats['opened']}, клики={stats['ctr_unique']}/{stats['clicks_total']}, "
                   f"дскт={stats.get('desktop')}, план={stats.get('tablet')}, моб={stats.get('mobile')}")

            if not stats.get("sent"):
                yield f"   Диагностика (текст страницы 'Обзор'): {stats.get('_review_text', '')!r}"
            stats.pop("_review_text", None)

            warnings = validate_stats(stats)
            for w in warnings:
                yield f"   ! ПРОВЕРЬТЕ: {w}"

            write_stats(sheet, row_num, stats, weekday, cols)
            yield f"   Записано в строку {row_num}"
            filled += 1
            if stats.get("sent"):
                results.append((row_num, segment, True, ""))
            else:
                results.append((row_num, segment, False, "не удалось прочитать статистику"))
            time.sleep(2)

    except RuntimeError as e:
        yield f"Прерван: {e}"
    except Exception as e:
        import traceback
        yield f"Ошибка: {e}"
        yield traceback.format_exc()
    finally:
        driver.quit()
        yield "\nБраузер закрыт"

    ok_count  = sum(1 for _, _, ok, _ in results if ok)
    bad_count = len(results) - ok_count

    yield f"\n{'='*50}"
    yield f"ИТОГ: успешно {ok_count} из {len(results)}"
    for row_num, segment, ok, note in results:
        mark = "✓" if ok else "✗"
        suffix = f" — {note}" if note else ""
        yield f"   {mark} [{row_num}] {segment}{suffix}"
    if bad_count == 0 and results:
        yield "Все строки заполнены успешно."


