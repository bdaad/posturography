#!/usr/bin/env python3
"""Measure BLE notification sampling rate from ESP32-AD7193-BLE."""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional


DEFAULT_DEVICE_NAME = "ESP32-AD7193-BLE"
DATA_CHAR_UUID = "7b7f0002-8f4c-4d52-a9f8-9c7d2b1f0001"
FRAME = struct.Struct("<IIIII")


@dataclass
class RateState:
    total_count: int = 0
    count_1s: int = 0
    count_10s: int = 0
    bad_frames: int = 0
    dropped_by_index: int = 0
    last_sample_index: Optional[int] = None
    start_all: float = 0.0
    start_1s: float = 0.0
    start_10s: float = 0.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BLE版AD7193の受信サンプリングレートを確認します。"
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_DEVICE_NAME,
        help=f"スキャンするBLEデバイス名。既定値: {DEFAULT_DEVICE_NAME}",
    )
    parser.add_argument(
        "--address",
        help="BLEアドレス/UUIDを直接指定します。指定時は名前スキャンを省略します。",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=8.0,
        help="BLEスキャン時間[秒]。既定値: 8",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="指定秒数で自動終了します。未指定ならCtrl+Cまで継続します。",
    )
    parser.add_argument(
        "--print-samples",
        action="store_true",
        help="受信サンプルを毎回表示します。100Hz付近では出力が多くなります。",
    )
    return parser


async def find_device(args, BleakScanner):
    if args.address:
        return args.address

    print(f"'{args.name}' を {args.scan_timeout:.1f} 秒スキャンします...")
    devices = await BleakScanner.discover(timeout=args.scan_timeout)
    matches = [device for device in devices if device.name == args.name]

    if matches:
        device = matches[0]
        print(f"Found: {device.name} [{device.address}]")
        return device

    print("対象BLEデバイスが見つかりませんでした。")
    if devices:
        print("検出されたBLEデバイス:")
        for device in devices:
            shown_name = device.name or "(no name)"
            print(f"  - {shown_name} [{device.address}]")
    else:
        print("BLEデバイスは検出されませんでした。")
    return None


def update_drop_count(state: RateState, sample_index: int) -> None:
    if state.last_sample_index is None:
        state.last_sample_index = sample_index
        return

    expected = (state.last_sample_index + 1) & 0xFFFFFFFF
    if sample_index != expected:
        state.dropped_by_index += (sample_index - expected) & 0xFFFFFFFF
    state.last_sample_index = sample_index


def print_periodic_rate(state: RateState, now: float) -> None:
    if now - state.start_1s >= 1.0:
        elapsed = now - state.start_1s
        rate = state.count_1s / elapsed if elapsed > 0 else 0.0
        print(
            f"[{time.strftime('%H:%M:%S')}] "
            f"1秒間のサンプル数: {state.count_1s} samples "
            f"(約 {rate:.1f} Hz)"
        )
        state.count_1s = 0
        state.start_1s = now

    if now - state.start_10s >= 10.0:
        elapsed = now - state.start_10s
        rate = state.count_10s / elapsed if elapsed > 0 else 0.0
        print(
            f"[{time.strftime('%H:%M:%S')}] "
            f"10秒間のサンプル数: {state.count_10s} samples "
            f"(平均 {rate:.1f} Hz, 累計 {state.total_count} samples, "
            f"index欠落 {state.dropped_by_index})"
        )
        state.count_10s = 0
        state.start_10s = now


async def run(args) -> int:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        print("bleak が見つかりません。")
        print("インストール例: pip install bleak")
        return 1

    device = await find_device(args, BleakScanner)
    if device is None:
        return 1

    state = RateState()
    state.start_all = time.perf_counter()
    state.start_1s = state.start_all
    state.start_10s = state.start_all

    first_samples_to_show = 5

    def handle_notification(_, data: bytearray) -> None:
        nonlocal first_samples_to_show

        now = time.perf_counter()
        if len(data) != FRAME.size:
            state.bad_frames += 1
            if state.bad_frames <= 5:
                print(f"[WARN] フレーム長が不正です: {len(data)} bytes")
            return

        sample_index, dat1, dat2, dat3, dat4 = FRAME.unpack(bytes(data))
        update_drop_count(state, sample_index)

        state.total_count += 1
        state.count_1s += 1
        state.count_10s += 1

        if args.print_samples or first_samples_to_show > 0:
            print(f"{sample_index},{dat1},{dat2},{dat3},{dat4}")
            first_samples_to_show -= 1

        print_periodic_rate(state, now)

    async with BleakClient(device) as client:
        if not client.is_connected:
            print("BLE接続に失敗しました。")
            return 1

        print("BLE接続しました。")
        try:
            print(f"MTU size: {client.mtu_size}")
        except Exception:
            pass
        print("サンプリングレートを測定します。Ctrl+C で終了します。\n")

        await client.start_notify(DATA_CHAR_UUID, handle_notification)

        try:
            if args.duration is None:
                while True:
                    await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(max(0.0, args.duration))
        finally:
            await client.stop_notify(DATA_CHAR_UUID)

    elapsed = max(time.perf_counter() - state.start_all, 1e-9)
    print(
        "\n終了しました。"
        f"総サンプル数: {state.total_count} samples, "
        f"平均: {state.total_count / elapsed:.1f} Hz, "
        f"index欠落: {state.dropped_by_index}, "
        f"不正フレーム: {state.bad_frames}"
    )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n停止要求を受けました。終了します。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
