# ============================================================
#  TutorAgent - Docker 启动脚本（构建同步 + 启动 + 验证）
#
#  作用：保证「容器里跑的就是当前工作区的代码」
#    ① 比较「源码最新修改时间」与「镜像构建时间」
#    ② 只有代码比镜像新（或 -Force）才重建，否则直接启动
#    ③ 等健康检查通过，打印访问地址（本机 + 局域网）
#
#  用法：
#    .\start-docker.ps1              # 日常用这个（自动判断要不要重建）
#    .\start-docker.ps1 -Force       # 强制重建（改了代码但不放心时用）
#    .\start-docker.ps1 -Port 8081   # 换端口启动
#
#  说明：Dockerfile 不需要脚本改写 —— 镜像"由什么构成"由 Dockerfile
#        静态声明（改它必须重建镜像，故意如此，避免隐式魔法）；
#        本脚本解决的是「何时重建」这个判断问题，而不是「怎么构建」。
# ============================================================
param(
    [switch]$Force,
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ImageName   = "ai-tutor:latest"
$ServiceName = "ai-tutor"

function Info($m) { Write-Host "[信息]  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[ERROR] $m" -ForegroundColor Red }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   TutorAgent - Docker 启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. Docker 可用性 ----------
$dockerVersion = docker info --format "{{.ServerVersion}}" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $dockerVersion) {
    Fail "Docker 守护进程不可用。请先启动 Docker Desktop，等它就绪后重试。"
    exit 1
}
Ok "Docker Engine $dockerVersion 已就绪"

# ---------- 2. 源码最新修改时间（决定是否需要重建）----------
# 只盯「会被 COPY 进镜像」的东西；data/knowledge 等挂载卷不参与判断
$watchPaths = @(
    "backend\app", "backend\requirements.txt",
    "frontend\src", "frontend\package.json", "frontend\package-lock.json",
    "frontend\index.html", "frontend\vite.config.js",
    "data\prompts",
    "Dockerfile", "nginx.conf", "entrypoint.sh", ".dockerignore"
)
$srcFiles = foreach ($rel in $watchPaths) {
    $full = Join-Path $PSScriptRoot $rel
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

# ---------- 3. 镜像构建时间 ----------
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

# ---------- 4. 决策 ----------
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

# ---------- 5. 端口（-Port 优先，其次根 .env 的 PORT，最后默认 8080）----------
if ($Port -gt 0) {
    $env:PORT = "$Port"
    Info "端口：使用命令行指定的 $Port"
} else {
    $env:PORT = $null   # 交回 compose：读 .env 的 PORT，没有则用默认 8080
}

# ---------- 6. 构建 ----------
if ($needBuild) {
    Write-Host ""
    Info "docker compose build 开始（改了依赖/前端时会较慢，请耐心等待）..."
    $sw = [Diagnostics.Stopwatch]::StartNew()
    docker compose build
    if ($LASTEXITCODE -ne 0) {
        Fail "镜像构建失败，请查看上方构建日志。"
        exit 1
    }
    $sw.Stop()
    Ok ("镜像构建完成，耗时 {0} 秒" -f [int]$sw.Elapsed.TotalSeconds)
}

# ---------- 7. 启动 ----------
Write-Host ""
Info "docker compose up -d ..."
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Fail "容器启动失败，请查看上方输出。"
    exit 1
}

# ---------- 8. 等健康检查（最多 180 秒）----------
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

# ---------- 9. 输出访问地址 ----------
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

# ---------- 10. 局域网入站自检（Windows 防火墙 profile 陷阱）----------
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
Write-Host "  强制重建   : .\start-docker.ps1 -Force" -ForegroundColor DarkGray
Write-Host ""
