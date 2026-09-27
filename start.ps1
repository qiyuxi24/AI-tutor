# ============================================================
# TutorAgent 一键启动脚本 (PowerShell) —— 唯一启动入口
#
#  默认：装了 Docker 且守护进程可用 → 容器模式（docker compose）
#        否则                        → 本机模式（uvicorn --reload + vite dev）
#
#  用法：
#    .\start.ps1                      # 自动选择（有 Docker 就用容器）
#    .\start.ps1 -Local               # 强制本机开发模式（热重载 + 运维后台 8001/5174）
#    .\start.ps1 -Docker              # 强制容器模式
#    .\start.ps1 -Docker -Force       # 容器模式并强制重建镜像
#    .\start.ps1 -Docker -Port 8081   # 容器模式换宿主端口
# ============================================================
param(
    [switch]$Local,
    [switch]$Docker,
    [switch]$Force,
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot

# ---------- 原生命令包装（必须用，否则脚本会被自己的偏好杀掉）----------
# 坑：$ErrorActionPreference = "Stop" 下，原生命令（python / npm / taskkill）只要往 stderr 写
# 一个字节，且该 stderr 被 2>&1 / 2>$null 重定向，PowerShell 5.1 就会把它包装成**终止性**的
# NativeCommandError（实测 2>$null 也挡不住）→ 脚本当场中止。若中止点不在 try 内，finally 也
# 不会执行，最终只见「窗口还开着、服务却少起几个」（曾真机踩到：backend-admin 依赖缺失时
# 依赖探测命令往 stderr 打印 ModuleNotFoundError，脚本死在运维段之前，8001/5174 永不启动）。
# 所以：凡"允许失败 / 会往 stderr 写日志"的**被重定向**原生命令，一律走下面三个包装，只认
# 退出码；**不重定向**的原生命令（pip / npm install 的进度输出要给人看）不受此坑影响，
# 实测写 stderr 不会抛错，保持原样即可。
function Invoke-Native {
    # 统一入口：临时降级偏好执行原生命令，返回 @{ Output = 文本; ExitCode = 退出码 }
    param([scriptblock]$Command)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $text = & $Command *>&1 | Out-String
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    return [pscustomobject]@{ Output = $text.Trim(); ExitCode = $code }
}

function Invoke-NativeQuiet {
    # 只取退出码，输出丢弃。用于 taskkill / 依赖探测
    param([scriptblock]$Command)
    return (Invoke-Native $Command).ExitCode
}

function Invoke-NativeCapture {
    # 只取输出文本（stderr 一并收进来）。用于版本探测
    param([scriptblock]$Command)
    return (Invoke-Native $Command).Output
}

# ---------- 模式决策：装了 Docker 且守护进程可用 → 容器；否则本机 ----------
function Get-DockerVersion {
    # 允许失败：docker 命令不存在 / Docker Desktop 未启动（守护进程不可用）都返回空串。
    # 用 Invoke-Native 包装 —— 原生命令往 stderr 写时不会被 Stop 偏好升级成终止错误。
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return "" }
    $r = Invoke-Native { docker info --format "{{.ServerVersion}}" }
    if ($r.ExitCode -eq 0 -and $r.Output -match "^\d") { return $r.Output }
    return ""
}

if ($Local -and $Docker) {
    Write-Host "[ERROR] -Local 与 -Docker 互斥，只能选一个" -ForegroundColor Red
    exit 1
}

$dockerVersion = Get-DockerVersion
if ($Docker -and -not $dockerVersion) {
    Write-Host "[ERROR] 指定了 -Docker，但 Docker 守护进程不可用（请先启动 Docker Desktop）" -ForegroundColor Red
    exit 1
}
$useDocker = if ($Local) { $false } elseif ($Docker) { $true } else { [bool]$dockerVersion }

