"""Export Tingwu text minutes through the user's Feishu CLI; never download audio."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from html import escape, unescape
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit

from tingwu_trial import Tingwu, emit, fetch_artifacts

EXPORT_ROOT = Path(__file__).with_name("exports")


def document_title(artifacts, title=None, started_at=None, prepared_at=None):
    if title is not None:
        result = " ".join(title.split())
        if not result or len(result) > 160:
            raise ValueError("Document title must contain 1..160 characters")
    else:
        topic = artifacts.get("Summarization", {}).get("Summarization", {}).get("ParagraphTitle")
        topic = " ".join(topic.split()) if isinstance(topic, str) else ""
        topic = topic[:80] or "未命名会议"
        when = datetime.fromisoformat(started_at) if started_at else (prepared_at or datetime.now().astimezone())
        # Old tasks do not expose a recording timestamp. Explicitly mark the
        # export time instead of inventing the meeting's start time.
        prefix = "" if started_at else "导出 "
        result = f"{prefix}{when:%Y-%m-%d %H:%M}｜{topic}"
    validate_content(result)
    return result


def plain(value):
    if not isinstance(value, str):
        return ""
    # Provider text is literal content, not Markdown commands or remote media.
    return re.sub(r"([\\`*_\[\]$~<>#!+\-|])", r"\\\1", value.strip())


def validate_content(content):
    if not content.strip() or len(content.encode("utf-8")) > 256_000:
        raise ValueError("Minutes are empty or exceed this exporter size limit")
    if re.search(r"\bBearer\s+\S+|\bLTAI[A-Za-z0-9]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----", content):
        raise ValueError("Potential credential found; remove it before export")
    for key in ("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                "ALIBABA_CLOUD_SECURITY_TOKEN", "MEETING_WIFI_PASSWORD"):
        secret = os.environ.get(key)
        if secret and len(secret) >= 6 and secret in content:
            raise ValueError("Configured secret found in minutes; export refused")


def render_minutes(task_id, artifacts):
    summary = artifacts.get("Summarization", {}).get("Summarization", {})
    body = summary.get("ParagraphSummary")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("No nonempty cloud summary is available")
    actions = artifacts.get("MeetingAssistance", {}).get("MeetingAssistance", {}).get("Actions", [])
    validate_content(json.dumps({"summary": summary, "actions": actions}, ensure_ascii=False))
    lines = ["# 会议纪要", "", "来源：阿里听悟。以下为 AI 生成内容，未经人工校对。", "",
             "## 会议主题", "", plain(summary.get("ParagraphTitle")) or "未提供主题", "",
             "## 摘要", "", plain(body), "", "## 待办事项", ""]
    texts = [plain(action.get("Text")) for action in actions if isinstance(action, dict)]
    lines.extend(["- " + text for text in texts if text] or ["云端未提取到明确待办。"])
    lines += ["", "## 记录信息", "", "来源任务 ID：" + task_id,
              "", "本次仅导出文字摘要和待办，不附加录音。", ""]
    content = "\n".join(lines)
    validate_content(content)
    return content


def save_receipt(folder, receipt):
    path = folder / "receipt.json"
    candidate = folder / "receipt.json.tmp"
    candidate.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate.replace(path)


def prepare(task_id, artifacts, root=EXPORT_ROOT, *, title=None, started_at=None):
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("Invalid task ID")
    folder = root / task_id
    folder.mkdir(parents=True, exist_ok=True)
    receipt_path = folder / "receipt.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "prepared":
            return folder  # Preserve any created document or uncertain create.
    content = render_minutes(task_id, artifacts)
    (folder / "minutes.md").write_text(content, encoding="utf-8")
    save_receipt(folder, {"task_id": task_id, "title": document_title(artifacts, title, started_at),
                         "started_at": started_at,
                         "status": "prepared", "sha256": hashlib.sha256(content.encode()).hexdigest()})
    return folder


def cli(folder, *args):
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not npx:
        raise RuntimeError("Node.js/npx is required on the user's computer or NAS")
    command = [npx, "--yes", "@larksuite/cli@latest", "docs", *args,
               "--api-version", "v2", "--as", "user"]
    reply = subprocess.run(command, cwd=folder, capture_output=True, encoding="utf-8",
                           errors="strict", timeout=180, check=False)
    try:
        data = json.loads(reply.stdout)
    except ValueError:
        raise RuntimeError("CLI returned an unreadable response") from None
    if reply.returncode or data.get("ok") is False or data.get("error"):
        # The exact CLI error can contain document content; do not print it.
        raise RuntimeError("Feishu CLI request failed; inspect CLI authorization and permissions")
    return data


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def document_url(value):
    for text in strings(value):
        p = urlsplit(text) if text.startswith("https://") else None
        if p and (p.hostname or "").endswith((".feishu.cn", ".larksuite.com")) and re.fullmatch(r"/docx/[A-Za-z0-9]+", p.path):
            return text
    raise RuntimeError("Create response has no recognized document URL")


def publish(folder):
    folder = folder.resolve()
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    content = (folder / "minutes.md").read_text(encoding="utf-8")
    validate_content(content)
    if hashlib.sha256(content.encode()).hexdigest() != receipt.get("sha256"):
        raise ValueError("Prepared minutes changed; prepare them again before publishing")
    if receipt["status"] == "creating":
        raise RuntimeError("Previous create outcome is uncertain; find the existing document before retrying")
    if receipt["status"] == "prepared":
        validate_content(receipt["title"])
        # User/AI titles are data in a file, never arguments passed through the
        # Windows npx.cmd wrapper. Markdown accepts an escaped XML title block.
        candidate = folder / "create.md"
        candidate.write_text("<title>" + escape(receipt["title"]) + "</title>\n\n" + content, encoding="utf-8")
        try:
            cli(folder, "+create", "--doc-format", "markdown", "--content", "@create.md", "--dry-run")
            receipt["status"] = "creating"
            save_receipt(folder, receipt)
            created = cli(folder, "+create", "--doc-format", "markdown", "--content", "@create.md")
        finally:
            candidate.unlink(missing_ok=True)
        receipt.update(status="created", url=document_url(created), title_verified=False)
        save_receipt(folder, receipt)
    if receipt["status"] not in {"created", "verified"}:
        raise ValueError("Unexpected export state")
    fetched = cli(folder, "+fetch", "--doc", receipt["url"], "--doc-format", "markdown")
    visible = "\n".join(strings(fetched))
    if not all(marker in visible for marker in (receipt["task_id"], "摘要", "待办事项")):
        raise RuntimeError("Created document did not pass content readback verification")
    summary_text = content.split("## 摘要\n\n", 1)[1].split("\n\n## 待办事项", 1)[0]
    normalize = lambda text: re.sub(r"\s+", "", re.sub(r"\\([\\`*_\[\]$~<>#!+\-|])", r"\1", text))
    if normalize(summary_text) not in normalize(visible):
        raise RuntimeError("Created document summary differs from the prepared minutes")
    if receipt.get("title_verified") is False:
        if normalize(receipt["title"]) not in normalize(unescape(visible)):
            raise RuntimeError("Created document title differs from the requested title")
        receipt["title_verified"] = True
    receipt["status"] = "verified"
    save_receipt(folder, receipt)
    emit("feishu_document", status="verified", url=receipt["url"])
    return receipt["url"]


def main():
    from device_trial import load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--publish", action="store_true", help="Create a user-owned document and fetch it to verify")
    parser.add_argument("--title", help="Custom document title before first publication; otherwise use time and cloud topic")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name(".env"))
    args = parser.parse_args()
    load_config(args.config)
    if not re.fullmatch(r"[0-9a-f]{32}", args.task_id):
        parser.error("Expected a 32-character task ID")
    folder = EXPORT_ROOT / args.task_id
    if not (folder / "receipt.json").exists():
        data = Tingwu().result(args.task_id)
        if data.get("TaskStatus") != "COMPLETED":
            raise ValueError("Cloud task is not complete")
        folder = prepare(args.task_id, fetch_artifacts(data), title=args.title)
    elif args.title is not None:
        receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
        if receipt["status"] != "prepared":
            raise ValueError("Document already exists; rename it in Feishu instead of creating a duplicate")
        receipt["title"] = document_title({}, args.title)
        save_receipt(folder, receipt)
    emit("minutes_prepared", path=str(folder / "minutes.md"))
    if args.publish:
        publish(folder)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("export_failed", error_type=type(exc).__name__, message=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Export did not complete")
        raise SystemExit(1)
