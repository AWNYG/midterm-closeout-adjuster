"""健壮性测试：极端数值、畸形输入、直接 API 调用边界。"""

import json
import math
from pathlib import Path

import pytest

from src.ingestion import ValidationError, load_input, validate
from src.main import DEFAULT_CONFIG, load_config, run
from src.risk import check, floor_lot
from src.settlement import (
    marginal_value,
    settle_price,
    update_blended_avg,
)
from src.value import band_bounds, mv_curve

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample" / "2026-08-22.json"

cfg = load_config(DEFAULT_CONFIG)


def base_data() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def full_config(**over) -> dict:
    c = dict(cfg)
    c.update(over)
    return c


def _period(avg_price, **kw):
    from src.decision import decide_period
    params = dict(Q_c=1000, L=1000, S=300, P_avg=400, position_limit=5000,
                  min_lot=10, theta=2.0)
    params.update(kw)
    return decide_period(avg_price, **params)


# ---------- 输入校验：非法数值类型 / NaN / Inf ----------


class TestValidateFinite:
    @pytest.mark.parametrize("idx", range(24))
    def test_nan_spot_rejected(self, idx):
        d = base_data()
        d["spot_forecast_yuan_mwh"][idx] = float("nan")
        with pytest.raises(ValidationError):
            validate(d)

    def test_nan_load_rejected(self):
        d = base_data()
        d["load_forecast_mwh"][0] = float("nan")
        with pytest.raises(ValidationError):
            validate(d)

    def test_inf_load_rejected(self):
        d = base_data()
        d["load_forecast_mwh"][5] = float("inf")
        with pytest.raises(ValidationError):
            validate(d)

    def test_neg_inf_spot_rejected(self):
        d = base_data()
        d["spot_forecast_yuan_mwh"][3] = float("-inf")
        with pytest.raises(ValidationError):
            validate(d)

    @pytest.mark.parametrize("val", ["abc", None, True, [1], {}])
    def test_non_numeric_spot_rejected(self, val):
        d = base_data()
        d["spot_forecast_yuan_mwh"][7] = val
        with pytest.raises(ValidationError):
            validate(d)

    def test_non_numeric_load_rejected(self):
        d = base_data()
        d["load_forecast_mwh"][7] = "12"
        with pytest.raises(ValidationError):
            validate(d)

    def test_str_avg_rejected(self):
        d = base_data()
        d["avg_trade_price_yuan_mwh"][0] = "x"
        with pytest.raises(ValidationError):
            validate(d)

    def test_nan_avg_rejected(self):
        d = base_data()
        d["avg_trade_price_yuan_mwh"][1] = float("nan")
        with pytest.raises(ValidationError):
            validate(d)

    def test_inf_avg_rejected(self):
        d = base_data()
        d["avg_trade_price_yuan_mwh"][1] = float("inf")
        with pytest.raises(ValidationError):
            validate(d)

    def test_non_numeric_contract_volume(self):
        d = base_data()
        d["contract"][0]["volume_mwh"] = "1000"
        with pytest.raises(ValidationError):
            validate(d)

    def test_nan_contract_avg_price(self):
        d = base_data()
        d["contract"][0]["avg_price_yuan_mwh"] = float("nan")
        with pytest.raises(ValidationError):
            validate(d)

    def test_inf_position_limit(self):
        d = base_data()
        d["position_limit_mwh"] = float("inf")
        with pytest.raises(ValidationError):
            validate(d)

    def test_top_level_list_rejected(self):
        with pytest.raises(ValidationError):
            validate([1, 2, 3])

    def test_top_level_string_rejected(self):
        with pytest.raises(ValidationError):
            validate("as_of")

    def test_zero_volume_contract_rejected(self):
        d = base_data()
        d["contract"][0]["volume_mwh"] = 0.0
        with pytest.raises(ValidationError):
            validate(d)


# ---------- 输入校验：结构边界 ----------


