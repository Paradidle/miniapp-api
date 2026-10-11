# prosperity-bookkeeping-api（富贵记账 · 后端）

富贵记账微信小程序的服务端，技术栈 **Python 3.11 + Flask + PyMySQL + gunicorn**，部署在**自有服务器**上，通过 **CloudBase AnyService** 接入微信小程序（前端用 `wx.cloud.callContainer` 调用），低内存方案（单 worker 常驻约 40–70MB）。

统一响应格式：

```json
{ "code": 0, "message": "ok", "data": {} }
```

`code = 0` 表示成功，非 0 为业务错误（401 未登录、404 不存在、500 服务异常等）。

---

## 一、目录结构

```
prosperity-bookkeeping-api/
├── app/
│   ├── __init__.py        # Flask 应用工厂、健康检查、全局异常
│   ├── config.py          # 环境变量配置
│   ├── db.py              # PyMySQL + DBUtils 连接池
│   ├── auth.py            # 登录态（X-WX-OPENID / Bearer token）
│   ├── wechat.py          # code2session、小程序码
│   ├── response.py        # ok() / fail() 统一响应
│   └── routes/
│       ├── auth.py        # 登录、资料修改
│       ├── room.py        # 房间增删查改、我的房间
│       ├── score.py       # 转账记分、流水
│       └── misc.py        # 小程序码
├── sql/init.sql           # 建库建表脚本
├── config.ini             # 项目配置文件（MySQL/微信/上传等，不入库）
├── start.sh / stop.sh     # gunicorn 后台启动/停止脚本（日志 info.log）
├── wsgi.py                # 应用入口
├── gunicorn.conf.py       # 低内存 gunicorn 配置
├── Dockerfile             # Docker 部署（可选）
├── .dockerignore
└── requirements.txt
```

## 二、接口清单

| 方法 | 路径 | 说明 | 主要入参 |
| --- | --- | --- | --- |
| POST | `/api/login` | 登录（code 或网关注入 openid，openid 即用户唯一标识） | `code` / `userInfo` |
| POST | `/api/user/update` | 修改全局资料 | `userInfo{nickName,avatarUrl}` |
| POST | `/api/room/member/update` | 修改房间内资料 | `roomId, userInfo` |
| POST | `/api/room/create` | 创建房间 | `maxPlayers, userInfo` |
| POST | `/api/room/join` | 加入房间 | `roomNo` 或 `roomId`，`userInfo` |
| POST | `/api/room/start` | 开始游戏（房主） | `roomId` |
| POST | `/api/room/finish` | 结束游戏（房主） | `roomId` |
| GET | `/api/room/detail` | 房间详情 | `?roomId=` 或 `?roomNo=` |
| GET | `/api/rooms/mine` | 我的房间 | `?type=all/created/joined&status=all/waiting/playing/finished` |
| POST | `/api/score/transfer` | 转账记分 | `roomId, toOpenid, amount` |
| GET | `/api/score/records` | 房间流水 | `?roomId=` |
| POST | `/api/qrcode` | 生成加入房间小程序码（data URL） | `roomNo` |
| GET | `/healthz` | 健康检查（含数据库探测） | — |

关键约定：

- 房间状态：`waiting` / `playing` / `finished`；房号 `roomNo` 为 6 位数字；房间唯一 ID `roomId`（后端 BIGINT，序列化为字符串返回，对应前端的 `_id`）。
- 转账语义：**当前登录用户**付款给 `toOpenid`，付款人 `-amount`、收款人 `+amount`，两步更新与流水写入在同一事务内。
- 流水同时返回 `createdAt`（`YYYY-MM-DD HH:mm:ss`）与 `createTime`（毫秒时间戳），前端统一使用 `createTime`（iOS 不支持 `YYYY-MM-DD HH:mm:ss` 解析）。
- 头像不经本服务上传：小程序端直传 **CloudBase 云存储**（`wx.cloud.uploadFile`，免配下载域名），库中存 `cloud://` fileID；展示时前端用 `wx.cloud.getTempFileURL` 换临时链接。

## 三、初始化数据库

在服务器上自装 MySQL（5.7+ / 8.0 均可），创建数据库并导入表结构：

```bash
mysql -h127.0.0.1 -uroot -p < sql/init.sql
```

默认库名 `prosperity_bookkeeping`。如改库名，请同步修改 `sql/init.sql` 与环境变量 `MYSQL_DATABASE`。

四张表：`users`（用户）、`rooms`（房间）、`room_members`（成员+实时积分）、`score_records`（积分流水）。

## 四、本地调试

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

修改项目根目录的 `config.ini`（MySQL 地址账号、微信 AppID/AppSecret、`token_secret` 等），然后：

```bash
python wsgi.py
# 监听 0.0.0.0:80（可用环境变量 PORT 改端口）
curl http://127.0.0.1/healthz
```

注意：本机开发时可在请求头手动加 `X-WX-OPENID: <openid>` 模拟网关注入的登录态，方便用 Postman/curl 调试。

