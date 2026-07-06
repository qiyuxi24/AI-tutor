# ============================================================
# AI Tutor 一键安装依赖脚本 (PowerShell)
# 安装所有后端 (Python) 和前端 (Node.js) 依赖
# ============================================================

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   AI Tutor - 一键安装依赖" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 1. 检查系统环境
# ============================================================

Write-Host "[1/5] 检查系统环境..." -ForegroundColor Yellow

# 检查 Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  [OK] Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] 未找到 Python，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# 检查 Python 版本 >= 3.10
$pyVer = (python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") 2>&1
$pyMajor = [int]$pyVer.Split('.')[0]
$pyMinor = [int]$pyVer.Split('.')[1]
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 10)) {
    Write-Host "  [ERROR] Python 版本需 >= 3.10，当前: $pyVer" -ForegroundColor Red
    exit 1
}

# 检查 Node.js
try {
    $nodeVersion = node --version 2>&1
    Write-Host "  [OK] Node.js: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] 未找到 Node.js，请先安装 Node.js 18+" -ForegroundColor Red
    exit 1
}

# 检查 Node 版本 >= 18
$nodeVer = ($nodeVersion -replace 'v', '').Split('.')[0]
if ([int]$nodeVer -lt 18) {
    Write-Host "  [ERROR] Node.js 版本需 >= 18，当前: $nodeVersion" -ForegroundColor Red
    exit 1
}

Write-Host ""

# ============================================================
# 2. 安装 Python 后端依赖
# ============================================================

Write-Host "[2/5] 配置 Python 虚拟环境..." -ForegroundColor Yellow

$backendDir = Join-Path $projectRoot "backend"
$venvPath = Join-Path $backendDir "venv"

if (-not (Test-Path $venvPath)) {
    Write-Host "  创建虚拟环境..." -ForegroundColor Gray
    Push-Location $backendDir
    python -m venv venv
    Pop-Location
    Write-Host "  [OK] 虚拟环境创建完成" -ForegroundColor Green
} else {
    Write-Host "  [OK] 虚拟环境已存在" -ForegroundColor Green
}

Write-Host ""
Write-Host "[3/5] 安装 Python 依赖..." -ForegroundColor Yellow

$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsFile = Join-Path $backendDir "requirements.txt"

Push-Location $backendDir

# 升级 pip
& $venvPython -m pip install --upgrade pip -q 2>&1 | Out-Null

# 安装依赖
Write-Host "  requirements.txt → 开始安装..." -ForegroundColor Gray
& $venvPython -m pip install -r $requirementsFile 2>&1 | ForEach-Object {
    if ($_ -match "Successfully installed|Requirement already satisfied") {
        Write-Host "  $_" -ForegroundColor Green
    } elseif ($_ -match "ERROR|error") {
        Write-Host "  $_" -ForegroundColor Red
    } else {
        Write-Host "  $_" -ForegroundColor Gray
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Python 依赖安装失败！" -ForegroundColor Red
    Pop-Location
    exit 1
}

Pop-Location
Write-Host "  [OK] Python 依赖安装完成" -ForegroundColor Green

Write-Host ""

# ============================================================
# 3. 安装 Node.js 前端依赖
# ============================================================

Write-Host "[4/5] 安装前端依赖..." -ForegroundColor Yellow

$frontendDir = Join-Path $projectRoot "frontend"

Push-Location $frontendDir
Write-Host "  package.json → npm install..." -ForegroundColor Gray
npm install 2>&1 | ForEach-Object {
    if ($_ -match "added|up to date") {
        Write-Host "  $_" -ForegroundColor Green
    } elseif ($_ -match "ERR|error|npm error") {
        Write-Host "  $_" -ForegroundColor Red
    } else {
        Write-Host "  $_" -ForegroundColor Gray
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] 前端依赖安装失败！" -ForegroundColor Red
    Pop-Location
    exit 1
}

Pop-Location
Write-Host "  [OK] 前端依赖安装完成" -ForegroundColor Green

Write-Host ""

# ============================================================
# 4. 配置环境变量
# ============================================================

Write-Host "[5/5] 检查环境变量配置..." -ForegroundColor Yellow

$envFile = Join-Path $backendDir ".env"
$envExample = Join-Path $backendDir ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "  [OK] 已从 .env.example 创建 .env 文件" -ForegroundColor Green
        Write-Host "  [WARN] 请编辑 backend/.env 填入你的 DASHSCOPE_API_KEY" -ForegroundColor Yellow
    } else {
        Write-Host "  [WARN] 未找到 .env.example，请手动创建 backend/.env" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] .env 文件已存在" -ForegroundColor Green

    # 快速检查关键变量是否已配置
    $envContent = Get-Content $envFile -Raw
    if ($envContent -match "DASHSCOPE_API_KEY\s*=\s*(your_api_key_here|$|#)") {
        Write-Host "  [WARN] DASHSCOPE_API_KEY 似乎未配置，请检查 backend/.env" -ForegroundColor Yellow
    }
}