if ($useDocker) {
    # ==================================================================
    #  容器模式（原 start-docker.ps1 合并而来，2026-09-26）
    #
    #  作用：保证「容器里跑的就是当前工作区的代码」
    #    ① 比较「源码最新修改时间」与「镜像构建时间」
    #    ② 只有代码比镜像新（或 -Force）才重建，否则直接启动
    #    ③ 等健康检查通过，打印访问地址（本机 + 局域网）
    # ==================================================================
    $ImageName   = "ai-tutor:latest"
    $ServiceName = "ai-tutor"

    function Info($m) { Write-Host "[信息]  $m" -ForegroundColor Cyan }
    function Ok($m)   { Write-Host "[OK]    $m" -ForegroundColor Green }
    function Warn($m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
    function Fail($m) { Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

    Set-Location $projectRoot

    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "   TutorAgent - Docker 启动" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Ok "Docker Engine $dockerVersion 已就绪"

    # ---------- 1. 源码最新修改时间（决定是否需要重建）----------
    # 只盯「会被 COPY 进镜像」的东西；data/knowledge 等挂载卷不参与判断
    $watchPaths = @(
        "backend\app", "backend\requirements.txt",
        "frontend\src", "frontend\package.json", "frontend\package-lock.json",
        "frontend\index.html", "frontend\vite.config.js",
        "data\prompts",
        "Dockerfile", "nginx.conf", "entrypoint.sh", ".dockerignore"
    )
    $srcFiles = foreach ($rel in $watchPaths) {
        $full = Join-Path $projectRoot $rel
        if (Test-Path $full -PathType Container) {
            Get-ChildItem $full -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.FullName -notmatch '\\__pycache__\\|\\node_modules\\|\\\.vite\\' -and
                    $_.Extension -ne ".pyc"
                }
        } elseif (Test-Path $full) {
            Get-Item $full
        }
    }
    $srcNewest = $srcFiles | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1

    # ---------- 2. 镜像构建时间 ----------
    $imageCreatedUtc = $null
    $rawCreated = docker image inspect $ImageName --format "{{.Created}}" 2>$null
    if ($LASTEXITCODE -eq 0 -and $rawCreated) {
        # Docker 给的是纳秒精度（9 位小数），.NET 最多解析 7 位 → 先截断
        $trimmed = [regex]::Replace($rawCreated.Trim(), "(\.\d{7})\d*", '$1')
        try {
            $imageCreatedUtc = [datetime]::Parse(
                $trimmed,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal)
        } catch {
            $imageCreatedUtc = $null
        }
    }

    if ($imageCreatedUtc) {
        Info ("镜像构建时间 : {0}" -f $imageCreatedUtc.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss"))
    } else {
        Warn "本地还没有 $ImageName 镜像（将执行首次构建）"
    }
    if ($srcNewest) {
        Info ("源码最新修改 : {0}  ({1})" -f $srcNewest.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"), $srcNewest.Name)
    }

    # ---------- 3. 决策 ----------
    $needBuild = $true
    if (-not $imageCreatedUtc) {
        Info "判断：镜像不存在 → 需要构建"
    } elseif ($Force) {
        Info "判断：指定了 -Force → 强制重建"
    } elseif ($srcNewest -and $srcNewest.LastWriteTimeUtc -gt $imageCreatedUtc) {
        Info "判断：源码比镜像新 → 需要重建"
    } else {
        $needBuild = $false
        Ok "判断：镜像已包含当前代码 → 跳过重建（要强制重建请加 -Force）"
    }

    # ---------- 4. 端口（-Port 优先，其次根 .env 的 PORT，最后默认 8080）----------
    if ($Port -gt 0) {
        $env:PORT = "$Port"
        Info "端口：使用命令行指定的 $Port"
    } else {
        $env:PORT = $null   # 交回 compose：读 .env 的 PORT，没有则用默认 8080
    }

    # ---------- 5. 构建 ----------
    if ($needBuild) {
        Write-Host ""
        Info "docker compose build 开始（改了依赖/前端时会较慢，请耐心等待）..."
        $sw = [Diagnostics.Stopwatch]::StartNew()
        docker compose build
        if ($LASTEXITCODE -ne 0) {
            Fail "镜像构建失败，请查看上方构建日志。"
        }
        $sw.Stop()
        Ok ("镜像构建完成，耗时 {0} 秒" -f [int]$sw.Elapsed.TotalSeconds)
    }

    # ---------- 6. 启动 ----------
    Write-Host ""
    Info "docker compose up -d ..."
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Fail "容器启动失败，请查看上方输出。"
    }

    # ---------- 7. 等健康检查（最多 180 秒）----------
    $deadline = (Get-Date).AddSeconds(180)
    $status = "starting"
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        $status = docker inspect --format "{{.State.Health.Status}}" $ServiceName 2>$null
        if ($LASTEXITCODE -ne 0) { $status = "notfound"; break }
        if ($status -ne "starting") { break }
    }

    if ($status -ne "healthy") {
        Warn "容器未在 180 秒内变为 healthy（当前：$status）。最近 30 行日志："
        docker compose logs --tail=30
        exit 1
    }
    Ok "容器 $ServiceName 已就绪（healthy）"

    # ---------- 8. 输出访问地址 ----------
    $mapped = (docker port $ServiceName 2>$null | Select-Object -First 1)
    if ($mapped -and $mapped -match ":(\d+)\s*$") { $hostPort = $matches[1] } else { $hostPort = "8080" }

    # 局域网 IP：取「默认路由所在网卡」的地址 —— 这才是同学实际要走的那条路
    # （不能靠网段猜：本机可能同时有 VirtualBox 192.168.56.1、WSL 172.x 等虚拟网卡）
    $lanIp = $null
    $defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -ne "0.0.0.0" } |
        Sort-Object RouteMetric | Select-Object -First 1
    if ($defaultRoute) {
        $lanIp = (Get-NetIPAddress -InterfaceIndex $defaultRoute.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "169.254.*" } |
            Select-Object -First 1).IPAddress
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  启动完成" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ("  本机访问   : http://127.0.0.1:{0}" -f $hostPort)
    if ($lanIp) {
        Write-Host ("  局域网访问 : http://{0}:{1}" -f $lanIp, $hostPort) -ForegroundColor White
        Write-Host "               ↑ 同学用这个地址（需在同一局域网）" -ForegroundColor DarkGray
    } else {
        Warn "未检测到局域网 IP（可能未联网）"
    }

    # ---------- 9. 局域网入站自检（Windows 防火墙 profile 陷阱）----------
    # 踩过的真实坑：网络从「公用」切到「专用」后，Docker Desktop 那条
    # com.docker.backend.exe 规则（Profile=Public）不再匹配 → 同学连不上，
    # 而本机自测永远是 200（本机访问不走入站规则），极难发现。
    if ($lanIp) {
        $curProfile = (Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select-Object -First 1).NetworkCategory
        if ($curProfile) {
            # 只认"与本项目真正相关"的规则：Docker Desktop 的端口转发规则 + 我们自己加的规则。
            # 不能遍历全部规则 —— 大量系统规则同样是 LocalPort=Any/Profile=Any（但限定了 Program），
            # 会让判定结果永远是"已放行"（假阴性，等于自检失效）。
            $candidates = Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -match "com\.docker\.backend|TutorAgent LAN" }
            $portAllowed = $false
            foreach ($rule in $candidates) {
                if ($rule.Profile -ne "Any" -and $rule.Profile -notmatch $curProfile) { continue }
                $pf = $rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
                if ($pf -and ($pf.LocalPort -eq "Any" -or $pf.LocalPort -eq $hostPort)) {
                    $portAllowed = $true
                    break
                }
            }

            if (-not $portAllowed) {
                Write-Host ""
                Warn "局域网访问可能被 Windows 防火墙拦截！"
                Warn ("  当前网络类别：{0}；未找到放行 {1} 端口的入站规则" -f $curProfile, $hostPort)
                Write-Host "  本机自测 200 不能证明同学能连上（本机访问不走入站规则）。" -ForegroundColor DarkGray
                Write-Host "  以管理员身份运行下面这条命令即可放行：" -ForegroundColor Yellow
                Write-Host ('    New-NetFirewallRule -DisplayName "TutorAgent LAN {0}" -Direction Inbound -Protocol TCP -LocalPort {0} -Action Allow -Profile Any' -f $hostPort) -ForegroundColor White
            }
        }
    }
    Write-Host ""
    Write-Host "  查看日志   : docker compose logs -f" -ForegroundColor DarkGray
    Write-Host "  停止服务   : docker compose stop" -ForegroundColor DarkGray
    Write-Host "  强制重建   : .\start.ps1 -Docker -Force" -ForegroundColor DarkGray
    Write-Host ""

    exit 0
}

