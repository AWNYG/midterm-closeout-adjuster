"""端到端：示例输入 → 24 个 decisions + summary，结构完整、区间方向正确。"""

import json
from pathlib import Path

from src.main import DEFAULT_CONFIG, run, load_config, save_result

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample" / "2026-08-22.json"


def test_e2e_sample(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    with open(SAMPLE, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = run(config, data)
    save_result(result, out_dir=tmp_path)

    assert len(result["decisions"]) == 24
    for d in result["decisions"]:
        assert d["period"] in range(24)
        assert d["action"] in ("buy", "sell", "hold")
        assert "reasons" in d and "mv" in d and "expected_pnl_cny" in d
        assert set(d.keys()) >= {"period", "action", "price_range", "volume_mwh",
                                 "orders", "mv", "expected_pnl_cny", "reasons"}
        if d["action"] == "hold":
            assert d["price_range"] is None
            assert d["volume_mwh"] == 0
        else:
            lo, hi = d["price_range"]
            assert lo <= hi
            assert d["orders"][0]["volume"] == d["volume_mwh"]
            assert lo <= d["orders"][0]["price"] <= hi
            # 方向一致：买的价格在 ask 侧、卖在 bid 侧（与 mv 比较）
            if d["action"] == "buy":
                assert d["mv"] - d["price_range"][1] >= 0
            else:
                assert d["price_range"][0] - d["mv"] >= 0

    s = result["summary"]
    assert s["buy_total_mwh"] >= 0
    assert s["sell_total_mwh"] >= 0
    assert s["hold_count"] + sum(1 for d in result["decisions"] if d["action"] != "hold") == 24
    assert s["confidence"] == config.get("confidence", 1.0)

    out_file = tmp_path / "2026-08-22-decision.json"
    assert out_file.exists()


def _sample_data() -> dict:
    with open(SAMPLE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_config_params_thread_through(tmp_path, monkeypatch):
    """params.yaml 的结算/考核参数必须传入 decide_period（防死配置回归）。"""
    import src.main as m

    captured = {}
    orig = m.decide_period

    def spy(avg_trade_price, Q_c, L, S, P_avg, **kw):
        captured.update(kw)
        return orig(avg_trade_price, Q_c, L, S, P_avg, **kw)

    monkeypatch.setattr(m, "decide_period", spy)
    config = load_config(DEFAULT_CONFIG)
    run(config, _sample_data())

    assert captured["band_low"] == config["deviation_band_low"]
    assert captured["band_high"] == config["deviation_band_high"]
    assert captured["recover"] == config["recover_factor"]
    assert captured["shrink"] == config["shrink_bands"]
    assert captured["initial_order_policy"] == config["initial_order_policy"]

    cfg = dict(config)
    cfg["deviation_band_low"] = 0.9
    cfg["deviation_band_high"] = 1.1
    cfg["recover_factor"] = 1.5
    cfg["shrink_bands"] = 0.3
    run(cfg, _sample_data())
    assert captured["band_low"] == 0.9
    assert captured["band_high"] == 1.1
    assert captured["recover"] == 1.5
    assert captured["shrink"] == 0.3

    cfg2 = dict(cfg)
    cfg2["confidence_shrink"] = False
    run(cfg2, _sample_data())
    assert captured["shrink"] == 0.0


def test_marginal_value_recover_matters():
    """recover 改变必须影响欠配时段的 MV（k = recover·P_avg − (recover−1)·S）。"""
    from src.settlement import marginal_value
    assert marginal_value(800, 1000, 300, 400, recover=1.1) == 410
    assert marginal_value(800, 1000, 300, 400, recover=1.5) == 450


def test_over_limit_position_can_sell(tmp_path):
    """超限持仓允许卖出减仓（不被持仓限额否决）。"""
    config = load_config(DEFAULT_CONFIG)
    data = _sample_data()
    data["position_limit_mwh"] = min(c["volume_mwh"] for c in data["contract"]) / 2  # 低于全部时段持仓 → 只能卖不能买
    result = run(config, data)
    s = result["summary"]
    assert s["buy_total_mwh"] == 0
    assert s["sell_total_mwh"] >= 0
    for d in result["decisions"]:
        if d["action"] == "sell":
            assert "position_cap" not in d["reasons"]
