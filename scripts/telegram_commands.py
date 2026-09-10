"""定期執行(規格書 4.2 第 1 項):抓 Telegram 新訊息,處理指令 / 履歷上傳 / 履歷編修對話。

指令總表見規格書第 5 節。分三類:
  搜尋條件  → 改 config.json
  履歷 / 需求 / 簽證 → 改 profile/ 或 config.json
  履歷編修 agent(Phase 6)→ 走 resume_agent
"""
import os
import sys
from html import escape as _escape

import requests

import dotenv_bootstrap  # noqa: F401  # 必須最先：讓 .env 在其他模組讀 os.environ 前生效

import resume_agent
from resume_agent import build_diff
from resume_files import extract_text, list_history, read_history_version, undo_last
from utils import (
    STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    answer_callback_query, get_file_bytes, load_config, load_json, load_offset,
    load_preferences, load_resume, save_config, save_json, save_offset,
    save_preferences, save_resume, send_telegram_message,
)

BOT_STATE_PATH = os.path.join(STATE_DIR, "bot_state.json")

VALID_EMPLOYMENT_TYPES = {"FULLTIME", "PARTTIME", "CONTRACTOR", "INTERN", "ANY"}
VALID_DATE_POSTED = {"today", "3days", "week", "month", "all"}
VALID_VISA_POLICY = {"exclude", "flag", "ignore"}

HELP_TEXT = (
    "<b>搜尋條件</b>\n"
    "/status - 查看目前搜尋條件\n"
    "/add_location 地點 | /remove_location 地點\n"
    "/add_keyword 關鍵字 | /remove_keyword 關鍵字\n"
    "/set_jobtype FULLTIME|PARTTIME|CONTRACTOR|INTERN|ANY\n"
    "/set_remote on|off\n"
    "/set_dateposted today|3days|week|month|all\n"
    "/set_min_score 0-100 - 最低匹配分數門檻\n"
    "/set_visa 狀態 - 簽證/工作權利狀態(比對硬條件)\n"
    "/set_visa_policy exclude|flag|ignore - 簽證不符職缺的處理方式\n\n"
    "<b>履歷 / 需求</b>\n"
    "/upload_resume [文字] - 貼文字或接著上傳 .txt/.pdf/.docx\n"
    "/set_preferences 文字 - 設定求職需求描述\n"
    "/show_profile - 查看目前履歷摘要、需求與簽證狀態\n\n"
    "<b>履歷編修(AI agent)</b>\n"
    "/resume_chat | /resume_chat_end - 進入 / 離開對話式編修模式\n"
    "/edit_resume 指示 - 單次請 agent 依指示修改\n"
    "/resume_history | /resume_undo | /resume_diff\n\n"
    "/help - 顯示這個說明"
)


# ---------------------------------------------------------------------------
# bot_state:目前是否在等使用者補上履歷內容
# ---------------------------------------------------------------------------
def load_bot_state():
    return load_json(BOT_STATE_PATH, {"awaiting": None})


def save_bot_state(state):
    save_json(BOT_STATE_PATH, state)


def set_awaiting(value):
    save_bot_state({"awaiting": value})


# ---------------------------------------------------------------------------
# 搜尋條件指令
# ---------------------------------------------------------------------------
def format_status(cfg):
    return (
        "<b>目前搜尋條件</b>\n"
        f"地點: {_escape(', '.join(cfg['locations'])) or '(無)'}\n"
        f"關鍵字: {_escape(', '.join(cfg['keywords'])) or '(無)'}\n"
        f"職缺類型: {cfg['employment_type']}\n"
        f"只看遠端工作: {'是' if cfg['remote_only'] else '否'}\n"
        f"發布時間範圍: {cfg['date_posted']}\n"
        f"每個關鍵字最多抓: {cfg['max_results_per_keyword']} 筆\n"
        f"最低匹配分數: {cfg['min_match_score']}\n"
        f"簽證狀態: {_escape(cfg.get('visa_status') or '') or '(未設定)'}\n"
        f"簽證不符處理: {cfg.get('visa_policy', 'flag')}"
    )


