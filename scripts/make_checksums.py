#!/usr/bin/env python3
"""生成 SHA256SUMS.txt —— 供用户校验下载到的安装脚本与运行时模块。

为什么需要它
------------
README 提供的安装方式包含「一行管道执行」（`curl … | bash` / `irm … | iex`），
这条路径上用户看不到脚本内容、也无从核对来源。发布一份哈希清单，让愿意核对的人
有一个可比对的基准 —— 这是把「信任」从「盲信」降级为「可验证」的最低成本手段。

为什么从 git blob 取内容而不是直接读工作区文件
--------------------------------------------
仓库通过 `.gitattributes` 把 `*.sh` / `*.py` 强制为 LF，而 Windows 工作区检出的是
CRLF。直接哈希工作区文件会得到与「用户在 Linux/macOS 上下载到的文件」不同的结果，
校验必然失败。因此以 **git 中存储的 blob 字节**为准 —— 那正是 GitHub raw 下发的
内容。不在 git 仓库中时（例如用户解压 zip），退化为读文件并把 CRLF 归一为 LF。

用法:
    python scripts/make_checksums.py            # 写入仓库根目录 SHA256SUMS.txt
    python scripts/make_checksums.py --check    # 只校验现有清单是否与当前内容一致
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 需要校验的文件：安装脚本（管道执行的入口）+ server.py 及其运行时依赖模块。
# 测试、文档、模板不在此列 —— 它们不影响「执行了什么代码」。
TARGETS = [
    "install.sh",
    "install.ps1",
    "server.py",
    "memory_index.py",
    "yaml_io.py",
    "text_utils.py",
    "locks.py",
    "metrics.py",
]

OUT = ROOT / "SHA256SUMS.txt"


def blob_bytes(rel: str) -> bytes:
    """取仓库中存储的规范字节（优先 git blob，退化为归一化后的工作区内容）。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
            capture_output=True, check=True,
        ).stdout
        if out:
            return out
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    data = (ROOT / rel).read_bytes()
    return data.replace(b"\r\n", b"\n")


def build() -> str:
    lines = []
    for rel in TARGETS:
        digest = hashlib.sha256(blob_bytes(rel)).hexdigest()
        lines.append(f"{digest}  {rel}")
    return "\n".join(lines) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    content = build()

    if check:
        if not OUT.exists():
            print("SHA256SUMS.txt 不存在")
            return 1
        current = OUT.read_text(encoding="utf-8")
        if current == content:
            print("SHA256SUMS.txt 与当前内容一致")
            return 0
        print("SHA256SUMS.txt 已过期，请重新生成：python scripts/make_checksums.py")
        return 1

    OUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"已写入 {OUT.relative_to(ROOT)}")
    print(content, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
