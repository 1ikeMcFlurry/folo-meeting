"""Phone UI regression: parent/system back and unsaved forms, without cloud calls or recording.

Start on MainActivity's device tab. The only edited preference is an invalid
duration (0), which must be rejected and then discarded. Never prints inputs.
"""
import json
import time
from pathlib import Path
from provision_check import adb, ui, click

EVIDENCE = Path("tools/meeting_trial/evidence/brand-navigation-20260910")
checks = []


def nodes(tree=None):
    return list((tree if tree is not None else ui()).iter("node"))


def find(label, tree=None):
    found = [n for n in nodes(tree) if n.get("text") == label or n.get("content-desc") == label]
    assert len(found) == 1, "Expected unique control: " + label
    return found[0]


def tap(label):
    click(find(label))


def expect(label, seconds=12):
    end = time.monotonic() + seconds
    while True:
        tree = ui()
        if any(n.get("text") == label or n.get("content-desc") == label for n in nodes(tree)):
            return tree
        assert time.monotonic() < end, "Expected page/control: " + label
        time.sleep(0.4)


def back():
    adb("shell", "input", "keyevent", "4")


def passed(name):
    checks.append(name)
    print("PASS:", name, flush=True)


def screenshot(name):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_bytes(adb("exec-out", "screencap", "-p"))


def main():
    tap("设置")
    expect("录音偏好  ›")
    screenshot("phone-settings.png")
    for label in ("听悟服务", "飞书连接"):
        tap(label + "  ›")
        tree = expect("返回设置")
        assert not any(n.get("text") == "记录" for n in nodes(tree)), "Child page must hide bottom tabs"
        tap("返回设置")
        expect("录音偏好  ›")
    passed("settings children return to hub; bottom tabs hidden")

    tap("录音偏好  ›")
    # The label and field both use the description: select the EditText explicitly.
    minutes = next(n for n in nodes() if n.get("class") == "android.widget.EditText" and n.get("content-desc") == "单场录音上限（分钟）")
    original = minutes.get("text", "")
    assert original.isdigit() and 1 <= int(original) <= 1440
    click(minutes)
    adb("shell", "input", "keyevent", "123")
    adb("shell", "input", "keyevent", *(["67"] * len(original)))
    adb("shell", "input", "text", "0")
    back()  # IME consumes the first system back.
    tree = expect("录音偏好")
    assert not any(n.get("text") == "保存修改？" for n in nodes(tree)), "Back must first close keyboard"
    back()
    expect("保存修改？")
    tap("继续编辑")
    expect("录音偏好")
    passed("system back closes keyboard before unsaved prompt; continue editing works")

    tap("返回设置")
    expect("保存修改？")
    tap("保存并返回")
    expect("时长应为 1 至 1440 分钟")
    expect("录音偏好")
    passed("invalid preference cannot save or leave form")
    tap("返回设置")
    tap("不保存")
    expect("录音偏好  ›")
    tap("录音偏好  ›")
    minutes = next(n for n in nodes() if n.get("class") == "android.widget.EditText" and n.get("content-desc") == "单场录音上限（分钟）")
    assert minutes.get("text") == original, "Discard must preserve stored preference"
    back()
    expect("录音偏好  ›")
    passed("discard restores stored preference; unchanged form returns without prompt")

    tap("声纹管理  ›")
    expect("讯飞声纹设置  ›")
    tap("讯飞声纹设置  ›")
    expect("返回声纹管理")
    back()
    expect("讯飞声纹设置  ›")
    tap("返回设置")
    expect("录音偏好  ›")
    passed("voiceprint settings return to voiceprint; voiceprint returns to settings origin")

    tap("记录")
    tree = expect("记录，当前页面")
    records = [n for n in nodes(tree) if n.get("class") == "android.widget.Button"]
    assert records, "Existing meeting required to verify editor parent navigation"
    click(records[0])
    tree = expect("编辑纪要")
    assert not any(n.get("text") == "设置" for n in nodes(tree))
    back()
    expect("记录，当前页面")
    passed("editor system back returns to records without changing text")
    back()
    expect("设备，当前页面")
    tap("声纹管理")
    expect("返回设备")
    tap("返回设备")
    expect("设备，当前页面")
    passed("root back returns to device; voiceprint remembers device origin")

    (EVIDENCE / "navigation-results.json").write_text(json.dumps({"passed": checks, "recording_started": False, "cloud_calls": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