# ==========================================================================
#  本机模式（以下为原逻辑）
# ==========================================================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   TutorAgent - 一键启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 检查依赖 ----------

# 解析「建 venv 用的解释器」（要求 >= 3.10）
# 为什么单独解析：脚本正常路径全程只用各 venv 里的 python（backend\venv\Scripts\python.exe、
# backend-admin\venv\Scripts\python.exe），系统 python 只在 venv 缺失、需要现建时用到。而本机
# 系统 python 是 3.8 —— 用它建的 venv 装不上新版 fastapi，且报错点离现场很远（下游 pip 里才炸）。
# 所以按 3.10+ 挑一个，优先用 `py -3.13` 这类启动器；**这里不退出**：venv 已存在时脚本照常启动，
# 真正的报错留到确实要建 venv 的那两处（见下方与运维段的 exit 1）。
function Get-PythonInfo {
    # 解析 "Python 3.13.1"；命令不可用 / 退出码非 0 / 输出不像版本行 → 返回 $null
    # 必须看退出码：`py -3.13` 在没装 3.13 时也会打印含 "Python 3.13" 的提示，光靠正则会把"没装"读成"装了"。
    param([scriptblock]$Command)
    $result = try { Invoke-Native $Command } catch { return $null }
    if ($result.ExitCode -ne 0) { return $null }
    $first = ($result.Output -split "\r?\n")[0].Trim()
    if ($first -notmatch '^Python\s+(\d+)\.(\d+)') { return $null }
    return [pscustomobject]@{
        Major = [int]$Matches[1]
        Minor = [int]$Matches[2]
        Raw   = $first
    }
}

