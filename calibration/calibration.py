import re
import serial
import serial.tools.list_ports
import time
import csv
from datetime import datetime
import os
import sys

# ========= Parameters =========
BIT_RATE = 115200                 # Serial baudrate
KEEP_SAMPLES = 1600                # 最終的に残すサンプル数
DROP_HEAD = 40                    # 先頭ドロップ
DROP_TAIL = 40                    # 末尾ドロップ（確保後に切り落とす）
BASELINE_READY_N = 120            # 基準統計を作る最小サンプル数（DROP_HEAD後の良品）
MAD_MULT = 8.0                    # 外れ値しきい値: median ± MAD*係数
MAD_FALLBACK = 3                  # MAD=0時の代替しきい（整数なので±3カウント）
SAMPLING_HZ = 80                  # 目安（タイムアウト計算用）
TIMEOUT_MARGIN = 5.0              # 余裕秒
SHOW_AFTER_SAVE = False           # 画像は保存のみ（Trueにすると表示）
CSV_HEADER = ["DAT1", "DAT2", "DAT3", "DAT4"]

# 読み切りタイムアウト（外れ値/ドロップ分の余裕を多めに）
READ_TIMEOUT_SEC = (KEEP_SAMPLES + DROP_HEAD + DROP_TAIL + 300) / SAMPLING_HZ + TIMEOUT_MARGIN

# ========= Utilities =========
def list_com_ports_numbered():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No COM ports found.")
        return []
    print("Available COM ports:")
    for idx, p in enumerate(ports):
        desc = f" - {p.description}" if getattr(p, "description", None) else ""
        print(f"  [{idx}] {p.device}{desc}")
    return ports

def choose_com_port():
    while True:
        ports = list_com_ports_numbered()
        if not ports:
            input("Connect a device and press Enter to rescan...")
            continue
        sel = input("Select port by number (e.g., 0): ").strip()
        try:
            i = int(sel)
            if 0 <= i < len(ports):
                return ports[i].device
            else:
                print(f"Out of range. Enter 0..{len(ports)-1}.")
        except ValueError:
            print("Please enter a valid integer.")

def ask_filename():
    name = input("Enter CSV filename (extension optional): ").strip()
    if not name:
        name = f"serial_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not name.lower().endswith(".csv"):
        name += ".csv"
    base, ext = os.path.splitext(name)
    final = name
    i = 1
    while os.path.exists(final):
        final = f"{base}_{i}{ext}"
        i += 1
    return final

def wait_for_start():
    s = input("Press 'y' + Enter to start capture: ").strip().lower()
    return s == "y"

def parse_line_to_ints(line_bytes):
    """Expect: b'v1,v2,v3,v4\\r\\n' -> [int,int,int,int] or None"""
    try:
        line_bytes = re.sub(rb'\r?\n$', b'', line_bytes)
    except re.error:
        pass
    try:
        text = line_bytes.decode(errors="strict")
    except UnicodeDecodeError:
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        return None
    vals = []
    for p in parts:
        if p == "":
            return None
        try:
            vals.append(int(p))
        except ValueError:
            return None
    return vals