def handle_config_command(cmd, arg, cfg):
    """回傳 (是否有修改, 回覆文字);不是搜尋條件指令則回 None。"""
    if cmd == "/status":
        return False, format_status(cfg)

    if cmd == "/add_location":
        if not arg:
            return False, "請提供地點,例如: /add_location Sydney"
        if arg not in cfg["locations"]:
            cfg["locations"].append(arg)
        return True, f"已新增地點: {_escape(arg)}\n\n{format_status(cfg)}"

    if cmd == "/remove_location":
        if arg in cfg["locations"]:
            cfg["locations"].remove(arg)
            return True, f"已移除地點: {_escape(arg)}\n\n{format_status(cfg)}"
        return False, f"目前地點清單中沒有: {_escape(arg)}"

    if cmd == "/add_keyword":
        if not arg:
            return False, "請提供關鍵字,例如: /add_keyword Data Scientist"
        if arg not in cfg["keywords"]:
            cfg["keywords"].append(arg)
        return True, f"已新增關鍵字: {_escape(arg)}\n\n{format_status(cfg)}"

    if cmd == "/remove_keyword":
        if arg in cfg["keywords"]:
            cfg["keywords"].remove(arg)
            return True, f"已移除關鍵字: {_escape(arg)}\n\n{format_status(cfg)}"
        return False, f"目前關鍵字清單中沒有: {_escape(arg)}"

    if cmd == "/set_jobtype":
        val = arg.upper()
        if val not in VALID_EMPLOYMENT_TYPES:
            return False, f"職缺類型只能是: {', '.join(VALID_EMPLOYMENT_TYPES)}"
        cfg["employment_type"] = val
        return True, f"職缺類型已設為: {val}\n\n{format_status(cfg)}"

    if cmd == "/set_remote":
        val = arg.lower()
        if val not in ("on", "off"):
            return False, "請用 /set_remote on 或 /set_remote off"
        cfg["remote_only"] = (val == "on")
        return True, f"只看遠端工作已設為: {val}\n\n{format_status(cfg)}"

    if cmd == "/set_dateposted":
        val = arg.lower()
        if val not in VALID_DATE_POSTED:
            return False, f"發布時間範圍只能是: {', '.join(VALID_DATE_POSTED)}"
        cfg["date_posted"] = val
        return True, f"發布時間範圍已設為: {val}\n\n{format_status(cfg)}"

    if cmd == "/set_min_score":
        try:
            score = max(0, min(100, int(arg)))
        except ValueError:
            return False, "請提供 0-100 的整數,例如: /set_min_score 60"
        cfg["min_match_score"] = score
        return True, f"最低匹配分數門檻已設為: {score}\n\n{format_status(cfg)}"

    if cmd == "/set_visa":
        if not arg:
            return False, ("請描述你的簽證 / 工作權利狀態,例如:\n"
                           "/set_visa 澳洲 PR\n"
                           "/set_visa 學生簽,畢業後可申請 485\n"
                           "/set_visa 需要雇主 sponsorship")
        cfg["visa_status"] = arg
        return True, f"簽證狀態已設為: {_escape(arg)}\n\n{format_status(cfg)}"

    if cmd == "/set_visa_policy":
        val = arg.lower()
        if val not in VALID_VISA_POLICY:
            return False, "只能是: exclude(直接排除) / flag(標記但仍推播) / ignore(不判斷)"
        cfg["visa_policy"] = val
        return True, f"簽證不符處理方式已設為: {val}\n\n{format_status(cfg)}"

    return None


# ---------------------------------------------------------------------------
# 履歷 / 需求 / 簽證指令
# ---------------------------------------------------------------------------
def _profile_summary():
    resume, prefs = load_resume(), load_preferences()
    cfg = load_config()
    preview = (resume[:400] + " …") if len(resume) > 400 else resume
    return (
        "<b>目前 Profile</b>\n\n"
        f"<b>履歷</b>({len(resume)} 字):\n{_escape(preview) if preview else '(尚未上傳)'}\n\n"
        f"<b>求職需求</b>:\n{_escape(prefs) if prefs else '(尚未設定)'}\n\n"
        f"<b>簽證狀態</b>:{_escape(cfg.get('visa_status') or '') or '(尚未設定,請用 /set_visa)'}"
    )


def handle_profile_command(cmd, arg):
    """回傳回覆文字;不是這類指令則回 None。"""
    if cmd == "/upload_resume":
        if arg:
            save_resume(arg)
            set_awaiting(None)
            return f"已更新履歷({len(arg)} 字)。可用 /show_profile 確認。"
        set_awaiting("resume")
        return "請「接著」貼上履歷文字,或上傳 .txt / .pdf / .docx 檔案。"

    if cmd == "/set_preferences":
        if not arg:
            return "請提供求職需求描述,例如: /set_preferences 想找資料科學方向,可遠端,避免博弈產業"
        save_preferences(arg)
        return "已更新求職需求描述。"

    if cmd == "/show_profile":
        return _profile_summary()

    return None


# ---------------------------------------------------------------------------
# 履歷編修 agent 指令(Phase 6)
# ---------------------------------------------------------------------------
def handle_agent_command(cmd, arg):
    """回傳 (回覆文字, reply_markup 或 None);不是這類指令則回 None。"""
    if cmd == "/resume_chat":
        resume_agent.set_chat_mode(True)
        return ("已進入履歷編修模式。直接用中文描述你要的修改即可,"
                "我會提出修改提案讓你按鈕確認。輸入 /resume_chat_end 離開。", None)

    if cmd == "/resume_chat_end":
        resume_agent.set_chat_mode(False)
        return ("已離開履歷編修模式。", None)

    if cmd == "/edit_resume":
        if not arg:
            return ("請在指令後面寫要改什麼,例如: /edit_resume 把工作經歷改成條列式並強調量化成果", None)
        return resume_agent.handle_message(arg)

    if cmd == "/resume_history":
        return (resume_agent.history_text(), None)

    if cmd == "/resume_undo":
        name = undo_last()
        if not name:
            return ("沒有可還原的歷史版本。", None)
        return (f"已還原到 {name} 的內容(還原前的版本也已另外備份)。", None)

    if cmd == "/resume_diff":
        history = list_history()
        if not history:
            return ("沒有可比較的歷史版本。", None)
        diff = build_diff(read_history_version(history[0][0]), load_resume())
        return (f"目前履歷 vs 上一版:\n<pre>{_escape(diff)}</pre>", None)

    return None