class TestValidateStructure:
    def test_avg_zero_price_ok(self):
        # 平均成交价为 0（限价下界）合法
        d = base_data()
        d["avg_trade_price_yuan_mwh"][3] = 0.0
        validate(d)

    def test_tiny_lot_ok(self):
        # 最小单位可以是任意正数（如 1 MWh 单测场景）
        d = base_data()
        d["min_lot_mwh"] = 1.0
        validate(d)

    def test_fractional_volume_ok(self):
        # 小数电量校验层不拒（取整由决策层负责）
        d = base_data()
        d["contract"][0]["volume_mwh"] = 1234.5
        validate(d)

    def test_price_at_boundaries_ok(self):
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = 0.0
        d["spot_forecast_yuan_mwh"][1] = 9999.0
        validate(d, 0.0, 9999.0)

    def test_price_just_over_boundary_rejected(self):
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = 9999.01
        with pytest.raises(ValidationError):
            validate(d, 0.0, 9999.0)

    def test_avg_price_out_of_range_rejected(self):
        d = base_data()
        d["avg_trade_price_yuan_mwh"][0] = 50000.0
        with pytest.raises(ValidationError):
            validate(d, 0.0, 9999.0)

    def test_avg_price_negative_rejected(self):
        d = base_data()
        d["avg_trade_price_yuan_mwh"][0] = -1.0
        with pytest.raises(ValidationError):
            validate(d, 0.0, 9999.0)

    def test_delivery_date_path_traversal_rejected(self):
        d = base_data()
        d["delivery_date"] = "../../etc/2026-08-22"
        with pytest.raises(ValidationError):
            validate(d)

    def test_delivery_date_non_string_rejected(self):
        d = base_data()
        d["delivery_date"] = 20260822
        with pytest.raises(ValidationError):
            validate(d)

    def test_as_of_non_string_rejected(self):
        d = base_data()
        d["as_of"] = 12345
        with pytest.raises(ValidationError):
            validate(d)

    def test_avg_price_at_boundaries_ok(self):
        d = base_data()
        d["avg_trade_price_yuan_mwh"][0] = 0.0
        d["avg_trade_price_yuan_mwh"][1] = 9999.0
        validate(d, 0.0, 9999.0)


class TestSplitPriceLimits:
    """平均成交价/合同限价与现货预测限价分离（中长期 20% 上浮 vs 现货 [-50, 800]）。"""

    def test_spot_above_midterm_cap_ok_with_spot_cap(self):
        # 现货预测 600 元/MWh 超中长期上限 481.44，但现货上限 800 → 通过
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = 600.0
        validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_spot_negative_ok(self):
        # 现货可负（-50 边界内）→ 通过
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = -50.0
        d["spot_forecast_yuan_mwh"][1] = -20.0
        validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_spot_below_min_rejected(self):
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = -50.01
        with pytest.raises(ValidationError):
            validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_spot_above_max_rejected(self):
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = 800.01
        with pytest.raises(ValidationError):
            validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_spot_at_boundaries_ok(self):
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = -50.0
        d["spot_forecast_yuan_mwh"][1] = 800.0
        validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_avg_above_midterm_cap_rejected(self):
        # 平均成交价 600 超中长期上限 → 拒绝
        d = base_data()
        d["avg_trade_price_yuan_mwh"][0] = 600.0
        with pytest.raises(ValidationError):
            validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_contract_above_midterm_cap_rejected(self):
        d = base_data()
        d["contract"][0]["avg_price_yuan_mwh"] = 500.0
        with pytest.raises(ValidationError):
            validate(d, 0.0, 481.44, spot_price_min=-50.0, spot_price_max=800.0)

    def test_spot_cap_defaults_to_price_bounds(self):
        # 未给现货上下限 → 与中长期 price_min/max 相同
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = 600.0
        with pytest.raises(ValidationError):
            validate(d, 0.0, 481.44)

    def test_run_uses_spot_bounds_from_config(self, tmp_path):
        # run 按 config 拆分校验：现货可负、可超 481.44（≤800），平均成交价不可
        d = base_data()
        d["spot_forecast_yuan_mwh"][0] = -30.0
        result = run(full_config(price_max_yuan_mwh=481.44,
                                 spot_price_min_yuan_mwh=-50.0,
                                 spot_price_max_yuan_mwh=800.0), d)
        assert result["summary"]["confidence"] == 0.8

        d2 = base_data()
        d2["spot_forecast_yuan_mwh"][0] = 900.0
        with pytest.raises(ValidationError):
            run(full_config(price_max_yuan_mwh=481.44,
                            spot_price_min_yuan_mwh=-50.0,
                            spot_price_max_yuan_mwh=800.0), d2)

        d3 = base_data()
        d3["avg_trade_price_yuan_mwh"][0] = 600.0
        with pytest.raises(ValidationError):
            run(full_config(price_max_yuan_mwh=481.44,
                            spot_price_min_yuan_mwh=-50.0,
                            spot_price_max_yuan_mwh=800.0), d3)