$venvPythonExe = $null   # 如 "py" / "python"
$venvPythonExtra = @()   # 如 @("-3.13")；与上者拼成完整命令
foreach ($cand in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python3.13", "python3.12", "python")) {
    $parts = $cand.Split(" ")
    $exe = $parts[0]
    $extra = @($parts | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $info = Get-PythonInfo { & $exe @extra --version }
    if ($info -and ($info.Major -gt 3 -or ($info.Major -eq 3 -and $info.Minor -ge 10))) {
        $venvPythonExe = $exe
        $venvPythonExtra = $extra
        Write-Host "[OK] Python(建 venv 用): $($info.Raw)  [$cand]" -ForegroundColor Green
        break
    }
    if ($info) { Write-Host "[跳过] $($info.Raw) 低于 3.10，不能用于建 venv" -ForegroundColor DarkYellow }
}
if (-not $venvPythonExe) {
    Write-Host "[WARN] 未找到 3.10+ 的 Python：venv 已存在时不影响启动，缺失时无法自动创建" -ForegroundColor Yellow
}

# 检查 Node.js
try {
    $nodeVersion = Invoke-NativeCapture { node --version }
    Write-Host "[OK] Node.js: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] 未找到 Node.js，请先安装 Node.js 18+" -ForegroundColor Red
    exit 1
}

# 检查后端依赖
$venvPath = Join-Path $projectRoot "backend\venv"
if (-not (Test-Path $venvPath)) {
    if (-not $venvPythonExe) {
        Write-Host "[ERROR] 需要创建 backend\venv，但本机没有 3.10+ 的 Python（低版本建的 venv 装不上依赖）" -ForegroundColor Red
        exit 1
    }
    Write-Host "[WARN] 未找到虚拟环境，正在创建（解释器: $venvPythonExe $venvPythonExtra）..." -ForegroundColor Yellow
    Push-Location (Join-Path $projectRoot "backend")
    & $venvPythonExe @venvPythonExtra -m venv venv
    $venvExit = $LASTEXITCODE
    Pop-Location
    if ($venvExit -ne 0) {
        Write-Host "[ERROR] 虚拟环境创建失败，请检查上面的输出" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] 虚拟环境创建完成" -ForegroundColor Green
}

# 检查前端依赖
$nodeModulesPath = Join-Path $projectRoot "frontend\node_modules"
if (-not (Test-Path $nodeModulesPath)) {
    Write-Host "[WARN] 未找到 node_modules，正在安装前端依赖..." -ForegroundColor Yellow
    Push-Location (Join-Path $projectRoot "frontend")
    npm install
    Pop-Location
    Write-Host "[OK] 前端依赖安装完成" -ForegroundColor Green
}

Write-Host ""

# ---------- 清理上轮残留进程（端口预检）----------
# 必须清理的原因：uvicorn --reload 是「reloader + spawn worker」双层进程，只杀一个 PID 会
# 留下孤儿 worker 继续 Listen 8000 → 本次启动 bind 失败（WinError 10048），但孤儿仍会响应
# /api/health 200 把健康检查骗过，最终表现为「提示启动完成、服务却立刻全关」。
$staleProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='python3.13.exe' OR Name='node.exe' OR Name='cmd.exe'" |
    Where-Object { $_.CommandLine -match 'uvicorn|spawn_main|vite' }

if ($staleProcs) {
    Write-Host "----------------------------------------" -ForegroundColor Cyan
    foreach ($p in $staleProcs) {
        Write-Host "[清理] 结束上轮残留进程 PID $($p.ProcessId)" -ForegroundColor Yellow
        Invoke-NativeQuiet { taskkill /PID $p.ProcessId /T /F } | Out-Null
    }
    Start-Sleep -Seconds 2
}

# 兜底：命令行匹配不到、但确实占着端口的进程（含上轮被强杀留下的孤儿）按端口清
foreach ($port in 8000, 5173, 8001, 5174) {
    $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $owners) {
        if ($procId) {
            Write-Host "[清理] 端口 $port 仍被 PID $procId 占用，强制结束" -ForegroundColor Yellow
            Invoke-NativeQuiet { taskkill /PID $procId /T /F } | Out-Null
        }
    }
}

