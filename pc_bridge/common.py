"""
ClaudeMeter PC Bridge - 共用模組 (Shared Module)

win / macOS 兩支 bridge 腳本共用的設定讀取、時間解析、以及 Claude.ai Usage API
抓取邏輯都集中在這裡，避免兩份檔案各自維護一份重複程式碼。
"""

from __future__ import annotations

import logging
import os
import struct
from datetime import datetime
from typing import NamedTuple

from curl_cffi import requests
from dateutil import parser, tz
from dotenv import load_dotenv

# ================= 載入設定 =================
load_dotenv()

COOKIE_STRING: str = os.getenv("CLAUDE_COOKIE", "")
ORG_ID: str = os.getenv("CLAUDE_ORG_ID", "")
USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

# 每次抓取 API 的間隔秒數，可在 .env 用 POLL_INTERVAL_SECONDS 覆蓋
POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

# API 逾時秒數
REQUEST_TIMEOUT_SECONDS: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))

# ESP32 螢幕背光亮度 (0~255)，對應韌體 main.cpp 的 currentBrightness 預設值
BRIGHTNESS: int = min(max(int(os.getenv("BRIGHTNESS", "200")), 0), 255)

CLAUDE_API_URL: str = f"https://claude.ai/api/organizations/{ORG_ID}/usage"

# ESP32 廣播名稱與 UUID (須與 ESP32 韌體設定一致)
DEVICE_NAME = "ClaudeMeter"
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHARACTERISTIC_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
# ============================================

log = logging.getLogger("claudemeter")


def setup_logging(level: int = logging.INFO) -> None:
    """設定統一的 log 格式：[HH:MM:SS] LEVEL 訊息"""
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def has_credentials() -> bool:
    """檢查 .env 是否已填入必要的認證資訊"""
    if not COOKIE_STRING or not ORG_ID:
        log.warning("=" * 65)
        log.warning("未檢測到完整的 CLAUDE_COOKIE 或 CLAUDE_ORG_ID 設定！")
        log.warning("請先將根目錄下的 .env.example 複製為 .env，並填入您的認證資訊。")
        log.warning("=" * 65)
        return False
    return True


class ResetTime(NamedTuple):
    """ISO 時間字串解析後的本地時間欄位"""

    month: int
    day: int
    hour: int
    minute: int

    @property
    def is_ready(self) -> bool:
        return self == ResetTime(0, 0, 0, 0)

    def format_short(self) -> str:
        """HH:MM，用於 5-hour session"""
        return "Ready" if self.is_ready else f"{self.hour:02d}:{self.minute:02d}"

    def format_full(self) -> str:
        """MM/DD HH:MM，用於 7-day weekly"""
        return (
            "Ready"
            if self.is_ready
            else f"{self.month:02d}/{self.day:02d} {self.hour:02d}:{self.minute:02d}"
        )


def parse_iso_datetime(iso_str: str | None) -> ResetTime:
    """解析 ISO 時間字串，轉為電腦當前本地時區 (如 UTC+8) 的 (月, 日, 時, 分)"""
    if not iso_str:
        return ResetTime(0, 0, 0, 0)
    try:
        utc_dt = parser.parse(iso_str)
        local_dt = utc_dt.astimezone(tz.tzlocal())
        return ResetTime(local_dt.month, local_dt.day, local_dt.hour, local_dt.minute)
    except Exception:
        log.exception("時間解析失敗: %s", iso_str)
        return ResetTime(0, 0, 0, 0)


def _normalize_utilization(raw: float) -> int:
    """API 若回傳 <= 1.0 代表舊格式(小數)，若 > 1.0 代表新格式(百分比)"""
    val = raw * 100 if raw <= 1.0 else raw
    return min(max(round(val), 0), 100)


def fetch_usage_packet() -> bytes | None:
    """
    同步抓取 Claude.ai Usage API，回傳打包好的 9-byte 封包：
    [s_util, s_h, s_m, w_util, w_mon, w_day, w_h, w_m, brightness]
    第 9 個 byte（brightness）為可選的螢幕背光亮度，韌體若收到少於 9 bytes
    仍會照常運作、僅略過調整亮度。失敗時回傳 None。
    """
    if not has_credentials():
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": COOKIE_STRING,
        "Accept": "*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://claude.ai/chats",
        "Origin": "https://claude.ai",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "anthropic-client-version": "1.0.0",
    }

    try:
        res = requests.get(
            CLAUDE_API_URL,
            headers=headers,
            impersonate="chrome120",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:
        log.error("Fetch 發生例外: %s", e)
        return None

    if res.status_code != 200:
        log.error("HTTP 錯誤: %s", res.status_code)
        log.error("伺服器回應內文: %s", res.text[:200])
        return None

    data = res.json()
    five_hr = data.get("five_hour", {}) or {}
    seven_day = data.get("seven_day", {}) or {}

    s_util = _normalize_utilization(five_hr.get("utilization", 0) or 0)
    w_util = _normalize_utilization(seven_day.get("utilization", 0) or 0)

    # 抓取 ISO 時間字串 (session 重置時間可能在 limits 陣列裡)
    s_reset_iso = five_hr.get("resets_at")
    if not s_reset_iso and "limits" in data:
        for lim in data.get("limits", []):
            if lim.get("kind") == "session" and lim.get("resets_at"):
                s_reset_iso = lim.get("resets_at")
                break
    w_reset_iso = seven_day.get("resets_at")

    s_reset = parse_iso_datetime(s_reset_iso)
    w_reset = parse_iso_datetime(w_reset_iso)

    packet = struct.pack(
        "BBBBBBBBB",
        s_util,
        s_reset.hour,
        s_reset.minute,
        w_util,
        w_reset.month,
        w_reset.day,
        w_reset.hour,
        w_reset.minute,
        BRIGHTNESS,
    )

    log.info("額度數據讀取成功：")
    log.info(" ├─ 5-Hour Session : %s%% (重置時間: %s)", s_util, s_reset.format_short())
    log.info(" └─ 7-Day Weekly   : %s%% (重置時間: %s)", w_util, w_reset.format_full())

    return packet


def timestamp() -> str:
    """相容舊版腳本用的手動時間戳字串（新程式碼請改用 logging，它會自動加時間）"""
    return datetime.now().strftime("%H:%M:%S")
