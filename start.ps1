# ============================================================
# TutorAgent 一键启动脚本 (PowerShell)
# 同时启动后端 (FastAPI) 和前端 (Vite Dev Server)
# ============================================================

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   TutorAgent - 一键启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 检查依赖 ----------

# 检查 Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[OK] Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] 未找到 Python，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# 检查 Node.js
try {
    $nodeVersion = node --version 2>&1
    Write-Host "[OK] Node.js: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] 未找到 Node.js，请先安装 Node.js 18+" -ForegroundColor Red
    exit 1
}

# 检查后端依赖
$venvPath = Join-Path $projectRoot "backend\venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "[WARN] 未找到虚拟环境，正在创建..." -ForegroundColor Yellow
    Push-Location (Join-Path $projectRoot "backend")
    python -m venv venv
    Pop-Location
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
        taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Null
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
            taskkill /PID $procId /T /F 2>&1 | Out-Null
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
$requirementsCheck = & $venvPython -c "import fastapi, uvicorn" 2>&1
if ($LASTEXITCODE -ne 0) {
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
    Write-Host "[运维后端] 创建虚拟环境..." -ForegroundColor Yellow
    Push-Location $adminBackendDir
    python -m venv venv
    Pop-Location
}

# 安装运维后端依赖
$adminReqCheck = & $adminVenvPython -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
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
            taskkill /PID $proc.Id /T /F 2>&1 | Out-Null
        }
    }

    Write-Host "所有服务已停止。" -ForegroundColor Green
}
