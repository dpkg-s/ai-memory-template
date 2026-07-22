#!/usr/bin/env pwsh
# ai-memory-template installer for Windows

$ErrorActionPreference = "Stop"

Write-Host "=== ai-memory-template 安装 ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
try {
    $py = (Get-Command python -ErrorAction Stop).Source
    $ver = & $py --version
    Write-Host "[OK] Python: $ver" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Python not found" -ForegroundColor Red
    exit 1
}

# 2. Install mcp package
Write-Host "[...] 安装 mcp 包..." -ForegroundColor Yellow
try {
    & $py -m pip install mcp -q
    Write-Host "[OK] mcp 安装完成" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] pip install mcp 失败: $_" -ForegroundColor Red
    exit 1
}

# 3. Create memory directory
$memDir = "$HOME\ai-memory"
if (-not (Test-Path $memDir)) {
    New-Item -ItemType Directory -Path $memDir -Force | Out-Null
}
Write-Host "[OK] 记忆库目录: $memDir" -ForegroundColor Green

# 4. Copy template files
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$templateDir = Join-Path $scriptDir "template"
if (Test-Path $templateDir) {
    Copy-Item "$templateDir\*" -Destination $memDir -Recurse -Force
    Write-Host "[OK] 初始记忆文件已复制" -ForegroundColor Green
}

# 5. Copy rules
$rulesDir = Join-Path $scriptDir "rules"
if (Test-Path $rulesDir) {
    $rulesDest = Join-Path $memDir "rules"
    New-Item -ItemType Directory -Path $rulesDest -Force | Out-Null
    Copy-Item "$rulesDir\*" -Destination $rulesDest -Force
    Write-Host "[OK] 规则文件已复制" -ForegroundColor Green
}

# 6. Copy server.py
$serverPy = Join-Path $scriptDir "server.py"
if (Test-Path $serverPy) {
    Copy-Item $serverPy -Destination $memDir -Force
    Write-Host "[OK] server.py 已复制" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 安装完成 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "记忆库: $memDir"
Write-Host ""
Write-Host "=== 配置各 AI 工具 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Python 路径: $py"
Write-Host "Server 路径: $memDir\server.py"
Write-Host ""
Write-Host "--- Codex CLI ($env:USERPROFILE\.codex\config.toml) ---"
Write-Host "[mcp_servers]"
Write-Host "[mcp_servers.ai-memory]"
Write-Host "command = ""$py"""
Write-Host "args = [""$memDir\server.py""]"
Write-Host ""
Write-Host "--- Claude Desktop ($env:APPDATA\Claude\claude_desktop_config.json) ---"
Write-Host '{'
Write-Host '  "mcpServers": {'
Write-Host '    "ai-memory": {'
Write-Host "      ""command"": ""$py"","
Write-Host '      "args": ["'$memDir'\server.py"]'
Write-Host '    }'
Write-Host '  }'
Write-Host '}'
Write-Host ""
Write-Host "具体配置示例见 setup/ 目录"
