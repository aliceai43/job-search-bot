"""透過 Claude Code CLI(`claude -p`)呼叫 Claude。

用 Claude Pro/Max 訂閱額度,不是 API 按量計費:
  - 本機:先跑一次 `claude setup-token`,把值放進 .env 的 CLAUDE_CODE_OAUTH_TOKEN
  - GitHub Actions:同一個值放進程式碼 repo 的 Secret `CLAUDE_CODE_OAUTH_TOKEN`
`claude` CLI 需先安裝:`npm install -g @anthropic-ai/claude-code`

對外介面:
  chat_json(system, user)                                -> dict
  run_resume_agent(system, history, user_text, context)  -> (reply, proposal|None)

環境變數:
  CLAUDE_CODE_OAUTH_TOKEN  訂閱長效 token(或改設 ANTHROPIC_API_KEY,兩者都由 claude CLI 自行讀取)
  CLAUDE_BIN               claude 執行檔(預設 "claude")
  CLAUDE_MODEL             選填,傳給 `claude --model`,例如 claude-sonnet-5
  CLAUDE_TIMEOUT           單次呼叫逾時秒數(預設 300)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or "claude"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or ""
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT") or "300")


# ---------------------------------------------------------------------------
# 呼叫 claude CLI
# ---------------------------------------------------------------------------
def _run_claude(prompt, retries=2):
    """呼叫 `claude -p`,回傳模型輸出的純文字。非零結束或逾時會重試。"""
    if not (shutil.which(CLAUDE_BIN) or os.path.isfile(CLAUDE_BIN)):
        raise RuntimeError(
            f"找不到 claude CLI({CLAUDE_BIN})。請先 `npm install -g @anthropic-ai/claude-code`。"
        )

    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]

    # 在空的暫存資料夾執行,避免模型讀寫到專案檔案。
    workdir = tempfile.mkdtemp(prefix="claude-job-bot-")
    last_err = None
    try:
        for _ in range(retries + 1):
            try:
                proc = subprocess.run(
                    cmd, input="", capture_output=True, text=True,
                    timeout=CLAUDE_TIMEOUT, cwd=workdir,
                )
            except subprocess.TimeoutExpired:
                last_err = f"claude CLI 逾時({CLAUDE_TIMEOUT}s)"
                continue
            if proc.returncode == 0:
                return _extract_result(proc.stdout)
            last_err = f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:500]}"
        raise RuntimeError(last_err or "claude CLI 呼叫失敗")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _extract_result(stdout):
    """`--output-format json` 會包一層 envelope,取出 result 欄位;非 JSON 就原樣回傳。"""
    stdout = stdout.strip()
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise RuntimeError(f"claude CLI 回報錯誤:{str(envelope.get('result'))[:500]}")
        return str(envelope.get("result", "")).strip()
    return stdout


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------
def _parse_json_loose(text):
    """盡量把模型輸出解析成 dict:先直接 parse,失敗就去掉 ``` 圍籬 / 取第一個 {...}。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"無法解析成 JSON:{text[:300]}")


# ---------------------------------------------------------------------------
# 對外
# ---------------------------------------------------------------------------
def chat_json(system, user, max_tokens=2500, effort="low"):
    """單次呼叫,回傳解析後的 dict。max_tokens / effort 只為相容舊介面,不使用。"""
    prompt = (
        f"{system}\n\n"
        f"{user}\n\n"
        "只輸出符合上述格式的 JSON,不要有任何其他文字、說明或 markdown 圍籬。"
    )
    return _parse_json_loose(_run_claude(prompt))


_RESUME_OUTPUT_SPEC = """\
最後只輸出一段 JSON(前後不要有其他文字或圍籬):
{
  "reply": "<顯示給使用者的回覆;若有提修改,順帶用一句話說明改了什麼、為什麼>",
  "proposal": null 或 {
    "new_content": "<完整的新履歷全文>",
    "change_summary": "<一句話說明這次修改>"
  }
}
只有在使用者要求修改履歷、且你確實要動內容時才給 proposal,否則 proposal 為 null。
不得杜撰經歷、學歷、數字;不確定的量化成果要在 reply 反問使用者而不是自己填。"""


def run_resume_agent(system, history, user_text, context):
    """履歷編修:把所需資料直接放進 prompt(不走 tool use)。

    history:  list[{"role","content"}] 先前對話(純文字)
    context:  {"resume","preferences","recent_jobs"}
    回傳:     (reply_text, proposal_dict_or_None)
    """
    recent = json.dumps(context.get("recent_jobs") or [], ensure_ascii=False, indent=2)
    convo = "\n".join(
        f"{'使用者' if m.get('role') == 'user' else '你'}:{m.get('content', '')}"
        for m in history
    )
    prompt = (
        f"{system}\n\n"
        f"# 目前履歷\n{context.get('resume') or '(尚未上傳)'}\n\n"
        f"# 求職需求\n{context.get('preferences') or '(未設定)'}\n\n"
        f"# 最近推播的職缺(含比對結果)\n{recent}\n\n"
        f"# 先前對話\n{convo or '(無)'}\n\n"
        f"# 使用者這次的訊息\n{user_text}\n\n"
        f"{_RESUME_OUTPUT_SPEC}"
    )
    data = _parse_json_loose(_run_claude(prompt))
    reply = str(data.get("reply") or "").strip() or "(無回覆)"
    proposal = data.get("proposal")
    if isinstance(proposal, dict) and proposal.get("new_content"):
        return reply, {
            "new_content": str(proposal["new_content"]),
            "change_summary": str(proposal.get("change_summary") or ""),
        }
    return reply, None
