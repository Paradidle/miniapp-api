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
| `RACE_RISK` | on | 设为 `off` 可整体关闭风险过滤 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址 |
| `LLM_API_KEY` | 无 | 配置后启用 LLM 编译；不配则走规则解析 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_TIMEOUT` | 90 | 单次调用超时（秒） |
| `LLM_JSON_MODE` | on | 服务不支持 response_format 时设 off |

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
| `FileNotFoundError: .../sh/lday/sh000001.day` | **TDX_VIPDOC 指错** | 见下方说明 |
| 回测返回 503 | 数据路径无效 | `curl /api/health` 看 `detail` 里尝试过的路径 |

### TDX_VIPDOC 指错的典型情况

`TDX_VIPDOC` 应该指向**含 `sh/lday/` 的目录**，即 `vipdoc` 本身，不是 TDX 根目录。

```
✅ /usr/stock-data-processor/new_tdx/vipdoc
❌ /usr/stock-data-processor/new_tdx          ← 少一层 vipdoc
```

从 v1.1 起代码会**自动探测** `p`、`p/vipdoc`、`p.parent/vipdoc`、`p.parent` 四个候选，
所以即使配成根目录也能跑。排查时看 `/api/health` 的 `vipdoc_resolved` 字段确认实际用的路径。

## 8. 前端

前端随代码一起发布，**不需要单独的构建或部署步骤**：

- 页面：`web/index.html`，由 FastAPI 的 `/` 路由返回
- 依赖：`web/lib/echarts.min.js`（1 MB，已本地化）
- 访问：`http://<host>:9201/`

### 可选：用 nginx 提供域名与 HTTPS

如果想让前端走独立域名，在 nginx 里反代即可（后端仍然只有 9201）：

```nginx
server {
    listen 80;
    server_name race.example.com;

    location / {
        proxy_pass http://127.0.0.1:9201;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;      # 全市场回测可能较慢
    }

    gzip on;
    gzip_types text/css application/javascript application/json;
}
```

注意：`proxy_read_timeout` 要放大，首次加载全市场矩阵和跑回测都可能超过 60 秒。

## 9. 特殊情况：文件名里含反斜杠（flat 布局）

从 Windows zip 解压到 Linux 时，若解压工具没转换分隔符，会得到：

```
-rw-r--r-- 1 root root 138528 'sz\lday\sz399990.day'
```

注意：**反斜杠是文件名的一部分**，不是目录分隔符。这时 `sz/lday/` 目录根本不存在。

### 诊断

```bash
cd /usr/strategy-racecourse
.venv/bin/python -m app.check /usr/stock-data-processor/new_tdx
```

输出会显示 `布局: flat（文件名含反斜杠）`。

### 方案一：让程序直接兼容（无需改数据）

引擎已支持 flat 布局，`detect_layout()` 会自动识别并直接读取，
不需要任何手动操作。启动后 `/api/health` 里的 `vipdoc_resolved` 会显示实际目录。

### 方案二：一次性迁移成标准目录（推荐）

```bash
cd /usr/strategy-racecourse

# 先看会怎么改
.venv/bin/python -m app.flatten --root /usr/stock-data-processor/new_tdx --dry-run

# 确认后执行
.venv/bin/python -m app.flatten --root /usr/stock-data-processor/new_tdx

# 再次验证
.venv/bin/python -m app.check /usr/stock-data-processor/new_tdx
```

迁移会把 `sh\lday\sh600000.day` 移动成 `sh/lday/sh600000.day`，
目标已存在时跳过不覆盖，失败会报告并中止。

> 迁移后如果以后又从 zip 重新解压，flat 文件会再次出现 —— 
> 引擎两种布局都支持，不影响使用。

## 10. 风险过滤与「0 笔交易」排查

风险过滤依赖 `T0002/hq_cache/base.dbf`（提供上市日期、流通股本）。
**如果只有解压出来的 .day 文件、没有 base.dbf**，过滤器拿不到数据，此时：

- 上市日期 / 流通市值条件**自动跳过**（不误杀）
- 只保留「价格 ≥ 2 元」这一条

如果回测出现 0 笔，按顺序排查：

```bash
# 1. 数据是否可用
curl -s http://127.0.0.1:9201/api/health | python3 -m json.tool

# 重点看：
#   stocks           是否为 0（数据没加载）
#   risk.pass_ratio  是否接近 0（过滤器把市场滤空了）
#   risk.has_float_shares  是否为 0（没有 base.dbf）

# 2. 临时关闭过滤器验证
RACE_RISK=off ./stop.sh && RACE_RISK=off ./start.sh
curl -X POST http://127.0.0.1:9201/api/strategies/<id>/backtest
```

若关闭后恢复正常，说明是过滤条件过严（调低 `RACE_MIN_PRICE` / `RACE_MIN_FLOAT_MV` 即可）。

## 11. 接入 LLM（自然语言 → 策略）

配置后，前端"新建策略"用自然语言描述即可生成 DSL，不需要改代码。

### 配置

```bash
# start.sh 里或 Jenkins environment 里加
export LLM_API_KEY=sk-xxxxxxxx
export LLM_BASE_URL=https://api.deepseek.com      # 可选
export LLM_MODEL=deepseek-chat                    # 可选
```

任何 OpenAI 兼容接口都能用：

| 服务 | LLM_BASE_URL | LLM_MODEL 示例 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 硅基流动 | `https://api.siliconflow.cn` | `Qwen/Qwen2.5-72B-Instruct` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode` | `qwen-plus` |
| OpenAI | `https://api.openai.com` | `gpt-4o-mini` |

### 验证

```bash
curl http://127.0.0.1:9201/api/llm/status
# {"configured":true,"base_url":"...","model":"deepseek-chat","api_key_hint":"sk-x****abcd"}

curl -X POST http://127.0.0.1:9201/api/llm/test
# {"ok":true,"model":"deepseek-chat","latency":1.2,"usage":{...}}

# 前端也会在"新建策略"面板顶部显示引擎状态，点"测试"直连验证
```

### 工作流程

1. 用户输入「箱体低位买下影线，止盈15%止损12%」
2. LLM 输出 `{name, interpreted_cn, dsl, unsupported}`
3. **白名单校验**：DSL 里出现不在白名单的指标 → 把错误回喂给 LLM 让它改（最多 1 轮）
4. 仍然不合法 → 422，告诉用户哪个指标不支持
5. LLM 不可用（无 key / 401 / 超时）→ **自动降级为规则解析**，并在响应里标 `warnings`

响应里的 `source` 字段说明来源：`llm` / `llm-repaired` / `rule`。

### 安全边界

- LLM 只输出受限 DSL，**不生成可执行代码**，不存在代码注入风险
- 所有指标必须过白名单，编造的指标会被拦下
- 因果性由数据层保证（指标全部是 rolling，用未来数据需要显式破坏）

### 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `{"configured": false}` | 没设 LLM_API_KEY | 设置环境变量后重启 |
| `HTTP 401` | key 无效或过期 | 换 key |
| `HTTP 400 response_format` | 服务不支持 JSON 模式 | 设 `LLM_JSON_MODE=off` |
| 编译很慢（>10s） | 模型太大或网络差 | 换小模型，或调 `LLM_TIMEOUT` |
| 一直 `source: rule` | LLM 调用失败后降级了 | 看响应里的 `warnings` |
