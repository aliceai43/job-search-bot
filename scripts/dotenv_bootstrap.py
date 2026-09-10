"""本機 / cron 執行時，把 repo 根目錄的 .env 載入 os.environ。

- 沒有 .env 就什麼都不做（GitHub Actions 由 repo Secrets 注入環境變數）。
- 不覆蓋已存在的環境變數（真正的環境變數優先）。
- 只用標準函式庫，不需要 python-dotenv。

用法：在每個進入點（job_scraper.py / telegram_commands.py）最上方
`import dotenv_bootstrap`，且要在 import 其他專案模組之前。
"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_ROOT, ".env")


def load(path=_ENV_PATH):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load()
