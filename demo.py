"""终端演示：跑一次收盘微调算法，逐时段打印决策明细与汇总，直观查看效果。

用法（在项目根目录）：
    python demo.py                          # 用示例输入跑
    python demo.py --input data/xxx.json    # 指定输入
    python demo.py --no-books               # 不打印逐时段盘口明细，只打决策表
"""

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from src.main import DEFAULT_CONFIG, DEFAULT_OUTPUT_DIR, load_config, run, save_result

SEP = "=" * 78
LINE = "-" * 78


def print_decision_table(result, data):
    print(SEP)
    print(f"交割日 {result['delivery_date']}   输入快照 {result['as_of']}   "
          f"置信度 {result['summary']['confidence']}")
    print(f"持仓限额 {data['position_limit_mwh']:.2f} MWh   最小交易单位 {data['min_lot_mwh']:.2f} MWh")
    print(LINE)
    hdr = (f"{'时段':>3} {'动作':<5} {'合同Q':>8} {'负荷L':>8} {'现货S':>8} {'均价P':>8} "
           f"{'MV_t0':>8} {'买1':>8} {'卖1':>8} {'报价区间':>14} {'量MWh':>7} {'挂单价':>8} "
           f"{'期望收益':>10}  原因")
    print(hdr)
    print(LINE)

    for d in result["decisions"]:
        t = d["period"]
        c = data["contract"][t]
        b = data["books"][t]
        if d["action"] == "hold":
            rng = "    --    "
            vol = 0.0
            px = 0.0
            pnl = 0.0
        else:
            lo, hi = d["price_range"]
            rng = f"[{lo:8.2f}, {hi:8.2f}]"
            vol = d["volume_mwh"]
            px = d["orders"][0]["price"]
            pnl = d["expected_pnl_cny"]
        print(f"{t:>3} {d['action']:<5} {c['volume_mwh']:>8.2f} {data['load_forecast_mwh'][t]:>8.2f} "
              f"{data['spot_forecast_yuan_mwh'][t]:>8.1f} {c['avg_price_yuan_mwh']:>8.1f} "
              f"{d['mv']:>8.2f} {b['bid'][0]['px']:>8.1f} {b['ask'][0]['px']:>8.1f} "
              f"{rng:>14} {vol:>7.2f} {px:>8.2f} {pnl:>10.2f}  {','.join(d['reasons'])}")

    s = result["summary"]
    print(LINE)
    print(f"汇总: 买入 {s['buy_total_mwh']:.2f} MWh | 卖出 {s['sell_total_mwh']:.2f} MWh | "
          f"不动 {s['hold_count']} 时段 | 期望总收益 ≈ "
          f"{sum(x['expected_pnl_cny'] for x in result['decisions']):.2f} 元")


def print_book_detail(result, data, top_n=None):
    print(SEP)
    print("逐时段盘口明细（仅非 hold 时段，按价差降序）")
    print(LINE)
    rows = []
    for d in result["decisions"]:
        if d["action"] == "hold":
            continue
        spread = abs(d["mv"] - (d["price_range"][0] if d["action"] == "sell" else d["price_range"][1]))
        rows.append((spread, d))
    rows.sort(key=lambda r: r[0], reverse=True)
    if top_n:
        rows = rows[:top_n]

    for spread, d in rows:
        t = d["period"]
        b = data["books"][t]
        print(f"\n时段 {t}  {d['action']:<4}  价差 {spread:.2f}   MV_t0={d['mv']:.2f}")
        print("  bid 档:  " + " | ".join(f"{lvl['px']:.1f}×{lvl['vol']:.2f}" for lvl in b["bid"]))
        print("  ask 档:  " + " | ".join(f"{lvl['px']:.1f}×{lvl['vol']:.2f}" for lvl in b["ask"]))
        lo, hi = d["price_range"]
        print(f"  报价区间 [{lo:.2f}, {hi:.2f}]   初始挂单 {d['orders'][0]['price']:.2f} × "
              f"{d['volume_mwh']:.2f} MWh   期望收益 {d['expected_pnl_cny']:.2f} 元")


def main(argv=None):
    parser = argparse.ArgumentParser(description="滚动撮合收盘微调 · 终端演示")
    parser.add_argument("--input", type=Path, default=None, help="输入 JSON（缺省用示例）")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-books", action="store_true", help="不打印逐时段盘口明细")
    parser.add_argument("--top", type=int, default=8, help="盘口明细最多显示几个时段")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    in_path = args.input or (Path(__file__).parent / "data" / "sample" / "2026-08-22.json")
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = run(config, data)
    save_result(result, DEFAULT_OUTPUT_DIR)

    print(SEP)
    print(f"滚动撮合收盘微调 · 演示运行  输入: {in_path.name}")
    print_decision_table(result, data)
    if not args.no_books:
        print_book_detail(result, data, top_n=args.top)
    print(SEP)
    print(f"完整结果已写入: {DEFAULT_OUTPUT_DIR / (data['delivery_date'] + '-decision.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