## 五、部署到自有服务器 + AnyService 接入

部署架构：小程序 → AnyService → 服务器 **80（nginx）** → **127.0.0.1:8089（gunicorn）**。gunicorn 只监听本机回环地址，外部只能经 nginx（80）访问，AnyService 源站直接填服务器 IP 即可。

1. **服务器上安装 MySQL** 并执行 `sql/init.sql`（见第三节）。

2. **部署服务**（venv + gunicorn 启停脚本）：

   ```bash
   # 上传代码到服务器，例如 /opt/prosperity-bookkeeping-api
   cd /opt/prosperity-bookkeeping-api
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
   ```

   修改项目根目录的 `config.ini`（无需配置任何系统环境变量）：

   | 节/项 | 说明 |
   | --- | --- |
   | `[mysql] host/port/user/password/database` | MySQL 连接信息；与本服务同机部署时 host 填 `127.0.0.1` |
   | `[wechat] appid/secret` | 小程序 AppID / AppSecret |
   | `[wechat] env_version` | `release` / `trial` / `develop` |
   | `[business] token_secret` | 任意长随机串（签名登录 token） |
   | `[business] upload_dir` | 上传目录，留空默认 `<项目目录>/uploads/` |

   启动 / 停止（项目自带脚本，gunicorn 后台监听 `127.0.0.1:8089`，日志写入项目根目录 `info.log`，PID 记录在 `gunicorn.pid`）：

   ```bash
   chmod +x start.sh stop.sh   # 首次执行赋予执行权限
   ./start.sh                  # 启动
   ./stop.sh                   # 停止（优雅退出，超时自动强杀）
   tail -f info.log            # 查看日志
   curl http://127.0.0.1:8089/healthz   # 本机直连 gunicorn 验证
   ```

3. **配置 nginx 反向代理**（在现有 80 端口的 `server {}` 中新增 location，配置片段见部署时输出/下方说明）：

   - `location /` 转发到 `http://127.0.0.1:8089`，并设置 `client_max_body_size 5m`
   - `location /uploads/` 用 `alias` 指向项目目录下的 `uploads/`，头像由 nginx 直出
   - 注意同一 server 内不能已有 `location /`（重复会报 `duplicate location`），有的话改为本代理配置

   改完执行 `nginx -t && systemctl reload nginx`，再 `curl http://127.0.0.1/healthz` 验证。

   可选：若想让服务开机自启，可配 systemd unit（注意需显式指定内部监听地址）：

   ```ini
   [Unit]
   Description=Prosperity Bookkeeping API
   After=network.target mysql.service

   [Service]
   WorkingDirectory=/opt/prosperity-bookkeeping-api
   Environment=GUNICORN_BIND=127.0.0.1:8089
   ExecStart=/opt/prosperity-bookkeeping-api/.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
   Restart=on-failure
   User=www-data

   [Install]
   WantedBy=multi-user.target
   ```

   也可用本目录 `Dockerfile` 以 Docker 方式运行（容器场景用环境变量覆盖 config.ini 中的值）。

4. **CloudBase 控制台配置 AnyService**：添加接入服务，源站填服务器公网地址（`IP:80` 或直接填 `IP`），记下**服务标识**（前端 `ANYSERVICE_NAME` 要用）。健康检查路径填 `/healthz`。

5. 验证：`curl http://<服务器IP>/healthz` 返回 `"code": 0` 即部署成功（安全组只需放行 80，不要对外开 8089）。

## 六、小程序端接入说明

前端 `utils/api.js` 已接入 AnyService，统一调用方式：

```js
wx.cloud.callContainer({
  config: { env: 'CloudBase环境ID' },   // utils/api.js 顶部 CLOUD_ENV
  path: '/api/room/detail',
  method: 'GET',
  data: { roomId },
  header: {
    'X-WX-SERVICE': 'tcbanyservice',
    'X-AnyService-Name': '你的AnyService服务标识'  // utils/api.js 顶部 ANYSERVICE_NAME
  }
})
```

要点：

- AnyService 网关自动注入 `X-WX-OPENID`，后端无需前端传 openid；`/api/login` 只传 `userInfo`。**openid 即用户唯一标识，不采集手机号**，个人主体小程序也可正常使用。
- 头像在小程序端直传 **CloudBase 云存储**（`wx.cloud.uploadFile`，免配下载域名），库中存 `cloud://` fileID；展示用 `wx.cloud.getTempFileURL` 换临时链接（云存储权限设为"所有用户可读"可让链接长期有效）。
- 扫小程序码进入已在 `pages/join/join.js` 的 `onLoad` 解析 `options.scene`（格式 `roomNo=xxxxxx`）。
- 前端使用前需填写 `utils/api.js` 顶部两个常量：`CLOUD_ENV`（CloudBase 环境 ID）、`ANYSERVICE_NAME`（AnyService 服务标识）。
