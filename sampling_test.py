# # 230426
# # arduinoでシリアル通信テスト + サンプリングレート計測
# import re
# import serial
# import serial.tools.list_ports
# import time
# import sys

# bitRate = 115200

# # 生データ(1行ごと)も表示したい場合 True
# PRINT_RAW_LINE = False


# def list_and_choose_com():
#     """
#     利用可能なシリアルポートを一覧表示し、
#     番号で選択してポート名(例: 'COM3' や '/dev/ttyUSB0')を返す
#     """
#     ports = list(serial.tools.list_ports.comports())

#     if not ports:
#         print("利用可能なシリアルポートが見つかりません。")
#         sys.exit(1)

#     print("利用可能なシリアルポート:")
#     for i, port in enumerate(ports):
#         # 例: [0] COM3 (USB-SERIAL CH340)
#         print(f"[{i}] {port.device} ({port.description})")

#     while True:
#         s = input("使用するポート番号を入力してください: ")
#         try:
#             idx = int(s)
#             if 0 <= idx < len(ports):
#                 return ports[idx].device
#         except ValueError:
#             pass
#         print("不正な番号です。もう一度入力してください。")


# def main():
#     # ポート選択
#     port_name = list_and_choose_com()

#     # タイムアウトを短め(0.1s)にして、タイマー処理を定期的に回せるようにする
#     try:
#         ser = serial.Serial(port_name, bitRate, timeout=0.1)
#     except serial.SerialException as e:
#         print(f"シリアルポートが開けませんでした: {e}")
#         sys.exit(1)

#     print(f"\n{port_name} をオープンしました。サンプリングレートを測定します。")
#     print("Ctrl + C で終了します。\n")

#     # 計測用カウンタと開始時刻
#     count_1s = 0
#     count_10s = 0
#     start_1s = time.time()
#     start_10s = start_1s

#     try:
#         while True:
#             # 1行読み込み（タイムアウト 0.1s）
#             line = ser.readline()

#             now = time.time()

#             if line:
#                 # 改行を除去
#                 line = re.sub(rb'[\r\n]+$', b'', line)

#                 # 必要なら生データも表示
#                 if PRINT_RAW_LINE:
#                     try:
#                         print(line.decode(errors="replace"))
#                     except UnicodeDecodeError:
#                         print(line)

#                 # サンプルを 1 件としてカウント
#                 count_1s += 1
#                 count_10s += 1

#             # --- 1秒ごとの出力 ---
#             if now - start_1s >= 1.0:
#                 # 1秒間に受信したサンプル数
#                 print(f"[{time.strftime('%H:%M:%S')}] "
#                       f"1秒間のサンプル数: {count_1s} samples (約 {count_1s:.1f} Hz)")

#                 count_1s = 0
#                 # 次の 1秒区間の基準時刻を更新
#                 start_1s = now

#             # --- 10秒ごとの出力 ---
#             if now - start_10s >= 10.0:
#                 # 10秒間に受信したサンプル数
#                 rate_10s = count_10s / 10.0
#                 print(f"[{time.strftime('%H:%M:%S')}] "
#                       f"10秒間のサンプル数: {count_10s} samples "
#                       f"(平均 {rate_10s:.1f} Hz)")

#                 count_10s = 0
#                 # 次の 10秒区間の基準時刻を更新
#                 start_10s = now

#     except KeyboardInterrupt:
#         print("\n停止要求を受けました。終了します。")
#     finally:
#         ser.close()
#         print("シリアルポートをクローズしました。")


# if __name__ == "__main__":
#     main()




import re
import serial
import serial.tools.list_ports
import time
import sys

bitRate = 115200

# 生データ(1行ごと)も表示したい場合 True
PRINT_RAW_LINE = False


def list_and_choose_com():
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        print("利用可能なシリアルポートが見つかりません。")
        sys.exit(1)

    print("利用可能なシリアルポート:")
    for i, port in enumerate(ports):
        print(f"[{i}] {port.device} ({port.description})")

    while True:
        s = input("使用するポート番号を入力してください: ")
        try:
            idx = int(s)
            if 0 <= idx < len(ports):
                return ports[idx].device
        except ValueError:
            pass
        print("不正な番号です。もう一度入力してください。")


def main():
    port_name = list_and_choose_com()

    try:
        ser = serial.Serial(port_name, bitRate, timeout=0.1)
    except serial.SerialException as e:
        print(f"シリアルポートが開けませんでした: {e}")
        sys.exit(1)

    print(f"\n{port_name} をオープンしました。サンプリングレートを測定します。")
    print("Ctrl + C で終了します。\n")

    count_1s = 0
    count_10s = 0
    start_1s = time.time()
    start_10s = start_1s

    prev_time = None  # サンプル間隔測定用

    try:
        while True:
            line = ser.readline()
            now = time.time()

            if line:
                line = re.sub(rb'[\r\n]+$', b'', line)

                # 生データ表示（オプション）
                if PRINT_RAW_LINE:
                    try:
                        print(line.decode(errors="replace"))
                    except UnicodeDecodeError:
                        print(line)

                # サンプリング間隔計算（最初のサンプル除外）
                if prev_time is not None:
                    interval_ms = (now - prev_time) * 1000
                    # print(f"サンプリング間隔: {interval_ms:.2f} ms")

                prev_time = now

                count_1s += 1
                count_10s += 1

            # --- 1秒ごとの統計出力 ---
            if now - start_1s >= 1.0:
                print(f"[{time.strftime('%H:%M:%S')}] "
                      f"1秒間のサンプル数: {count_1s} samples (約 {count_1s:.1f} Hz)")

                count_1s = 0
                start_1s = now

            # --- 10秒ごとの統計出力 ---
            if now - start_10s >= 10.0:
                rate_10s = count_10s / 10.0
                print(f"[{time.strftime('%H:%M:%S')}] "
                      f"10秒間のサンプル数: {count_10s} samples "
                      f"(平均 {rate_10s:.1f} Hz)")

                count_10s = 0
                start_10s = now

    except KeyboardInterrupt:
        print("\n停止要求を受けました。終了します。")
    finally:
        ser.close()
        print("シリアルポートをクローズしました。")


if __name__ == "__main__":
    main()
