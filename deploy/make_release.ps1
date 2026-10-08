# ResumeAgent 本地打包脚本（Windows PowerShell）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -IncludeFrontendDist
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64 -Apply
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64 -Apply -UpdateArgs backend
#
# 参数：
#   -Remote              打包后自动 scp 上传到服务器（默认家目录 ~）
#   -RemoteDir           服务器上的上传目录，默认 "~"；如填 /opt/ResumeAgent 则直接传到部署目录
#   -Apply               上传后自动 ssh 到服务器解压并执行 deploy/update.sh（一步到位）
#   -AppDir              服务器上的项目目录，默认 /opt/ResumeAgent（-Apply 用）
#   -UpdateArgs          传给 update.sh 的参数：backend | frontend | data | 留空为全量
#   -IncludeFrontendDist 连本地已构建的 frontend/dist 一起打包（服务器就不必再装 Node 构建）
#
# 注意：-Remote 只负责「上传」，不解压。不带 -Apply 时仍需上服务器执行：
#   cd /opt/ResumeAgent && sudo tar -xzf ~/resume-agent.tgz && sudo bash deploy/update.sh
#
# 默认排除：node_modules、__pycache__、.git、课程 PDF（约 98MB）、.env（含密钥，单独传）
param(
    [string]$Remote = "",
    [string]$RemoteDir = "~",
    [switch]$Apply,
    [string]$AppDir = "/opt/ResumeAgent",
    [string]$UpdateArgs = "",
    [switch]$IncludeFrontendDist
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$out  = Join-Path (Split-Path -Parent $root) "resume-agent.tgz"

if (Test-Path $out) { Remove-Item $out -Force }

$items = @(
    "app_api.py", "main.py",
    "requirements.txt", "requirements-prod.txt",
    "Dockerfile", "docker-compose.yml", ".dockerignore",
    # 数据库迁移：服务端部署/更新需执行 alembic upgrade head
    "alembic.ini", "alembic", ".env.example",
    "src", "deploy",
    "frontend/index.html", "frontend/package.json", "frontend/package-lock.json",
    "frontend/src", "frontend/env.d.ts", "frontend/vite.config.ts",
    "frontend/tsconfig.json", "frontend/tsconfig.node.json",
    "data/databases", "data/processed",
    "data/knowledge_base/project_intros", "data/knowledge_base/project_highlights"
)

if ($IncludeFrontendDist -and (Test-Path (Join-Path $root "frontend/dist/index.html")) -eq $false) {
    Write-Host "[警告] 本地没有 frontend/dist，请先执行: cd frontend; npm run build" -ForegroundColor Yellow
}
if ($IncludeFrontendDist) { $items += "frontend/dist" }

Write-Host "[打包] 生成 $out" -ForegroundColor Green
Push-Location $root
try {
    tar --exclude="*__pycache__*" -czf $out $items
    if ($LASTEXITCODE -ne 0) { throw "tar 打包失败" }
} finally {
    Pop-Location
}

$size = (Get-Item $out).Length / 1MB
Write-Host ("[完成] {0:N1} MB" -f $size) -ForegroundColor Green

if ($Remote) {
    # 远端目录统一以 / 结尾；"~" 交给服务器 shell 展开（不要用 $HOME 本地展开）
    $targetDir = if ($RemoteDir -eq "~") { "~/" } else { $RemoteDir.TrimEnd("/") + "/" }
    Write-Host "[上传] -> ${Remote}:$targetDir" -ForegroundColor Green
    scp $out "${Remote}:$targetDir"
    if ($LASTEXITCODE -ne 0) { throw "scp 上传失败" }

    if ($Apply) {
        $tgzRemote  = $targetDir + "resume-agent.tgz"
        $appDirSafe = $AppDir.TrimEnd("/")
        # ~ 由远端 shell 在 sudo 之前展开，指向 ubuntu 家目录（不会被 sudo 改成 /root）
        $sshCmd = "cd `"$appDirSafe`" && sudo tar -xzf `"$tgzRemote`" && sudo bash `"$appDirSafe/deploy/update.sh`" $UpdateArgs"
        Write-Host "[应用] $Remote : $sshCmd" -ForegroundColor Green
        ssh -t $Remote $sshCmd            # -t：给 sudo 密码提示分配 TTY
        if ($LASTEXITCODE -ne 0) { throw "远程更新失败（ExitCode=$LASTEXITCODE）" }
        Write-Host "[完成] 已在服务器解压并更新" -ForegroundColor Green
    } else {
        Write-Host "[完成] 已上传（未解压）。请在服务器执行:" -ForegroundColor Green
        Write-Host "  cd $AppDir && sudo tar -xzf $targetDir`resume-agent.tgz && sudo bash deploy/update.sh"
    }
}
