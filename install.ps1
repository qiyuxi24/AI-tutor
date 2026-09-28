# ============================================================
# TutorAgent 一键安装依赖脚本 (PowerShell)
# 安装所有后端 (Python) 和前端 (Node.js) 依赖
# ============================================================

# 注意：必须用 Continue 而不是 Stop（同 start.ps1）：
# PS 5.1 下 native 命令（pip / npm / python）写 stderr 后再被重定向，会包装成 RemoteException，
# 配合 Stop 会中断整个脚本。关键步骤已改为显式判断退出码。
$ErrorActionPreference = "Continue"
$projectRoot = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   TutorAgent - 一键安装依赖" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 1. 检查系统环境
# ============================================================

Write-Host "[1/5] 检查系统环境..." -ForegroundColor Yellow

# 探测本机 Python 解释器：必须 >= 3.10
# （不能直接用裸 python：PATH 里可能是 3.8/3.9，会用它建 venv 导致 pip 装不上依赖）
function Get-ProjectPython {
    # 探测期间静音：某个候选版本不存在时 py 会写 stderr，PS 5.1 会把它包装成错误记录（噪音）
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        foreach ($cand in @(
            @{ Exe = "py"; Args = @("-3.13") },
            @{ Exe = "py"; Args = @("-3.12") },
            @{ Exe = "py"; Args = @("-3.11") },
            @{ Exe = "py"; Args = @("-3.10") },
            @{ Exe = "python"; Args = @() }
        )) {
            if (-not (Get-Command $cand.Exe -ErrorAction SilentlyContinue)) { continue }
            $probeArgs = @($cand.Args) + @("-c", "import sys; print('%d.%d.%d|%s' % (sys.version_info[0], sys.version_info[1], sys.version_info[2], sys.executable))")
            $out = & $cand.Exe @probeArgs 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
            $parts = ($out | Select-Object -Last 1).ToString().Trim().Split("|")
            if ($parts.Count -ne 2) { continue }
            $v = $parts[0].Split(".")
            if ([int]$v[0] -gt 3 -or ([int]$v[0] -eq 3 -and [int]$v[1] -ge 10)) {
                return @{ Path = $parts[1]; Version = $parts[0] }
            }
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return $null
}

$projectPython = Get-ProjectPython
if (-not $projectPython) {
    Write-Host "  [ERROR] 未找到 Python 3.10+（项目要求，推荐 3.11/3.13）" -ForegroundColor Red
    Write-Host "          请安装新版本后重试：https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Python: $($projectPython.Version)  ($($projectPython.Path))" -ForegroundColor Green

# 检查 Node.js
try {
    $nodeVersion = node --version 2>$null
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

# 已存在的 venv 要确认是 >= 3.10 解释器建的（旧脚本可能用系统 3.8 建过，装不上依赖）
$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (Test-Path $venvPython) {
    $venvVerOut = & $venvPython -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    $venvVer = if ($venvVerOut) { ($venvVerOut | Select-Object -Last 1).ToString().Trim() } else { "" }
    $venvOk = $false
    if ($venvVer -match '^(\d+)\.(\d+)$') {
        $venvOk = ([int]$Matches[1] -gt 3) -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10)
    }
    if (-not $venvOk) {
        Write-Host "  现有虚拟环境版本过低（$venvVer），正在重建..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $venvPath -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $venvPython)) {
    Write-Host "  创建虚拟环境（Python $($projectPython.Version)）..." -ForegroundColor Gray
    & $projectPython.Path -m venv $venvPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Host "  [ERROR] 虚拟环境创建失败！" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] 虚拟环境创建完成" -ForegroundColor Green
} else {
    Write-Host "  [OK] 虚拟环境已存在（Python $venvVer）" -ForegroundColor Green
}

Write-Host ""
Write-Host "[3/5] 安装 Python 依赖..." -ForegroundColor Yellow

$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsFile = Join-Path $backendDir "requirements.txt"

Push-Location $backendDir

# 升级 pip（用 cmd /c 合并 stderr，避免 PS 5.1 把 native stderr 包装成错误记录）
cmd /c "`"$venvPython`" -m pip install --upgrade pip -q 2>&1" | Out-Null

# 安装依赖
Write-Host "  requirements.txt → 开始安装..." -ForegroundColor Gray
# 用 cmd /c 合并 stderr：PS 5.1 直接 `2>&1` 会把 native 的 stderr 包装成 ErrorRecord（噪音）
cmd /c "`"$venvPython`" -m pip install -r `"$requirementsFile`" 2>&1" | ForEach-Object {
    if ($_ -match "Successfully installed|Requirement already satisfied") {
        Write-Host "  $_" -ForegroundColor Green
    } elseif ($_ -match "ERROR|error") {
        Write-Host "  $_" -ForegroundColor Red
    } else {
        Write-Host "  $_" -ForegroundColor Gray
    }
}

# 注意：经管道后 $LASTEXITCODE 可能被 cmdlet 覆盖，先快照再判断
$pipExitCode = $LASTEXITCODE

if ($pipExitCode -ne 0) {
    Write-Host "  [ERROR] Python 依赖安装失败！手动排查：" -ForegroundColor Red
    Write-Host "          `"$venvPython`" -m pip install -r `"$requirementsFile`"" -ForegroundColor Red
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
cmd /c "npm install 2>&1" | ForEach-Object {
    if ($_ -match "added|up to date") {
        Write-Host "  $_" -ForegroundColor Green
    } elseif ($_ -match "ERR|error|npm error") {
        Write-Host "  $_" -ForegroundColor Red
    } else {
        Write-Host "  $_" -ForegroundColor Gray
    }
}

$npmExitCode = $LASTEXITCODE

if ($npmExitCode -ne 0) {
    Write-Host "  [ERROR] 前端依赖安装失败！手动排查：cd frontend; npm install" -ForegroundColor Red
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

$envFile = Join-Path $projectRoot ".env"
$envExample = Join-Path $projectRoot ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "  [OK] 已从 .env.example 创建 .env 文件" -ForegroundColor Green
        Write-Host "  [WARN] 请编辑根目录 .env 填入 LLM_API_KEY / DASHSCOPE_API_KEY" -ForegroundColor Yellow
    } else {
        Write-Host "  [WARN] 未找到 .env.example，请手动创建根目录 .env" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] .env 文件已存在" -ForegroundColor Green
}

# SECRET_KEY 为空时后端 import 阶段就会拒绝启动（core/auth.py），这里自动补一个随机密钥
# 正则用 [ \t]* 而不是 \s*：.NET 的 \s 含换行，空值行「SECRET_KEY=」会顺着换行匹配到
# 下一行内容，被误判为"已配置"而跳过生成（曾导致 .env 里 SECRET_KEY 一直是空的）。
# 必须显式按 UTF8 读：PS 5.1 的 Get-Content 默认按 ANSI 解码，会把 .env 里的 UTF-8
# 中文注释读成乱码，写回时乱码就被固化了（曾把整个 .env 注释变成「鈹€鈹€」）
$envContent = [System.IO.File]::ReadAllText($envFile, (New-Object System.Text.UTF8Encoding $false))
if ($null -eq $envContent) { $envContent = "" }

if ($envContent -notmatch '(?m)^[ \t]*SECRET_KEY[ \t]*=[ \t]*\S') {
    $secretKey = -join (1..64 | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
    if ($envContent -match '(?m)^[ \t]*SECRET_KEY[ \t]*=') {
        $envContent = $envContent -replace '(?m)^[ \t]*SECRET_KEY[ \t]*=.*$', "SECRET_KEY=$secretKey"
    } else {
        $envContent = $envContent.TrimEnd() + "`nSECRET_KEY=$secretKey`n"
    }
    [System.IO.File]::WriteAllText($envFile, $envContent, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "  [OK] .env 中 SECRET_KEY 为空，已自动生成随机密钥" -ForegroundColor Green
}

# 快速检查关键变量是否已配置
if ($envContent -notmatch '(?m)^[ \t]*DASHSCOPE_API_KEY[ \t]*=[ \t]*\S') {
    Write-Host "  [WARN] DASHSCOPE_API_KEY 未配置：后端将因 AsyncOpenAI 缺 key 而无法启动" -ForegroundColor Yellow
}
if ($envContent -notmatch '(?m)^[ \t]*LLM_API_KEY[ \t]*=[ \t]*\S') {
    Write-Host "  [WARN] LLM_API_KEY 未配置：对话主模型将回退使用 DASHSCOPE_API_KEY" -ForegroundColor Yellow
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
# 清单与 backend/requirements.txt 对齐（此前含项目并未使用的 pandas/scipy，会导致永远 FAIL）
$pythonDeps = @(
    @{Name="fastapi"; Import="fastapi"},
    @{Name="uvicorn"; Import="uvicorn"},
    @{Name="httpx"; Import="httpx"},
    @{Name="openai"; Import="openai"},
    @{Name="numpy"; Import="numpy"},
    @{Name="PyYAML"; Import="yaml"},
    @{Name="Jinja2"; Import="jinja2"},
    @{Name="python-jose"; Import="jose"},
    @{Name="bcrypt"; Import="bcrypt"},
    @{Name="python-multipart"; Import="multipart"},
    @{Name="python-dotenv"; Import="dotenv"},
    @{Name="PyMuPDF"; Import="pymupdf"},
    @{Name="python-docx"; Import="docx"},
    @{Name="python-pptx"; Import="pptx"},
    @{Name="Pillow"; Import="PIL"},
    @{Name="whoosh"; Import="whoosh"},
    @{Name="tiktoken"; Import="tiktoken"},
    @{Name="mcp"; Import="mcp"},
    @{Name="ddgs"; Import="ddgs"}
)

foreach ($dep in $pythonDeps) {
    cmd /c "`"$venvPython`" -c `"import $($dep.Import)`" >nul 2>&1"
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
Write-Host "    1. 编辑根目录 .env 填入 LLM_API_KEY / DASHSCOPE_API_KEY" -ForegroundColor Gray
Write-Host "    2. 运行 .\start.ps1 启动应用" -ForegroundColor Gray
Write-Host "    3. 访问 http://localhost:5173" -ForegroundColor Gray
Write-Host ""
Write-Host "  启动命令: .\start.ps1" -ForegroundColor Yellow
Write-Host ""

if (-not $allOk) {
    exit 1
}
