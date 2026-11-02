# 策略赛马场（strategy-racecourse）

FastAPI + numpy + 静态前端的策略回测/前向跟踪服务。**无数据库**，纯文件存储。

## 快速开始

```bash
cd strategy-racecourse
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 指向你的 vipdoc 目录
export TDX_VIPDOC=/data/vipdoc
export RACE_DATA=./data

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
# 打开 http://服务器IP:8000/
```

接口文档：`http://服务器IP:8000/docs`（FastAPI 自动生成）

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `TDX_VIPDOC` | `/data/vipdoc` | 通达信数据目录 |
| `RACE_DATA` | `./data` | 策略存储目录 |
| `RACE_LIMIT` | 不限制 | 只加载前 N 只股票（调试用，加速启动） |

## 目录结构

```
strategy-racecourse/
├── app/
│   ├── main.py        FastAPI 路由
│   ├── engine.py      .day 读取 + 指标 + 回测引擎
│   └── store.py       文件存储（原子写）
├── web/index.html     赛马图前端（ECharts）
├── data/              运行数据（gitignore）
└── requirements.txt
```

## 数据目录布局

```
data/strategies/s_20261102_0001/
├── meta.json          策略定义 + DSL + 中文描述
├── bt_metrics.json    回测指标
├── bt_trades.json     回测明细（分页读取）
└── live_trades.json   前向成交（每日追加）
```

## 指标白名单

`close` `ret` `ma` `close_to_ma` `vol_ratio` `amount_ratio`
`high_n` `low_n` `breakout` `box_position` `shadow_up` `shadow_down`
`amplitude` `limit_up`

**不支持**：换手率、市值、财务指标、北向资金、龙虎榜、分钟级数据。

## DSL 示例

```json
{
  "signal": [
    {"ind": "box_position", "window": 120, "op": "<=", "value": 0.2},
    {"ind": "vol_ratio", "window": 7, "op": ">=", "value": 1.5}
  ],
  "rank_by": {"ind": "vol_ratio", "window": 7},
  "entry": {"max_per_day": 1},
  "exit": {"take_profit": 0.05, "stop_loss": 0.12, "max_hold_days": 30}
}
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 数据状态 |
| GET | `/api/strategies` | 策略列表 |
| POST | `/api/strategies` | 新建策略 |
| GET | `/api/strategies/{id}` | 详情 |
| POST | `/api/strategies/{id}/backtest` | 跑回测 |
| GET | `/api/strategies/{id}/trades?kind=live|backtest&page=1&size=50` | 交易分页 |
| GET | `/api/ranking` | 赛马图数据 |
| POST | `/api/admin/run-daily` | 前向任务 |

## 部署（systemd）

```ini
[Unit]
Description=Strategy Racecourse
After=network.target

[Service]
WorkingDirectory=/opt/miniapp-api/strategy-racecourse
Environment=TDX_VIPDOC=/data/vipdoc
Environment=RACE_DATA=/data/racecourse
ExecStart=/opt/miniapp-api/strategy-racecourse/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
User=tdx

[Install]
WantedBy=multi-user.target
```

每日任务（systemd timer，工作日 16:40）：

```ini
[Timer]
OnCalendar=Mon..Fri 16:40
Persistent=true
```

ExecStart 调 `curl -X POST http://127.0.0.1:8000/api/admin/run-daily`。

## 前端

前端是**单页原生 HTML**，由 FastAPI 直接托管，无需单独构建或部署：

| 路径 | 内容 |
|---|---|
| `/` | 赛马图页面（`web/index.html`） |
| `/static/lib/echarts.min.js` | ECharts 5.5.1（本地内置，不依赖 CDN） |

启动服务后直接访问 `http://<host>:9201/` 即可。

功能：
- 赛马图：按平均每笔收益率排序，颜色区分正负
- 点击策略看详情（前向 + 回测对照 + 背离度）
- 自然语言 / DSL 两种方式新建策略
- 指标白名单实时展示，不支持的指标当场报错

> ECharts 已本地化，不访问外网 CDN；仅当本地文件缺失时才回退到 jsdelivr。

## 收益口径：等额投资

每笔交易投入**相同金额**，不涉及仓位管理和复利：

- `avg_return`  平均每笔收益率 —— **核心指标**
- `total_return` 累计盈亏 = Σ各笔收益率（单位：单笔本金的倍数）
- `win_rate` 胜率
- `profit_factor` 盈亏比 = 总盈利 / 总亏损
- `max_loss` 最大单笔亏损（真实风险，不能用止损值估算）

> 例：2754 笔，平均每笔 -0.30%，则累计盈亏 = -8.35 倍单笔本金。
> 这比"复利 -100%"更能反映选股信号的长期有效性。

## 已知限制

1. **止损在跳空面前失效**：实测设 -12% 止损，实际最大亏损 -37%（跌停+跳空穿透）。
   详情页应显示 `max_loss` 字段，不能用止损值估算风险。
2. **复利假设满仓单笔**：`total_return` 按每笔全仓复利计算，实际应按仓位折算。
3. **回测未做样本外切分**：赛马图只用 `live` 指标，回测仅作参考。
4. **指标尚未注册未来函数检查**：所有指标都是 rolling（因果），但新增指标需自行确认。
