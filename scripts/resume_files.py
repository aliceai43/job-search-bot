"""履歷檔案處理:把上傳檔案(.txt / .pdf / .docx)轉純文字,以及履歷歷史版本管理。"""
import io
import os
import time

from utils import HISTORY_DIR, RESUME_PATH, load_resume, read_text, save_resume, write_text


# ---------------------------------------------------------------------------
# 檔案 → 純文字
# ---------------------------------------------------------------------------
def extract_text(filename, data):
    """依副檔名把 bytes 轉純文字;不支援的格式回傳 None。"""
    ext = os.path.splitext(filename or "")[1].lower()

    if ext in ("", ".txt", ".md", ".text"):
        return data.decode("utf-8", errors="replace")

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    if ext == ".docx":
        import docx
        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)

    return None


# ---------------------------------------------------------------------------
# 歷史版本(profile/history/resume-<UTC timestamp>.txt)
# ---------------------------------------------------------------------------
def _timestamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def backup_current_resume(change_summary=""):
    """把目前的 resume.txt 複製一份到 history/。回傳備份路徑(沒有履歷則回 None)。"""
    current = load_resume()
    if not current:
        return None
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"resume-{_timestamp()}.txt")
    header = f"# change_summary: {change_summary}\n" if change_summary else ""
    write_text(path, header + current + "\n")
    return path


def list_history():
    """回傳 [(檔名, change_summary), ...],新到舊。"""
    if not os.path.isdir(HISTORY_DIR):
        return []
    out = []
    for name in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not name.startswith("resume-"):
            continue
        first_line = read_text(os.path.join(HISTORY_DIR, name)).split("\n", 1)[0]
        summary = first_line[len("# change_summary:"):].strip() \
            if first_line.startswith("# change_summary:") else ""
        out.append((name, summary))
    return out


def _strip_header(text):
    return text.split("\n", 1)[1] if text.startswith("# change_summary:") else text


def read_history_version(name):
    return _strip_header(read_text(os.path.join(HISTORY_DIR, name))).strip()


def apply_resume(new_content, change_summary=""):
    """備份舊版 → 寫入新履歷。回傳備份路徑。"""
    backup = backup_current_resume(change_summary)
    save_resume(new_content)
    return backup


def undo_last():
    """還原上一個歷史版本(還原前先備份現況)。回傳被還原的檔名,沒有可還原則回 None。"""
    history = list_history()
    if not history:
        return None
    latest_name = history[0][0]
    previous = read_history_version(latest_name)
    backup_current_resume("undo 前自動備份")
    save_resume(previous)
    return latest_name
