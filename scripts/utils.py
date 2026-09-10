"""共用工具函式:路徑、設定 / 履歷 / 狀態的讀寫、Telegram API 呼叫。"""
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(ROOT, "config.json")
PROFILE_DIR = os.path.join(ROOT, "profile")
RESUME_PATH = os.path.join(PROFILE_DIR, "resume.txt")
PREFERENCES_PATH = os.path.join(PROFILE_DIR, "preferences.txt")
HISTORY_DIR = os.path.join(PROFILE_DIR, "history")

STATE_DIR = os.path.join(ROOT, "state")
SEEN_JOBS_PATH = os.path.join(STATE_DIR, "seen_jobs.json")
OFFSET_PATH = os.path.join(STATE_DIR, "telegram_offset.json")
RECENT_JOBS_PATH = os.path.join(STATE_DIR, "recent_jobs.json")
RESUME_SESSION_PATH = os.path.join(STATE_DIR, "resume_chat_session.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

DEFAULT_CONFIG = {
    "locations": [],
    "keywords": [],
    "employment_type": "ANY",
    "remote_only": False,
    "date_posted": "today",
    "country": "au",
    "sources": [],
    "min_match_score": 60,
    "max_results_per_keyword": 10,
    "visa_status": "",
    "visa_policy": "flag",
}


# ---------------------------------------------------------------------------
# 通用 JSON 讀寫
# ---------------------------------------------------------------------------
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_text(path, default=""):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# config.json
# ---------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}))
    # 補齊舊設定檔缺少的欄位
    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, value)
    return cfg


def save_config(cfg):
    save_json(CONFIG_PATH, cfg)


# ---------------------------------------------------------------------------
# 履歷 / 求職需求
# ---------------------------------------------------------------------------
def load_resume():
    return read_text(RESUME_PATH).strip()


def load_preferences():
    return read_text(PREFERENCES_PATH).strip()


def save_resume(text):
    write_text(RESUME_PATH, text.strip() + "\n")


def save_preferences(text):
    write_text(PREFERENCES_PATH, text.strip() + "\n")


# ---------------------------------------------------------------------------
# 已推播職缺 / Telegram offset / 最近職缺
# ---------------------------------------------------------------------------
def load_seen_jobs():
    return set(load_json(SEEN_JOBS_PATH, {"seen_job_ids": []})["seen_job_ids"])


def save_seen_jobs(job_ids, cap=1000):
    ids = list(job_ids)[-cap:]  # 只保留最近 cap 筆,避免檔案無限長大
    save_json(SEEN_JOBS_PATH, {"seen_job_ids": ids})


def load_offset():
    return load_json(OFFSET_PATH, {"offset": 0})["offset"]


def save_offset(offset):
    save_json(OFFSET_PATH, {"offset": offset})


def load_recent_jobs():
    return load_json(RECENT_JOBS_PATH, {"jobs": []})["jobs"]


def save_recent_jobs(jobs, cap=40):
    save_json(RECENT_JOBS_PATH, {"jobs": jobs[-cap:]})


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------
def _telegram_api(method, payload, retries=3):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 環境變數")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    last = None
    for attempt in range(retries):
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            return resp.json()
        last = resp
        time.sleep(2)
    print(f"[警告] Telegram {method} 失敗: {last.status_code if last else '?'} "
          f"{last.text[:200] if last else ''}")
    return None


def send_telegram_message(text, chat_id=None, reply_markup=None):
    """傳送訊息給指定 chat_id(預設用環境變數),自動處理 4096 字上限的分段。"""
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if not chat_id:
        raise RuntimeError("缺少 TELEGRAM_CHAT_ID 環境變數")

    max_len = 3900
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]
    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # inline keyboard 只掛在最後一段
        if reply_markup and idx == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        _telegram_api("sendMessage", payload)
        time.sleep(0.4)  # 避免觸發 Telegram rate limit


def answer_callback_query(callback_query_id, text=""):
    _telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text,
    })


def get_file_bytes(file_id):
    """依 file_id 從 Telegram 下載檔案內容(bytes)。"""
    info = _telegram_api("getFile", {"file_id": file_id})
    if not info or not info.get("ok"):
        return None
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content
