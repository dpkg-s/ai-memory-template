#!/usr/bin/env bash
# ai-memory-template installer for Linux/macOS

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
echo "[...] 安装 mcp 包..."
$PY -m pip install mcp -q
echo "[OK] mcp 安装完成"

# 3. Create memory directory
MEM_DIR="$HOME/ai-memory"
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

# 6. Copy server.py
if [ -f "$SCRIPT_DIR/server.py" ]; then
    cp "$SCRIPT_DIR/server.py" "$MEM_DIR/"
    echo "[OK] server.py 已复制"
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "记忆库: $MEM_DIR"
echo ""
echo "Python: $PY"
echo "Server: $MEM_DIR/server.py"
