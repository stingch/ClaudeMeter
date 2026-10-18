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

# 封包第 10 個 byte：資料狀態旗標 (須與韌體 main.cpp 的 DataState 對應)
STATUS_OK = 0       # 有正常的額度數據
STATUS_NO_DATA = 1  # API 回 200 但沒有任何額度視窗（例如免費方案）
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
    """
    把 API 的 utilization 轉成 0~100 的整數百分比。

    API 回傳的已經是百分比（例如 23.0 代表 23%），直接取整即可。

    注意：舊版這裡用「<= 1.0 就當成小數再乘 100」的猜測法，但百分比格式下
    剛重置時的用量常常就是 0.x% 或 1.0%，會被誤判成 50%、100%，
    造成「額度一重置 Current 反而顯示 100%」的 bug，所以拿掉這個猜測。
    """
    return min(max(round(raw), 0), 100)


def _pack(
    s_util: int = 0,
    s_reset: ResetTime = ResetTime(0, 0, 0, 0),
    w_util: int = 0,
    w_reset: ResetTime = ResetTime(0, 0, 0, 0),
    status: int = STATUS_OK,
) -> bytes:
    """
    打包 10-byte 封包：
    [s_util, s_h, s_m, w_util, w_mon, w_day, w_h, w_m, brightness, status]

    後兩個 byte（brightness / status）都是可選的，舊韌體只讀前 8 或 9 個 byte
    仍可正常運作。
    """
    return struct.pack(
        "BBBBBBBBBB",
        s_util,
        s_reset.hour,
        s_reset.minute,
        w_util,
        w_reset.month,
        w_reset.day,
        w_reset.hour,
        w_reset.minute,
        BRIGHTNESS,
        status,
    )


def fetch_usage_packet() -> bytes | None:
    """
    同步抓取 Claude.ai Usage API，回傳打包好的 10-byte 封包（格式見 _pack）。

    三種結果：
      - 正常取得數據           -> 回傳 status=STATUS_OK 的封包
      - API 通了但沒有額度數據 -> 回傳 status=STATUS_NO_DATA 的封包，讓螢幕明確
                                 顯示「無數據」而不是誤導性的 0%
      - 認證缺失 / 網路或 HTTP 失敗 -> 回傳 None，不送任何東西；韌體會在逾時後
                                 自己切換成 Offline 狀態
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
        if res.status_code in (401, 403):
            log.error("=> Cookie 很可能已經過期，請重新從瀏覽器複製一份到 .env")
        return None

    try:
        data = res.json()
    except Exception:
        log.error("回應不是合法的 JSON: %s", res.text[:200])
        return None

    five_hr = data.get("five_hour") or {}
    seven_day = data.get("seven_day") or {}
    limits = data.get("limits") or []

    # ---- 關鍵防呆：API 回 200 但整包都是 null / 空的 ----
    # 免費方案沒有五小時 / 七天的額度視窗，此時所有欄位都會是 null。
    # 舊版程式在這裡會算出 0% 送給螢幕，看起來就像「用量真的是 0」，
    # 完全無法跟正常狀態區分。現在改成送出明確的 NO_DATA 狀態。
    if not five_hr and not seven_day and not limits:
        log.warning("=" * 65)
        log.warning("API 回 200，但沒有任何額度數據（five_hour / seven_day / limits 全空）")
        log.warning("最常見原因：這個帳號是免費方案，沒有五小時 / 七天的額度視窗。")
        log.warning("已送出 NO_DATA 狀態，螢幕會顯示「No usage data」而非 0%%。")
        log.warning("=" * 65)
        return _pack(status=STATUS_NO_DATA)

    s_util = _normalize_utilization(five_hr.get("utilization", 0) or 0)
    w_util = _normalize_utilization(seven_day.get("utilization", 0) or 0)

    # 抓取 ISO 時間字串 (session 重置時間可能在 limits 陣列裡)
    s_reset_iso = five_hr.get("resets_at")
    if not s_reset_iso:
        for lim in limits:
            if lim.get("kind") == "session" and lim.get("resets_at"):
                s_reset_iso = lim.get("resets_at")
                break
    w_reset_iso = seven_day.get("resets_at")

    s_reset = parse_iso_datetime(s_reset_iso)
    w_reset = parse_iso_datetime(w_reset_iso)

    log.info("額度數據讀取成功：")
    log.info(" ├─ 5-Hour Session : %s%% (重置時間: %s)", s_util, s_reset.format_short())
    log.info(" └─ 7-Day Weekly   : %s%% (重置時間: %s)", w_util, w_reset.format_full())

    return _pack(s_util, s_reset, w_util, w_reset, STATUS_OK)


def timestamp() -> str:
    """相容舊版腳本用的手動時間戳字串（新程式碼請改用 logging，它會自動加時間）"""
    return datetime.now().strftime("%H:%M:%S")
