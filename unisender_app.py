"""
Streamlit-приложение: Unisender -> Google Sheets
Запуск: python -m streamlit run unisender_app.py
"""

import streamlit as st
import json
import os

ACCOUNTS_FILE = "accounts.json"
GOOGLE_JSON   = "credentials.json"

st.set_page_config(
    page_title="Unisender → Sheets",
    page_icon=None,
    layout="centered",
)

st.markdown("""
<style>
    .block-container { padding-top: 2rem; max-width: 780px; }
    h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 0; }
    .subtitle { color: #888; font-size: 0.85rem; margin-bottom: 1.5rem; }
    .account-card {
        border: 1px solid #e0e0e0;
        border-radius: 6px;
        padding: 1rem 1.2rem;
        margin-bottom: 1rem;
        background: #fafafa;
    }
    .account-card p { margin: 0.15rem 0; font-size: 0.85rem; color: #555; }
    .account-card strong { color: #222; }
    .col-table { font-size: 0.8rem; color: #777; line-height: 1.8; margin-top: 0.5rem; }
    .svc-email {
        font-size: 0.78rem;
        word-break: break-all;
        background: #f4f4f4;
        border-radius: 4px;
        padding: 6px 8px;
        font-family: monospace;
        color: #333;
        margin-top: 4px;
    }
</style>
""", unsafe_allow_html=True)


# ── Конфигурация столбцов ─────────────────────────────────────────────────────

# (название поля, ключ в account["cols"], значение по умолчанию, это формула?)
COL_DEFS = [
    ("Отправлено",     "sent",          "G", False),
    ("Доставлено",     "delivered",     "H", False),
    ("Доставлено %",   "delivered_pct", "I", True),
    ("Прочитано",      "opened",        "J", False),
    ("Открыто %",      "opened_pct",    "K", True),
    ("УП CTR",         "ctr_unique",    "L", False),
    ("УП %",           "ctr_pct",       "M", True),
    ("Переходы",       "clicks_total",  "N", False),
    ("Переходы %",     "clicks_pct",    "O", True),
    ("Отписки",        "unsub",         "P", False),
    ("Отписки %",      "unsub_pct",     "Q", True),
    ("Спам",           "spam",          "R", False),
    ("Спам %",         "spam_pct",      "S", True),
    ("Недоставлено",   "undelivered",   "T", False),
    ("Декстоп",        "desktop",       "U", False),
    ("Планшет",        "tablet",        "V", False),
    ("Мобильный",      "mobile",        "W", False),
    ("День недели",    "weekday",       "X", False),
    ("Время",          "time",          "Y", False),
]

def default_cols():
    return {key: default for _, key, default, _ in COL_DEFS}

def get_cols(account):
    return account.get("cols", default_cols())


# ── Утилиты ──────────────────────────────────────────────────────────────────

def load_accounts():
    # Streamlit Cloud: берём из Secrets
    from_secrets = load_accounts_from_secrets()
    if from_secrets:
        return from_secrets
    # Локально: берём из файла
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def get_service_email():
    """Читает email сервисного аккаунта из Secrets или credentials.json."""
    try:
        return st.secrets["gcp_service_account"]["client_email"]
    except Exception:
        pass
    try:
        with open("credentials.json", encoding="utf-8") as f:
            return json.load(f).get("client_email", "")
    except Exception:
        return ""


def load_accounts_from_secrets():
    """Читает аккаунты из Streamlit Secrets (для деплоя на облако)."""
    try:
        raw = st.secrets["accounts"]
        return [dict(a) for a in raw]
    except Exception:
        return []


# ── Модальное окно: добавить аккаунт ─────────────────────────────────────────

