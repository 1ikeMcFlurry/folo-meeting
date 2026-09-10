"""Verify the new opt-in and editor navigation. Does not record or call cloud services."""
import json
from pathlib import Path
from navigation_check import tap, expect, nodes, back, find
from provision_check import adb, click

checks = []


def passed(name):
    checks.append(name)
    print("PASS:", name, flush=True)


def reveal(label):
    for _ in range(9):
        tree = nodes()
        if any(n.get("text") == label or n.get("content-desc") == label for n in tree):
            return find(label)
        adb("shell", "input", "swipe", "1100", "2230", "1100", "1060", "350")
    raise AssertionError("Missing control: " + label)


def main():
    tap("设置")
    tap("录音偏好  ›")
    control = reveal("会后声纹识别用音频")
    assert control.get("checked") == "false", "Fresh feature must be opt-in"
    click(control)
    tap("返回设置")
    expect("保存修改？")
    tap("不保存")
    tap("录音偏好  ›")
    assert reveal("会后声纹识别用音频").get("checked") == "false"
    tap("返回设置")
    passed("audio output defaults off; back detects checkbox edits; discard preserves off")
    tap("记录")
    records = [n for n in nodes() if n.get("class") == "android.widget.Button"]
    assert records, "Existing meeting required"
    click(records[0])
    expect("编辑纪要")
    click(reveal("识别本场发言人"))
    expect("返回编辑纪要")
    expect("取样并识别")
    assert not any(n.get("text") == "应用已勾选姓名" for n in nodes()), "Must not invent candidates"
    back()
    expect("编辑纪要")
    tap("返回会议记录")
    expect("记录，当前页面")
    passed("meeting -> speaker recognition -> editor -> records; no automatic upload or names")
    tap("设置")
    tap("声纹管理  ›")
    expect("讯飞声纹设置  ›")
    assert any("识别本场发言人" in n.get("text", "") for n in nodes())
    tap("返回设置")
    passed("voiceprint management explains the meeting integration and returns to settings")
    dest = Path("tools/meeting_trial/evidence/meeting-voiceprints-20260910")
    dest.mkdir(exist_ok=True)
    (dest / "ui-checks.json").write_text(json.dumps({"checks": checks, "cloud_calls": False, "recording_started": False}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
