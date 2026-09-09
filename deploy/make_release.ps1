# ResumeAgent 本地打包脚本（Windows PowerShell）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64
#   powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -IncludeFrontendDist
#
# 参数：
#   -Remote              打包后自动 scp 上传到服务器家目录
#   -IncludeFrontendDist 连本地已构建的 frontend/dist 一起打包（服务器就不必再装 Node 构建）
#
# 默认排除：node_modules、__pycache__、.git、课程 PDF（约 98MB）、.env（含密钥，单独传）
param(
    [string]$Remote = "",
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
    Write-Host "[上传] -> ${Remote}:~/" -ForegroundColor Green
    scp $out "${Remote}:~/"
    Write-Host "[完成] 已上传。请在服务器执行: sudo bash /opt/ResumeAgent/deploy/update.sh" -ForegroundColor Green
}
