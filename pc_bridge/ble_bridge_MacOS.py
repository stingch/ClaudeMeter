"""
[⚠️ EXPERIMENTAL / UNTESTED]
Note: The macOS BLE bridge script is currently experimental and untested.
Due to CoreBluetooth restrictions on macOS, this script runs a GATT Server locally
which ESP32 connects to, or broadcasts updates via GATT notifications.
"""

import asyncio
import os
import struct
from datetime import datetime
from dateutil import parser, tz
from dotenv import load_dotenv
from curl_cffi import requests
from bleak import BleakServer

# ================= 載入設定 =================
load_dotenv()

COOKIE_STRING = os.getenv("CLAUDE_COOKIE", "")
ORG_ID = os.getenv("CLAUDE_ORG_ID", "")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

if not COOKIE_STRING or not ORG_ID:
    print("=================================================================")
    print("[⚠️ 警告] 未檢測到完整的 CLAUDE_COOKIE 或 CLAUDE_ORG_ID 設定！")
    print("請先將根目錄下的 .env.example 複製為 .env，並填入您的認證資訊。")
    print("=================================================================")

CLAUDE_API_URL = f"https://claude.ai/api/organizations/{ORG_ID}/usage"

# 自訂 BLE UUID (須與 ESP32 程式碼完全一致)
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHARACTERISTIC_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
# =========================================

# 預設 8-bytes 數據包: [s_util, s_h, s_m, w_util, w_mon, w_day, w_h, w_m]
latest_packet = b"\x00\x00\x00\x00\x00\x00\x00\x00"

def parse_iso_datetime(iso_str):
    """解析 ISO 時間字串轉為 (Month, Day, Hour, Minute)"""
    if not iso_str:
        return 0, 0, 0, 0
    try:
        utc_dt = parser.parse(iso_str)
        local_dt = utc_dt.astimezone(tz.tzlocal())
        return local_dt.month, local_dt.day, local_dt.hour, local_dt.minute
    except Exception:
        return 0, 0, 0, 0

def fetch_data_sync():
    """同步抓取 API (於獨立 Thread 執行，避免阻塞 BLE Event Loop)"""
    if not COOKIE_STRING or not ORG_ID:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 錯誤: 缺少 .env 認證設定，跳過 Fetch。")
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": COOKIE_STRING,
        "Accept": "application/json",
        "Referer": "https://claude.ai/chats",
        "Origin": "https://claude.ai",
    }
    try:
        res = requests.get(CLAUDE_API_URL, headers=headers, impersonate="chrome120", timeout=15)
        if res.status_code == 200:
            data = res.json()
            five_hr = data.get("five_hour", {}) or {}
            seven_day = data.get("seven_day", {}) or {}

            raw_s_util = five_hr.get("utilization", 0) or 0
            raw_w_util = seven_day.get("utilization", 0) or 0

            s_val = raw_s_util * 100 if raw_s_util <= 1.0 else raw_s_util
            w_val = raw_w_util * 100 if raw_w_util <= 1.0 else raw_w_util

            s_util = min(max(round(s_val), 0), 100)
            w_util = min(max(round(w_val), 0), 100)

            _, _, s_h, s_m = parse_iso_datetime(five_hr.get("resets_at"))
            w_mon, w_day, w_h, w_m = parse_iso_datetime(seven_day.get("resets_at"))

            # 打包成 8-bytes 數據包
            packet = struct.pack("BBBBBBBB", s_util, s_h, s_m, w_util, w_mon, w_day, w_h, w_m)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] API 數據更新: Session={s_util}%, Weekly={w_util}%")
            return packet
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] HTTP 錯誤: {res.status_code}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetch 發生例外: {e}")
    return None

def update_gatt_characteristic_value(server: BleakServer, char_uuid: str, new_value: bytes):
    """正確由 BleakServer 直接取得特徵值並更新內部 value (針對 ESP32 發起 Read)"""
    try:
        char = server.get_characteristic(char_uuid)
        if char:
            char.value = new_value
    except Exception:
        pass

async def api_poll_loop(server: BleakServer):
    global latest_packet
    while True:
        await asyncio.sleep(60)  # 每 60 秒輪詢一次

        new_packet = await asyncio.to_thread(fetch_data_sync)
        if new_packet:
            latest_packet = new_packet

            # 1. 更新特徵值底層 Value (針對 ESP32 發起 BLE Read)
            update_gatt_characteristic_value(server, CHARACTERISTIC_UUID, latest_packet)

            # 2. 發送 Notify 推播 (針對已連線訂閱的 ESP32)
            try:
                await server.notify(CHARACTERISTIC_UUID, data=latest_packet)
            except Exception:
                pass

async def main():
    print("=========================================================================")
    print("[⚠️ 實驗性/未測試版本 - EXPERIMENTAL / UNTESTED]")
    print("此 macOS BLE Daemon 腳本目前尚未實測，僅供開發研究與測試參考。")
    print("=========================================================================")
    print("啟動 macOS BLE Daemon...")
    global latest_packet

    # 開機先抓取一次最新 API 資料
    initial_packet = await asyncio.to_thread(fetch_data_sync)
    if initial_packet:
        latest_packet = initial_packet

    server = BleakServer(
        services={
            SERVICE_UUID: [
                {
                    "uuid": CHARACTERISTIC_UUID,
                    "properties": ["read", "notify"],
                    "permissions": ["read"],
                    "value": latest_packet,
                }
            ]
        }
    )

    await server.start()
    print(f"BLE Daemon 啟動成功！等待 ESP32 連線 (Service UUID: {SERVICE_UUID})...")

    try:
        await api_poll_loop(server)
    finally:
        await server.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n停止 BLE Daemon")
