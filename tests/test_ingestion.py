import pytest

from src.ingestion import ValidationError, validate


def make_valid() -> dict:
    spot = [300.0] * 24
    load = [1000.0] * 24
    contract = [{"period": t, "volume_mwh": 1000.0, "avg_price_yuan_mwh": 400.0} for t in range(24)]
    return {
        "as_of": "2026-08-20T14:55:00+08:00",
        "delivery_date": "2026-08-22",
        "spot_forecast_yuan_mwh": spot,
        "load_forecast_mwh": load,
        "contract": contract,
        "avg_trade_price_yuan_mwh": [400.0] * 24,
        "position_limit_mwh": 5000,
        "min_lot_mwh": 10,
    }


def test_valid_passes():
    validate(make_valid())


def test_missing_field():
    d = make_valid()
    del d["avg_trade_price_yuan_mwh"]
    with pytest.raises(ValidationError, match="avg_trade_price"):
        validate(d)


def test_wrong_length_forecast():
    d = make_valid()
    d["spot_forecast_yuan_mwh"] = [300.0] * 23
    with pytest.raises(ValidationError):
        validate(d)


def test_price_out_of_range():
    d = make_valid()
    d["spot_forecast_yuan_mwh"][5] = -1.0
    with pytest.raises(ValidationError):
        validate(d)


def test_missing_contract_period():
    d = make_valid()
    d["contract"] = d["contract"][:23]
    with pytest.raises(ValidationError):
        validate(d)


def test_duplicate_period():
    d = make_valid()
    d["contract"][0] = {"period": 1, "volume_mwh": 1000.0, "avg_price_yuan_mwh": 400.0}
    with pytest.raises(ValidationError):
        validate(d)


def test_contract_out_of_order_rejected():
    d = make_valid()
    d["contract"][0], d["contract"][1] = d["contract"][1], d["contract"][0]
    with pytest.raises(ValidationError):
        validate(d)


def test_missing_avg_price():
    d = make_valid()
    d["avg_trade_price_yuan_mwh"] = d["avg_trade_price_yuan_mwh"][:23]
    with pytest.raises(ValidationError):
        validate(d)


def test_avg_price_out_of_range_rejected():
    d = make_valid()
    d["avg_trade_price_yuan_mwh"][3] = -1.0
    with pytest.raises(ValidationError):
        validate(d)


def test_avg_price_above_price_max_rejected():
    d = make_valid()
    d["avg_trade_price_yuan_mwh"][3] = 9999.01
    with pytest.raises(ValidationError):
        validate(d, 0.0, 9999.0)


def test_avg_price_non_finite_rejected():
    d = make_valid()
    d["avg_trade_price_yuan_mwh"][5] = float("nan")
    with pytest.raises(ValidationError):
        validate(d)


def test_avg_price_non_numeric_rejected():
    d = make_valid()
    d["avg_trade_price_yuan_mwh"][5] = "400"
    with pytest.raises(ValidationError):
        validate(d)
