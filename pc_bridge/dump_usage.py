"""
ClaudeMeter - Usage API 原始回應傾印 (dump_usage.py)

claude.ai 的 usage API 已經改版：舊的 five_hour / seven_day 兩個物件現在是空的 {}，
真正的數據搬到別的欄位去了。這支腳本把完整 JSON 印出來，好對照出新的欄位名稱，
再回頭修 common.py 的解析邏輯。

用法（在 pc_bridge 目錄下）：
    ..\\claudemeter-env\\Scripts\\python.exe dump_usage.py

輸出同時會存成 usage_dump.json（已被 .gitignore 的 *.json 規則涵蓋與否請自行確認，
這個檔只有額度數字，不含 Cookie）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from curl_cffi import requests  # noqa: E402

from common import (  # noqa: E402
    CLAUDE_API_URL,
    COOKIE_STRING,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

# 可能代表「用量比例」的欄位名稱關鍵字
UTIL_HINTS = ("utilization", "used", "usage", "percent", "pct", "remaining", "consumed")
TIME_HINTS = ("resets_at", "reset_at", "expires_at", "window_end", "ends_at")


def walk(node: object, path: str = "$") -> list[tuple[str, object]]:
    """遞迴走訪 JSON，撈出所有看起來像用量 / 重置時間的欄位"""
    hits: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}"
            kl = k.lower()
            if any(h in kl for h in UTIL_HINTS) or any(h in kl for h in TIME_HINTS):
                if not isinstance(v, (dict, list)):
                    hits.append((child, v))
            hits.extend(walk(v, child))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits.extend(walk(v, f"{path}[{i}]"))
    return hits


def main() -> int:
    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": COOKIE_STRING,
        "Accept": "*/*",
        "Referer": "https://claude.ai/chats",
        "Origin": "https://claude.ai",
        "anthropic-client-version": "1.0.0",
    }
    res = requests.get(
        CLAUDE_API_URL,
        headers=headers,
        impersonate="chrome120",
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if res.status_code != 200:
        print(f"HTTP {res.status_code}: {res.text[:300]}")
        return 1

    data = res.json()

    out = Path(__file__).resolve().parent / "usage_dump.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    print("完整回應")
    print("=" * 70)
    print(json.dumps(data, ensure_ascii=False, indent=2))

    print()
    print("=" * 70)
    print("疑似「用量 / 重置時間」欄位（把這段貼出來就夠判斷了）")
    print("=" * 70)
    for p, v in walk(data):
        print(f"{p:60s} = {v!r}")

    print()
    print("=" * 70)
    print("limits 陣列細節")
    print("=" * 70)
    limits = data.get("limits")
    if isinstance(limits, list):
        for i, lim in enumerate(limits):
            print(f"[{i}] {json.dumps(lim, ensure_ascii=False)}")
    else:
        print(f"limits 不是陣列，型別為 {type(limits).__name__}：{limits!r}")

    print(f"\n已另存一份到 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
