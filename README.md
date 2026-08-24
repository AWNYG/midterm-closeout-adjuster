# 四川电力市场滚动撮合收盘微调算法

D 日滚动撮合收盘前，基于 D+2 现货/负荷预测与 24 个时段各自的**预测盘口平均成交价**，对持仓做最后一次微调。每个时段独立决策，各做一笔交易（挂单为主，不追价）。

完整业务规则与设计说明见仓库根目录 `代码交接与中台接入说明.md`。

## 安装

```bash
pip install numpy pyyaml pytest
```

## 运行

```bash
# 用示例输入端到端跑（输出到 data/output/）
python -m src.main --input data/sample/2026-08-22.json

# 正式运行：把当日输入放到 data/input/ 下，直接运行取最新文件
python -m src.main
```

终端演示（中文表格）：

```bash
python demo.py                      # 示例输入
```

输出：`data/output/<交割日>-decision.json`（字段说明见下文"输出"节）；控制台按价差降序打印摘要。

## 输入（data/input/YYYY-MM-DD.json）

8 个必填字段，结构示例：

```json
{
  "as_of": "2026-08-20T14:55:00+08:00",
  "delivery_date": "2026-08-22",
  "spot_forecast_yuan_mwh": [333.0, 338.0, "…共 24 点"],
  "load_forecast_mwh":      [0.31,  0.27,  "…共 24 点"],
  "avg_trade_price_yuan_mwh": [337.0, 343.0, "…共 24 点"],
  "contract": [
    {"period": 0, "volume_mwh": 0.39, "avg_price_yuan_mwh": 330.0},
    "…共 24 条，period 必须等于列表下标"
  ],
  "position_limit_mwh": 1.0,
  "min_lot_mwh": 0.1
}
```

| 字段 | 类型/长度 | 含义 | 校验 |
|---|---|---|---|
| `as_of` | str | 输入快照/预测生成时刻（ISO8601，仅回显，不参与计算） | 非空字符串 |
| `delivery_date` | str | 交割日 D+2，决定输出文件名 | `YYYY-MM-DD`（防路径注入） |
| `spot_forecast_yuan_mwh` | float[24] | 24 点现货价预测 S，**公平价值锚**，下标=时段 t | 有限数值，∈ [-50, 800]（**可负**，现货不受 20% 上浮限制） |
| `load_forecast_mwh` | float[24] | 24 点负荷预测 L，与持仓比较得偏差带位置 | 有限数值，≥ 0 |
| `avg_trade_price_yuan_mwh` | float[24] | 24 时段预测盘口平均成交价 A，**方向判定与报价锚** | 有限数值，∈ [0, 481.44]（与合同同属中长期限价轨） |
| `contract` | dict[24] | 每时段聚合合同：`period`(0~23 且=下标)、`volume_mwh` 总持仓 Q_c(>0)、`avg_price_yuan_mwh` 加权均价 P_avg | 电量 > 0；均价 ∈ [0, 481.44]；period 升序不重不漏 |
| `position_limit_mwh` | float | 单时段持仓上限，**只约束买入**（卖出减仓不受限） | 正数 |
| `min_lot_mwh` | float | 最小交易单位（样例 0.1 MWh），成交量必须为其整数倍 | 正数 |

> 任一字段不合法即抛 `ValidationError` 中止整个决策，不会产出半成品。
> 完整逐字段说明见交接文档 6 节。

## 输出（data/output/<交割日>-decision.json）

```json
{
  "as_of": "2026-08-20T14:55:00+08:00",
  "decision_time": "2026-08-20T14:55:01+08:00",
  "delivery_date": "2026-08-22",
  "decisions": [
    {
      "period": 11,
      "action": "sell",
      "price_range": [388.0, 390.0],
      "volume_mwh": 0.10,
      "orders": [{"side": "sell", "price": 389.0, "volume": 0.10}],
      "mv": 381.7,
      "expected_pnl_cny": 0.58,
      "reasons": ["spread>theta", "band_edge"]
    },
    "…共 24 个时段"
  ],
  "summary": {
    "buy_total_mwh": 0.60,
    "sell_total_mwh": 0.50,
    "hold_count": 16,
    "confidence": 0.8
  }
}
```

**顶层字段**：