Write-Host ""

# ---------- 启动后端 ----------

$backendDir = Join-Path $projectRoot "backend"
$venvPython = Join-Path $backendDir "venv\Scripts\python.exe"
$backendLog = Join-Path $projectRoot "logs\uvicorn-dev.log"

Write-Host "----------------------------------------" -ForegroundColor Cyan
Write-Host "[后端] 启动 FastAPI 服务 (端口 8000)..." -ForegroundColor Yellow

# 检查并安装后端依赖
$backendDepsExit = Invoke-NativeQuiet { & $venvPython -c "import fastapi, uvicorn" }
if ($backendDepsExit -ne 0) {
    Write-Host "[WARN] 后端依赖未安装，正在安装..." -ForegroundColor Yellow
    Push-Location $backendDir
    & $venvPython -m pip install -r requirements.txt -q
    Pop-Location
    Write-Host "[OK] 后端依赖安装完成" -ForegroundColor Green
}

New-Item -ItemType Directory -Force -Path (Split-Path $backendLog -Parent) | Out-Null

# 后端必须在独立控制台启动（这里刻意不加 -NoNewWindow），否则热重载会把整个脚本一起打死：
# uvicorn 在 Windows 上用 os.kill(worker_pid, CTRL_C_EVENT) 重启 worker（见 supervisors/basereload.py），
# 而 CTRL_C_EVENT 会广播给「同一控制台的所有进程」——共享控制台时，改一次 app/ 下的代码就会
# 连带中断本脚本，finally 于是把前端也一起关掉，表现为「提示启动完成、服务却莫名全关」。
# 代价是后端日志不再打在本窗口，改写入 logs\uvicorn-dev.log（logs/ 已在 .gitignore 中）。
# --reload-dir app：本机缺 watchfiles，uvicorn 降级为 StatReload 并监视整个 backend/ 目录，
# 跑测试或在 backend/ 下写临时文件都会触发重载；限定只监视 app/ 即可。
$backendProcess = Start-Process -FilePath $venvPython -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload", "--reload-dir", "app", "--no-use-colors" -PassThru -WindowStyle Hidden -WorkingDirectory $backendDir -RedirectStandardError $backendLog