class TestCli:
    def test_main_missing_config_returns_1(self, tmp_path, capsys):
        from src.main import main
        rc = main(["--config", str(tmp_path / "no-such.yaml"),
                   "--input", str(SAMPLE), "--out", str(tmp_path / "out")])
        assert rc == 1
        assert "执行失败" in capsys.readouterr().err

    def test_main_bad_config_yaml_returns_1(self, tmp_path, capsys):
        from src.main import main
        bad = tmp_path / "bad.yaml"
        bad.write_text("theta_yuan_mwh: [unclosed", encoding="utf-8")
        rc = main(["--config", str(bad),
                   "--input", str(SAMPLE), "--out", str(tmp_path / "out")])
        assert rc == 1
        assert "执行失败" in capsys.readouterr().err

    def test_main_wrong_type_config_returns_1(self, tmp_path, capsys):
        # 配置值为字符串（如 theta="2"）→ 友好报错而非 TypeError 裸崩
        from src.main import main
        bad = tmp_path / "badtype.yaml"
        bad.write_text("theta_yuan_mwh: \"2\"\n", encoding="utf-8")
        rc = main(["--config", str(bad),
                   "--input", str(SAMPLE), "--out", str(tmp_path / "out")])
        assert rc == 1
        assert "执行失败" in capsys.readouterr().err


class TestConfigTypeChecks:
    def test_run_string_theta_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(theta_yuan_mwh="2"), base_data())

    def test_run_nan_theta_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(theta_yuan_mwh=float("nan")), base_data())

    def test_run_string_recover_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(recover_factor="1.1"), base_data())

    def test_run_inverted_bands_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(deviation_band_low=1.2, deviation_band_high=0.9),
                base_data())

    def test_run_negative_max_amount_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(max_amount_yuan_mwh=-1.0), base_data())

    def test_floor_lot_zero_min_lot(self):
        with pytest.raises(ValueError):
            floor_lot(100, 0.0)


