"""Additional local-only navigation checks; temporarily edits AppID then discards it."""
import json
from navigation_check import adb, nodes, click, tap, expect, back, checks, passed, EVIDENCE


def app_id_field():
    return next(n for n in nodes() if n.get("class") == "android.widget.EditText")


def main():
    tap("设置")
    tap("声纹管理  ›")
    expect("讯飞声纹设置  ›")
    tap("讯飞声纹设置  ›")
    original = app_id_field().get("text", "")
    assert original
    click(app_id_field())
    adb("shell", "input", "keyevent", "123")
    adb("shell", "input", "text", "_ui")
    back()
    back()
    expect("保存修改？")
    tap("继续编辑")
    assert app_id_field().get("text") == original + "_ui"
    tap("返回声纹管理")
    tap("不保存")
    expect("讯飞声纹设置  ›")
    tap("讯飞声纹设置  ›")
    assert app_id_field().get("text") == original
    tap("返回声纹管理")
    tap("返回设置")
    passed("voiceprint config dirty prompt retains input; discard preserves stored AppID")

    tap("录音偏好  ›")
    expect("返回设置")
    # Physical edge swipe exercises Android's OnBackInvokedDispatcher path.
    adb("shell", "input", "swipe", "3", "1450", "530", "1450", "350")
    expect("录音偏好  ›")
    passed("Android edge back gesture returns from settings child to hub")

    tap("记录")
    tree = expect("记录，当前页面")
    click(next(n for n in nodes(tree) if n.get("class") == "android.widget.Button"))
    expect("编辑纪要")
    tap("返回会议记录")
    expect("记录，当前页面")
    passed("editor visible parent link returns to records")
    back()
    expect("设备，当前页面")
    back()
    state = adb("shell", "dumpsys", "activity", "activities").decode("utf-8", errors="replace")
    resumed = [line for line in state.splitlines() if "mResumedActivity" in line or "topResumedActivity" in line]
    assert resumed and not any("com.folotoy.meeting" in line for line in resumed)
    adb("shell", "am", "start", "-n", "com.folotoy.meeting/.MainActivity")
    expect("设备，当前页面")
    passed("device-root back returns to launcher; reopening resumes device page")
    (EVIDENCE / "navigation-detail-results.json").write_text(json.dumps({"passed": checks, "credentials_changed": False, "cloud_calls": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
