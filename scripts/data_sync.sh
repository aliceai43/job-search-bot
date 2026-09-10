#!/usr/bin/env bash
#
# 在 GitHub Actions 裡把「個資 / 狀態」跟一個私有 data repo 同步。
# 下面這些路徑都被 .gitignore 排除、不會進程式碼 repo，只存在私有 data repo：
#   config.json  profile/  state/
#
#   bash scripts/data_sync.sh pull        # 執行前：把 data repo 內容拉進工作目錄
#   bash scripts/data_sync.sh push [msg]  # 執行後：把變動 commit 回 data repo
#
# 需要環境變數：
#   DATA_REPO        私有 data repo，例如 hungai/job-bot-data
#   DATA_REPO_TOKEN  對該 repo 有 Contents 讀寫權限的 fine-grained PAT
#
set -euo pipefail

DATA_DIR="${DATA_DIR:-_data}"
SYNC_PATHS=(config.json profile state)

require_env() {
  : "${DATA_REPO:?需要 DATA_REPO}"
  : "${DATA_REPO_TOKEN:?需要 DATA_REPO_TOKEN}"
}

clone_url() {
  printf 'https://x-access-token:%s@github.com/%s.git' "$DATA_REPO_TOKEN" "$DATA_REPO"
}

pull() {
  require_env
  rm -rf "$DATA_DIR"
  git clone --quiet "$(clone_url)" "$DATA_DIR"
  rsync -a --exclude='.git' "$DATA_DIR"/ ./
  mkdir -p profile/history state
  echo "data_sync: pulled from $DATA_REPO"
}

push() {
  require_env
  local msg="${1:-sync}"

  local existing=()
  local p
  for p in "${SYNC_PATHS[@]}"; do
    [ -e "$p" ] && existing+=("$p")
  done
  [ ${#existing[@]} -gt 0 ] && rsync -a "${existing[@]}" "$DATA_DIR"/

  cd "$DATA_DIR"
  git config user.name  "job-bot"
  git config user.email "job-bot@users.noreply.github.com"
  git add -A
  if git diff --cached --quiet; then
    echo "data_sync: nothing changed, skip push"
    return 0
  fi
  git commit --quiet -m "chore(data): ${msg} @ $(date -u +%FT%TZ)"
  for attempt in 1 2 3; do
    if git push --quiet; then
      echo "data_sync: pushed to $DATA_REPO"
      return 0
    fi
    echo "data_sync: push rejected, rebase & retry (${attempt}/3)"
    git pull --rebase --autostash --quiet || true
  done
  echo "data_sync: push failed after 3 attempts" >&2
  return 1
}

case "${1:-}" in
  pull) pull ;;
  push) shift; push "${1:-sync}" ;;
  *) echo "usage: $0 {pull|push [msg]}" >&2; exit 2 ;;
esac