class TestNegativeSpot:
    """现货价为负（现货市场允许 [-50, 0)）时全链路正常运行。"""

    def test_settle_price_negative_spot(self):
        # S=-30：k = 1.1*400 − 0.1*(−30) = 443
        assert settle_price(1000, 2000, -30.0, 400) == pytest.approx(443.0)   # 多用 max(S,k)
        assert settle_price(1000, 500, -30.0, 400) == -30.0    # 少用 min(S,k)
        assert settle_price(1000, 1000, -30.0, 400) == -30.0   # 带内=S

    def test_marginal_value_negative_spot(self):
        assert marginal_value(800, 1000, -30.0, 400) == pytest.approx(443.0)   # 欠配 max(S,k)
        assert marginal_value(1500, 1000, -30.0, 400) == -30.0  # 超配 min(S,k)

    def test_update_blended_avg_negative_trade_price(self):
        # (1000*400 + 100*(-30)) / 1100 = 360.9090...
        assert update_blended_avg(1000, 400.0, 100, -30.0) == pytest.approx(360.909, rel=1e-3)

    def test_decide_period_negative_spot(self):
        from src.decision import decide_period
        # 带内 mv0 = S = -30 → 买不可能（-32 > 382 假），卖可能（382-2 > -30 真）
        d = decide_period(382.0, 1000, 1000, -30.0, 400, 5000, 10, 2.0)
        assert d.action == "sell"
        assert d.mv == -30.0
        assert d.expected_pnl_cny > 0

    def test_run_all_periods_negative_spot(self, tmp_path):
        # 24 时段现货全为 -50（下限边界）→ 运行成功，输出结构完整
        d = base_data()
        d["spot_forecast_yuan_mwh"] = [-50.0] * 24
        result = run(cfg, d)
        assert len(result["decisions"]) == 24
        for x in result["decisions"]:
            assert x["action"] in ("buy", "sell", "hold")
            if x["action"] != "hold":
                assert x["price_range"][0] <= x["price_range"][1]

    def test_run_mixed_negative_spot_with_avg(self, tmp_path):
        # 负现货 + 平均成交价贴近下限 → 全链路无异常
        d = base_data()
        d["spot_forecast_yuan_mwh"] = [round(-50 + 8 * (t % 24), 1) for t in range(24)]
        d["avg_trade_price_yuan_mwh"] = [round(20.0 + (t % 24), 1) for t in range(24)]
        result = run(cfg, d)
        assert len(result["decisions"]) == 24


# ---------- 结算/价值：极端数值稳定性 ----------


class TestNumericStability:
    def test_settle_huge_position(self):
        # 1e12 MWh 数量级不溢出
        assert settle_price(1e12, 1.2e12, 300, 400) == 410
        assert settle_price(1e12, 0.8e12, 300, 400) == 300

    def test_settle_tiny_position(self):
        assert settle_price(1e-6, 2e-6, 300, 400) == 410

    def test_settle_zero_spot(self):
        # S=0：k = 1.1*P_avg（浮点近似比较）
        assert settle_price(1000, 2000, 0.0, 400) == pytest.approx(440.0)

    def test_marginal_huge_position(self):
        assert marginal_value(1e12, 1e6, 300, 400) == 300

    def test_blend_extreme_weights(self):
        # 1e12 持仓 + 10 MWh 微量成交
        new = update_blended_avg(1e12, 400.0, 10, 300.0)
        assert 399.0 < new < 400.0

    def test_blend_sell_nearly_all_ok(self):
        # 剩 1 MWh 合法
        assert update_blended_avg(1000, 400.0, -999, 450.0) == pytest.approx(
            (1000 * 400 - 999 * 450) / 1.0)

    def test_blend_sell_all_raises(self):
        with pytest.raises(ValueError):
            update_blended_avg(1000, 400.0, -1000, 450.0)

    def test_blend_sell_more_than_hold_raises(self):
        with pytest.raises(ValueError):
            update_blended_avg(1000, 400.0, -1500, 450.0)

    def test_blend_delta_zero_nan_px_untouched(self):
        # 不成交时挂单价不影响结果
        assert update_blended_avg(1000, 400.0, 0, float("nan")) == 400.0

    def test_mv_curve_max_q_zero(self):
        qs, mv = mv_curve(1000, 1000, 300, 400, max_q=0.0)
        assert len(qs) == 1 and mv[0] == 300.0

    def test_mv_curve_negative_max_q(self):
        qs, mv = mv_curve(1000, 1000, 300, 400, max_q=-5.0)
        assert len(qs) == 0

    def test_band_bounds_extreme_load(self):
        lo, hi = band_bounds(1e-9)
        assert 0.0 < lo < hi
        lo, hi = band_bounds(1e12)
        assert lo < hi


# ---------- 决策/风控：直接 API 边界 ----------


