"""Exercise Wi-Fi navigation only. Selects a network but never enters/sends a password."""
import json
from navigation_check import adb, nodes, click, tap, expect, back, screenshot, checks, passed, EVIDENCE


def select_network():
    tree = expect("选择录音器要连接的 Wi-Fi")
    network = next(n for n in nodes(tree) if n.get("class") == "android.widget.Button" and "\n" in n.get("text", ""))
    click(network)
    expect("返回网络列表")


def main():
    expect("选择录音器要连接的 Wi-Fi")
    screenshot("phone-wifi-networks.png")
    select_network()
    tap("返回网络列表")
    expect("选择录音器要连接的 Wi-Fi")
    passed("password parent link returns to network list, keeping BLE session")
    select_network()
    back()
    expect("选择录音器要连接的 Wi-Fi")
    passed("password system back returns to network list")
    # Hidden-network entry may be below the viewport; focus remains in this page.
    tree = nodes()
    if not any(n.get("text") == "手动添加隐藏网络" for n in tree):
        scroll = next(n for n in tree if n.get("scrollable") == "true")
        import re
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", scroll.get("bounds")))
        adb("shell", "input", "swipe", str((x1+x2)//2), str(y2-120), str((x1+x2)//2), str(y1+80), "400")
    tap("手动添加隐藏网络")
    expect("添加隐藏网络")
    tap("返回网络列表")
    expect("选择录音器要连接的 Wi-Fi")
    passed("hidden-network form returns to the same network session")
    # Find rescan at the bottom without ever tapping a connect/upload action.
    for _ in range(10):
        if any(n.get("text") == "重新搜索" for n in nodes()):
            break
        adb("shell", "input", "swipe", "636", "2400", "636", "1100", "350")
    tap("重新搜索")
    back()
    expect("退出本次配网？")
    tap("继续配网")
    expect("选择录音器要连接的 Wi-Fi")
    passed("in-progress exit asks once; continue keeps provisioning alive")
    tap("返回设备")
    expect("录音器已连接", seconds=40)
    passed("network-list back exits provisioning and reconnects device control")
    (EVIDENCE / "wifi-navigation-results.json").write_text(json.dumps({"passed": checks, "wifi_credentials_changed": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
