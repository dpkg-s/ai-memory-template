#!/usr/bin/env bash
# ai-memory-template installer for Linux/macOS
#
# ============================================================================
#  安全声明 / SECURITY NOTICE —— 运行前请先读完
# ============================================================================
#  本脚本会做三件改变你系统状态的事：
#    1. 用 pip 安装 Python 包 mcp（版本约束 mcp>=1.27,<2）
#    2. 创建记忆库目录 ~/ai-memory 并写入初始模板
#    3. 把 server.py 及其同目录的 .py 模块复制进该目录
#  它不会：读取或上传你的任何文件、修改 shell 配置文件、请求 sudo、
#  在后台常驻或联外网（除 pip 安装依赖外）。
#
#  **如果你打算用「一行命令管道执行」（curl ... | bash），请先停下** ——
#  管道执行时你看不到脚本内容，也无法核对来源。推荐做法：
#      curl -sSLO https://raw.githubusercontent.com/dpkg-s/ai-memory-template/master/install.sh
#      less install.sh          # 先读一遍
#      bash install.sh
#  更稳妥的做法是 clone 仓库后本地运行（见 README「快速开始」）。
#  校验完整性：与仓库根目录 SHA256SUMS.txt 或 Release 说明里的哈希比对。
#
#  可用环境变量：
#      AI_MEMORY_DIR          指定记忆库目录（默认 ~/ai-memory）
#      AI_MEMORY_SKIP_PIP=1   跳过 pip 安装（依赖已就绪时）
# ============================================================================

set -e

echo "=== ai-memory-template 安装 ==="
echo ""

# 1. Check Python
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "[FAIL] Python not found"
    exit 1
fi
VER=$($PY --version)
echo "[OK] Python: $VER"

# 2. Install mcp
#    必须带版本上界：mcp 2.x 已把 FastMCP 更名为 MCPServer，装到 2.x 会 import 失败。
if [ "${AI_MEMORY_SKIP_PIP:-0}" = "1" ]; then
    echo "[SKIP] 按要求跳过 mcp 安装（AI_MEMORY_SKIP_PIP=1）"
else
    echo "[...] 安装 mcp 包（mcp>=1.27,<2）..."
    if ! $PY -m pip install "mcp>=1.27,<2" -q; then
        echo "[FAIL] mcp 安装失败。请检查网络，或手动执行：$PY -m pip install \"mcp>=1.27,<2\""
        exit 1
    fi
    echo "[OK] mcp 安装完成"
fi

# 3. Create memory directory
MEM_DIR="${AI_MEMORY_DIR:-$HOME/ai-memory}"
mkdir -p "$MEM_DIR"
echo "[OK] 记忆库目录: $MEM_DIR"

# 4. Copy template files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$SCRIPT_DIR/template" ]; then
    cp -r "$SCRIPT_DIR/template/"* "$MEM_DIR/" 2>/dev/null || true
    echo "[OK] 初始记忆文件已复制"
fi

# 5. Copy rules
if [ -d "$SCRIPT_DIR/rules" ]; then
    mkdir -p "$MEM_DIR/rules"
    cp -r "$SCRIPT_DIR/rules/"* "$MEM_DIR/rules/" 2>/dev/null || true
    echo "[OK] 规则文件已复制"
fi

# 6. Copy server.py 及其同目录模块
#    server.py 依赖 memory_index / yaml_io / text_utils / locks / metrics，
#    只复制 server.py 会导致启动即 ImportError。
if [ -f "$SCRIPT_DIR/server.py" ]; then
    for mod in server.py memory_index.py yaml_io.py text_utils.py locks.py metrics.py; do
        if [ -f "$SCRIPT_DIR/$mod" ]; then
            cp "$SCRIPT_DIR/$mod" "$MEM_DIR/"
        fi
    done
    echo "[OK] server.py 及依赖模块已复制"
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "记忆库: $MEM_DIR"
echo ""
echo "Python: $PY"
echo "Server: $MEM_DIR/server.py"

echo ""
echo "=== 配置各 AI 工具 ==="
echo ""
echo "--- Codex CLI (~/.codex/config.toml) ---"
echo "[mcp_servers]"
echo "[mcp_servers.ai-memory]"
echo "command = \"$PY\""
echo "args = [\"$MEM_DIR/server.py\"]"
echo ""
echo "--- Claude Desktop (claude_desktop_config.json) ---"
echo '{'
echo '  "mcpServers": {'
echo '    "ai-memory": {'
echo "      \"command\": \"$PY\","
echo "      \"args\": [\"$MEM_DIR/server.py\"]"
echo '    }'
echo '  }'
echo '}'
echo ""
echo "具体配置示例见 setup/ 目录"