Write-Host "[后端] PID: $($backendProcess.Id)" -ForegroundColor Green

# 等待后端启动
Write-Host "[后端] 等待服务就绪..." -ForegroundColor Yellow
$maxRetries = 30
$retry = 0
do {
    Start-Sleep -Seconds 1
    $retry++
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "[后端] 服务已就绪!" -ForegroundColor Green
            break
        }
    } catch {}
} while ($retry -lt $maxRetries)

if ($retry -ge $maxRetries) {
    Write-Host "[WARN] 后端启动超时，请查看日志: $backendLog" -ForegroundColor Yellow
}

Write-Host ""

# ---------- 启动前端 ----------

$frontendDir = Join-Path $projectRoot "frontend"

Write-Host "----------------------------------------" -ForegroundColor Cyan
Write-Host "[前端] 启动 Vite 开发服务器 (端口 5173)..." -ForegroundColor Yellow

# 找到 npx 的完整路径（避免直接调用 cmd 内置命令）
$npmPath = (Get-Command npm -ErrorAction Stop).Source
$npxPath = Join-Path (Split-Path $npmPath -Parent) "npx.cmd"
if (-not (Test-Path $npxPath)) {
    $npxPath = Join-Path (Split-Path $npmPath -Parent) "npx"
}
$frontendProcess = Start-Process -FilePath $npxPath -ArgumentList "vite", "--host" -PassThru -NoNewWindow -WorkingDirectory $frontendDir

Write-Host "[前端] PID: $($frontendProcess.Id)" -ForegroundColor Green

Write-Host ""

# ---------- 启动运维后台后端 ----------

$adminBackendDir = Join-Path $projectRoot "backend-admin"
$adminVenvPython = Join-Path $adminBackendDir "venv\Scripts\python.exe"
$adminBackendLog = Join-Path $projectRoot "logs\uvicorn-admin.log"

Write-Host "----------------------------------------" -ForegroundColor Cyan
Write-Host "[运维后端] 启动 FastAPI 服务 (端口 8001)..." -ForegroundColor Yellow

# 创建运维后端虚拟环境并安装依赖
if (-not (Test-Path (Join-Path $adminBackendDir "venv"))) {
    if (-not $venvPythonExe) {
        Write-Host "[ERROR] [运维后端] 需要创建 backend-admin\venv，但本机没有 3.10+ 的 Python" -ForegroundColor Red
        exit 1
    }
    Write-Host "[运维后端] 创建虚拟环境（解释器: $venvPythonExe $venvPythonExtra）..." -ForegroundColor Yellow
    Push-Location $adminBackendDir
    & $venvPythonExe @venvPythonExtra -m venv venv
    $adminVenvExit = $LASTEXITCODE
    Pop-Location
    if ($adminVenvExit -ne 0) {
        Write-Host "[ERROR] [运维后端] 虚拟环境创建失败，请检查上面的输出" -ForegroundColor Red
        exit 1
    }
}

# 安装运维后端依赖
$adminDepsExit = Invoke-NativeQuiet { & $adminVenvPython -c "import fastapi, uvicorn" }
if ($adminDepsExit -ne 0) {
    Write-Host "[运维后端] 安装依赖..." -ForegroundColor Yellow
    Push-Location $adminBackendDir
    & $adminVenvPython -m pip install -r requirements.txt -q
    Pop-Location
    Write-Host "[运维后端] 依赖安装完成" -ForegroundColor Green
}

