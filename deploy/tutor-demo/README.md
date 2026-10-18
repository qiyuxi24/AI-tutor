# 演示模式：公开免口令 + 在线规模限制（仅演示用）

比赛演示时把「访客口令门」临时关掉：公开 IP 直达应用登录页，同时**关闭公开注册**并限制在线规模。平时保持关闭，演示前 `on`、演示后 `off`。

## 组成

宿主 nginx 站点配置里有三处钩子，都在 `/etc/nginx/sites-available/default`：

1. `limit_conn_zone $server_name zone=tutor_seat:1m;` 和 `zone=tutor_chat`（http 上下文，常驻无害）；
2. `map "$tutor_gate_ok:$tutor_demo" $tutor_pass`（复合开关，缺通行证但演示模式 = 放行）；
3. server 块内 `set $tutor_demo 0;` + `include /etc/nginx/tutor-demo.d/*.conf;`，`location /` 用 `if ($tutor_pass = 0) { return 302 /gate; }`。

演示片段文件存在 → 生效；删除 → 恢复门口。`/gate` 与 `/admin`（含 `/api/v1/admin/`）**始终**要口令。

| 文件 | 作用 |
|---|---|
| `demo.conf.template` | 演示片段：`set $tutor_demo 1;` + 关注册 + SSE/对话流并发上限 3 |
| `toggle.sh` | 开关脚本：`on` / `off` / `status` / `restore`；`nginx -t` 失败自动回滚，绝不带坏配置 reload |

服务器落点：`/root/tutor-demo.sh`（脚本）+ `/root/tutor-demo/`（`demo.conf.template`、`default.base.conf` 当前站点配置快照、`original-default.conf` 上线时原配置）。

## 用法

```bash
/root/tutor-demo.sh on       # 开：/ → 200 直达登录页；注册 403；在线页面/并发对话各限 3
/root/tutor-demo.sh status   # 演示模式状态 + / 状态码 + 网关口令 + 容器健康 + nginx 状态
/root/tutor-demo.sh off      # 关：/ → 302 /gate，恢复原行为
/root/tutor-demo.sh restore  # 应急：站点配置恢复成上线时原版（同时删演示片段）
```

## 限制语义（重要，别当成「登录占席」）

- **在线页面 ≤ 3**：每个页面恰好 1 条 `/api/v1/knowledge/events` SSE 长连接，第 4 个页面的实时通道返回 429（页面能打开，但收不到实时刷新），**关标签立刻释放**。
- **并发对话 ≤ 3**：`/api/v1/chat/stream` 第 4 个并发提问返回 429（保护 LLM 额度）。
- **注册关闭**：`POST /api/v1/auth/register` → 403 JSON。
- 不是「登录时占席、满员拒绝登录」：已登录会话不会被踢；`off` 之后已建立的 SSE 长连接可能活到自然断开（`proxy_read_timeout` 300s），但**新请求**立刻回门口。
- 纯 nginx 实现，不改后端代码；`limit_conn` 只能在 `http` 上下文定义 zone（已常驻配置，演示模式关闭时不参与任何请求判定）。

## 首次部署 / 迁移到新服务器

```bash
mkdir -p /root/tutor-demo /root/nginx-backups /etc/nginx/tutor-demo.d
# 本机：把两个文件传上去
scp deploy/tutor-demo/demo.conf.template root@<IP>:/root/tutor-demo/
scp deploy/tutor-demo/toggle.sh          root@<IP>:/root/tutor-demo.sh
ssh root@<IP> 'chmod +x /root/tutor-demo.sh'
```

然后把下面三处钩子合进站点配置（`/etc/nginx/sites-available/default`），并各存一份快照：
`cp 站点配置 /root/tutor-demo/default.base.conf`（带钩子的当前版）与 `/root/tutor-demo/original-default.conf`（上线时原版）。

**① http 上下文**（放在 `limit_req_zone` 附近）：

```nginx
limit_conn_zone $server_name zone=tutor_seat:1m;
limit_conn_zone $server_name zone=tutor_chat:1m;
```

**② http 上下文**（放在通行证 `map $cookie_tutor_gate …` 之后）：

```nginx
map "$tutor_gate_ok:$tutor_demo" $tutor_pass {
    default  0;   # 没通行证，也不是演示模式 → 赶回 /gate
    "1:0"    1;   # 有通行证，非演示
    "0:1"    1;   # 演示模式，直接放行
    "1:1"    1;   # 两者都有
}
```

**③ server 块内**（`client_max_body_size` 之后）：

```nginx
set $tutor_demo 0;
include /etc/nginx/tutor-demo.d/*.conf;
```

并把业务 `location /` 的判定从 `if ($tutor_gate_ok = 0)` 改成 `if ($tutor_pass = 0)`。
`/gate`、`/admin`、`/api/v1/admin/` 的判定**保持 `$tutor_gate_ok`**（后台不放开）。

> ⚠️ 仓库里的 `deploy/gateway-nginx.conf` 是「白名单 + 硬限流」模板，**不含**上述钩子；服务器现网站点配置与它已分叉（现网另有 `/admin` 两个 location）。重新套用模板前要手工把三处钩子并进去，否则演示开关会失效。
>
> ⚠️ 复核 `map` 复合键的写法：`"$a:$b"` 拼出来的是 `1:0` 这种**带冒号**的字符串，写成 `"10"` 会永远匹配不上（2026-10-18 首次落地踩过：演示模式开了但 `/` 仍 302）。

## 回滚

- 演示片段异常：`/root/tutor-demo.sh off`（自动 `rm` + `nginx -t` + reload）。
- 站点配置被改坏：`/root/tutor-demo.sh restore`。
- 完整回退：`cp /root/nginx-backups/default.orig-<时间戳>.conf /etc/nginx/sites-available/default && nginx -t && systemctl reload nginx`（备份含演示上线前原配置）。
