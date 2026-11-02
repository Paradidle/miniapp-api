# 部署指南（Linux · 端口 9201）

## 0. 约定

与同仓库其他模块保持一致：

| 文件 | 作用 |
|---|---|
| `wsgi.py` | gunicorn 入口，暴露 `app` |
| `gunicorn.conf.py` | gunicorn 配置（单 worker） |
| `start.sh` | 后台启动，PID 写 `gunicorn.pid`，日志写 `info.log` |
| `stop.sh` | 优雅停止，超时强杀 |
| `requirements.txt` | 依赖清单 |

**端口：9201**　**健康检查：`GET /healthz`**（不加载数据，立即返回）

## 1. 手动部署

```bash
cd /opt && git clone https://github.com/Paradidle/miniapp-api.git
cd miniapp-api/strategy-racecourse

python3 -m venv .venv
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

export TDX_VIPDOC=/data/vipdoc
export RACE_DATA=/data/racecourse

chmod +x start.sh stop.sh
./start.sh
curl http://127.0.0.1:9201/healthz
```

停止：`./stop.sh`

## 2. Jenkins 流水线

已按流水线约定实现，无需改动 pipeline：

- 代码在 `strategy-racecourse/` 子目录（`APP_SUBDIR`）
- 部署目录 `/usr/strategy-racecourse`（`DEPLOY_DIR`）
- `start.sh` 支持 `JENKINS_NODE_COOKIE=dontKillMe`（gunicorn `--daemon` 后不会被 Jenkins 杀）
- `stop.sh` 退出码恒为 0（`./stop.sh || true` 安全）
- `start.sh` 内置端口占用检查，端口未释放时返回非 0 并打印占用进程
- `/healthz` 秒级返回，满足 15 次 × 2s 的探测窗口

## 3. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `GUNICORN_BIND` | `0.0.0.0:9201` | 监听地址（`start.sh` 读取） |
| `TDX_VIPDOC` | `/data/vipdoc` | 通达信数据目录 |
| `RACE_DATA` | `./data` | 策略存储目录 |
| `TDX_BASE_DBF` | 自动推导 | base.dbf 路径 |
| `RACE_LIMIT` | 不设 | 只加载前 N 只（调试） |
| `RACE_MIN_LISTED` | 120 | 上市不足 N 个交易日排除 |
| `RACE_MIN_PRICE` | 2.0 | 低价股门槛 |
| `RACE_MIN_FLOAT_MV` | 20 | 流通市值门槛（亿） |
| `LLM_API_KEY` | 无 | 配置后启用 LLM 编译；不配则走规则解析 |

## 4. 每日任务

数据同步完成后执行：

```bash
cd /usr/strategy-racecourse
TDX_VIPDOC=/data/vipdoc RACE_DATA=/data/racecourse \
  .venv/bin/python -m app.daily --force
```

建议用 systemd timer（工作日 16:35）。

## 5. 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | **健康检查（Jenkins 用）** |
| GET | `/api/health` | 数据状态（会加载矩阵） |
| GET | `/api/indicators` | 指标白名单 |
| POST | `/api/strategies/compile` | 自然语言 → DSL（不落库） |
| POST | `/api/strategies/from-text` | 自然语言 → 建策略 + 回测 |
| GET/POST | `/api/strategies` | 列表 / 新建（DSL 直填） |
| GET | `/api/strategies/{id}` | 详情 |
| POST | `/api/strategies/{id}/backtest` | 跑回测 |
| GET | `/api/strategies/{id}/trades` | 交易分页 |
| GET | `/api/ranking` | 赛马图数据 |
| POST | `/api/admin/run-daily` | 通过 HTTP 触发每日任务 |

## 6. LLM 编译

配置 `LLM_API_KEY` 后，`/api/strategies/from-text` 会：

1. 调用兼容 OpenAI 的 `/chat/completions`（`response_format=json_object`）
2. 拿到 `{name, interpreted_cn, dsl, unsupported}`
3. **白名单校验**：任何不在白名单的指标直接 422
4. 校验通过后落库并回测

未配 key 时自动降级为规则解析（正则），覆盖常见中文表述，仍会做过白名单校验。

**安全设计**：LLM 只输出受限 DSL，不生成可执行代码，因此不存在任意代码执行风险。

## 7. 排障

| 现象 | 排查 |
|---|---|
| 启动失败 | `tail -n 50 info.log` |
| 端口被占 | `ss -lntp \| grep 9201`，或 `pkill -f 'gunicorn.*wsgi:app'` |
| `/healthz` 200 但 `/api/health` 慢 | 正常，首次加载矩阵需 10-30 秒 |
| 编译报 422 | 看 `items` 字段，说明用了不支持的指标 |
| 全市场无信号 | 数据未更新，或 `RACE_MIN_*` 过严 |