$adminBackendProcess = Start-Process -FilePath $adminVenvPython -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001", "--reload", "--reload-dir", "app", "--no-use-colors" -PassThru -WindowStyle Hidden -WorkingDirectory $adminBackendDir -RedirectStandardError $adminBackendLog

Write-Host "[运维后端] PID: $($adminBackendProcess.Id)" -ForegroundColor Green

# 等待运维后端启动
Write-Host "[运维后端] 等待服务就绪..." -ForegroundColor Yellow
$maxRetries = 30
$retry = 0
do {
    Start-Sleep -Seconds 1
    $retry++
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8001/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "[运维后端] 服务已就绪!" -ForegroundColor Green
            break
        }
    } catch {}
} while ($retry -lt $maxRetries)

Write-Host ""

# ---------- 启动运维前端 ----------

$adminFrontendDir = Join-Path $projectRoot "frontend-admin"

Write-Host "----------------------------------------" -ForegroundColor Cyan
Write-Host "[运维前端] 启动 Vite 开发服务器 (端口 5174)..." -ForegroundColor Yellow

# 安装运维前端依赖
if (-not (Test-Path (Join-Path $adminFrontendDir "node_modules"))) {
    Write-Host "[运维前端] 安装依赖..." -ForegroundColor Yellow
    Push-Location $adminFrontendDir
    npm install
    Pop-Location
    Write-Host "[运维前端] 依赖安装完成" -ForegroundColor Green
}

$adminFrontendProcess = Start-Process -FilePath $npxPath -ArgumentList "vite", "--host", "--port", "5174" -PassThru -NoNewWindow -WorkingDirectory $adminFrontendDir

Write-Host "[运维前端] PID: $($adminFrontendProcess.Id)" -ForegroundColor Green

Write-Host ""

# ---------- 汇总信息 ----------

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  启动完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  主系统前端:   http://localhost:5173" -ForegroundColor White
Write-Host "  主系统后端:   http://localhost:8000" -ForegroundColor White
Write-Host "  运维前端:     http://localhost:5174" -ForegroundColor White
Write-Host "  运维后端:     http://localhost:8001" -ForegroundColor White
Write-Host "  后端日志:     logs\uvicorn-dev.log" -ForegroundColor White
Write-Host "  运维后端日志: logs\uvicorn-admin.log" -ForegroundColor White
Write-Host ""
Write-Host "  运维后台管理员: admin / admin123" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  默认管理员: admin / admin123" -ForegroundColor DarkGray
Write-Host ""
Write-Host "按 Ctrl+C 停止所有服务..." -ForegroundColor Yellow
Write-Host ""

# ---------- 等待用户终止 ----------

try {
    while ($true) {
        Start-Sleep -Seconds 1
        
        # 检查进程是否还在运行
        if ($backendProcess.HasExited) {
            Write-Host "[WARN] 后端进程已退出 (code: $($backendProcess.ExitCode))" -ForegroundColor Yellow
            break
        }
        if ($frontendProcess.HasExited) {
            Write-Host "[WARN] 前端进程已退出 (code: $($frontendProcess.ExitCode))" -ForegroundColor Yellow
            break
        }
    }
} finally {
    Write-Host ""
    Write-Host "正在停止所有服务..." -ForegroundColor Yellow

    # /T 结束整棵进程树：uvicorn 的 spawn worker、vite 的 node 子进程只杀父 PID 会变成孤儿继续占端口
    foreach ($proc in @($backendProcess, $frontendProcess, $adminBackendProcess, $adminFrontendProcess)) {
        if ($proc -and -not $proc.HasExited) {
            Invoke-NativeQuiet { taskkill /PID $proc.Id /T /F } | Out-Null
        }
    }

    Write-Host "所有服务已停止。" -ForegroundColor Green
}
