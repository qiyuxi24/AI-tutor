# ============================================================
#  TutorAgent - 一键部署到远程 Linux 服务器
#
#  流程：打包本地工作区(tar) → scp 上传 → 远端解包
#        → docker compose up -d --build（远端后台执行+轮询）
#        → 健康检查 → 打印前端访问地址
#
#  用法（在仓库根目录）：
#    .\deploy\deploy.ps1                        # 全流程（默认 wojtek@100.90.96.111）
#    .\deploy\deploy.ps1 -SkipBuild             # 只同步代码，不构建
#    .\deploy\deploy.ps1 -Port 80               # 换宿主机端口（默认 8080）
#    .\deploy\deploy.ps1 -Server root@1.2.3.4 -RemoteDir /opt/ai-tutor
#
#  前置条件：
#    1. 本机能免密 ssh 到目标机（本机公钥已在对方 authorized_keys）
#    2. 目标机已装 docker + docker compose
#    3. ⚠️ 每次部署都会把【本机 .env（含真实密钥）】覆盖到远端
#       —— 根目录 .env 是项目唯一真值文件，远端手改会在下次部署时丢失
#    4. ponytail: 本地删除的文件不会在远端删除（tar 解包非 rsync 语义），
#       需要精确镜像时先 ssh 上去 rm -rf 再重跑
# ============================================================
param(
    [string]$Server    = "wojtek@100.90.96.111",
    [string]$RemoteDir = "/home/wojtek/ai-tutor",
    [int]   $Port      = 8080,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$sshExe = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$scpExe = "$env:WINDIR\System32\OpenSSH\scp.exe"
$tarExe = "$env:WINDIR\System32\tar.exe"

function Info($m) { Write-Host "[信息]  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK]    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

function Remote([string]$cmd) {
    $out = & $sshExe -o BatchMode=yes -o ConnectTimeout=10 $Server $cmd
    if ($LASTEXITCODE -ne 0) { Fail "远程命令失败：$cmd`n$out" }
    return $out
}

# ---------- 1. SSH 连通性 ----------
Info "连接 $Server ..."
$probe = Remote "echo OK"
if ("$probe" -notmatch "OK") { Fail "无法免密登录 $Server（先把公钥写进对方 authorized_keys）" }
Ok "SSH 免密连通"

# ---------- 2. 打包工作区 ----------
# 排除：本地依赖 / 运行时数据（远端由 volume 提供）/ IDE 与 AI 会话产物。
# 注意：.env 故意不排除 —— 部署语义 = 远端配置与本地一致（见文件头说明 3）
$stage = Join-Path $env:TEMP "ai-tutor-deploy.tgz"
if (Test-Path $stage) { Remove-Item $stage -Force }
Info "打包工作区 → $stage"
& $tarExe -czf $stage `
    --exclude=./.git --exclude=./.codebuddy --exclude=./.workbuddy `
    --exclude=./.research --exclude=./.vscode --exclude=./.idea --exclude=./.trae `
    --exclude=./frontend/node_modules --exclude=./frontend/dist `
    --exclude=./backend/venv --exclude=./venv --exclude=./.venv `
    --exclude=./backend/data --exclude=./backend/eval_data `
    --exclude=./data/knowledge --exclude=./data/conversations --exclude=./data/profiles `
    --exclude=./data/quiz --exclude=./data/kb --exclude=./data/rag `
    --exclude=./data/traces --exclude=./data/collector `
    --exclude=./reports --exclude=*.pyc --exclude=*.log --exclude=*.db `
    --exclude=./.pytest_cache --exclude=./.mypy_cache .
if ($LASTEXITCODE -ne 0) { Fail "tar 打包失败" }
$sizeMB = [math]::Round((Get-Item $stage).Length / 1MB, 1)
Ok "打包完成（$sizeMB MB）"

# ---------- 3. 上传 + 远端解包 ----------
Info "上传到 ${Server}:/tmp ..."
& $scpExe -o BatchMode=yes $stage "${Server}:/tmp/ai-tutor-deploy.tgz"
if ($LASTEXITCODE -ne 0) { Fail "scp 上传失败" }
Ok "上传完成"

Remote "mkdir -p $RemoteDir/data/knowledge $RemoteDir/data/conversations $RemoteDir/data/profiles $RemoteDir/backend/data && tar -xzf /tmp/ai-tutor-deploy.tgz -C $RemoteDir && rm -f /tmp/ai-tutor-deploy.tgz"
Ok "远端代码已更新：$RemoteDir"

# ---------- 4. 构建 + 启动 ----------
if ($SkipBuild) {
    Warn "指定了 -SkipBuild，仅同步代码，跳过构建"
    return
}

Info "远端 docker compose up -d --build（首次构建约 5~15 分钟，远端后台执行防 SSH 断连）..."
Remote "cd $RemoteDir && rm -f /tmp/ai-tutor-build.log && PORT=$Port nohup docker compose up -d --build > /tmp/ai-tutor-build.log 2>&1 & echo BUILD_STARTED"

$deadline = (Get-Date).AddMinutes(25)
$status = ""
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
    $lines = Remote 's=$(docker inspect --format {{.State.Health.Status}} ai-tutor 2>/dev/null || echo no); echo STATUS=$s; tail -n 1 /tmp/ai-tutor-build.log 2>/dev/null'
    $status = (($lines | Where-Object { $_ -match "^STATUS=" }) -replace "^STATUS=", "")
    if ($status -eq "healthy" -or $status -eq "unhealthy") { break }
    $lastLog = ($lines | Where-Object { $_ -notmatch "^STATUS=" } | Select-Object -Last 1)
    Write-Host ("        {0}  [{1}]  {2}" -f (Get-Date -Format "HH:mm:ss"), $status, $lastLog)
}

if ($status -ne "healthy") {
    Warn "容器未在 25 分钟内 healthy（当前：$status）。最近 60 行构建/运行日志："
    Remote "tail -n 60 /tmp/ai-tutor-build.log"
    Fail "部署未完成，请根据上方日志排查"
}
Ok "容器 ai-tutor 已 healthy"

# ---------- 5. 验证 + 访问地址 ----------
$code = (Remote "curl -s -o /dev/null -w %{http_code} http://127.0.0.1:$Port/api/health").Trim()
if ($code -eq "200") { Ok "远端本机 /api/health = 200" }
else { Warn "远端本机 /api/health 返回 $code（容器 healthy 但接口异常，看 docker logs）" }

$hostIp = ($Server -split "@")[-1]
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  部署完成" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ("  前端访问   : http://{0}:{1}" -f $hostIp, $Port) -ForegroundColor White
Write-Host ("  健康检查   : http://{0}:{1}/api/health" -f $hostIp, $Port)
Write-Host ("  查看日志   : ssh {0} 'docker logs -f ai-tutor'" -f $Server)
Write-Host ("  手动重启   : ssh {0} 'cd {1} && docker compose up -d --build'" -f $Server, $RemoteDir)
Write-Host ""
