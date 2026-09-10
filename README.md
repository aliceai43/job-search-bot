# Job Alert Bot

每天自動搜尋職缺(LinkedIn / Indeed / Seek 等),用 AI 拿你的履歷跟每筆職缺做**語意配對、算匹配度、判斷簽證/PR 條件、依分數排序**,再推播到 Telegram。
你也可以直接在 Telegram 聊天室**跟 AI 對話修改履歷**。

對應規格書 `job-match-spec.md` 的 Phase 1~4 與 Phase 6。

---

## 功能

- **每日職缺掃描**:依你設定的「關鍵字 × 地點」呼叫職缺 API → 去重 → 關鍵字預篩 → 逐筆 AI 比對 → 簽證政策 → 分數門檻 → 排序 → 推播。
- **每筆職缺附上**:匹配度分數、你符合哪些要求、缺哪些要求、簽證/PR/sponsorship 需求與你是否符合、一句話摘要、應徵連結。
- **今日總覽**:找到幾筆、平均與最高匹配度、幾筆簽證不符、幾筆比對失敗待重試。
- **Telegram 即時調整**:地點、關鍵字、職缺類型、遠端、發布時間範圍、最低分數、簽證狀態、簽證政策,全部用指令改,不用重新部署。
- **履歷管理**:貼文字或上傳 `.txt` / `.pdf` / `.docx`;`/set_preferences` 補求職需求描述。
- **AI 履歷編修**(Phase 6):對話式請 AI 改寫履歷 → 顯示變更摘要 + diff + 按鈕確認 → 確認後才寫入,寫入前自動備份到 `profile/history/`,可 `/resume_undo` 還原。
- **簽證判斷貫穿全流程**:每筆都從職缺說明萃取工作權利要求,與你的 `visa_status` 比對,標記 `eligible` / `not_eligible` / `unknown`;`not_eligible` 依政策 `exclude` 剔除 / `flag` 標記置底 / `ignore` 不判斷。

---

## 運作方式

| 元件 | 說明 |
|---|---|
| **職缺資料來源** | [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)(RapidAPI,底層 Google for Jobs,聚合 LinkedIn / Indeed / Seek / Glassdoor)。用 `/search-v2` endpoint。 |
| **AI 比對 / 履歷編修** | 透過 **Claude Code CLI**(`claude -p`)呼叫 Claude,用 **Claude Pro/Max 訂閱額度**,不是 API 按量計費(`scripts/llm.py`)。 |
| **使用者介面** | Telegram Bot。bot 只回應設定好的那一個 chat id。 |
| **執行環境** | GitHub Actions 兩個排程 workflow。 |
| **資料儲存** | **個資與狀態不進程式碼 repo**,存在另一個**私有 data repo**,workflow 執行時拉下來、跑完再 commit 回去。 |

### 三個模組

- `scripts/job_scraper.py` — 每日搜尋 + 比對 + 排序 + 推播。
- `scripts/telegram_commands.py` — 處理 Telegram 指令、履歷上傳、履歷編修對話、按鈕回呼。
- `scripts/resume_agent.py` — Phase 6 履歷編修(提案 + diff + 確認後寫入 + 歷史/還原)。

### 兩個 workflow

- `.github/workflows/daily-scan.yml` — 每天一次,跑 `job_scraper.py`。
- `.github/workflows/poll-telegram.yml` — 每 15 分鐘,跑 `telegram_commands.py`。

兩者共用 `concurrency.group: job-bot-data`,不會同時寫 data repo。

### 為什麼分兩個 repo

履歷、求職需求、搜尋設定(`config.json`)、執行狀態(`state/`)都是個資,`.gitignore` 已把它們擋在程式碼 repo 外。workflow 靠 `scripts/data_sync.sh` 跟一個**私有** data repo 同步這些檔案。這樣程式碼 repo 可以設 public(GitHub Actions 對 public repo 免費),個資完全不進去。

---

## 需要準備

