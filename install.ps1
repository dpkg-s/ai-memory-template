#!/usr/bin/env pwsh
# ai-memory-template installer for Windows
#
# ============================================================================
#  安全声明 / SECURITY NOTICE —— 运行前请先读完
# ============================================================================
#  本脚本会做三件改变你系统状态的事：
#    1. 用 pip 安装 Python 包 mcp（版本约束 mcp>=1.27,<2）
#    2. 创建记忆库目录 ~/ai-memory 并写入初始模板
#    3. 把 server.py 及其同目录的 .py 模块复制进该目录
#  它不会：读取或上传你的任何文件、修改注册表或系统环境变量、请求管理员
#  权限、在后台常驻或联外网（除 pip 安装依赖外）。
#
#  **如果你打算用「一行命令管道执行」（irm ... | iex），请先停下** ——
#  管道执行时你看不到脚本内容，也无法核对来源。推荐做法：
#      Invoke-WebRequest -Uri https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.ps1 -OutFile install.ps1
#      Get-Content install.ps1   # 先读一遍
#      .\install.ps1
#  更稳妥的做法是 clone 仓库后本地运行（见 README「快速开始」）。
#  校验完整性：与仓库根目录 SHA256SUMS.txt 或 Release 说明里的哈希比对
#      Get-FileHash install.ps1 -Algorithm SHA256
#
#  可用环境变量：
#      AI_MEMORY_DIR          指定记忆库目录（默认 $HOME\ai-memory）
#      AI_MEMORY_SKIP_PIP=1   跳过 pip 安装（依赖已就绪时）
# ============================================================================

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
#    必须带版本上界：mcp 2.x 已把 FastMCP 更名为 MCPServer，装到 2.x 会 import 失败。
if ($env:AI_MEMORY_SKIP_PIP -eq "1") {
    Write-Host "[SKIP] 按要求跳过 mcp 安装（AI_MEMORY_SKIP_PIP=1）" -ForegroundColor Yellow
} else {
    Write-Host "[...] 安装 mcp 包（mcp>=1.27,<2）..." -ForegroundColor Yellow
    try {
        & $py -m pip install "mcp>=1.27,<2" -q
        Write-Host "[OK] mcp 安装完成" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] pip install mcp 失败: $_" -ForegroundColor Red
        Write-Host "       可手动执行: $py -m pip install ""mcp>=1.27,<2""" -ForegroundColor Red
        exit 1
    }
}

# 3. Create memory directory
if ($env:AI_MEMORY_DIR) {
    $memDir = $env:AI_MEMORY_DIR
} else {
    $memDir = "$HOME\ai-memory"
}
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

# 6. Copy server.py 及其同目录模块
#    server.py 依赖 memory_index / yaml_io / text_utils / locks / metrics，
#    只复制 server.py 会导致启动即 ImportError。
if (Test-Path (Join-Path $scriptDir "server.py")) {
    $runtimeModules = @("server.py", "memory_index.py", "yaml_io.py", "text_utils.py", "locks.py", "metrics.py")
    foreach ($mod in $runtimeModules) {
        $src = Join-Path $scriptDir $mod
        if (Test-Path $src) {
            Copy-Item $src -Destination $memDir -Force
        }
    }
    Write-Host "[OK] server.py 及依赖模块已复制" -ForegroundColor Green
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