| 字段 | 含义 |
|---|---|
| `as_of` / `delivery_date` | 原样回显输入 |
| `decision_time` | 决策生成时刻（本机时区 ISO8601） |
| `decisions` | 24 个时段的决策，按下标对应时段 |
| `summary` | 汇总统计 |

**decisions[]. 每时段决策**：

| 字段 | 含义 |
|---|---|
| `period` | 时段编号 0~23 |
| `action` | `buy` / `sell` / `hold`，交易员只看非 hold 时段 |
| `price_range` | **报价区间 [低, 高]**：买卖对称 `[A−θ/2, A+θ/2]`，在此区间内挂单/微调、不突破；hold 为 null |
| `volume_mwh` | 目标成交量 = min(带内回拉余量, 持仓限额余量) 向下取整到 min_lot；hold 为 0 |
| `orders` | 初始挂单建议 `[{side, price, volume}]`，price = 区间中点 = A；hold 为 [] |
| `mv` | 该时段合约内在价值 MV_t(0)（元/MWh），复盘基准 |
| `expected_pnl_cny` | 期望收益 = (挂单价−mv 的差值) × 量 × confidence（元） |
| `reasons` | 决策留痕标签（`spread>theta`/`band_edge`/`position_cap`/`below_min_lot`/`hold_no_spread`/`amount_cap`/`low_confidence`），见交接文档 §5.8 |

**summary 汇总**：`buy_total_mwh`（买入量合计）、`sell_total_mwh`（卖出量合计）、`hold_count`（不动时段数）、`confidence`（本次置信度）。

> 交易员消费指引：对每个非 hold 时段按 `orders[0]` 挂单，在 `price_range` 内人工微调价格促成成交；收盘前未成交即放弃该时段，不追价、不突破区间。

## 测试

```bash
python -m pytest tests/ -v
```

## 参数（config/params.yaml）

- 结算/考核：`deviation_band_low/high`（0.85/1.15）、`recover_factor`（1.1）
- 交易：`theta_yuan_mwh`（不交易带半宽，无回测先保守 2.0；同时定义报价区间半宽 θ/2）、`max_amount_yuan_mwh`（单时段金额上限，默认关闭）
- 风险：`confidence`（全局置信度）、`confidence_min`（低于则 hold）、`sigma_*`/`shrink_bands`（带边界收缩，默认关闭）
- 校验：`price_min/max_yuan_mwh`（平均成交价/合同，上限 481.44 = 四川燃煤基准价 401.2 × 1.2）、`spot_price_min/max_yuan_mwh`（现货 [-50, 800]，可负）

> 注：`min_lot_mwh`（最小交易单位）与 `position_limit_mwh`（单时段持仓上限）**由输入 JSON 提供**，params.yaml 不重复定义。

## 说明与口径

- 偏差考核带采用 **L/Q ∈ [0.85, 1.15]**（边界 L/1.15、L/0.85）。文档 2.1 偏差率式 `r=(L−Q)/Q` 与其自身"多用 L>1.15Q"表述矛盾，实现与 3.1/MV 阶梯一致，采用 L/Q 带。
- 方向判定与报价均围绕**预测平均成交价 A**：买触发 `mv0−θ > A`、卖触发 `A−θ > mv0`；报价区间买卖对称 `[A−θ/2, A+θ/2]`、初始挂单价 = A。触发条件保证挂单价 A 的边际收益 ≥ θ（买：`mv0−A > θ`）；区间边缘报价的边际收益 ≥ θ/2（不额外封顶，边界不突破 mv0±θ 并非恒成立）。
- 输入不含盘口深度信息，**目标量只受带内回拉余量与持仓限额约束**（无 depth_cap），量 = floor_lot(min(带内余量, 限额余量))。
- 校验严格化：输入字段必须为有限数值（拒绝 NaN/Inf/字符串/None），`avg_trade_price_yuan_mwh` 24 值须在 [0, 481.44] 内，`contract` 列表须按时段 0~23 升序排列。
- 持仓限额仅约束买入（买后持仓不得超过限额）；**卖出减仓不受持仓限额约束**，超限持仓允许卖出。
- 结算/考核参数（`deviation_band_low/high`、`recover_factor`）、`shrink_bands`、`initial_order_policy` 均从 params.yaml 读取并传入决策链。
- 成交量向下取整到 0.1 MWh（100 kWh，样例最小交易单位）；期望量不足一包即 hold；未成交放弃该时段，不追价。