class TestDecisionEdge:
    def test_buy_with_zero_room_hold(self):
        # 持仓已超目标位 → 无买入余量 → hold（方向信号仍保留）
        d = _period(380.0, Q_c=2000, L=1000, S=500, P_avg=400)
        assert d.action == "hold"

    def test_huge_room_ok(self):
        # 带内余量极大时按限额封顶，不溢出
        d = _period(405.0, Q_c=800, L=1000)
        assert d.action == "buy"
        assert 0 < d.volume_mwh <= 200

    def test_avg_zero_price_buy(self):
        # 平均成交价为 0（限价下界）→ 买触发，区间围绕 0 对称，不崩
        d = _period(0.0, Q_c=800, L=1000)
        assert d.action == "buy"
        assert d.volume_mwh > 0

    def test_theta_zero_buy(self):
        # θ=0：只要 mv0 > A 就买
        d = _period(381.0, Q_c=800, L=1000, theta=0.0)
        assert d.action == "buy"

    def test_theta_huge_hold(self):
        d = _period(381.0, Q_c=800, L=1000, theta=1e9)
        assert d.action == "hold"

    def test_negative_theta_sell(self):
        # θ 为负（激进）不崩；超配持仓下 A−θ > mv0 → 卖方向
        d = _period(415.5, Q_c=1200, L=1000, S=500, theta=-5.0)
        assert d.action == "sell"
        assert d.volume_mwh > 0

    def test_fractional_min_lot(self):
        from src.decision import decide_period
        d = decide_period(381.0, 800, 1000, 300, 400, 5000, 2.5, 2.0)
        # 取整到 2.5 的倍数
        assert d.volume_mwh % 2.5 == 0


class TestRiskEdge:
    def test_confidence_zero_rejects(self):
        vol, reasons = check(100, 0, 5000, None, 400, 0.0, 10)
        assert vol == 0 and "low_confidence" in reasons

    def test_confidence_one_ok(self):
        vol, _ = check(100, 0, 5000, None, 400, 1.0, 10)
        assert vol == 100

    def test_confidence_above_one_accepted(self):
        # 超范围置信度不崩（校验留给配置层）
        vol, _ = check(100, 0, 5000, None, 400, 1.5, 10)
        assert vol == 100

    def test_nan_confidence_rejects(self):
        vol, reasons = check(100, 0, 5000, None, 400, float("nan"), 10)
        assert vol == 0 and "low_confidence" in reasons

    def test_max_amount_zero(self):
        # 金额上限 0 → 量为 0
        vol, _ = check(100, 0, 5000, 0.0, 400, 0.8, 10)
        assert vol == 0

    def test_best_price_zero_amount(self):
        # 价格为 0 时金额上限跳过（避免除零）
        vol, _ = check(100, 0, 5000, 10000, 0.0, 0.8, 10)
        assert vol == 100

    def test_min_lot_one(self):
        vol, _ = check(37, 0, 5000, None, 400, 0.8, 1.0)
        assert vol == 37

    def test_floor_lot_nan(self):
        with pytest.raises(ValueError):
            floor_lot(float("nan"), 10)

    def test_floor_lot_tiny_min_lot(self):
        assert floor_lot(3.3, 0.5) == 3.0

    def test_side_unknown_treated_as_sell(self):
        # 未知 side 不裁剪限额（宽松处理）
        vol, _ = check(500, 5100, 5000, None, 400, 0.8, 10, side="weird")
        assert vol == 500


# ---------- 端到端：极端/畸形配置与输入 ----------


