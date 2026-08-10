"""
ClaudeMeter PC Bridge - Windows

透過 BLE Client 模式主動連線到 ESP32，把 Claude.ai Usage API 的資料寫入
ESP32 的 GATT characteristic。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from common import (
    CHARACTERISTIC_UUID,
    DEVICE_NAME,
    POLL_INTERVAL_SECONDS,
    fetch_usage_packet,
    log,
    setup_logging,
)

# 確保控制台輸出正常處理 UTF-8 字元 (避免 Windows CP950 編碼錯誤)
if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def find_esp32() -> BLEDevice | None:
    log.info("正在搜尋 ESP32 (%s)...", DEVICE_NAME)

    def match_device(d: BLEDevice, ad) -> bool:  # noqa: ANN001
        name = d.name or ad.local_name or ""
        return DEVICE_NAME in name

    return await BleakScanner.find_device_by_filter(match_device, timeout=10.0)


async def send_to_esp32(packet: bytes) -> bool:
    device = await find_esp32()
    if not device:
        log.warning("未找到 ESP32 裝置，等待下次重試...")
        return False

    try:
        async with BleakClient(device, timeout=10.0) as client:
            if client.is_connected:
                log.info("已成功連線至 ESP32！寫入數據中...")
                await client.write_gatt_char(CHARACTERISTIC_UUID, packet, response=True)
                log.info("數據成功寫入 ESP32！")
                return True
    except Exception as e:
        log.error("BLE 寫入失敗: %s", e)
    return False


async def main() -> None:
    setup_logging()
    log.info("啟動 Windows BLE Client Bridge...")
    while True:
        packet = await asyncio.to_thread(fetch_usage_packet)
        if packet:
            await send_to_esp32(packet)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n停止 BLE Daemon")
