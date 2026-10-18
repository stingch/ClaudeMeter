"""
ClaudeMeter 測試腳本 (send_test.py)

不經過 Claude.ai API，直接用參數指定用量送到 ESP32，方便測試螢幕顯示
（例如進度條在高用量時的變色）。

用法（在 pc_bridge 目錄下，先關掉正在跑的 ble_bridge，避免數值被蓋掉）：
    ..\\claudemeter-env\\Scripts\\python.exe send_test.py 85            # Current 85%
    ..\\claudemeter-env\\Scripts\\python.exe send_test.py 85 -w 95      # Current 85%、Weekly 95%
    ..\\claudemeter-env\\Scripts\\python.exe send_test.py --sweep       # Current 0→100% 每 3 秒跳 10%
    ..\\claudemeter-env\\Scripts\\python.exe send_test.py 1.0 --raw     # 模擬 API 原始值 1.0，驗證不會變成 100%
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from datetime import datetime, timedelta

from bleak import BleakClient, BleakScanner

from common import (
    CHARACTERISTIC_UUID,
    DEVICE_NAME,
    ResetTime,
    _normalize_utilization,
    _pack,
    log,
    setup_logging,
)

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def reset_in(hours: float) -> ResetTime:
    t = datetime.now() + timedelta(hours=hours)
    return ResetTime(t.month, t.day, t.hour, t.minute)


def to_pct(value: float, raw: bool) -> int:
    """--raw 時走跟正式 bridge 一樣的轉換流程，否則直接當百分比"""
    return _normalize_utilization(value) if raw else min(max(round(value), 0), 100)


async def run(args: argparse.Namespace) -> None:
    log.info("正在搜尋 ESP32 (%s)...", DEVICE_NAME)
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: DEVICE_NAME in (d.name or ad.local_name or ""), timeout=10.0
    )
    if not device:
        log.error("找不到 ESP32，請確認已開機，且沒有其他程式（例如 ble_bridge）佔用連線")
        return

    s_reset = ResetTime(0, 0, 0, 0) if args.ready else reset_in(3)
    w_reset = reset_in(24 * 4)
    weekly = to_pct(args.weekly, args.raw)

    async with BleakClient(device, timeout=10.0) as client:
        log.info("已連線，開始送出測試數據")

        async def send(current: int) -> None:
            packet = _pack(current, s_reset, weekly, w_reset)
            await client.write_gatt_char(CHARACTERISTIC_UUID, packet, response=True)
            log.info("送出 → Current %3d%% | Weekly %3d%%", current, weekly)

        if args.sweep:
            for pct in range(0, 101, args.step):
                await send(pct)
                await asyncio.sleep(args.delay)
        else:
            await send(to_pct(args.current, args.raw))

    log.info("完成。ESP32 會維持這個畫面約 5 分鐘，之後沒收到新數據會顯示 Offline")


def main() -> None:
    p = argparse.ArgumentParser(description="直接送測試用量到 ClaudeMeter 螢幕")
    p.add_argument("current", nargs="?", type=float, default=50, help="Current 用量 %% (預設 50)")
    p.add_argument("-w", "--weekly", type=float, default=30, help="Weekly 用量 %% (預設 30)")
    p.add_argument("--raw", action="store_true", help="把數值當成 API 原始值，套用正式的轉換邏輯")
    p.add_argument("--ready", action="store_true", help="Current 的重置時間顯示為 Ready")
    p.add_argument("--sweep", action="store_true", help="Current 從 0%% 逐步跑到 100%%")
    p.add_argument("--step", type=int, default=10, help="--sweep 每次增加幾 %% (預設 10)")
    p.add_argument("--delay", type=float, default=3, help="--sweep 每步間隔秒數 (預設 3)")
    args = p.parse_args()

    setup_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
