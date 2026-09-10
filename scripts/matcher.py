"""AI 職缺匹配模組(規格書 3.3)。

對一筆職缺呼叫一次 LLM,輸出結構化比對結果:
  match_score / matched_requirements / missing_requirements /
  visa_requirement / visa_requirement_type / pr_or_citizen_required /
  visa_fit / visa_note / summary
"""
import re

from llm import chat_json

MAX_DESC_CHARS = 6000  # job_description 過長先截斷,控制 context 與成本
_WORD_RE = re.compile(r"[a-z][a-z+#.]{2,}")

VISA_TYPES = {
    "open", "work_rights_only", "sponsorship_available", "no_sponsorship",
    "pr_or_citizen", "citizen_only", "unknown",
}

SYSTEM_PROMPT = """你是求職媒合助理。根據「使用者履歷」+「求職需求」+「職缺說明」做客觀比對,輸出純 JSON。

嚴格規則:
1. 只根據履歷已寫明的事實比對,不要幫使用者腦補沒寫的經歷。
2. 只輸出 JSON,不要有任何多餘文字或 markdown 圍籬。
3. 一定要檢查簽證 / PR / 工作權利:從職缺說明找出對工作權利、簽證、PR(永久居留)、
   公民身分、sponsorship、security clearance 的要求。注意「permanent resident」「PR」
   「citizen」「must have full working rights」「no sponsorship」「security clearance」
   這類字眼。要求 PR 或公民時 pr_or_citizen_required 必為 true。職缺沒寫就填
   「未提及」/ "unknown",禁止臆測。若與使用者的簽證狀態衝突(含「有工簽但非 PR、
   而職缺要 PR」),visa_fit 必為 "not_eligible",並在 summary 說明卡在哪一關。
4. match_score 評分標準:
   90-100 幾乎完全符合所有硬性條件;70-89 符合大部分,有 1-2 項不符;
   50-69 符合基本方向,缺少數項重要條件;0-49 方向不符或缺太多必要條件。

輸出 JSON 欄位:
{
  "match_score": <int 0-100>,
  "matched_requirements": [<string>, ...],
  "missing_requirements": [<string>, ...],
  "visa_requirement": <string，職缺原文摘要，未提及填「未提及」>,
  "visa_requirement_type": "open|work_rights_only|sponsorship_available|no_sponsorship|pr_or_citizen|citizen_only|unknown",
  "pr_or_citizen_required": <bool>,
  "visa_fit": "eligible|not_eligible|unknown",
  "visa_note": <string，一句話說明簽證/PR判斷理由>,
  "summary": <string，精簡摘要>
}"""


def _fmt_location(job):
    parts = [job.get("job_city"), job.get("job_state"), job.get("job_country")]
    loc = ", ".join(p for p in parts if p)
    return loc or job.get("job_location") or "(未提供)"


def _job_description(job):
    desc = (job.get("job_description") or "").strip()
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[:MAX_DESC_CHARS] + "\n...(內容過長,已截斷)"
    return desc


def keyword_prefilter(job, resume, preferences, min_overlap=1):
    """成本優化(規格書 7):用關鍵字快速篩掉明顯不相關的職缺。

    履歷 / 需求裡出現、且長度 >= 4 的英文詞,至少要有 min_overlap 個出現在職缺描述中,
    才值得送去做完整 AI 比對。資料不足(履歷或描述太短)時一律放行。
    """
    text = (resume + " " + preferences).lower()
    tokens = {w for w in _WORD_RE.findall(text) if len(w) >= 4}
    if len(tokens) < 8:
        return True
    desc = (_job_description(job) + " " + (job.get("job_title") or "")).lower()
    hits = sum(1 for t in tokens if t in desc)
    return hits >= min_overlap


def _normalise(result, job):
    """確保欄位齊全、型別正確,並補上必要的預設值。"""
    out = {
        "job_id": job.get("job_id"),
        "job_title": job.get("job_title"),
        "employer_name": job.get("employer_name"),
        "job_city": job.get("job_city"),
        "job_apply_link": job.get("job_apply_link"),
        "match_score": 0,
        "matched_requirements": [],
        "missing_requirements": [],
        "visa_requirement": "未提及",
        "visa_requirement_type": "unknown",
        "pr_or_citizen_required": False,
        "visa_fit": "unknown",
        "visa_note": "",
        "summary": "",
    }
    for key in list(out):
        if key in result and result[key] is not None:
            out[key] = result[key]

    try:
        out["match_score"] = max(0, min(100, int(out["match_score"])))
    except (TypeError, ValueError):
        out["match_score"] = 0

    if out["visa_requirement_type"] not in VISA_TYPES:
        out["visa_requirement_type"] = "unknown"
    if out["visa_fit"] not in {"eligible", "not_eligible", "unknown"}:
        out["visa_fit"] = "unknown"
    out["pr_or_citizen_required"] = bool(out["pr_or_citizen_required"]) or \
        out["visa_requirement_type"] in {"pr_or_citizen", "citizen_only"}

    for key in ("matched_requirements", "missing_requirements"):
        if not isinstance(out[key], list):
            out[key] = [str(out[key])]
        out[key] = [str(x) for x in out[key]]

    return out


def evaluate_job(job, resume, preferences, visa_status):
    """回傳規格書 4.3 定義的「單筆比對結果」dict;比對呼叫失敗時回 None
    (讓 job_scraper 保留該職缺、下次執行再重試,而不是永久漏掉)。"""
    user = (
        f"# 使用者履歷\n{resume or '(未提供,請保守評分)'}\n\n"
        f"# 求職需求\n{preferences or '(未提供)'}\n\n"
        f"# 使用者的簽證 / 工作權利狀態\n{visa_status or '(未設定,visa_fit 請填 unknown)'}\n\n"
        f"# 職缺\n"
        f"職稱:{job.get('job_title')}\n"
        f"公司:{job.get('employer_name')}\n"
        f"地點:{_fmt_location(job)}\n"
        f"類型:{job.get('job_employment_type')}\n"
        f"遠端:{job.get('job_is_remote')}\n\n"
        f"職缺說明:\n{_job_description(job)}"
    )
    try:
        raw = chat_json(SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001 - 單筆失敗不要拖垮整批,回 None 待重試
        print(f"[警告] 比對失敗({job.get('job_title')}):{exc}")
        return None
    return _normalise(raw, job)
