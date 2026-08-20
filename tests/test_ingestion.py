import copy

import pytest

from src.ingestion import ValidationError, validate


def make_valid() -> dict:
    spot = [300.0] * 24
    load = [1000.0] * 24
    contract = [{"period": t, "volume_mwh": 1000.0, "avg_price_yuan_mwh": 400.0} for t in range(24)]
    books = []
    for _ in range(24):
        books.append({
            "bid": [{"px": 398.0 - i, "vol": 100 - 20 * i} for i in range(5)],
            "ask": [{"px": 402.0 + i, "vol": 100 - 20 * i} for i in range(5)],
        })
    return {
        "as_of": "2026-08-20T14:55:00+08:00",
        "delivery_date": "2026-08-22",
        "spot_forecast_yuan_mwh": spot,
        "load_forecast_mwh": load,
        "contract": contract,
        "books": books,
        "position_limit_mwh": 5000,
        "min_lot_mwh": 10,
    }


def test_valid_passes():
    validate(make_valid())


def test_missing_field():
    d = make_valid()
    del d["books"]
    with pytest.raises(ValidationError, match="books"):
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


def test_missing_book():
    d = make_valid()
    d["books"] = d["books"][:23]
    with pytest.raises(ValidationError):
        validate(d)


def test_book_bid_ascending_rejected():
    d = make_valid()
    d["books"][3]["bid"] = [{"px": 390.0 + i, "vol": 10} for i in range(5)]
    with pytest.raises(ValidationError):
        validate(d)


def test_book_ask_descending_rejected():
    d = make_valid()
    d["books"][3]["ask"] = [{"px": 405.0 - i, "vol": 10} for i in range(5)]
    with pytest.raises(ValidationError):
        validate(d)


def test_book_bad_level_count():
    d = make_valid()
    d["books"][3]["ask"] = d["books"][3]["ask"][:4]
    with pytest.raises(ValidationError):
        validate(d)


def test_negative_volume_rejected():
    d = make_valid()
    d["books"][3]["bid"][2]["vol"] = -5.0
    with pytest.raises(ValidationError):
        validate(d)


def test_crossed_book_rejected():
    d = make_valid()
    d["books"][3]["bid"][0]["px"] = 403.0  # 高于 ask1=402
    with pytest.raises(ValidationError):
        validate(d)