1. 一個 Telegram 帳號
2. RapidAPI 帳號(申請 JSearch 免費方案)
3. **Claude Pro 或 Max 訂閱**(履歷比對 / 編修要用)
4. GitHub 帳號
5. 設定時需要用一次終端機(本機電腦,或用手機開 GitHub Codespaces 的瀏覽器終端機)

---

## 設定步驟

### 1. 建立 Telegram Bot

1. Telegram 搜尋 **@BotFather** → 傳 `/newbot` → 取得 **Bot Token**。
2. 跟你的 bot 隨便傳一句話 → 開 `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` → 在 JSON 裡找 `"chat":{"id": ...}`,這串數字就是你的 **Chat ID**。

### 2. 申請 JSearch API Key

到 [RapidAPI - JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch),右上角 **Subscribe** → 選 **Basic (Free)**(免費方案也一定要按訂閱)。到 **Endpoints** 分頁複製 `X-RapidAPI-Key`。

> 免費方案約每月 200 次請求,且每個 query 通常只回 1 頁(~10 筆)。

### 3. 產生 Claude Code 訂閱 token

找一台能開終端機的機器(或手機開 [GitHub Codespaces](https://github.com/codespaces) 的瀏覽器終端機):

```bash
npm install -g @anthropic-ai/claude-code
claude setup-token          # 用你的 Pro/Max 帳號登入,複製印出來的 token
```

成功會印出一長串(通常 `sk-ant-oat01-...` 開頭)。這串就是下面的 `CLAUDE_CODE_OAUTH_TOKEN`。

驗證這串能用:

```bash
HOME=$(mktemp -d) CLAUDE_CODE_OAUTH_TOKEN="貼上token" claude -p "reply OK" --output-format json
```

回一段含 `"result"` 的 JSON 就代表有效。

> 也可以改用 Anthropic API key:設 `ANTHROPIC_API_KEY`(`sk-ant-...`)代替,`claude` CLI 會自動吃 —— 但那會變回按量計費。

### 4. 建立兩個 repo

**(a) 程式碼 repo**(建議 **public**,這樣 Actions 免費;不含任何個資):

```bash
cd job-alert-bot
git status                          # 確認沒有 .env / config.json / profile / state
git add -A && git commit -m "init job alert bot"
git branch -M main
git remote add origin https://github.com/<你的帳號>/<repo名>.git
git push -u origin main
```

**(b) 私有 data repo**(存履歷 / 設定 / 狀態,**一定要 private**):

在 GitHub 建一個 **private** repo,例如 `job-bot-data`,放個空 README 即可。
workflow 第一次執行會自動把 `config.json` / `profile/` / `state/` 補進去。

若你已在本機測試過(見下方),可以直接拿本機結果初始化:

```bash
git clone https://github.com/<你的帳號>/job-bot-data.git
cd job-bot-data
cp ../job-alert-bot/config.json .
cp -R ../job-alert-bot/profile ../job-alert-bot/state .
git add -A && git commit -m "seed from local test" && git push
```

### 5. 建立 data repo 的存取 token(PAT)

**GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token**:

- Repository access → **Only select repositories** → 只勾 `job-bot-data`
- Permissions → **Contents: Read and write**
- 產生後複製,下一步當 `DATA_REPO_TOKEN`。

### 6. 設定程式碼 repo 的 Secrets / Variables

**(程式碼 repo)Settings → Secrets and variables → Actions**:

| 類型 | 名稱 | 值 |
|---|---|---|
| Secret | `TELEGRAM_BOT_TOKEN` | 步驟 1 的 Bot Token |
| Secret | `TELEGRAM_CHAT_ID` | 步驟 1 的 Chat ID |
| Secret | `RAPIDAPI_KEY` | 步驟 2 的 API Key |
| Secret | `CLAUDE_CODE_OAUTH_TOKEN` | 步驟 3 的 token |
| Secret | `DATA_REPO_TOKEN` | 步驟 5 的 fine-grained PAT |
| Variable | `DATA_REPO` | 私有 data repo,例如 `你的帳號/job-bot-data` |
| Variable(選填) | `CLAUDE_MODEL` | 指定模型,例如 `claude-sonnet-5`;不填用訂閱預設 |

### 7.(選填)設定 Telegram 指令選單

到 **@BotFather** → `/setcommands` → 選你的 bot → 貼上:

```
status - 查看目前搜尋條件
add_location - 新增搜尋地點
remove_location - 移除搜尋地點
add_keyword - 新增關鍵字/職稱
remove_keyword - 移除關鍵字
set_jobtype - 職缺類型 FULLTIME/PARTTIME/CONTRACTOR/INTERN/ANY
set_remote - 只看遠端工作 on/off
set_dateposted - 發布時間 today/3days/week/month/all
set_min_score - 最低匹配分數 0-100
set_visa - 設定簽證/工作權利狀態
set_visa_policy - 簽證不符處理 exclude/flag/ignore
upload_resume - 上傳/更新履歷
set_preferences - 設定求職需求描述
show_profile - 查看履歷摘要/需求/簽證
resume_chat - 進入對話式履歷編修
resume_chat_end - 離開履歷編修
edit_resume - 單次請 agent 修改履歷
resume_history - 列出履歷備份版本
resume_undo - 還原上一版履歷
resume_diff - 顯示履歷與上一版差異
help - 顯示指令說明
```

之後在聊天室輸入 `/` 就會跳出指令提示。

### 8. 啟用 Actions 並初始化 profile

1. 到程式碼 repo 的 **Actions** 分頁,若提示則啟用 workflow。
2. 在 Telegram 對 bot 設定:
   ```
   /set_visa 你的簽證狀態,例如「485 畢業工簽,剩 2 年」
   /add_keyword Data Scientist
   /add_location Sydney
   /set_dateposted week
   /set_min_score 60
   /upload_resume            (接著貼履歷文字,或上傳檔案)
   /set_preferences 想找的方向、期望、想避免的產業
   /status
   ```
3. **先手動跑 `poll-telegram`**(Actions → poll-telegram → Run workflow)→ 它會把上面的設定 commit 進 data repo,bot 也會回覆你。
4. **再手動跑 `daily-scan`** → 確認有推播職缺。
5. 打開 data repo 的 commit 紀錄,確認 `config.json` / `profile/` / `state/` 有被更新回去。
6. 都正常後,排程自己接手。

---

## 本機測試

上 GitHub 前先在本機跑通(回饋最快)。

```bash
cd job-alert-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 編輯 .env 填 4 個值(見下)
cp config.example.json config.json

# 需要 claude CLI(訂閱已在本機登入的話直接可用)
npm install -g @anthropic-ai/claude-code

cd scripts
python3 -c "import llm; print(llm.chat_json('只輸出 JSON', '輸出 {\"ok\": true}'))"   # 測 claude 橋接
python3 telegram_commands.py   # 處理你傳給 bot 的訊息 / 指令
python3 job_scraper.py         # 跑一次完整搜尋 + 比對 + 推播
```

`.env` 需要:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
RAPIDAPI_KEY=
CLAUDE_CODE_OAUTH_TOKEN=
# 選填
CLAUDE_MODEL=
```

`.env` 與 `config.json` 都已被 `.gitignore` 排除。本機測試流程:傳訊息 → 跑 `telegram_commands.py` 讓它處理 → 需要時 `job_scraper.py`。

---

## Telegram 指令

### 搜尋條件(改 `config.json`)

```
/status                       查看目前搜尋條件
/add_location Sydney          新增搜尋地點(/remove_location 移除)
/add_keyword Data Scientist   新增關鍵字/職稱(/remove_keyword 移除)
/set_jobtype FULLTIME         FULLTIME / PARTTIME / CONTRACTOR / INTERN / ANY
/set_remote on                只看遠端工作(on/off)
/set_dateposted week          today / 3days / week / month / all
/set_min_score 60             低於此分數不推播(0-100)
/set_visa 485 剩 2 年         你的簽證 / 工作權利狀態(每次比對的硬條件)
/set_visa_policy flag         簽證不符職缺:exclude 排除 / flag 標記置底 / ignore 不判斷
```

### 履歷 / 需求

```
/upload_resume [文字]          貼文字,或單獨傳後接著貼文字 / 上傳 .txt/.pdf/.docx
/set_preferences 文字          設定求職需求描述(方向、薪資、想避免的產業…)
/show_profile                 查看目前履歷摘要、需求與簽證狀態
```

### 履歷編修(AI agent)

```
/resume_chat                  進入對話式編修模式,之後直接用中文說要改什麼
/resume_chat_end              離開編修模式
/edit_resume 指示             單次請 agent 依指示修改
/resume_history               列出履歷備份版本
/resume_undo                  還原上一版履歷(還原前先備份現況)
/resume_diff                  顯示目前履歷與上一版差異
```

編修流程:你說要改什麼 → AI 提出修改版 → Telegram 顯示變更摘要 + diff + 按鈕(✅ 套用 / ❌ 放棄 / ✏️ 繼續修改)→ 按「套用」才寫入,寫入前自動備份到 `profile/history/`。

> 正式環境下這是透過每 15 分鐘的 poll 處理,所以每次來回會有最多 ~15 分鐘延遲。

---

## 排程與時間

- `daily-scan.yml` cron 是 **UTC**。`0 22 * * *` ≈ 雪梨早上 8~9 點(日光節約時間切換前後差一小時)。
- `poll-telegram.yml` 每 15 分鐘;GitHub 排程在尖峰時會延遲數分鐘甚至偶爾跳過,屬正常。
- cron 最短間隔 5 分鐘。
- **程式碼 repo 若 60 天沒有 commit,排程會被 GitHub 自動停用** —— 屆時到 Actions 分頁重新啟用,或偶爾推個 commit。

---

## 成本 / 用量

- **GitHub Actions**:程式碼 repo 設 **public** → 免費無上限。設 private 則每月只有 2000~3000 分鐘,`poll-telegram` 每 15 分鐘跑會超,建議 public。
- **Claude**:走訂閱額度,不按次計費,但訂閱有滾動時間窗的用量上限。每筆通過預篩的新職缺會呼叫一次 `claude -p`,自動化跑太密集可能被限流(被限流的職缺不會標記已看過,下次會重試)。
- **JSearch**:免費方案每月約 200 次請求。一次執行的請求數 = 關鍵字數 × 地點數。
- 省用量:`/set_min_score` 拉高、縮小關鍵字 × 地點組合、`config.json` 的 `max_results_per_keyword` 設小、日常用 `today`/`3days`(首次鋪底才用 `month`)。
- `matcher.keyword_prefilter()` 會先用履歷/需求的關鍵字擋掉明顯不相關的職缺,不送 Claude。

---

## 已知限制

- JSearch 免費方案每個 query 大概只回 ~10 筆,`num_pages` 常被鎖成 1;要更廣得多開關鍵字變體 + 地點,靠 bot 的迴圈 + 去重涵蓋。
- 履歷編修在正式環境走 15 分鐘輪詢,互動體感較慢。
- `claude -p` 每次約 10~40 秒,職缺多時 `daily-scan` job 會拉長,留意 Actions job 逾時。
- `CLAUDE_CODE_OAUTH_TOKEN` 在 GitHub Actions 無人值守環境是否可用,依你的訂閱方案而定 —— 第一次一定要手動 Run workflow 驗證。
- 若某則 Telegram 訊息每次處理都失敗,會卡住訊息佇列(需手動改 `state/telegram_offset.json` 跳過)。

---

## 檔案結構

**程式碼 repo**(不含個資,可公開):

```
job-alert-bot/
├── .github/workflows/
│   ├── daily-scan.yml         # 每天:搜尋 + AI 比對 + 推播
│   └── poll-telegram.yml      # 每15分:處理 Telegram 指令 / 履歷編修
├── scripts/
│   ├── utils.py               # 路徑、設定/履歷/狀態讀寫、Telegram API
│   ├── llm.py                 # 呼叫 claude CLI(chat_json / run_resume_agent)
│   ├── matcher.py             # AI 職缺匹配(規格書 3.3)
│   ├── job_scraper.py         # 每日搜尋 + 比對 + 排序 + 推播
│   ├── resume_files.py        # 履歷檔案解析(.txt/.pdf/.docx)+ 歷史版本
│   ├── resume_agent.py        # Phase 6 履歷編修(提案 + diff + 確認後寫入)
│   ├── telegram_commands.py   # Telegram 指令處理
│   ├── dotenv_bootstrap.py    # 本機執行時載入 .env
│   └── data_sync.sh           # workflow 用:跟私有 data repo 同步個資/狀態
├── config.example.json        # config.json 的範本(真正的 config.json 不進 repo)
├── .env.example               # 本機開發用;複製成 .env 填 token
├── job-match-spec.md          # 系統規格書
└── requirements.txt
```

**私有 data repo**(例如 `job-bot-data`,一定 private;由 workflow 自動維護):

```
job-bot-data/
├── config.json                # 搜尋條件 + country + visa_status / visa_policy
├── profile/
│   ├── resume.txt             # 履歷純文字(由 Telegram 指令更新)
│   ├── preferences.txt        # 求職需求
│   └── history/               # 履歷歷史版本備份
└── state/
    ├── seen_jobs.json         # 已處理職缺 ID(避免重複推播)
    ├── recent_jobs.json       # 最近推播職缺 + 比對結果(給履歷 agent 讀)
    ├── telegram_offset.json   # Telegram 讀取進度
    ├── bot_state.json         # 是否在等待履歷上傳
    └── resume_chat_session.json  # 履歷編修對話狀態
```

### `config.json` 欄位

| 欄位 | 說明 | 範例 |
|---|---|---|
| `locations` | 搜尋地點,可多個 | `["Sydney"]` |
| `keywords` | 關鍵字/職稱,可多個(各自搜尋) | `["Data Scientist"]` |
| `employment_type` | `FULLTIME`/`PARTTIME`/`CONTRACTOR`/`INTERN`/`ANY` | `ANY` |
| `remote_only` | 只看遠端 | `false` |
| `date_posted` | `today`/`3days`/`week`/`month`/`all` | `week` |
| `country` | JSearch 國家碼(ISO 兩碼) | `au` |
| `min_match_score` | 低於此分數不推播 | `60` |
| `max_results_per_keyword` | 每個 query 最多留幾筆 | `10` |
| `visa_status` | 你的工作權利狀態,比對硬條件 | `"485 畢業工簽,剩 2 年"` |
| `visa_policy` | `exclude`/`flag`/`ignore` | `flag` |

(`sources` 欄位為舊版遺留,`/search-v2` 不使用。)

---

## 疑難排解

| 症狀 | 可能原因 |
|---|---|
| `claude setup-token` 顯示 authorization failed | 帳號沒有 Pro/Max 訂閱;或授權時登入到錯的帳號;或授權碼過期 |
| JSearch 回 `404 Endpoint '/search' does not exist` | 舊 endpoint,已改用 `/search-v2`(本 repo 已修) |
| JSearch 回 403 / 「not subscribed」 | 沒在 RapidAPI 按訂閱,或 key 不對 |
| 職缺抓很少 | `date_posted` 太窄(改 `week`/`month`);免費方案單 query 上限;關鍵字/地點太少 |
| 都評估但沒一筆過門檻 | 履歷還沒上傳(`/show_profile` 確認);先 `/set_min_score 0` 看真實分數 |
| Telegram 沒收到 bot 回覆 | `TELEGRAM_CHAT_ID` 填錯;`.env` 不在專案根目錄;workflow secrets 沒設 |
| workflow log 卡在 `claude -p` | CI 環境的信任資料夾提示或 token 問題 —— 看 log 訊息 |

要重新評估已看過的職缺:清空 data repo(或本機)的 `state/seen_jobs.json` 為 `{"seen_job_ids": []}`。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
