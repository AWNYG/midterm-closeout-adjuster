"""数据接入与校验：读当日输入 JSON，24 点对齐、盘口规范、价格/量合法性。"""

import json
import math
import re
from pathlib import Path

DELIVERY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(ValueError):
    pass


def _finite_number(x, name: str, *, allow_negative: bool = True) -> None:
    """校验 x 为有限数值（int/float），拒绝 NaN/Inf/字符串/None。"""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise ValidationError(f"{name}={x!r} 不是数值")
    if not math.isfinite(x):
        raise ValidationError(f"{name}={x} 不是有限数值")
    if not allow_negative and x < 0:
        raise ValidationError(f"{name}={x} 不能为负")


def fetch_books() -> list | None:
    """爬虫对接占位：返回 24 个盘口 JSON；爬虫未就绪返回 None 走本地输入。"""
    return None


def _check_levels(levels: list, descending: bool, name: str,
                  price_min: float, price_max: float):
    if not isinstance(levels, list) or len(levels) != 5:
        raise ValidationError(f"{name}: 必须恰为 5 档")
    prev = None
    for lvl in levels:
        if not isinstance(lvl, dict) or "px" not in lvl or "vol" not in lvl:
            raise ValidationError(f"{name}: 档位缺 px/vol 字段")
        _finite_number(lvl["vol"], f"{name} 档位量", allow_negative=False)
        _finite_number(lvl["px"], f"{name} 档位价", allow_negative=True)
        if not (price_min <= lvl["px"] <= price_max):
            raise ValidationError(f"{name} 档位价={lvl['px']} 越界 [{price_min}, {price_max}]")
        if prev is not None:
            bad = (lvl["px"] >= prev) if descending else (lvl["px"] <= prev)
            if bad:
                raise ValidationError(f"{name}: 价格顺序错误（{'应递减' if descending else '应递增'}）")
        prev = lvl["px"]


def validate(data, price_min: float = 0.0, price_max: float = 9999.0,
             spot_price_min: float | None = None,
             spot_price_max: float | None = None) -> None:
    """校验输入结构，失败抛 ValidationError。

    price_min/price_max 约束盘口价格与合同成交均价（中长期限价）；
    spot_price_min/spot_price_max 单独约束现货价预测（现货价格不受中长期
    上浮限制、可负），缺省取 price_min/price_max。
    """
    if not isinstance(data, dict):
        raise ValidationError(f"输入顶层必须是 JSON 对象，收到 {type(data).__name__}")
    for key in ("as_of", "delivery_date", "spot_forecast_yuan_mwh", "load_forecast_mwh",
                "contract", "books", "position_limit_mwh", "min_lot_mwh"):
        if key not in data:
            raise ValidationError(f"缺少字段: {key}")
    if not isinstance(data["as_of"], str):
        raise ValidationError(f"as_of 必须是字符串: {data['as_of']!r}")
    if not isinstance(data["delivery_date"], str) or not DELIVERY_DATE_RE.match(data["delivery_date"]):
        raise ValidationError(f"delivery_date 必须为 YYYY-MM-DD: {data['delivery_date']!r}")

    spot = data["spot_forecast_yuan_mwh"]
    load = data["load_forecast_mwh"]
    if len(spot) != 24 or len(load) != 24:
        raise ValidationError("spot_forecast_yuan_mwh / load_forecast_mwh 必须为 24 点")
    s_min = spot_price_min if spot_price_min is not None else price_min
    s_max = spot_price_max if spot_price_max is not None else price_max
    for i, (s, l) in enumerate(zip(spot, load)):
        _finite_number(s, f"spot_forecast[{i}]", allow_negative=True)
        if not (s_min <= s <= s_max):
            raise ValidationError(f"spot_forecast[{i}]={s} 越界 [{s_min}, {s_max}]")
        _finite_number(l, f"load_forecast[{i}]", allow_negative=False)

    if len(data["contract"]) != 24:
        raise ValidationError("contract 必须为 24 个时段")
    for c in data["contract"]:
        if not isinstance(c, dict) or "period" not in c or "volume_mwh" not in c or "avg_price_yuan_mwh" not in c:
            raise ValidationError(f"contract 条目缺字段: {c}")
        _finite_number(c["volume_mwh"], f"contract period {c['period']} 电量", allow_negative=False)
        if c["volume_mwh"] <= 0:
            raise ValidationError(f"contract period {c['period']}: 电量必须为正")
        _finite_number(c["avg_price_yuan_mwh"], f"contract period {c['period']} 均价", allow_negative=False)
        if not (price_min <= c["avg_price_yuan_mwh"] <= price_max):
            raise ValidationError(f"contract period {c['period']}: 均价越界")
    periods = {c["period"] for c in data["contract"]}
    if periods != set(range(24)):
        raise ValidationError("contract 时段编号必须为 0~23 且不重不漏")
    for t, c in enumerate(data["contract"]):
        if c["period"] != t:
            raise ValidationError(f"contract[{t}].period={c['period']} 与列表位置不一致（须按时段 0~23 升序）")

    if len(data["books"]) != 24:
        raise ValidationError("books 必须为 24 个时段盘口")
    for t, b in enumerate(data["books"]):
        if not isinstance(b, dict) or "bid" not in b or "ask" not in b:
            raise ValidationError(f"books[{t}] 缺 bid/ask")
        _check_levels(b["bid"], descending=True, name=f"books[{t}].bid",
                      price_min=price_min, price_max=price_max)
        _check_levels(b["ask"], descending=False, name=f"books[{t}].ask",
                      price_min=price_min, price_max=price_max)
        if b["bid"][0]["px"] >= b["ask"][0]["px"]:
            raise ValidationError(f"books[{t}]: bid1 应低于 ask1")

    _finite_number(data["position_limit_mwh"], "position_limit_mwh", allow_negative=False)
    if data["position_limit_mwh"] <= 0:
        raise ValidationError("position_limit_mwh 必须为正")
    _finite_number(data["min_lot_mwh"], "min_lot_mwh", allow_negative=False)
    if data["min_lot_mwh"] <= 0:
        raise ValidationError("min_lot_mwh 必须为正")


def load_input(path: str | Path) -> dict:
    """读取输入 JSON 并返回 dict（不做校验，校验由 validate 负责）。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
