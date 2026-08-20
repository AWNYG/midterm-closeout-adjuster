"""收盘前执行入口：读配置 → 读输入 → 校验 → 循环 24 时段决策 → 输出留痕。"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .decision import decide_period
from .ingestion import load_input, validate
from .risk import check

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "params.yaml"
DEFAULT_INPUT_DIR = ROOT / "data" / "input"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "output"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _cfg_number(value, name: str, *, lo: float | None = None, hi: float | None = None) -> float:
    """校验配置数值为有限数字（拒绝字符串/None/NaN/Inf），越界抛 ValueError。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"配置 {name}={value!r} 必须是数值")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"配置 {name}={value} 必须是有限数值")
    if lo is not None and value < lo:
        raise ValueError(f"配置 {name}={value} 低于下限 {lo}")
    if hi is not None and value > hi:
        raise ValueError(f"配置 {name}={value} 高于上限 {hi}")
    return value


def run(config: dict, data: dict) -> dict:
    """执行 24 时段决策，返回结果 dict（纯计算，不写文件；留痕由 save_result 负责）。"""
    price_min = config.get("price_min_yuan_mwh", 0.0)
    price_max = config.get("price_max_yuan_mwh", 9999.0)
    spot_price_min = config.get("spot_price_min_yuan_mwh", price_min)
    spot_price_max = config.get("spot_price_max_yuan_mwh", price_max)
    validate(data, price_min, price_max, spot_price_min, spot_price_max)

    confidence = _cfg_number(config.get("confidence", 1.0), "confidence", lo=0.0, hi=1.0)
    theta = _cfg_number(config.get("theta_yuan_mwh", 2.0), "theta_yuan_mwh")
    confidence_min = _cfg_number(config.get("confidence_min", 0.5), "confidence_min", lo=0.0, hi=1.0)
    band_low = _cfg_number(config.get("deviation_band_low", 0.85), "deviation_band_low")
    band_high = _cfg_number(config.get("deviation_band_high", 1.15), "deviation_band_high")
    recover = _cfg_number(config.get("recover_factor", 1.1), "recover_factor")
    min_lot = data["min_lot_mwh"]
    position_limit = data["position_limit_mwh"]
    max_amount = config.get("max_amount_yuan_mwh")
    if max_amount is not None:
        max_amount = _cfg_number(max_amount, "max_amount_yuan_mwh", lo=0.0)
    shrink_bands = config.get("shrink_bands", 0.0)
    if not config.get("confidence_shrink", True):
        shrink_bands = 0.0
    else:
        shrink_bands = _cfg_number(shrink_bands, "shrink_bands", lo=0.0)
        if shrink_bands >= 1.0:
            raise ValueError(f"shrink_bands 必须在 [0, 1) 内: {shrink_bands}")
    if band_low >= band_high:
        raise ValueError(f"deviation_band_low({band_low}) 必须小于 deviation_band_high({band_high})")
    decisions = []
    buy_total = sell_total = hold_count = 0
    for t in range(24):
        c = data["contract"][t]
        Q_c = c["volume_mwh"]
        P_avg = c["avg_price_yuan_mwh"]
        L = data["load_forecast_mwh"][t]
        S = data["spot_forecast_yuan_mwh"][t]
        book = data["books"][t]

        d = decide_period(
            book, Q_c, L, S, P_avg,
            position_limit=position_limit,
            min_lot=min_lot,
            theta=theta,
            confidence=confidence,
            period=t,
            band_low=band_low,
            band_high=band_high,
            recover=recover,
            shrink=shrink_bands,
            initial_order_policy=config.get("initial_order_policy", "mid"),
        )
        if d.action != "hold":
            vol, reasons = check(
                d.volume_mwh, Q_c, position_limit,
                max_amount, d.orders[0]["price"],
                confidence, min_lot,
                confidence_min=confidence_min,
                side=d.action,
            )
            if vol <= 0:
                d = d.__class__(t, "hold", None, 0, [], d.mv, 0.0,
                                d.reasons + reasons)
            else:
                d = d.__class__(t, d.action, d.price_range, vol,
                                [{"side": d.orders[0]["side"], "price": d.orders[0]["price"], "volume": vol}],
                                d.mv, (d.expected_pnl_cny / d.volume_mwh * vol) if d.volume_mwh else 0.0,
                                d.reasons + reasons)

        decisions.append({
            "period": d.period,
            "action": d.action,
            "price_range": [round(x, 2) for x in d.price_range] if d.price_range else None,
            "volume_mwh": d.volume_mwh,
            "orders": d.orders,
            "mv": round(d.mv, 2),
            "expected_pnl_cny": round(d.expected_pnl_cny, 2),
            "reasons": d.reasons,
        })
        if d.action == "buy":
            buy_total += d.volume_mwh
        elif d.action == "sell":
            sell_total += d.volume_mwh
        else:
            hold_count += 1

    result = {
        "as_of": data["as_of"],
        "decision_time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "delivery_date": data["delivery_date"],
        "decisions": decisions,
        "summary": {
            "buy_total_mwh": buy_total,
            "sell_total_mwh": sell_total,
            "hold_count": hold_count,
            "confidence": confidence,
        },
    }
    return result


def save_result(result: dict, out_dir: str | Path = DEFAULT_OUTPUT_DIR) -> Path:
    """写结果留痕文件 data/output/<交割日>-decision.json（UTF-8），返回文件路径。"""
    out_path = Path(out_dir) / f"{result['delivery_date']}-decision.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return out_path


def print_summary(result: dict) -> None:
    rows = []
    for d in result["decisions"]:
        spread = abs(d["mv"] - (d["price_range"][0] if d["action"] == "sell" else (d["price_range"][1] if d["action"] == "buy" else d["mv"])))
        rows.append((spread, d))
    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"交割日 {result['delivery_date']}  置信度 {result['summary']['confidence']}")
    for spread, d in rows:
        if d["action"] == "hold":
            print(f"  t{d['period']:>2}  hold            mv={d['mv']:>8.2f}   {','.join(d['reasons'])}")
        else:
            lo, hi = d["price_range"]
            px = d["orders"][0]["price"]
            print(f"  t{d['period']:>2}  {d['action']:<4} [{lo:>8.2f}, {hi:>8.2f}] 挂单 {px:>8.2f} x {d['volume_mwh']:>7.2f}  "
                  f"mv={d['mv']:>8.2f} pnl={d['expected_pnl_cny']:>10.2f}  {','.join(d['reasons'])}")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="滚动撮合收盘微调")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--input", type=Path, default=None, help="输入 JSON；缺省取 data/input 下最新文件")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    config = None
    try:
        config = load_config(args.config)
        if args.input is not None:
            in_path = args.input
        else:
            files = sorted(DEFAULT_INPUT_DIR.glob("*.json"))
            if not files:
                print("data/input/ 下无输入文件", file=sys.stderr)
                return 1
            in_path = files[-1]
        data = load_input(in_path)
        result = run(config, data)
        save_result(result, args.out)
    except (ValueError, OSError, yaml.YAMLError) as e:
        print(f"执行失败: {e}", file=sys.stderr)
        return 1
    print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