class TestRunRobust:
    def test_run_missing_config_keys(self, tmp_path):
        # 最小配置只给校验参数 → 其余走默认值
        result = run({"price_min_yuan_mwh": 0, "price_max_yuan_mwh": 9999},
                     base_data())
        assert len(result["decisions"]) == 24

    def test_run_confidence_out_of_range_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(confidence=1.5), base_data())
        with pytest.raises(ValueError):
            run(full_config(confidence=-0.1), base_data())

    def test_run_shrink_out_of_range_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            run(full_config(shrink_bands=1.0), base_data())

    def test_run_tiny_limit_all_hold_or_sell(self, tmp_path):
        # 极限小限额：买不起，卖可以
        d = base_data()
        d["position_limit_mwh"] = 0.05    # 样例持仓最低 0.23，均超限 → 只能卖
        result = run(cfg, d)
        assert result["summary"]["buy_total_mwh"] == 0

    def test_run_huge_limit_ok(self, tmp_path):
        d = base_data()
        d["position_limit_mwh"] = 1e12
        result = run(cfg, d)
        assert result["summary"]["buy_total_mwh"] + result["summary"]["sell_total_mwh"] >= 0

    def test_run_zero_theta_ok(self, tmp_path):
        result = run(full_config(theta_yuan_mwh=0.0), base_data())
        assert len(result["decisions"]) == 24

    def test_run_idempotent(self, tmp_path):
        out1 = tmp_path / "a"
        out2 = tmp_path / "b"
        r1 = run(cfg, base_data())
        r2 = run(cfg, base_data())
        # 决策内容一致（decision_time 时间戳可能相同或相差秒级，剔除后比较）
        for k in ("decisions", "summary", "delivery_date", "as_of"):
            assert r1[k] == r2[k]

    def test_run_empty_config(self, tmp_path):
        # 空配置：全部默认值，跑通不崩
        result = run({}, base_data())
        assert result["summary"]["confidence"] == 1.0


class TestLoadInputMalformed:
    def test_missing_file(self):
        with pytest.raises(OSError):
            load_input(ROOT / "data" / "sample" / "no-such.json")

    def test_empty_json(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            load_input(p)

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            load_input(p)

    def test_json_array(self, tmp_path):
        p = tmp_path / "arr.json"
        p.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(ValidationError):
            validate(load_input(p))


class TestFuzz:
    """确定性模糊：随机变异输入，validate 只抛 ValidationError，绝不抛其他异常。"""

    JUNK = [None, True, "x", {}, [], float("nan"), float("inf"), float("-inf"),
            -1.0, 0.0, 0.5, 1e300, "1", 123456789.123]

    def _mutate(self, obj):
        if isinstance(obj, dict):
            key = obj[next(iter(obj))]
            return {k: self._mutate(v) for k, v in obj.items()}
        if isinstance(obj, list):
            out = list(obj)
            out[self._rng.randrange(len(out))] = self.JUNK[self._rng.randrange(len(self.JUNK))]
            return out
        return obj

    def test_fuzz_500_rounds(self):
        import random
        for seed in range(500):
            self._rng = random.Random(seed)
            d = base_data()
            d = self._mutate(d)
            try:
                validate(d)
            except ValidationError:
                pass  # 期望路径
            except Exception as e:  # 任何其他异常都是缺陷
                raise AssertionError(f"seed={seed}: 意外异常 {type(e).__name__}: {e}")

    def test_fuzz_valid_mutations_pass_or_validerror(self):
        # 只变异数值幅度（保持结构），结果要么通过要么 ValidationError
        import random
        for seed in range(200):
            rng = random.Random(seed)
            d = base_data()
            for i in range(3):
                t = rng.randrange(24)
                kind = rng.randrange(3)
                if kind == 0:
                    d["spot_forecast_yuan_mwh"][t] = rng.uniform(-5000, 15000)
                elif kind == 1:
                    d["load_forecast_mwh"][t] = rng.uniform(-1000, 20000)
                else:
                    d["avg_trade_price_yuan_mwh"][t] = rng.uniform(-1000, 1e6)
            try:
                validate(d)
            except ValidationError:
                pass
            except Exception as e:
                raise AssertionError(f"seed={seed}: 意外异常 {type(e).__name__}: {e}")

    def test_fuzz_run_never_crashes(self, tmp_path):
        # 通过校验的输入跑 run 不得抛异常
        import random
        for seed in range(100):
            rng = random.Random(seed)
            d = base_data()
            for i in range(3):
                t = rng.randrange(24)
                d["load_forecast_mwh"][t] = rng.uniform(100, 20000)
                d["spot_forecast_yuan_mwh"][t] = rng.uniform(-50, 800)
            d["position_limit_mwh"] = rng.uniform(1, 1e6)
            try:
                run(cfg, d)
            except ValueError as e:
                raise AssertionError(f"seed={seed}: 合法输入被拒: {e}")
