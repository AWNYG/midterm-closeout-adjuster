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

输出：`data/output/<交割日>-decision.json`，含 24 个时段决策与 summary；控制台按价差降序打印摘要。

## 输入格式（data/input/YYYY-MM-DD.json）

见交接文档 6 节：24 点现货/负荷预测、24 时段聚合合同（电量+加权均价）、24 时段预测盘口平均成交价 `avg_trade_price_yuan_mwh`、持仓限额、最小交易单位。

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
