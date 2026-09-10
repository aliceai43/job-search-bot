"""每天執行一次(規格書 4.2 第 2 項)。

流程:
  讀 config + 履歷 + 求職需求
  → 依「關鍵字 × 地點」呼叫 JSearch API 取得候選職缺
  → 與 seen_jobs.json 比對,篩掉已推播過的
  → 關鍵字快速預篩(省 AI 成本)
  → 逐一呼叫 AI 匹配模組,取得 match_score / 符合落差 / 簽證判斷
  → 依 visa_policy 處理簽證不符的職缺(exclude / flag / ignore)
  → 過濾低於 min_match_score 的職缺
  → 依分數排序(簽證不符者在同分內往後)
  → 送出「今日總覽」+ 逐則職缺 → 更新 seen_jobs.json / recent_jobs.json

需要環境變數:
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / RAPIDAPI_KEY / CLAUDE_CODE_OAUTH_TOKEN
  (另需已安裝 `claude` CLI:npm install -g @anthropic-ai/claude-code)
"""
import os
import sys
import time
from html import escape

import requests

import dotenv_bootstrap  # noqa: F401  # 必須最先：讓 .env 在其他模組讀 os.environ 前生效

from matcher import evaluate_job, keyword_prefilter
from utils import (
    load_config, load_preferences, load_resume,
    load_seen_jobs, save_seen_jobs, save_recent_jobs, send_telegram_message,
)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
JSEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"
JSEARCH_HOST = "jsearch.p.rapidapi.com"


# ---------------------------------------------------------------------------
# 職缺搜尋(JSearch /search-v2)
# ---------------------------------------------------------------------------
def build_query(keyword, location):
    return f"{keyword} in {location}" if location else keyword


def _normalise_job(job):
    """補上舊程式碼會用到、但 /search-v2 可能改名或拿掉的欄位。"""
    if not job.get("job_city") and job.get("job_location"):
        job["job_city"] = job["job_location"]
    if job.get("job_is_remote") is None:
        job["job_is_remote"] = bool(job.get("job_work_from_home"))
    return job


def search_jobs(keyword, location, cfg):
    params = {
        "query": build_query(keyword, location),
        "page": "1",
        "num_pages": "1",
        "country": cfg.get("country") or "au",
        "date_posted": cfg.get("date_posted", "today"),
    }
    if cfg.get("employment_type", "ANY") != "ANY":
        params["employment_types"] = cfg["employment_type"]
    if cfg.get("remote_only"):
        params["work_from_home"] = "true"

    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": JSEARCH_HOST}
    resp = requests.get(JSEARCH_URL, headers=headers, params=params, timeout=30)
    if resp.status_code != 200:
        print(f"[警告] 查詢失敗 ({keyword} / {location}): {resp.status_code} {resp.text[:200]}")
        return []

    payload = resp.json().get("data") or {}
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    jobs = jobs[:cfg.get("max_results_per_keyword", 10)]
    return [_normalise_job(j) for j in jobs]


def collect_new_jobs(cfg, seen_ids):
    new_jobs, batch_seen = [], set()
    for keyword in cfg["keywords"]:
        for location in cfg["locations"]:
            for job in search_jobs(keyword, location, cfg):
                job_id = job.get("job_id")
                if job_id and job_id not in seen_ids and job_id not in batch_seen:
                    batch_seen.add(job_id)
                    new_jobs.append(job)
            time.sleep(1)  # 放慢一點,避免超過 API 速率限制
    return new_jobs


# ---------------------------------------------------------------------------
# 簽證判斷 / 排序
# ---------------------------------------------------------------------------
def apply_visa_policy(results, policy):
    """回傳 (保留的結果, 被 exclude 剔除的數量)。flag 模式只加註記,不在這裡丟。"""
    if policy == "ignore":
        for r in results:
            r["visa_fit"] = "unknown"
        return results, 0
    if policy == "exclude":
        kept = [r for r in results if r["visa_fit"] != "not_eligible"]
        return kept, len(results) - len(kept)
    return results, 0  # flag:全部保留


def sort_key(result):
    # 分數高在前;同分時簽證不符者往後
    return (-result["match_score"], result["visa_fit"] == "not_eligible")


# ---------------------------------------------------------------------------
# 推播格式(規格書 3.4)
# ---------------------------------------------------------------------------
def _visa_line(r):
    req = (r.get("visa_requirement") or "未提及").strip()
    if r["visa_fit"] == "not_eligible":
        tag = "要求 PR／公民" if r["pr_or_citizen_required"] else req
        return f"🛂 簽證：{escape(tag)}（⚠️ 與你的狀態不符）"
    if r["visa_fit"] == "eligible" and req not in ("未提及", ""):
        return f"🛂 簽證：{escape(req)}（符合）"
    return "🛂 簽證：職缺未提及，投遞前請自行向雇主確認"


