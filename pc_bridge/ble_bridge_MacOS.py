"""
ClaudeMeter PC Bridge - macOS

[⚠️ EXPERIMENTAL / UNTESTED / KNOWN BROKEN]
Note: The macOS BLE bridge script is currently experimental and untested.
Due to CoreBluetooth restrictions on macOS, this script runs a GATT Server locally
which ESP32 connects to, or broadcasts updates via GATT notifications.

KNOWN ISSUE: as of bleak 0.21-3.0.x, the mainline `bleak` package does not
actually ship a `BleakServer` / peripheral-mode API (it is client/scanner only),
so the import below will fail with `ImportError` on a stock `pip install bleak`.
Implementing a real GATT server on macOS requires talking to CoreBluetooth's
peripheral manager directly (e.g. via `pyobjc`), which this script does not
yet do. Left as-is (with a `type: ignore`) so it documents the intended
design without breaking CI; treat this file as a starting point, not a
working macOS bridge, until that peripheral-mode implementation lands.
"""

from __future__ import annotations

import asyncio

from bleak import BleakServer  # type: ignore[attr-defined]  # see KNOWN ISSUE above

from common import (
    CHARACTERISTIC_UUID,
    POLL_INTERVAL_SECONDS,
    SERVICE_UUID,
    fetch_usage_packet,
    log,
    setup_logging,
)

# 預設 9-bytes 數據包: [s_util, s_h, s_m, w_util, w_mon, w_day, w_h, w_m, brightness]
latest_packet: bytes = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00"


def update_gatt_characteristic_value(server: BleakServer, char_uuid: str, new_value: bytes) -> None:
    """正確由 BleakServer 直接取得特徵值並更新內部 value (針對 ESP32 發起 Read)"""
    try:
        char = server.get_characteristic(char_uuid)
        if char:
            char.value = new_value
    except Exception:
        log.exception("更新 GATT characteristic value 失敗")


async def api_poll_loop(server: BleakServer) -> None:
    global latest_packet
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        new_packet = await asyncio.to_thread(fetch_usage_packet)
        if new_packet:
            latest_packet = new_packet

            # 1. 更新特徵值底層 Value (針對 ESP32 發起 BLE Read)
            update_gatt_characteristic_value(server, CHARACTERISTIC_UUID, latest_packet)

            # 2. 發送 Notify 推播 (針對已連線訂閱的 ESP32)
            try:
                await server.notify(CHARACTERISTIC_UUID, data=latest_packet)
            except Exception:
                log.exception("BLE notify 失敗")


async def main() -> None:
    setup_logging()
    log.warning("=" * 73)
    log.warning("[⚠️ 實驗性/未測試版本 - EXPERIMENTAL / UNTESTED]")
    log.warning("此 macOS BLE Daemon 腳本目前尚未實測，僅供開發研究與測試參考。")
    log.warning("=" * 73)
    log.info("啟動 macOS BLE Daemon...")

    global latest_packet
    initial_packet = await asyncio.to_thread(fetch_usage_packet)
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
    log.info("BLE Daemon 啟動成功！等待 ESP32 連線 (Service UUID: %s)...", SERVICE_UUID)

    try:
        await api_poll_loop(server)
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n停止 BLE Daemon")
