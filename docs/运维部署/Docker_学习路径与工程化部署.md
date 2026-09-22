# Docker 完整学习路径与工程化部署指南

> 适用对象：已具备 Linux 基础、正在学习云原生 / K8s / Agent 工程化的同学
> 编写日期：2026-08-31（2026-09-13 更新：新增排障/瘦身/Windows/AI 工程四章，版本与镜像加速同步至 2026-09）
> 本项目（AI-tutor）本身就是一套完整的 Docker 化工程，文档中所有「实战注解」均对应仓库内的真实文件，可对照学习。

---

## 目录

1. [Docker 到底是什么](#一docker-到底是什么)
2. [三大核心概念](#二三大核心概念)
3. [镜像为什么分层](#三镜像为什么分层)
4. [核心命令速查](#四核心命令速查)
5. [Dockerfile 工程化写法](#五dockerfile-工程化写法)
6. [Dockerfile 关键指令对比与易错点](#六dockerfile-关键指令对比与易错点)
7. [Docker Compose 多容器编排](#七docker-compose-多容器编排)
8. [容器排障实战](#八容器排障实战)
9. [磁盘清理与镜像瘦身](#九磁盘清理与镜像瘦身)
10. [部署到服务器：完整可操作流程](#十部署到服务器完整可操作流程)
11. [生产环境工程方法](#十一生产环境工程方法)
12. [Windows / WSL2 本地开发](#十二windows--wsl2-本地开发)
13. [AI / Agent 工程 Docker 实践](#十三ai--agent-工程-docker-实践)
14. [完整学习路径（分阶段 + 验收标准）](#十四完整学习路径分阶段--验收标准)
15. [学生服务器资源推荐（2026-09）](#十五学生服务器资源推荐2026-09)
16. [学习资源与官方文档链接](#十六学习资源与官方文档链接)

---

## 一、Docker 到底是什么

### 1.1 一个比喻：散货码头 vs 集装箱码头

传统部署像**散货码头**：每个应用自带一整套运行时、依赖、配置文件。在一台机器上装好了，换台机器、换个环境（版本不一致、缺个库）就"装不上、跑不起来"，即经典的 **"在我电脑上是好的啊"** 问题。

Docker 像**集装箱码头**：把"应用 + 运行环境 + 配置"整个打包进一个标准集装箱（**镜像 Image**）。任何装了"吊车"（Docker 引擎）的服务器，都能无差别地把它卸下来、跑起来。**一次构建，到处运行。**

### 1.2 本质：操作系统层面的虚拟化

Docker **不是虚拟机**。虚拟机虚拟的是**硬件**（要装完整 Guest OS，重、慢、占资源）；Docker 虚拟的是**操作系统**——所有容器共享宿主机内核，只靠 Linux 内核的三个机制做隔离：

| 机制 | 作用 | 通俗理解 |
|---|---|---|
| **Namespace（命名空间）** | 隔离"视图" | 每个容器有自己的进程树、网络栈、文件系统、用户，看起来像一台独立的小系统 |
| **cgroups（控制组）** | 隔离"资源" | 限制每个容器最多用多少 CPU / 内存 / IO，防止一个容器拖垮整台机器 |
| **UnionFS / OverlayFS** | 分层存储 | 镜像分层 + 写时复制（CoW），多容器共享底层只读层，省空间、启动快 |

所以 Docker 容器**秒级启动**、**占用极小**（MB 级 vs 虚拟机的 GB 级），因为它没有多一层操作系统。

---

## 二、三大核心概念

```
┌─────────────────────────────────────────────────────────┐
│                   工作流（一句话记住）                     │
│                                                         │
│  写 Dockerfile ──build──▶ 镜像 Image ──push──▶ 仓库       │
│  （配方）        （打包好      （推送到  （Docker Hub等）    │
│                  的集装箱）    镜像仓库）                   │
│                                                         │
│  服务器 ──pull──▶ 镜像 ──run──▶ 容器 Container            │
│          （拉取）        （运行实例）                       │
└─────────────────────────────────────────────────────────┘
```

- **镜像 Image**：只读的模板，分层存储。类比**"类（Class）"**。
- **容器 Container**：镜像的运行实例，顶部有可写层。类比**"对象（Instance）"**。
- **仓库 Registry**：存镜像、分发镜像的地方。类比 **GitHub 之于代码**，Docker Hub 之于镜像。

### 实战注解（对照本项目）

本项目 `docker-compose.yml` 里的 `image: ai-tutor:latest` 就是构建产物镜像；每次 `docker compose up -d --build` 会用 `Dockerfile` 重新构建镜像再跑容器。容器被删后镜像还在，随时可以再 `run` 一个出来——这就是"镜像 vs 容器"最直观的区别。

---

## 三、镜像为什么分层

```
       容器运行时的可写层（写时复制）
      ┌───────────────────────────────┐
      │  Layer 5: CMD / ENTRYPOINT    │  ← 每一条 Dockerfile 指令
      │  Layer 4: COPY 代码           │     生成一个只读层
      │  Layer 3: RUN pip install     │
      │  Layer 2: 安装 Python         │
      │  Layer 1: FROM ubuntu:24.04   │  ← 基础镜像层
      └───────────────────────────────┘
```

- 多个镜像可以**共享底层公共层**（比如都基于 ubuntu/python），只需存一份，极大省磁盘。
- **构建缓存**：某层没变就直接复用，改一行代码重建只需几秒。
- 容器删除后，写层消失，但镜像层还在——所以**容器无状态、可随时重建**。

### 实战注解（对照本项目 `Dockerfile`）

本项目是**多阶段构建**，前端（node）与后端（python）分层处理：

```dockerfile
FROM node:22-alpine AS frontend-build   # Stage 1：只用来构建前端
...
FROM python:3.13-slim                    # Stage 2：真正的运行镜像
...
COPY --from=frontend-build /build/dist /usr/share/nginx/html   # 只拷构建产物
```

关键点：**"先拷贝依赖清单，再拷贝源码"**（`COPY frontend/package*.json ./` 在前，`COPY frontend/ ./` 在后）。这样只要 `package.json` 没变，`npm install` 那层缓存就不会失效，重建速度快很多。

---

## 四、核心命令速查

### 4.1 镜像管理

```bash
docker pull nginx:1.27          # 拉镜像（务必指定版本，别用 latest）
docker images                   # 查看本地镜像
docker rmi <镜像ID>             # 删除镜像
docker tag myapp:1.0.0 myapp:1.0.1   # 打标签
```

### 4.2 容器生命周期

```bash
docker run -d \                 # -d 后台运行
  --name myweb \                # 容器名
  -p 8080:80 \                  # 端口映射 宿主机8080→容器80
  -v /data:/app/data \          # 数据卷挂载
  -e ENV_NAME=prod \            # 环境变量
  --restart unless-stopped \    # 崩溃自动重启
  --memory 512m --cpus 1.0 \    # 资源限制
  nginx:1.27

docker ps                      # 查看运行中容器（-a 看全部）
docker logs -f myweb           # 实时看日志
docker exec -it myweb bash     # 进入容器内部
docker cp a.txt myweb:/app/    # 拷文件进出
docker inspect myweb           # 查看容器详细信息
docker stop/start/restart/rm myweb
```

### 4.3 网络与卷

```bash
docker network create mynet     # 自定义网络（容器间用服务名通信）
docker volume create mydata     # 命名卷（数据持久化推荐用这个）
```

### 实战注解（对照本项目 `docker-compose.yml`）

你项目里这些配置就是上面命令的"声明式写法"：

```yaml
ports:
  - "${PORT:-8080}:80"        # 对应 -p 端口映射
volumes:
  - ./data/knowledge:/app/data/knowledge   # 对应 -v 数据卷挂载
environment:
  - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY:?...}   # 对应 -e 环境变量
restart: unless-stopped       # 对应 --restart
healthcheck: ...              # 健康检查（见第五节）
```

> 注意 `:?` 语法：`${DASHSCOPE_API_KEY:?提示语}` 表示该变量**必须配置**，否则直接启动失败并报错——防止"静默带着空 Key 上线"这种事故。

---

## 五、Dockerfile 工程化写法

下面是一个**符合生产规范**的通用 Python 服务 Dockerfile——多阶段构建、非 root、健康检查一次到位：

```dockerfile
# ===== 阶段1：构建环境 =====
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===== 阶段2：运行环境（更小更安全）=====
FROM python:3.12-slim
# 1) 创建非 root 用户（安全：防止容器逃逸/提权）
RUN useradd --create-home appuser
# 2) 设置时区、必要依赖
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 3) 只拷贝阶段1的成果，丢掉构建中间产物 → 镜像小
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY app.py .

# 4) 切到非 root 用户
USER appuser

# 5) 健康检查（配合 --restart 和编排做自动恢复）
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["python", "app.py"]
```

构建与运行：

```bash
docker build -t myapp:1.0.0 .
docker run -d --name app -p 8000:8000 --restart unless-stopped myapp:1.0.0
```

### 实战注解（对照本项目 `Dockerfile`）

本项目有几个值得学习的生产技巧：

1. **ENV 一次性声明**：`ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Asia/Shanghai`——关闭 pyc、缓冲、pip 缓存，减少镜像体积并让日志实时输出。
2. **apt 清理**：`rm -rf /var/lib/apt/lists/*`——安装完系统依赖立即清理缓存。
3. **删默认站点**：`rm -f /etc/nginx/sites-enabled/default`——Debian nginx 自带默认站点会占用 `listen 80`，不删会与自定义配置冲突。
4. **Windows CRLF 兜底**：`sed -i 's/\r$//'`——仓库在 Windows 下编辑，文件可能带 CRLF 换行导致 Linux 执行报错，构建时强制转回 LF。
5. **`.dockerignore` 排除敏感与冗余**：`.env`、`venv`、`node_modules`、`data/`、`*.md` 等全部排除在构建上下文外——密钥不进镜像、镜像不背无关文件。

---

## 六、Dockerfile 关键指令对比与易错点

> 写 Dockerfile 时最容易混、面试也最爱考的四组对比。先记区别，再对照本项目 `Dockerfile` 找实例。

### 6.1 ENTRYPOINT vs CMD（谁才是主程序）

| 指令 | 作用 | 能被 `docker run` 参数覆盖？ |
|---|---|---|
| `ENTRYPOINT` | 固定主程序 / 入口 | ❌ 只有 `--entrypoint` 能换 |
| `CMD` | 默认启动命令 / 默认参数 | ✅ `docker run img <args>` 直接覆盖 |

**标准组合**：`ENTRYPOINT` 定程序，`CMD` 给默认参数。

```dockerfile
ENTRYPOINT ["python", "app.py"]
CMD ["--host", "0.0.0.0"]        # docker run img --port 8080 可覆盖
```

### 6.2 shell form vs exec form（生产必须用 exec）

| 写法 | 示例 | 后果 |
|---|---|---|
| shell form | `CMD python app.py` | 实际由 `/bin/sh -c` 执行，**PID 1 是 sh**，SIGTERM 被 sh 吞掉 → `docker stop` 等不到优雅退出，可能丢数据 |
| exec form | `CMD ["python", "app.py"]` | 程序直接是 PID 1，信号直达，能优雅停机 |

> 本项目 `entrypoint.sh` 的思路就是 exec/前台思维：主进程必须**前台运行**（`nginx -g "daemon off;"`），容器才不会起完就 `Exited (0)`。

### 6.3 ARG vs ENV（构建期 vs 运行期）

| | `ARG` | `ENV` |
|---|---|---|
| 作用时期 | 仅构建期（`docker build --build-arg` 传入） | 构建期 + 运行期 |
| 容器内可见 | ❌ | ✅（`printenv` 能查到） |
| 泄露风险 | 无 | `docker history` 能看到值，**别放密钥** |

```dockerfile
ARG PYTHON_VERSION=3.13          # 构建期参数，FROM 里可用
FROM python:${PYTHON_VERSION}-slim
ENV APP_ENV=prod                 # 运行期环境变量，进容器
```

### 6.4 COPY vs ADD（默认只用 COPY）

- `COPY`：只复制文件/目录。
- `ADD`：额外支持**自动解压本地 tar** 和 **URL 下载**（URL 下载不推荐，构建不可复现、难缓存）。
- 规则：**默认 COPY**；只有"要把本地 tar 包解压进镜像"这一种场景才用 ADD。

### 6.5 层与缓存：让重建快 10 倍的顺序

每条指令产生一层，**层缓存命中规则**：某层内容没变，该层及之后直接复用。所以把"变化频率低"的放前面：

```dockerfile
COPY requirements.txt .     # ① 先拷依赖清单（低频变化）
RUN pip install ...         # ② 装依赖 → 只要①没变就命中缓存
COPY . .                    # ③ 最后拷源码（高频变化）
```

> 本项目 `Dockerfile` 正是这个顺序（依赖清单在前、源码在后），改一行代码重建只重跑 ③ 之后，几秒完成。
> 减层技巧：`RUN apt-get update && apt-get install ... && rm -rf /var/lib/apt/lists/*` 用 `&&` 串成**一层**并在同层清理缓存（本项目 Stage 2 就是实例）。

---

## 七、Docker Compose 多容器编排

单容器用 `docker run` 就够了；一旦有 **"应用 + 数据库 + 缓存"** 这种多服务架构，就用 Compose——一个文件定义全部服务，一条命令启动整个系统。

```yaml
# compose.yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DB_HOST=db            # 直接用服务名通信，不用IP
      - SECRET_KEY=${SECRET_KEY}   # 从 .env 读取，不进Git
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data   # 数据持久化
    environment:
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
      interval: 10s

volumes:
  pgdata:
```

```bash
docker compose up -d          # 启动整个系统
docker compose logs -f web    # 看某服务日志
docker compose down           # 停止
docker compose pull && docker compose up -d   # 更新到新版本
```

### 实战注解（对照本项目）

本项目是**单容器方案**（前端 Nginx + 后端 Uvicorn 塞进同一个镜像，用 `entrypoint.sh` 同时拉起两个进程），所以 compose 里只有一个 `ai-tutor` 服务。它的 `healthcheck` 值得学：

```yaml
healthcheck:
  test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/api/health"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 30s
```

- 后端就绪判定用 `/api/health` 接口而不是 TCP 探测——**应用级健康检查**远比端口通不通靠谱。
- `start_period: 30s` 给首次启动的宽限期，期间失败不计入 retries。
- `entrypoint.sh` 里还做了"两个进程任一退出则整个容器退出"的逻辑，配合 `restart: unless-stopped` 实现自动恢复——这是多进程容器的标准做法。

> 版本说明（2026-09 时点）：Docker Engine 最新稳定版为 **29.8.0**（2026-09-03 发布），Compose CLI 为 **v5.5.x**（v5 于 2025 年发布，引入官方 Go SDK，命令仍是 `docker compose`）。**compose 文件格式（compose.yaml）依然是 v2/v3 语法**，网上绝大多数示例仍然适用。
> 安全提醒：Docker Engine **≥ 29.4.3** 才修复 CVE-2026-31431（"Copy Fail"），生产环境务必升级或打内核补丁。

### 7.4 Compose 进阶（先记住，用到再查）

```bash
docker compose config                       # 校验 + 查看变量替换后的最终配置（写错键会在这里报错）
docker compose ps                           # 只看本 compose 的容器
docker compose top                          # 容器内进程
docker compose events                       # 实时事件
docker compose -f base.yml -f override.yml up -d   # 多文件覆盖（dev/prod 差异）
docker compose watch                        # 开发模式：代码变更自动同步/重建
```

- **profiles**：给服务打标签、按需启动——`docker compose --profile dev up` 只起标了 `profiles: [dev]` 的服务，适合"开发要 redis、生产不要"的场景。
- **depends_on**：compose 只保证**启动顺序**，要真正"等就绪"必须配合 `condition: service_healthy`（见本节开头示例里 db 服务的写法）。

---

## 八、容器排障实战

> 学 Docker 最常遇到的不是"不会写"，而是"起不来"。记住**排障四板斧**，90% 的问题 5 分钟内定位。

### 8.1 排障四板斧

```bash
docker ps -a                       # ① 看状态：Exited / Restarting / Up(unhealthy)
docker logs --tail 100 <容器名>    # ② 看日志：程序真正报错的地方（-f 实时跟随）
docker inspect <容器名>            # ③ 看配置：State.ExitCode / State.Error / Mounts / Ports
docker stats <容器名>              # ④ 看资源：CPU / 内存是否被打满
```

### 8.2 常见症状速查表

| 症状 | 常见原因 | 处理 |
|---|---|---|
| `Exited (0)` | 主进程退出了（典型：CMD 写了后台命令 `&`，进程跑完就退） | `docker logs` 看输出；主进程必须前台运行（见 6.2） |
| `Exited (1)` | 程序启动即崩：缺依赖 / 密钥 / 配置错误 | `docker logs` 看 traceback；对照 `.env` 检查环境变量 |
| `Restarting (…)` | 崩溃→重启循环（restart 策略生效中） | `docker logs` 看退出原因；`docker inspect` 看 ExitCode |
| `Up (unhealthy)` | 健康检查失败 | 进容器手动 `curl 127.0.0.1:8000/api/health`；检查 start_period 是否给够 |
| 端口冲突 `bind: address already in use` | 宿主机端口被占 | `docker ps` 看谁占了；换 `${PORT}` |
| 容器内 404 | 静态文件路径 / 反代配置不对 | `docker exec -it <名> sh` 进去 `ls` 验证文件在不在 |
| 日志刷不出来 | 程序输出被缓冲 | 设 `PYTHONUNBUFFERED=1`（本项目已内置） |

### 8.3 进阶调试手法

```bash
docker exec -it <容器名> sh        # 进容器手动验证（ps / curl / ls）
docker cp a.txt <容器名>:/app/     # 拷文件进出容器
docker diff <容器名>               # 对比容器与镜像的文件差异（排查运行时改动）
docker top <容器名>                # 看容器内进程
docker events                      # 实时事件流（start / die / health_status）
```

> 实战：本项目容器 `Restarting` 时，先 `docker compose logs ai-tutor`，再 `docker inspect ai-tutor | grep -A2 ExitCode`——十有八九是 `SECRET_KEY` 没配或 `DASHSCOPE_API_KEY` 为空（compose 里 `:?` 语法会直接拦截）。

---

## 九、磁盘清理与镜像瘦身

> 用 Docker 半年后最痛的问题：C 盘 / 服务器磁盘莫名爆满。90% 是**镜像层 + 构建缓存 + 悬空卷**。

### 9.1 先看谁占的空间

```bash
docker system df                    # 镜像 / 容器 / 卷 / 构建缓存 四大类占用一览
docker system df -v                 # 明细到每个镜像、每个卷
docker image ls --format "{{.Repository}}:{{.Tag}} {{.Size}}"   # 只看镜像大小
```

### 9.2 清理命令（从温和到激进）

```bash
docker container prune              # 删所有已停止的容器
docker image prune                  # 删悬空镜像（dangling，即 <none>）
docker image prune -a               # 删所有未被容器使用的镜像
docker builder prune                # 清构建缓存（经常几十 GB！）
docker volume prune                 # 删未被引用的卷（⚠️ 数据没了，先确认）
docker system prune -a --volumes    # 大扫除（= 上面全做，⚠️ 慎用）
```

> 建议习惯：每月跑一次 `docker system df`；构建完顺手 `docker builder prune`。

### 9.3 镜像体积对比（选对基础镜像省一半）

| 基础镜像 | 压缩后约 | 特点 |
|---|---|---|
| `ubuntu:24.04` | ~80 MB | 完整工具链，适合装系统包 |
| `python:3.13-slim` | ~120 MB | Debian slim，**本项目在用**，体积/兼容平衡 |
| `python:3.13-alpine` | ~50 MB | 极小，但 musl 偶有二进制兼容坑 |
| `gcr.io/distroless` | ~30 MB | 无 shell 无包管理器，最安全，但调试难 |

> 体积为压缩后近似值，`docker image ls` 看到的是解压后大小，会更大。

### 9.4 buildx 多架构构建（Mac/Windows 构建 → 服务器直接拉）

```bash
docker buildx create --use
docker buildx build --platform linux/amd64,linux/arm64 \
  -t yourname/myapp:1.0.0 --push .
# 本地一次构建两种架构 → 推仓库 → 服务器（amd64）直接 pull 运行，免服务器编译
```

> 场景：你 Windows/Mac 本地构建，云服务器通常是 amd64，多架构镜像让两边免编译直接跑。

---

## 十、部署到服务器：完整可操作流程

下面以一台 **Ubuntu 云服务器** 为例，从零到 HTTPS 上线。

### 步骤 1：安装 Docker Engine（服务器上不要装 Docker Desktop）

```bash
# 官方安装脚本
curl -fsSL https://get.docker.com | sh

# 让当前用户免 sudo 使用 docker（避免每次 sudo）
sudo usermod -aG docker $USER
# 重新登录后生效
```

### 步骤 2：配置 daemon.json（日志限制 + 镜像加速）

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io",
    "https://docker.nju.edu.cn"
  ]
}
EOF
sudo systemctl restart docker
```

> 加速器说明：`docker.1ms.run`（2026-09 实测成功率最高的商业加速）为主用，`docker.m.daocloud.io`（DaoCloud）、`docker.nju.edu.cn`（南京大学，也加速 GCR/GHCR/Quay）为备选；加速器地址变化快，失效就换；腾讯云 `mirror.ccs.tencentyun.com` 仅腾讯云内网可用。注意 **JSON 不支持注释**，直接照抄上面的块即可。

### 步骤 3：本地构建 → 推送仓库 → 服务器拉取

```bash
# 本地
docker build -t yourname/myapp:1.0.0 .
docker push yourname/myapp:1.0.0

# 服务器上
docker pull yourname/myapp:1.0.0
```

> 镜像仓库（Docker Hub 私有库 / 阿里云 ACR / Harbor）是"本地 ↔ 服务器"之间传镜像的正规通道，别用 `docker save` 手动拷。

### 步骤 4：用 Compose 拉起完整应用

把 `compose.yaml` 和 `.env` 放到服务器 `/opt/myapp/`：

```bash
cd /opt/myapp
docker compose up -d
```

### 步骤 5：反向代理 + 域名 + HTTPS

应用容器监听内网端口，对外统一由 Nginx 反代——只暴露 80/443，**不把应用端口裸奔公网**。

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

申请免费 HTTPS 证书：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com   # 自动配好HTTPS并续期
```

> 更省心的替代：**Caddy / Traefik** 自动申请和续期 HTTPS 证书，配置比 Nginx 简单得多。

### 步骤 6：数据备份与恢复（命根子）

```bash
# 备份命名卷
docker run --rm -v myapp_pgdata:/data -v /backup:/backup \
  alpine tar czf /backup/pgdata_$(date +%F).tar.gz -C /data .

# 恢复
docker run --rm -v myapp_pgdata:/data -v /backup:/backup \
  alpine tar xzf /backup/pgdata_2026-08-31.tar.gz -C /data
```

### 步骤 7：监控（生产必配）

```bash
# 一行启动监控全家桶：cAdvisor(指标) + Prometheus(存储) + Grafana(看板)
docker compose -f /opt/monitoring/docker-compose.yml up -d
```

### 实战注解（对照本项目 `deploy/README.md`）

本项目仓库里已有完整的《AI Tutor Docker 部署指南》（`deploy/README.md`），包括：
- 本地验证（PowerShell + docker compose up -d --build）
- 服务器部署（git clone → 配置 .env → 迁移数据 → 构建启动）
- HTTPS（云厂商 CDN 免费证书 / Let's Encrypt 两种方案）
- 常见问题排查（Key 未配置、容器 unhealthy、国内镜像加速、单 worker 硬约束、备份）

**学完本教程后，建议直接照 `deploy/README.md` 把这个项目部署到一台云服务器上，作为你的第一次实战。**

---

## 十一、生产环境工程方法

### 11.1 安全清单（对照自查）

| 检查项 | 做法 | 为什么 |
|---|---|---|
| 非 root 运行 | Dockerfile 里 `USER appuser` | 减少容器逃逸提权风险 |
| 密钥管理 | `.env` 加 `.gitignore`，镜像里不硬编码密码 | 镜像可被任何人拉取检查 |
| 数据库隔离 | 数据库**不映射**到宿主机公网端口 | 避免暴露到公网被扫 |
| 镜像最小化 | 多阶段构建 + slim/alpine 基础镜像 | 攻击面小、镜像小 |
| 版本锁定 | 镜像 tag 用 `1.0.0`，**禁用 latest** | 避免意外升级 |
| 镜像扫描 | CI 里跑 `trivy image myapp:1.0.0` | 发现已知漏洞 |
| 资源限制 | `--memory 512m --cpus 1.0` | 防止单容器吃光机器 |

### 11.2 CI/CD 自动化（一次配置，永远省心）

GitHub Actions 流程：**push → 构建镜像 → 扫描 → 推送仓库 → SSH 到服务器滚动部署**

```yaml
# .github/workflows/deploy.yml
name: Build & Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build & push image
        run: |
          docker build -t yourname/myapp:${{ github.sha }} .
          docker push yourname/myapp:${{ github.sha }}
      - name: Deploy on server
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SERVER_HOST }}
          script: |
            docker pull yourname/myapp:${{ github.sha }}
            cd /opt/myapp && docker compose up -d
```

### 11.3 零停机更新（关键技巧）

不要"停旧起新"（中间有流量空白），而是**新容器起来并通过健康检查后，再切换**：

```bash
# Compose 自动先起新容器、等健康检查通过、再删旧的
docker compose pull && docker compose up -d --no-deps --scale web=2
# 借助负载均衡/反代平滑切换，K8s/Traefik 可自动完成
```

---

## 十二、Windows / WSL2 本地开发（你的开发机实战）

> 本项目就是 Windows 下开发的（Dockerfile 里专门有 CRLF 兜底），这节讲 Windows 上跑 Docker 的 4 个坑。

### 12.1 Docker Desktop = WSL2 后端

Windows 上 Docker Desktop 用 **WSL2** 跑 Linux 容器（不是虚拟机）。装好后 `docker version` 能看到 Server 端跑在 WSL 里，Linux 容器在 Windows 上原生运行。

### 12.2 坑 1：bind mount 性能慢

Windows 目录（`C:\...`）和 WSL 之间是跨文件系统读写，大项目把代码挂载进容器会明显卡顿。对策：

- 代码放 **WSL 内**（`~/projects/...`），别放 `/mnt/c/` 下
- 依赖、数据用 **named volume**（存在 WSL 虚拟磁盘里，快）

### 12.3 坑 2：路径与换行

- Windows 路径 `C:\Users\34239\...` → WSL 里是 `/mnt/c/Users/34239/...`
- Windows 编辑的文件常带 CRLF，Linux 容器里执行会报 `$'\r': command not found` —— 本项目 Dockerfile 的 `sed -i 's/\r$//'` 就是为此加的

### 12.4 坑 3：WSL2 吃内存

Docker Desktop 默认吃 WSL2 可用内存。限制：用户目录下建 `.wslconfig`：

```ini
[wsl2]
memory=4GB
swap=2GB
```

改完 `wsl --shutdown` 重启 WSL 生效。

### 12.5 本项目的一键脚本

仓库根目录 `start-docker.ps1` 已封装日常操作（自动判断是否重建、等健康检查、打印访问地址）：

```powershell
.\start-docker.ps1            # 日常启动（源码比镜像新才重建）
.\start-docker.ps1 -Force     # 强制重建
.\start-docker.ps1 -Port 8081 # 换端口
```

---

## 十三、AI / Agent 工程 Docker 实践（你的方向）

### 13.1 GPU 容器（跑模型推理）

宿主机装好 NVIDIA Container Toolkit 后，容器直接透传 GPU：

```bash
docker run --gpus all -it --rm nvidia/cuda:12.4.1-runtime-ubuntu22.04 nvidia-smi
# 指定卡：docker run --gpus '"device=0,1"' ...
```

> vLLM / Ollama / text-generation-webui 等官方镜像都支持 `--gpus all`。以后做本地模型服务 / Agent 评测环境必用。

### 13.2 LLM 应用的典型编排（app + 向量库 + 模型）

```yaml
services:
  app:                        # 你的 Agent / FastAPI 服务
    build: .
    depends_on:
      qdrant: { condition: service_healthy }
    volumes:
      - ./data:/app/data       # 知识库 / 对话数据持久化
  qdrant:                     # 向量数据库
    image: qdrant/qdrant
    volumes: [ qdrant_data:/qdrant/storage ]
  ollama:                     # 本地模型服务（GPU）
    image: ollama/ollama
    volumes: [ ollama_models:/root/.ollama ]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [ gpu ]
volumes:
  qdrant_data:
  ollama_models:
```

要点：**模型权重、向量库数据、应用数据全部用卷持久化**，镜像只装代码和依赖——否则镜像膨胀到几十 GB，重建一次半小时。

### 13.3 本地开发热更新

```bash
docker compose watch          # 代码变更自动同步/重建（Compose 2.23+）
# 传统做法：卷挂载源码 + uvicorn --reload，改完即生效
```

### 13.4 多阶段构建在 Agent 工程的价值

Agent 工程典型是"前端 + Python 后端 + Nginx"混合栈，多阶段构建（本项目已示范）能做到：**构建产物只有几十 MB 的单镜像**，层缓存让改一行代码秒级重建。

---

## 十四、完整学习路径（分阶段 + 验收标准）

### 阶段 0：前置（0.5 周）
Linux 基础：目录结构、文件权限、进程、端口、systemd。不用精通，会看日志、会用 vim、懂基本网络概念即可。
Windows 用户先看第十二章（WSL2 环境与坑），把本地环境配好再开始。

### 阶段 1：核心概念与命令（1 周）
目标：彻底搞懂镜像/容器/仓库，熟练常用命令。
练习：把 nginx、mysql 分别跑起来，学会端口映射、进入容器、看日志；故意删掉容器再 `run` 回来体会"容器无状态"。
验收：能不看文档完成"拉镜像→运行→进容器→看日志→删容器"全流程；会用 `docker ps -a` + `logs` 排查"起不来"的问题（对照第八章）。

### 阶段 2：Dockerfile 与镜像优化（1 周）
目标：写规范 Dockerfile，理解分层缓存。
练习：把一个自己的小项目（Python 脚本/网页）容器化，逐步加多阶段构建，对比镜像体积；对照第六章梳理 ENTRYPOINT/CMD、ARG/ENV、COPY/ADD 区别。
验收：镜像体积优化到合理范围，重建时缓存生效；会用 `docker system df` / `prune` 清理磁盘（对照第九章）。

### 阶段 3：数据卷与网络（0.5 周）
目标：理解容器无状态设计、数据持久化、容器间通信。
练习：MySQL 数据存命名卷，删容器数据不丢；两个容器通过自定义网络互通。
验收：说得出 named volume 与 bind mount 的区别、什么时候用哪个。

### 阶段 4：Compose 多容器编排（1 周）
目标：学会编排多服务。
练习：搭一个 `web + postgres + redis` 的完整栈，配置 healthcheck、依赖顺序、环境变量；用 `docker compose config` 校验配置。
验收：`docker compose up -d` 一条命令起整套系统，容器异常时能按第八章流程定位。

### 阶段 5：部署上云（1 周）★ 最优先落地
目标：把项目真正部署到一台云服务器，域名 + HTTPS 访问。
练习：按上文第十节的 7 个步骤走一遍；本项目可直接照 `deploy/README.md` 实操。
验收：浏览器通过 `https://你的域名` 稳定访问你的应用，重启服务器后服务自动恢复。

### 阶段 6：生产化（1-2 周）
目标：安全加固、监控告警、CI/CD、备份恢复。
练习：接上 GitHub Actions 自动部署，接上 Prometheus+Grafana，做一次数据备份恢复演练。
验收：push 代码后全自动上线；模拟容器崩溃能自动恢复。

### 阶段 7：进阶（按需）
- **多架构构建**：buildx（见 9.4）——本地构建、多平台分发
- **GPU 容器**：`--gpus` + NVIDIA Container Toolkit（见 13.1）——AI 推理环境必备
- **编排器**：Kubernetes（衔接云原生）
- **服务网格**：Istio
- **Serverless 容器**：Fargate / K8s 上的 Knative

---

## 附：本仓库 Docker 相关文件索引

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 多阶段构建（node 构建前端 → python 运行时 + nginx） |
| `docker-compose.yml` | 编排、端口、环境变量、数据卷、健康检查 |
| `nginx.conf` | 静态托管 + `/api` 反代 + SSE 支持 |
| `entrypoint.sh` | 容器启动脚本（拉起 Nginx + Uvicorn，任一退出则容器退出） |
| `.dockerignore` | 构建上下文排除（密钥/运行时数据/本地依赖/文档） |
| `.env.example` | 部署配置模板 |
| `deploy/README.md` | 本项目部署实战指南 |

---

## 十五、学生服务器资源推荐（2026-09）

> 说明：价格与活动为 2026-09 检索结果，**以各平台官网活动页为准**；学生活动通常需学生认证（学信网 / 高校邮箱），且每年政策可能调整。

### 15.1 主流平台对比

| 平台 | 学生活动 | 典型配置 | 参考价格 | 认证门槛 | 备注 |
|---|---|---|---|---|---|
| **腾讯云·云+校园** | 校园计划 | 轻量 1核2G / 2核2G / 2核4G | 首单 38 元/年起；1核2G 约 10 元/月（120 元/年）；2核2G 约 120 元/年 | **25 岁以下免学生证认证** | 国内节点，学生党最省心，首选 |
| **阿里云·云翼计划 / 高校计划** | 学生机 / 云工开物 | ECS 学生机 1年99元（续费同价）；轻量 2核2G 40G SSD 约 9 元/月 | 学生机 99 元/年；另有 300 元无门槛代金券 | 需学信网学生认证 | 代金券额度大，可覆盖入门机一年费用 |
| **华为云** | 沃土计划 / 新客活动 | Flexus 2核2G | 新客 36 元/年起 | 学生活动门槛不一 | 学生专属活动不如前两者明确，需按当期活动确认 |
| **GitHub Student Developer Pack** | DigitalOcean / Azure / Heroku | Droplet 基础款 | DigitalOcean $100–$200 额度（约够跑 4–6 个月）；Azure $100 额度 | 需 GitHub Education 学生认证（edu 邮箱） | 海外节点，国内访问延迟高，适合练手 + 学海外部署 |

### 15.2 直接推荐（结合你的场景）

你学 Docker 部署 + 部署 AI-tutor，**单容器、Nginx + Uvicorn、SQLite**，2核2G 完全够用，不要买高配：

- **首选：腾讯云「云+校园」轻量应用服务器 2核2G**（约 120 元/年）。25 岁以下免学生证认证，国内节点访问快；以后学 K8s 还能在单机上跑 minikube / k3s 练手。
- **预算极致方案：阿里云学生机 99 元/年**（ECS，续费同价）。先完成学信网认证、领「云工开物」300 元无门槛券。
- **白嫖方案：GitHub Student Pack** → 用 edu 邮箱认证 GitHub Education → 领 DigitalOcean / Azure 额度。适合体验海外云 + 学英文运维文档，但国内访问慢，不建议做主生产机。
- **免费练手（不买服务器也能练）**：Play with Docker / Killercoda（见第十六章），浏览器里直接跑 Docker 命令，学习阶段完全够用。

### 15.3 域名提示

部署 HTTPS 需要域名：

- 便宜域名：`.top` / `.xyz` / `.icu` 等新顶级域首年 9–20 元（阿里云 / 腾讯云 / 华为云均有）
- 或先用 `http://服务器IP:8080` 跑通，后续再挂域名 + Let's Encrypt 证书

---

## 十六、学习资源与官方文档链接

> 原则：优先官方文档与可动手的在线练习，少看二手总结帖。

### 16.1 官方文档（第一手资料，必收藏）

| 资源 | 链接 | 说明 |
|---|---|---|
| Docker 官方文档（总入口） | https://docs.docker.com/ | 所有概念的权威来源 |
| Docker 入门指南 Get Started | https://docs.docker.com/get-started/ | 官方手把手入门，含完整示例项目 |
| Dockerfile 最佳实践 | https://docs.docker.com/build/building/best-practices/ | 写 Dockerfile 前必读 |
| Docker Compose 文档 | https://docs.docker.com/compose/ | 多容器编排权威参考 |
| Docker Engine 发布说明 | https://docs.docker.com/engine/release-notes/ | 版本演进 / 新特性 / 破坏性变更 |
| Docker 命令参考 | https://docs.docker.com/reference/ | CLI 全命令速查 |
| Docker Hub | https://hub.docker.com/ | 镜像仓库，搜索官方镜像 |

### 16.2 免费在线练习（浏览器即开即用，无需服务器）

| 资源 | 链接 | 说明 |
|---|---|---|
| Play with Docker | https://labs.play-with-docker.com/ | Docker 官方赞助的免费在线沙箱，浏览器里跑完整 Docker 环境（用 GitHub 账号登录） |
| Killercoda | https://killercoda.com/ | 免费互动实验平台，内置 Docker / K8s / Linux 场景（免费层约 1GB RAM、30 分钟不活跃回收） |
| Play with Kubernetes | https://labs.play-with-k8s.com/ | 免费 K8s 练习环境，学完 Docker 衔接 K8s 用 |

### 16.3 高质量社区 / 延伸

| 资源 | 链接 | 说明 |
|---|---|---|
| DigitalOcean Community | https://www.digitalocean.com/community | 海量 Docker / Linux / 部署英文教程，面向实战 |
| Kubernetes 官方文档 | https://kubernetes.io/docs/ | Docker 之后的下一站，学完编排器衔接用 |
| Linux 基金会免费课程 | https://training.linuxfoundation.org/ | 部分免费云原生课程（如 Introduction to Kubernetes） |

> 学习建议：**官方文档 + 在线练习为主**。命令记不住就查 `docker --help` 或官方 reference；原理问题先看官方文档再搜社区。