def format_result(r):
    lines = []
    if r["visa_fit"] == "not_eligible":
        lines.append("🛂 <b>簽證不符</b>")
    lines.append(f"🏆 匹配度：{r['match_score']}/100")
    lines.append(f"💼 職稱：{escape(r.get('job_title') or '（無標題）')}")
    lines.append(f"🏢 公司：{escape(r.get('employer_name') or '（未知公司）')}")
    if r.get("job_city"):
        lines.append(f"📍 地點：{escape(r['job_city'])}")
    lines.append(_visa_line(r))
    if r.get("job_apply_link"):
        lines.append(f'🔗 <a href="{escape(r["job_apply_link"])}">前往應徵</a>')

    if r["matched_requirements"]:
        lines.append("\n✅ 符合的條件：")
        lines += [f"- {escape(x)}" for x in r["matched_requirements"]]
    if r["missing_requirements"]:
        lines.append("\n⚠️ 有落差的條件：")
        lines += [f"- {escape(x)}" for x in r["missing_requirements"]]
    if r["summary"]:
        lines.append(f"\n📝 摘要：\n{escape(r['summary'])}")
    return "\n".join(lines)


def format_overview(results, evaluated, prefiltered_out, visa_excluded, failed=0):
    scores = [r["match_score"] for r in results]
    avg = round(sum(scores) / len(scores)) if scores else 0
    top = max(results, key=lambda r: r["match_score"]) if results else None
    visa_bad = sum(1 for r in results if r["visa_fit"] == "not_eligible")

    lines = [
        "📋 <b>今日職缺總覽</b>",
        f"評估 {evaluated} 筆,通過門檻並推播 {len(results)} 筆",
    ]
    if prefiltered_out:
        lines.append(f"關鍵字預篩略過 {prefiltered_out} 筆")
    if visa_excluded:
        lines.append(f"簽證不符已剔除 {visa_excluded} 筆")
    if failed:
        lines.append(f"比對失敗 {failed} 筆(下次執行會重試)")
    if results:
        lines.append(f"平均匹配度 {avg} 分,最高 {top['match_score']} 分"
                     f"（{escape(top.get('job_title') or '')}）")
    if visa_bad:
        lines.append(f"其中 {visa_bad} 筆簽證不符(仍列出供參考)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    if not RAPIDAPI_KEY:
        print("缺少 RAPIDAPI_KEY,結束。")
        sys.exit(1)
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        print("缺少 CLAUDE_CODE_OAUTH_TOKEN(或 ANTHROPIC_API_KEY),結束。")
        sys.exit(1)

    cfg = load_config()
    if not cfg["locations"] or not cfg["keywords"]:
        send_telegram_message("⚠️ 目前沒有設定任何地點或關鍵字,請用 /add_location 和 /add_keyword 設定後再試。")
        return

    resume = load_resume()
    preferences = load_preferences()
    visa_status = cfg.get("visa_status", "").strip()

    warnings = []
    if not resume:
        warnings.append("尚未上傳履歷(/upload_resume),比對只能非常保守地評分。")
    if not visa_status:
        warnings.append("尚未設定簽證狀態(/set_visa),所有職缺的簽證判斷會是「未知」。")

    seen_ids = load_seen_jobs()
    new_jobs = collect_new_jobs(cfg, seen_ids)

    if not new_jobs:
        msg = "📭 今天沒有找到新的職缺。"
        if warnings:
            msg += "\n\n提醒：\n- " + "\n- ".join(warnings)
        send_telegram_message(msg)
        return

    # 關鍵字預篩 → AI 比對。
    # 只有「預篩略過」或「比對成功」的職缺才標記 seen;比對失敗(claude 逾時 / 限流)的
    # 不標記,留到下次執行重試,避免暫時性錯誤造成職缺永久漏掉。
    results, prefiltered_out, failed = [], 0, 0
    for job in new_jobs:
        job_id = job.get("job_id")
        if not keyword_prefilter(job, resume, preferences):
            prefiltered_out += 1
            if job_id:
                seen_ids.add(job_id)
            continue
        result = evaluate_job(job, resume, preferences, visa_status)
        if result is None:
            failed += 1
            continue
        results.append(result)
        if job_id:
            seen_ids.add(job_id)
        time.sleep(1)  # AI 呼叫速率限制

    evaluated = len(results)

    # 簽證政策 → 分數門檻 → 排序
    policy = cfg.get("visa_policy", "flag")
    results, visa_excluded = apply_visa_policy(results, policy)
    min_score = cfg.get("min_match_score", 60)
    results = [r for r in results if r["match_score"] >= min_score]
    results.sort(key=sort_key)

    # 推播
    if warnings:
        send_telegram_message("⚠️ 提醒：\n- " + "\n- ".join(warnings))

    if not results:
        extra = f",比對失敗 {failed} 筆待下次重試" if failed else ""
        send_telegram_message(
            f"今天評估了 {evaluated} 筆新職缺,沒有任何一筆達到 {min_score} 分門檻"
            f"(關鍵字預篩略過 {prefiltered_out} 筆,簽證剔除 {visa_excluded} 筆{extra})。"
        )
    else:
        send_telegram_message(
            format_overview(results, evaluated, prefiltered_out, visa_excluded, failed)
        )
        for r in results:
            send_telegram_message(format_result(r))

    save_seen_jobs(seen_ids)
    if results:
        save_recent_jobs(results)


if __name__ == "__main__":
    main()