Write-Host ""

# ============================================================
# 5. 验证安装结果
# ============================================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   验证依赖安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$allOk = $true

# 验证 Python 关键依赖
Write-Host "[验证] Python 依赖:" -ForegroundColor Yellow
$pythonDeps = @(
    @{Name="fastapi"; Import="fastapi"},
    @{Name="uvicorn"; Import="uvicorn"},
    @{Name="httpx"; Import="httpx"},
    @{Name="openai"; Import="openai"},
    @{Name="pandas"; Import="pandas"},
    @{Name="scipy"; Import="scipy"},
    @{Name="yaml"; Import="yaml"},
    @{Name="jinja2"; Import="jinja2"},
    @{Name="jose"; Import="jose"},
    @{Name="bcrypt"; Import="bcrypt"},
    @{Name="multipart"; Import="multipart"},
    @{Name="dotenv"; Import="dotenv"}
)

foreach ($dep in $pythonDeps) {
    $result = & $venvPython -c "import $($dep.Import)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] $($dep.Name)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($dep.Name) — 未安装" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""

# 验证 Node.js 关键依赖
Write-Host "[验证] 前端依赖:" -ForegroundColor Yellow
$nodeModules = Join-Path $frontendDir "node_modules"
$nodeDeps = @(
    @{Name="vue"; Path="vue"},
    @{Name="vue-router"; Path="vue-router"},
    @{Name="pinia"; Path="pinia"},
    @{Name="axios"; Path="axios"},
    @{Name="d3"; Path="d3"},
    @{Name="marked"; Path="marked"},
    @{Name="vite"; Path="vite"},
    @{Name="@vitejs/plugin-vue"; Path="@vitejs/plugin-vue"}
)

foreach ($dep in $nodeDeps) {
    $depPath = Join-Path $nodeModules $dep.Path
    if (Test-Path $depPath) {
        Write-Host "  [OK] $($dep.Name)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($dep.Name) — 未安装" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""

# ============================================================
# 6. 汇总
# ============================================================

Write-Host "========================================" -ForegroundColor Cyan
if ($allOk) {
    Write-Host "   安装完成！所有依赖就绪。" -ForegroundColor Green
} else {
    Write-Host "   安装完成，但部分依赖验证失败！" -ForegroundColor Red
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  下一步：" -ForegroundColor White
Write-Host "    1. 编辑 backend/.env 填入 DASHSCOPE_API_KEY" -ForegroundColor Gray
Write-Host "    2. 运行 .\start.ps1 启动应用" -ForegroundColor Gray
Write-Host "    3. 访问 http://localhost:5173" -ForegroundColor Gray
Write-Host ""
Write-Host "  启动命令: .\start.ps1" -ForegroundColor Yellow
Write-Host ""

if (-not $allOk) {
    exit 1
}