@st.dialog("Добавить аккаунт", width="large")
def modal_add_account():
    with st.form("add_account_form"):
        st.markdown("**Данные аккаунта**")
        c1, c2 = st.columns(2)
        with c1:
            new_name  = st.text_input("Название", placeholder="СМУ Казахстан")
            new_email = st.text_input("Email Unisender")
        with c2:
            new_pass  = st.text_input("Пароль Unisender", type="password")
            new_sheet = st.text_input("Google Sheet", placeholder="СМУ. Реестр email-рассылок")
        new_ws = st.text_input("Лист", placeholder="Реестр Email-рассылок СМУ")

        st.divider()
        st.markdown("**Столбцы**")
        st.caption("Укажите букву столбца для каждого поля. Поля с формулой заполняются автоматически.")

        cols_input = {}

        # Рисуем таблицу: 4 пары (поле + буква) в строке
        rows_of_fields = [COL_DEFS[i:i+2] for i in range(0, len(COL_DEFS), 2)]
        for row in rows_of_fields:
            form_cols = st.columns([3, 1, 3, 1])
            for j, (label, key, default, is_formula) in enumerate(row):
                with form_cols[j * 2]:
                    tag = " (формула)" if is_formula else ""
                    st.markdown(
                        f"<div style='padding-top:32px;font-size:0.85rem;color:#{'999' if is_formula else '222'}'>"
                        f"{label}{tag}</div>",
                        unsafe_allow_html=True
                    )
                with form_cols[j * 2 + 1]:
                    cols_input[key] = st.text_input(
                        label,
                        value=default,
                        max_chars=2,
                        label_visibility="hidden",
                        key=f"col_{key}",
                        disabled=is_formula,
                    )

        st.divider()
        submitted = st.form_submit_button("Сохранить", type="primary", use_container_width=True)

        if submitted:
            if new_name and new_email and new_pass and new_sheet and new_ws:
                accounts = load_accounts()

                # Для формульных столбцов берём дефолт (они disabled и не меняются)
                final_cols = {}
                for label, key, default, is_formula in COL_DEFS:
                    final_cols[key] = default if is_formula else (cols_input.get(key, default).strip().upper() or default)

                accounts.append({
                    "name":               new_name,
                    "unisender_email":    new_email,
                    "unisender_password": new_pass,
                    "sheet_name":         new_sheet,
                    "worksheet":          new_ws,
                    "cols":               final_cols,
                })
                save_accounts(accounts)
                st.rerun()
            else:
                st.error("Заполните все поля")


# ── Боковая панель ────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Аккаунты")
    accounts = load_accounts()

    # Доступ к таблице
    svc = get_service_email()
    if svc:
        with st.expander("Как дать доступ к таблице"):
            st.markdown("Добавьте этот email как **редактора** в настройках доступа Google Sheet:")
            st.markdown(f'<div class="svc-email">{svc}</div>', unsafe_allow_html=True)
            st.caption("Таблица → Настройки доступа → Добавить → роль «Редактор»")

    st.divider()

    if st.button("+ Добавить аккаунт", use_container_width=True):
        modal_add_account()

    st.divider()

    if accounts:
        with st.expander("Удалить аккаунт"):
            to_del = st.selectbox("Аккаунт", [a["name"] for a in accounts],
                                  key="del", label_visibility="collapsed")
            if st.button("Удалить", type="secondary"):
                accounts = [a for a in accounts if a["name"] != to_del]
                save_accounts(accounts)
                st.rerun()


# ── Основной экран ────────────────────────────────────────────────────────────

st.markdown("# Unisender → Google Sheets")
st.markdown('<p class="subtitle">Перенос статистики рассылок</p>', unsafe_allow_html=True)

accounts = load_accounts()

if not accounts:
    st.info("Добавьте аккаунт в боковой панели")
    st.stop()

col_sel, col_btn = st.columns([4, 1])
with col_sel:
    selected = st.selectbox("Аккаунт", [a["name"] for a in accounts],
                            label_visibility="collapsed")
with col_btn:
    run = st.button("Запустить", type="primary", use_container_width=True)

account = next(a for a in accounts if a["name"] == selected)
cols    = get_cols(account)

col_map = " &nbsp;&middot;&nbsp; ".join(
    f"<b>{cols.get(key, default)}</b>&nbsp;{label}"
    for label, key, default, _ in COL_DEFS
)

st.markdown(f"""
<div class="account-card">
    <p><strong>Unisender</strong>&nbsp;&nbsp;{account['unisender_email']}</p>
    <p><strong>Таблица</strong>&nbsp;&nbsp;&nbsp;&nbsp;{account['sheet_name']} / {account['worksheet']}</p>
    <p class="col-table">{col_map}</p>
</div>
""", unsafe_allow_html=True)

st.divider()

if run:
    log_area = st.empty()
    logs = []

    def append(msg):
        logs.append(msg)
        log_area.text_area("", value="\n".join(logs), height=420,
                           label_visibility="collapsed", key=f"l{len(logs)}")

    append("Запуск...")

    try:
        from parser_core import run_parser  # импортируем только здесь
        for msg in run_parser(account):
            append(msg)
        st.success("Готово")
    except Exception as e:
        import traceback
        st.error(f"Ошибка: {e}")
        st.code(traceback.format_exc())