def median_and_mad(int_list):
    """整数配列から(中央値, MAD)を返す"""
    import numpy as np
    arr = np.asarray(int_list, dtype=np.int64)
    med = int(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return med, mad

# ========= Plotting =========
# ========= Plotting =========
def plot_and_save_hist_grid(rows, csv_path):
    """
    rows: list of [v1,v2,v3,v4]  (長さ KEEP_SAMPLES)
    各chごと整数ヒスト(棒)
    - x軸レンジは各chごとに自動（数値は揃えない）
    - x軸目盛りの「間隔(ステップ)」のみ4面で統一
    - y軸は4面で固定
    - xラベル重なり対策あり
    """
    import os
    import math
    import matplotlib
    if not SHOW_AFTER_SAVE:
        try:
            matplotlib.use("Agg")
        except Exception:
            pass
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import ScalarFormatter, MaxNLocator, MultipleLocator

    base, _ = os.path.splitext(csv_path)
    png_path = base + "_hist.png"

    # チャンネルごとに配列化
    arr = np.asarray(rows, dtype=np.int64)
    chs = [arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]]

    # 各chの頻度（ユニーク値ごと）
    uniqs = []
    counts = []
    spans = []  # 各chのレンジ幅
    for ch in chs:
        if ch.size:
            u, c = np.unique(ch, return_counts=True)
            span = int(u.max() - u.min())
        else:
            u, c = np.array([]), np.array([])
            span = 0
        uniqs.append(u)
        counts.append(c)
        spans.append(span)

    # ---- 共通 y 上限（4面固定）----
    ymax = 1
    for c in counts:
        if c.size:
            ymax = max(ymax, int(c.max()))
    y_top = int((ymax * 1.10) + 0.5)  # 少しマージン

    # ---- x軸「間隔のみ」共通化するためのステップ算出 ----
    # 最大レンジのchに合わせて、だいたい6本前後の目盛りになる "nice step" を作る
    max_span = max(spans) if spans else 0

    def nice_step(raw_step: float) -> int:
        """ raw_step以上の見やすい整数ステップ(1,2,5*10^k系)を返す """
        if raw_step <= 1:
            return 1
        mag = 10 ** math.floor(math.log10(raw_step))
        for m in (1, 2, 5, 10):
            cand = m * mag
            if cand >= raw_step:
                return int(cand)
        return int(10 * mag)

    if max_span <= 0:
        x_step_global = 1
    else:
        target_ticks = 6  # 最大レンジの面でこの本数くらいにする
        raw = max_span / target_ticks
        x_step_global = nice_step(raw)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    titles = ["CH1", "CH2", "CH3", "CH4"]

    for ax, u, c, title in zip(axes.ravel(), uniqs, counts, titles):
        if u.size == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=12)
            xmin, xmax = -0.5, 0.5
        else:
            ax.bar(u, c, width=0.9, align="center",
                   edgecolor="black", linewidth=0.3)
            xmin = int(u.min()) - 0.5
            xmax = int(u.max()) + 0.5

        # xレンジは各chごとに決める（数値は揃えない）
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(0, y_top)

        ax.set_title(f"{title} – Value Distribution")
        ax.set_xlabel("Measured Value")
        ax.set_ylabel("Count")
        ax.grid(True, linestyle="--", alpha=0.3)

        # 非指数表記
        ax.ticklabel_format(style='plain', axis='x', useOffset=False)
        try:
            fmt = ax.xaxis.get_major_formatter()
            if isinstance(fmt, ScalarFormatter):
                fmt.set_scientific(False)
                fmt.set_useOffset(False)
        except Exception:
            pass

        # ---- ここが要件：目盛り間隔のみ4面統一 ----
        ax.xaxis.set_major_locator(MultipleLocator(base=x_step_global))
        # y軸は整数で揃える
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        # --------------------------------------------

        # ラベル重なり対策
        ax.tick_params(axis='x', labelrotation=45, labelsize=8)
        for label in ax.get_xticklabels():
            label.set_horizontalalignment('right')

    try:
        plt.savefig(png_path, dpi=140, bbox_inches="tight")
        print(f"Saved histogram image: {os.path.abspath(png_path)}")
        print(f"Global x tick step = {x_step_global}")
    except Exception as e:
        print(f"Failed to save image: {e}")

    if SHOW_AFTER_SAVE:
        try:
            plt.show()
        except Exception as e:
            print(f"Failed to display plot: {e}")

    plt.close(fig)