# ---------------------------------------------------------------------------
# 檔案上傳
# ---------------------------------------------------------------------------
def handle_document(document):
    filename = document.get("file_name", "")
    fn = _escape(filename)
    data = get_file_bytes(document["file_id"])
    if data is None:
        return "檔案下載失敗,請再試一次。"
    try:
        text = extract_text(filename, data)
    except Exception as exc:  # noqa: BLE001
        return f"檔案解析失敗({fn}):{_escape(str(exc))}"
    if not text or not text.strip():
        return f"無法從 {fn or '這個檔案'} 取得文字內容,請改貼純文字或換個檔案。"
    save_resume(text)
    set_awaiting(None)
    return f"已從 {fn or '上傳的檔案'} 更新履歷({len(text.strip())} 字)。"


# ---------------------------------------------------------------------------
# callback query(inline button)
# ---------------------------------------------------------------------------
def handle_callback(callback):
    data = callback.get("data", "")
    chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
    if TELEGRAM_CHAT_ID and chat_id and chat_id != str(TELEGRAM_CHAT_ID):
        answer_callback_query(callback["id"], "未授權")
        return

    if data == "resume_apply":
        reply = resume_agent.apply_pending()
    elif data == "resume_discard":
        reply = resume_agent.discard_pending()
    elif data == "resume_continue":
        reply = resume_agent.continue_editing()
    else:
        reply = None

    answer_callback_query(callback["id"])
    if reply:
        send_telegram_message(reply)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def process_message(message, cfg):
    """回傳是否有改到 config.json。"""
    chat_id = str(message["chat"]["id"])
    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        print(f"忽略來自未授權 chat_id 的訊息: {chat_id}")
        return False

    text = (message.get("text") or message.get("caption") or "").strip()
    document = message.get("document")
    awaiting = load_bot_state().get("awaiting")

    # 1) 檔案上傳(等待履歷中,或 caption 是 /upload_resume)
    if document and (awaiting == "resume" or text.startswith("/upload_resume")):
        send_telegram_message(handle_document(document))
        return False

    if not text:
        return False

    # 2) 正在等履歷純文字
    if awaiting == "resume" and not text.startswith("/"):
        save_resume(text)
        set_awaiting(None)
        send_telegram_message(f"已更新履歷({len(text)} 字)。")
        return False

    # 3) 履歷編修模式中的自由對話
    if not text.startswith("/") and resume_agent.load_session().get("chat_mode"):
        reply, markup = resume_agent.handle_message(text)
        send_telegram_message(reply, reply_markup=markup)
        return False

    if not text.startswith("/"):
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    # 去掉 @botname 後綴(群組中指令會帶)
    cmd = cmd.split("@", 1)[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/help", "/start"):
        send_telegram_message(HELP_TEXT)
        return False

    agent_result = handle_agent_command(cmd, arg)
    if agent_result is not None:
        reply, markup = agent_result
        send_telegram_message(reply, reply_markup=markup)
        return False

    profile_reply = handle_profile_command(cmd, arg)
    if profile_reply is not None:
        send_telegram_message(profile_reply)
        return False

    config_result = handle_config_command(cmd, arg, cfg)
    if config_result is not None:
        changed, reply = config_result
        if reply:
            send_telegram_message(reply)
        return changed

    return False  # 不認得的指令,忽略


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("缺少 TELEGRAM_BOT_TOKEN,結束。")
        sys.exit(1)

    offset = load_offset()
    resp = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
        params={"offset": offset + 1, "timeout": 0,
                "allowed_updates": '["message", "edited_message", "callback_query"]'},
        timeout=20,
    )
    resp.raise_for_status()
    updates = resp.json().get("result", [])
    if not updates:
        print("沒有新訊息。")
        return

    cfg = load_config()
    config_changed = False
    # 只把 offset 推進到「連續成功處理」的最後一則。某則出錯就停在那裡,
    # 下次執行從它開始重試,避免訊息(尤其履歷編修指令遇到 claude 限流時)被跳過永久遺失。
    # 注意:若某則訊息每次都失敗,佇列會卡住 —— 需手動改 state/telegram_offset.json 跳過。
    processed_through = offset

    for update in updates:
        try:
            if "callback_query" in update:
                handle_callback(update["callback_query"])
            else:
                message = update.get("message") or update.get("edited_message")
                if message:
                    config_changed |= process_message(message, cfg)
        except Exception as exc:  # noqa: BLE001 - 停在第一個出錯的 update,下次重試
            print(f"[警告] 處理 update {update['update_id']} 失敗,停在此處待下次重試: {exc}")
            break
        processed_through = update["update_id"]

    save_offset(processed_through)
    if config_changed:
        save_config(cfg)
        print("設定已更新。")
    else:
        print("設定沒有變更。")


if __name__ == "__main__":
    main()
