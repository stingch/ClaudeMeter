"""
ClaudeMeter 診斷腳本 (diagnose.py)

依序檢查整條資料鏈路，找出「螢幕數字不更新」卡在哪一段：
  1. .env 設定檔是否存在、憑證是否讀得到
  2. Claude.ai Usage API 是否回 200（Cookie 過期會是 401/403）
  3. 回傳的 JSON 欄位是否還是程式預期的格式
  4. BLE 是否掃得到名為 ClaudeMeter 的 ESP32

用法（在 pc_bridge 目錄下）：
    ..\\claudemeter-env\\Scripts\\python.exe diagnose.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

OK = "[ OK ]"
NG = "[FAIL]"
WARN = "[WARN]"

ROOT = Path(__file__).resolve().parent.parent


def check_env_file() -> bool:
    print("\n--- 1. 檢查 .env 設定檔 ---")
    candidates = [ROOT / ".env", Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    found = [p for p in candidates if p.exists()]
    if not found:
        print(f"{NG} 找不到 .env。已找過：")
        for p in candidates:
            print(f"       {p}")
        print("       => 請把 .env.example 複製成 .env 並填入 CLAUDE_COOKIE / CLAUDE_ORG_ID")
        return False
    print(f"{OK} 找到 .env：{found[0]}")
    return True


def check_credentials() -> bool:
    print("\n--- 2. 檢查憑證內容 ---")
    from common import COOKIE_STRING, ORG_ID

    ok = True
    if not COOKIE_STRING:
        print(f"{NG} CLAUDE_COOKIE 是空的")
        ok = False
    else:
        print(f"{OK} CLAUDE_COOKIE 長度 {len(COOKIE_STRING)}，開頭：{COOKIE_STRING[:24]}...")
        if "sessionKey" not in COOKIE_STRING:
            print(f"{WARN} Cookie 字串裡沒看到 sessionKey，很可能複製不完整")

    if not ORG_ID:
        print(f"{NG} CLAUDE_ORG_ID 是空的")
        ok = False
    else:
        print(f"{OK} CLAUDE_ORG_ID = {ORG_ID}")
    return ok


def check_api() -> bool:
    print("\n--- 3. 呼叫 Claude.ai Usage API ---")
    from curl_cffi import requests

    from common import CLAUDE_API_URL, COOKIE_STRING, REQUEST_TIMEOUT_SECONDS, USER_AGENT

    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": COOKIE_STRING,
        "Accept": "*/*",
        "Referer": "https://claude.ai/chats",
        "Origin": "https://claude.ai",
        "anthropic-client-version": "1.0.0",
    }
    print(f"       URL: {CLAUDE_API_URL}")
    try:
        res = requests.get(
            CLAUDE_API_URL,
            headers=headers,
            impersonate="chrome120",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:  # noqa: BLE001
        print(f"{NG} 連線就失敗了：{e}")
        return False

    print(f"       HTTP {res.status_code}")
    if res.status_code in (401, 403):
        print(f"{NG} 憑證被拒絕 —— Cookie 幾乎確定已經過期，請重新從瀏覽器複製一份")
        print(f"       回應：{res.text[:200]}")
        return False
    if res.status_code == 404:
        print(f"{NG} 404 —— CLAUDE_ORG_ID 可能填錯，或 API 路徑已經改掉")
        return False
    if res.status_code != 200:
        print(f"{NG} 非預期狀態碼，回應：{res.text[:300]}")
        return False

    try:
        data = res.json()
    except Exception:  # noqa: BLE001
        print(f"{NG} 回應不是 JSON（可能被 Cloudflare 擋成 HTML）：{res.text[:200]}")
        return False

    print(f"{OK} API 有回 200")
    print(f"       頂層欄位：{list(data.keys())}")

    missing = [k for k in ("five_hour", "seven_day") if k not in data]
    if missing:
        print(f"{NG} 缺少欄位 {missing} —— API 格式變了，common.py 的解析要跟著改")
        print(f"       完整回應：{json.dumps(data, ensure_ascii=False)[:600]}")
        return False

    five, seven = data.get("five_hour") or {}, data.get("seven_day") or {}
    print(f"{OK} five_hour  = {five}")
    print(f"{OK} seven_day  = {seven}")
    return True


async def check_ble() -> bool:
    print("\n--- 4. 掃描 BLE 裝置 ---")
    from bleak import BleakScanner

    from common import DEVICE_NAME

    try:
        devices = await BleakScanner.discover(timeout=10.0)
    except Exception as e:  # noqa: BLE001
        print(f"{NG} 掃描失敗（藍牙可能關著或介面卡有問題）：{e}")
        return False

    names = [d.name for d in devices if d.name]
    hit = [d for d in devices if DEVICE_NAME in (d.name or "")]
    if not hit:
        print(f"{NG} 掃不到 {DEVICE_NAME}。附近掃到的裝置：{names or '（一台都沒有）'}")
        print("       => ESP32 可能沒通電、或已經被別的裝置連走（BLE 一次只能連一台）")
        return False
    print(f"{OK} 找到 {hit[0].name} @ {hit[0].address}")
    return True


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    print("=" * 60)
    print("ClaudeMeter 鏈路診斷")
    print("=" * 60)

    has_env = check_env_file()
    creds = check_credentials() if has_env or os.getenv("CLAUDE_COOKIE") else False
    api = check_api() if creds else False
    ble = asyncio.run(check_ble())

    print("\n" + "=" * 60)
    print("結論")
    print("=" * 60)
    if not creds:
        print("PC 端根本沒送出任何資料：憑證缺失，fetch_usage_packet() 直接回 None。")
        print("ESP32 會停在最後一次收到的數字，動畫照跑，所以看起來像正常的。")
    elif not api:
        print("憑證有讀到但 API 拒絕/格式不符，PC 端一樣送不出資料（原因見上方第 3 段）。")
    elif not ble:
        print("資料抓得到，但連不上 ESP32（原因見上方第 4 段）。")
    else:
        print("四段都通過 —— 那問題多半是 bridge 腳本根本沒在跑。請開一個視窗執行：")
        print("    ..\\claudemeter-env\\Scripts\\python.exe ble_bridge_win.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