# ========= Main =========
def main():
    import json

    com = choose_com_port()
    try:
        ser = serial.Serial(com, BIT_RATE, timeout=0.2)
    except serial.SerialException as e:
        print(f"Failed to open port {com}: {e}")
        sys.exit(1)

    filename = ask_filename()
    print(f"Output CSV: {os.path.abspath(filename)}")

    if not wait_for_start():
        print("Canceled.")
        ser.close()
        return

    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    print("Capturing... (do not disconnect)")

    good_rows = []                 # 良品バッファ（DROP_HEAD後、外れ値除外済み）
    head_skipped = 0               # 先頭ドロップのカウンタ
    baseline_ready = False
    baseline_vals = [[], [], [], []]   # 基準生成用の各ch値（良品のみ）
    baseline = None

    # 外れ値ログ（保存用）
    outlier_cnt = [0, 0, 0, 0]
    outlier_examples = []          # [(idx_total, [v1..v4], reason), ...] 最初の数件だけ
    EXAMPLE_LIMIT = 8

    t0 = time.time()
    total_read = 0

    while (time.time() - t0) < READ_TIMEOUT_SEC:
        raw = ser.readline()
        if not raw:
            continue
        vals = parse_line_to_ints(raw)
        if vals is None:
            continue
        total_read += 1

        # 先頭ドロップ（途中行対策）
        if head_skipped < DROP_HEAD:
            head_skipped += 1
            continue

        # 基準が未構築なら一旦良品として蓄積（BASELINE_READY_N到達で基準確定）
        if not baseline_ready:
            for ch, v in zip(baseline_vals, vals):
                ch.append(v)
            good_rows.append(vals)   # 一旦採用
            if len(good_rows) >= BASELINE_READY_N:
                med = []
                thr = []
                for ch in baseline_vals:
                    m, mad = median_and_mad(ch)
                    med.append(m)
                    thr.append(MAD_FALLBACK if mad == 0 else MAD_MULT * mad)
                baseline = {"median": med, "thresh": thr}
                baseline_ready = True
                print(f"[Baseline] median={baseline['median']}, MAD*={MAD_MULT}, thr={baseline['thresh']}")
        else:
            # 外れ値判定：いずれかのchがしきい超過なら捨てる
            is_outlier = False
            reasons = []
            for i, v in enumerate(vals):
                dev = abs(v - baseline["median"][i])
                if dev > baseline["thresh"][i]:
                    outlier_cnt[i] += 1
                    is_outlier = True
                    reasons.append(f"CH{i+1}: |{v}-{baseline['median'][i]}|={dev} > {baseline['thresh'][i]:.2f}")
            if is_outlier:
                if len(outlier_examples) < EXAMPLE_LIMIT:
                    outlier_examples.append((total_read, vals, "; ".join(reasons)))
                continue  # カウントしない

            good_rows.append(vals)

        # 末尾ドロップ分も確保できたら終了
        if len(good_rows) >= (KEEP_SAMPLES + DROP_TAIL):
            break

    ser.close()

    # 末尾DROP_TAILを切り落として最終800を得る
    if len(good_rows) < (KEEP_SAMPLES + DROP_TAIL):
        print(f"[WARN] Timeout: collected {len(good_rows)} good rows (need {KEEP_SAMPLES + DROP_TAIL}).")
    use_rows = good_rows[:max(0, len(good_rows) - DROP_TAIL)]
    if len(use_rows) > KEEP_SAMPLES:
        use_rows = use_rows[:KEEP_SAMPLES]

    # ===== 保存（CSV, 画像, サマリ） =====
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(use_rows)
    print(f"Saved CSV: {os.path.abspath(filename)}")

    # サマリ保存（JSON + TXT）
    base, _ = os.path.splitext(filename)
    stats_json = base + "_stats.json"
    stats_txt  = base + "_summary.txt"

    total_outliers = int(sum(outlier_cnt))
    stats = {
        "csv": os.path.abspath(filename),
        "keep_samples": KEEP_SAMPLES,
        "drop_head": DROP_HEAD,
        "drop_tail": DROP_TAIL,
        "total_read_lines": total_read,
        "good_rows_collected": len(good_rows),
        "good_rows_used": len(use_rows),
        "outliers_total": total_outliers,
        "outliers_by_channel": {
            "CH1": int(outlier_cnt[0]),
            "CH2": int(outlier_cnt[1]),
            "CH3": int(outlier_cnt[2]),
            "CH4": int(outlier_cnt[3]),
        },
        "baseline": baseline,  # {"median":[...], "thresh":[...]} or None
        "example_outliers": [
            {"read_index": idx, "values": vals, "reason": reason}
            for idx, vals, reason in outlier_examples
        ],
    }

    try:
        import json
        with open(stats_json, "w", encoding="utf-8") as jf:
            json.dump(stats, jf, ensure_ascii=False, indent=2)
        print(f"Saved stats JSON: {os.path.abspath(stats_json)}")
    except Exception as e:
        print(f"[WARN] Failed to save stats JSON: {e}")

    try:
        with open(stats_txt, "w", encoding="utf-8") as tf:
            tf.write(f"CSV: {os.path.abspath(filename)}\n")
            tf.write(f"Keep samples: {KEEP_SAMPLES}\nDrop head: {DROP_HEAD}\nDrop tail: {DROP_TAIL}\n")
            tf.write(f"Total read lines: {total_read}\n")
            tf.write(f"Good rows collected: {len(good_rows)}\n")
            tf.write(f"Good rows used (after tail drop): {len(use_rows)}\n")
            tf.write("Outliers (counts): "
                     f"Total={total_outliers}, CH1={outlier_cnt[0]}, CH2={outlier_cnt[1]}, "
                     f"CH3={outlier_cnt[2]}, CH4={outlier_cnt[3]}\n")
            if baseline:
                tf.write(f"Baseline median: {baseline['median']}\n")
                tf.write(f"Baseline thresh: {baseline['thresh']}\n")
            if outlier_examples:
                tf.write("Examples:\n")
                for idx, vals, reason in outlier_examples:
                    tf.write(f"  - at read#{idx}: {vals} -> {reason}\n")
        print(f"Saved summary TXT: {os.path.abspath(stats_txt)}")
    except Exception as e:
        print(f"[WARN] Failed to save summary TXT: {e}")

    if len(use_rows) == 0:
        print("No data to plot.")
        return

    # プロット保存
    plot_and_save_hist_grid(use_rows, filename)

if __name__ == "__main__":
    main()
