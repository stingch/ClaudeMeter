"""
ClaudeMeter - 找出正確的 Organization ID (find_org.py)

症狀：usage API 回 200，但 five_hour / seven_day 全是 null、limits 是空陣列。
原因通常是 .env 裡的 CLAUDE_ORG_ID 指到了一個沒有訂閱／沒有用量的 organization。

這支腳本會：
  1. 呼叫 /api/organizations 列出你帳號底下所有 org
  2. 對每一個 org 都打一次 usage API
  3. 印出哪一個 org 真的有數據，並告訴你該把 CLAUDE_ORG_ID 換成什麼

用法（在 pc_bridge 目錄下）：
    ..\\claudemeter-env\\Scripts\\python.exe find_org.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from curl_cffi import requests  # noqa: E402

from common import COOKIE_STRING, REQUEST_TIMEOUT_SECONDS, USER_AGENT  # noqa: E402

ORG_LIST_URL = "https://claude.ai/api/organizations"
USAGE_URL = "https://claude.ai/api/organizations/{uuid}/usage"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Cookie": COOKIE_STRING,
    "Accept": "*/*",
    "Referer": "https://claude.ai/chats",
    "Origin": "https://claude.ai",
    "anthropic-client-version": "1.0.0",
}


def get(url: str):  # noqa: ANN201
    return requests.get(
        url, headers=HEADERS, impersonate="chrome120", timeout=REQUEST_TIMEOUT_SECONDS
    )


def has_real_data(payload: object) -> bool:
    """回應裡只要有任何非 null / 非空的用量欄位就算有數據"""
    if not isinstance(payload, dict):
        return False
    for k, v in payload.items():
        if k == "member_dashboard_available":
            continue
        if isinstance(v, dict) and v:
            return True
        if isinstance(v, list) and v:
            return True
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            return True
    return False


def main() -> int:
    print("=" * 70)
    print("步驟 1：列出帳號底下所有 organization")
    print("=" * 70)

    res = get(ORG_LIST_URL)
    if res.status_code != 200:
        print(f"[FAIL] HTTP {res.status_code}：{res.text[:300]}")
        print("       Cookie 可能不完整（缺 sessionKey）或已過期。")
        return 1

    orgs = res.json()
    if not isinstance(orgs, list) or not orgs:
        print(f"[FAIL] 沒有取得 org 清單：{json.dumps(orgs, ensure_ascii=False)[:400]}")
        return 1

    print(f"共找到 {len(orgs)} 個 organization：\n")
    for i, org in enumerate(orgs):
        print(f"[{i}] uuid         = {org.get('uuid')}")
        print(f"    name         = {org.get('name')}")
        print(f"    capabilities = {org.get('capabilities')}")
        print(f"    settings     = {json.dumps(org.get('settings'), ensure_ascii=False)[:160]}")
        print()

    print("=" * 70)
    print("步驟 2：逐一測試每個 org 的 usage API")
    print("=" * 70)

    winners: list[tuple[str, str, dict]] = []
    for org in orgs:
        uuid = org.get("uuid")
        name = org.get("name") or "(無名稱)"
        if not uuid:
            continue
        r = get(USAGE_URL.format(uuid=uuid))
        if r.status_code != 200:
            print(f"[  ] {uuid}  {name}  -> HTTP {r.status_code}")
            continue
        try:
            payload = r.json()
        except Exception:  # noqa: BLE001
            print(f"[  ] {uuid}  {name}  -> 回應不是 JSON")
            continue

        if has_real_data(payload):
            print(f"[OK] {uuid}  {name}  -> 有數據！")
            winners.append((uuid, name, payload))
        else:
            print(f"[  ] {uuid}  {name}  -> 全空 (null / [])")

    print()
    print("=" * 70)
    print("結論")
    print("=" * 70)

    if not winners:
        print("每一個 org 的 usage 都是空的。")
        print("代表問題不在 org ID，而是這支 API 本身改了（換路徑、需要額外參數，")
        print("或用量已經改由別的端點提供）。下一步請用瀏覽器 F12 的 Network 分頁，")
        print("在 claude.ai 的用量頁面上看它實際打了哪一支請求。")
        return 2

    uuid, name, payload = winners[0]
    print(f"請把 .env 裡的 CLAUDE_ORG_ID 改成：\n")
    print(f'    CLAUDE_ORG_ID="{uuid}"')
    print(f"\n（org 名稱：{name}）\n")
    print("該 org 的 usage 回應內容：")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    out = Path(__file__).resolve().parent / "usage_dump.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存到 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
