import asyncio
import os
import struct
import sys
from datetime import datetime
from dateutil import parser, tz
from dotenv import load_dotenv
from curl_cffi import requests
from bleak import BleakClient, BleakScanner

# 確保控制台輸出正常處理 UTF-8 字元 (避免 Windows CP950 編碼錯誤)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
    print("[WARNING] 未檢測到完整的 CLAUDE_COOKIE 或 CLAUDE_ORG_ID 設定！")
    print("請先將根目錄下的 .env.example 複製為 .env，並填入您的認證資訊。")
    print("=================================================================")

CLAUDE_API_URL = f"https://claude.ai/api/organizations/{ORG_ID}/usage"

# ESP32 廣播名稱與 UUID (須與 ESP32 韌體設定一致)
DEVICE_NAME = "ClaudeMeter"
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHARACTERISTIC_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
# ============================================

def parse_iso_datetime(iso_str):
    if not iso_str:
        return 0, 0, 0, 0
    try:
        # 明確解析 UTC 時間並轉為電腦當前本地時區 (如 UTC+8)
        utc_dt = parser.parse(iso_str)
        local_dt = utc_dt.astimezone(tz.tzlocal())
        return local_dt.month, local_dt.day, local_dt.hour, local_dt.minute
    except Exception as e:
        print(f"時間解析失敗: {e}")
        return 0, 0, 0, 0

def fetch_data_sync():
    if not COOKIE_STRING or not ORG_ID:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 錯誤: 缺少 .env 認證設定，跳過 Fetch。")
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
        "anthropic-client-version": "1.0.0"
    }
    try:
        res = requests.get(CLAUDE_API_URL, headers=headers, impersonate="chrome120", timeout=15)
        if res.status_code == 200:
            data = res.json()

            five_hr = data.get("five_hour", {}) or {}
            seven_day = data.get("seven_day", {}) or {}

            raw_s_util = five_hr.get("utilization", 0) or 0
            raw_w_util = seven_day.get("utilization", 0) or 0

            # API 若回傳 <= 1.0 代表舊格式(小數)，若 > 1.0 代表新格式(百分比)
            s_val = raw_s_util * 100 if raw_s_util <= 1.0 else raw_s_util
            w_val = raw_w_util * 100 if raw_w_util <= 1.0 else raw_w_util

            s_util = min(max(round(s_val), 0), 100)
            w_util = min(max(round(w_val), 0), 100)

            # 抓取 ISO 時間字串
            s_reset_iso = five_hr.get("resets_at")
            if not s_reset_iso and "limits" in data:
                for lim in data.get("limits", []):
                    if lim.get("kind") == "session" and lim.get("resets_at"):
                        s_reset_iso = lim.get("resets_at")
                        break

            w_reset_iso = seven_day.get("resets_at")

            # 時間解析 (轉為本地時間)
            s_mon, s_day, s_h, s_m = parse_iso_datetime(s_reset_iso)
            w_mon, w_day, w_h, w_m = parse_iso_datetime(w_reset_iso)

            # 格式化 Terminal 顯示字串
            if s_mon == 0 and s_day == 0 and s_h == 0 and s_m == 0:
                s_reset_str = "Ready"
            else:
                s_reset_str = f"{s_mon:02d}/{s_day:02d} {s_h:02d}:{s_m:02d}"

            if w_mon == 0 and w_day == 0 and w_h == 0 and w_m == 0:
                w_reset_str = "Ready"
            else:
                w_reset_str = f"{w_mon:02d}/{w_day:02d} {w_h:02d}:{w_m:02d}"

            # 打包 8-byte 封包發送給 ESP32 (維持原生數據格式)
            packet = struct.pack("BBBBBBBB", s_util, s_h, s_m, w_util, w_mon, w_day, w_h, w_m)

            # Terminal 日誌印出
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 額度數據讀取成功：")
            print(f" ├─ 5-Hour Session : {s_util}% (重置時間: {s_reset_str})")
            print(f" └─ 7-Day Weekly   : {w_util}% (重置時間: {w_reset_str})")

            return packet

        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] HTTP 錯誤: {res.status_code}")
            print(f"伺服器回應內文: {res.text[:200]}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetch 發生例外: {e}")
    return None

async def send_to_esp32(packet):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在搜尋 ESP32 ({DEVICE_NAME})...")

    def match_device(d, ad):
        name = d.name or ad.local_name or ""
        return DEVICE_NAME in name

    device = await BleakScanner.find_device_by_filter(match_device, timeout=10.0)

    if not device:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 未找到 ESP32 裝置，等待下次重試...")
        return False

    try:
        async with BleakClient(device, timeout=10.0) as client:
            if client.is_connected:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 已成功連線至 ESP32！寫入數據中...")
                await client.write_gatt_char(CHARACTERISTIC_UUID, packet, response=True)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 數據成功寫入 ESP32！")
                return True
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] BLE 寫入失敗: {e}")
    return False

async def main():
    print("啟動 Windows BLE Client Bridge...")
    while True:
        packet = await asyncio.to_thread(fetch_data_sync)
        if packet:
            await send_to_esp32(packet)
        await asyncio.sleep(60) # 每 60 秒循環一次

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n停止 BLE Daemon")
