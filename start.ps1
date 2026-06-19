# ============================================================
# AI Tutor 一键启动脚本 (PowerShell)
# 同时启动后端 (FastAPI) 和前端 (Vite Dev Server)
# ============================================================

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   AI Tutor - 一键启动" -ForegroundColor Cyan
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

# ---------- 启动后端 ----------

$backendDir = Join-Path $projectRoot "backend"
$venvPython = Join-Path $backendDir "venv\Scripts\python.exe"

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

$backendProcess = Start-Process -FilePath $venvPython -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload" -PassThru -NoNewWindow -WorkingDirectory $backendDir

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
    Write-Host "[WARN] 后端启动超时，但仍将继续启动前端..." -ForegroundColor Yellow
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

# ---------- 汇总信息 ----------

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  启动完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  前端地址:  http://localhost:5173" -ForegroundColor White
Write-Host "  后端 API:  http://localhost:8000" -ForegroundColor White
Write-Host "  健康检查:  http://localhost:8000/api/health" -ForegroundColor White
Write-Host "  API 文档:  http://localhost:8000/docs" -ForegroundColor White
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
    
    if (-not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $frontendProcess.HasExited) {
        Stop-Process -Id $frontendProcess.Id -Force -ErrorAction SilentlyContinue
    }
    
    Write-Host "所有服务已停止。" -ForegroundColor Green
}
