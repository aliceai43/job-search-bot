"""Phase 6:對話式履歷編修(規格書 3.5 / 4.4)。

- 透過 llm.run_resume_agent 讓 Claude 讀履歷 / 求職需求 / 最近職缺,產出「回覆 + 修改提案」。
- 要改履歷一律走「提案 → 使用者按鈕確認 → 才落地」,寫入前先備份到 profile/history/。
- 對話狀態存 state/resume_chat_session.json,messages 只留最近 N 則純文字。
"""
import difflib
import time

from llm import run_resume_agent
from resume_files import apply_resume, list_history
from utils import (
    RESUME_SESSION_PATH, load_json, load_preferences, load_recent_jobs,
    load_resume, save_json,
)

MAX_HISTORY_MSGS = 20
MAX_DIFF_LINES = 60
RECENT_JOBS_FOR_AGENT = 10

SYSTEM_PROMPT = """你是使用者的「履歷顧問」,透過 Telegram 對話協助他修改、優化、客製化履歷。

規則:
- 只能依使用者提供的事實改寫,不得杜撰經歷、學歷、數字。不確定的量化成果要反問使用者,不要自己填。
- 保持原履歷的語言(中/英)與既有格式風格,除非使用者要求更改。
- 使用者若只是問問題或要 cover letter,直接用文字回答即可(可參考下面提供的最近職缺),不要硬提修改。
- 要修改履歷時,把完整新版放進 proposal.new_content,並用一句話說明改了什麼、為什麼。"""


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def _empty_session():
    return {"chat_mode": False, "updated_at": None, "messages": [], "pending_proposal": None}


def load_session():
    return load_json(RESUME_SESSION_PATH, _empty_session())


def save_session(session):
    session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    session["messages"] = session["messages"][-MAX_HISTORY_MSGS:]
    save_json(RESUME_SESSION_PATH, session)


def set_chat_mode(on):
    session = load_session()
    session["chat_mode"] = on
    if not on:
        session["pending_proposal"] = None
    save_session(session)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------
def build_diff(old_text, new_text):
    diff = list(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile="現在", tofile="修改後", lineterm="",
    ))
    if not diff:
        return "(內容沒有變化)"
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"... (省略 {len(diff) - MAX_DIFF_LINES} 行,套用後可用 /resume_diff 看完整差異)"]
    return "\n".join(diff)


CONFIRM_MARKUP = {
    "inline_keyboard": [[
        {"text": "✅ 套用", "callback_data": "resume_apply"},
        {"text": "❌ 放棄", "callback_data": "resume_discard"},
        {"text": "✏️ 繼續修改", "callback_data": "resume_continue"},
    ]]
}


# ---------------------------------------------------------------------------
# 對外:處理一則使用者訊息
# ---------------------------------------------------------------------------
def handle_message(user_text):
    """回傳 (回覆文字, reply_markup 或 None)。"""
    session = load_session()
    context = {
        "resume": load_resume(),
        "preferences": load_preferences(),
        "recent_jobs": load_recent_jobs()[-RECENT_JOBS_FOR_AGENT:],
    }

    try:
        reply, proposal = run_resume_agent(
            SYSTEM_PROMPT, session["messages"], user_text, context,
        )
    except Exception as exc:  # noqa: BLE001 - 回報給使用者即可
        return _escape(f"履歷編修失敗:{exc}"), None

    session["messages"].append({"role": "user", "content": user_text})
    session["messages"].append({"role": "assistant", "content": reply})

    # session 存乾淨文字;送 Telegram 前才做 HTML escape(訊息帶 parse_mode: HTML)
    out, markup = _escape(reply), None
    if proposal and proposal.get("new_content"):
        session["pending_proposal"] = {
            "new_content": proposal["new_content"],
            "change_summary": proposal.get("change_summary", ""),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        diff = build_diff(load_resume(), proposal["new_content"])
        out = (
            f"{_escape(reply)}\n\n"
            f"<b>變更摘要</b>:{_escape(proposal.get('change_summary', ''))}\n\n"
            f"<b>diff</b>:\n<pre>{_escape(diff)}</pre>\n\n"
            f"要套用嗎?"
        )
        markup = CONFIRM_MARKUP

    save_session(session)
    return out, markup


def apply_pending():
    """使用者按「套用」。回傳給使用者的訊息。"""
    session = load_session()
    proposal = session.get("pending_proposal")
    if not proposal:
        return "目前沒有待確認的修改提案。"
    backup = apply_resume(proposal["new_content"], proposal.get("change_summary", ""))
    session["pending_proposal"] = None
    session["messages"].append({
        "role": "assistant",
        "content": f"(已套用修改:{proposal.get('change_summary', '')})",
    })
    save_session(session)
    note = f",舊版已備份為 {backup.split('/')[-1]}" if backup else ""
    return f"✅ 履歷已更新{note}。可用 /resume_undo 還原、/resume_history 看版本。"


def discard_pending():
    session = load_session()
    session["pending_proposal"] = None
    save_session(session)
    return "已放棄這次修改提案,履歷維持原狀。"


def continue_editing():
    return "好的,請告訴我還要怎麼調整,我會再提一版。"


def history_text():
    history = list_history()
    if not history:
        return "目前沒有履歷歷史版本。"
    lines = ["<b>履歷歷史版本</b>(新到舊):"]
    for name, summary in history[:15]:
        lines.append(f"- {_escape(name)}" + (f" — {_escape(summary)}" if summary else ""))
    return "\n".join(lines)


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
