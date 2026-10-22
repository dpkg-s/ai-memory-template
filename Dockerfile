# ai-memory-template 容器镜像（v3 阶段 1 · Streamable HTTP）
#
# 镜像里**没有业务逻辑**：只装 Python 运行时 + mcp 依赖 + 本仓的 .py 模块，
# 记忆库（vault）与运行时状态（索引 db + 指标）一律从卷挂载进来。因此升级只需换
# 镜像 tag，数据不动。
#
# 构建：
#     docker build -t ai-memory:2.6.0 .
# 运行（只绑回环，宿主机局域网看不到该端口）：
#     docker run -d --name ai-memory -p 127.0.0.1:8700:8700 \
#         -v ai-memory-vault:/vault -v ai-memory-state:/state ai-memory:2.6.0
# 或直接用 compose.yaml。

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖只有 mcp 一个。上界 <2 是硬约束：mcp 2.x 把 FastMCP 更名为 MCPServer，
# 装到 2.x 会 import 失败（与 install.sh / install.ps1 里的约束保持一致）。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 运行时模块 = 仓库根目录的 *.py（server.py + 叶子模块 + HTTP 传输层与入口）。
# 刻意用通配而非逐项列举：安装脚本里已经有一份显式清单，不再复制第二份，
# 新增模块无需改本文件（少一处会随重构腐烂的清单）。
COPY *.py ./

# 2026-10-16：Web 控制台（serve_web.py / web_console.py + web/）已拆分为独立仓
# ai-memory-web —— 本镜像只跑 MCP 服务端，故不再 COPY 前端静态资源。
# 需要控制台时单独 clone 该仓，让它对着本镜像暴露的 8700 端点启动即可。

# 非 root 运行。/vault 是记忆库卷的挂载点，/state 是运行时状态（索引 db + 指标）的
# 挂载点。
# 不用 --system：系统账号 uid 上限是 999，配 10001 会发警告（虽不致命）。
# 10001 而非 1000 是为了避开宿主常见用户 uid，减少 bind mount 场景的归属冲突。
#
# /state **必须在镜像里先建好并改属主**：Docker 首次挂载空命名卷时会连内容**带属主**
# 从镜像目录复制过去。镜像里没这个目录的话，挂载点会被建成 root:root，非 root 进程
# 写不了 —— 而失败症状是「索引静默回落全库扫描」，不是报错。
RUN useradd --create-home --home-dir /home/memory --uid 10001 memory \
 && mkdir -p /vault /state \
 && chown -R memory:memory /vault /app /state

USER memory

# 容器内必须监听 0.0.0.0（否则宿主机连不上）⇒ 触发 transport_http 的 fail-fast 规则，
# 必须同时给出 Host 白名单，否则进程拒绝启动。这里给的是「回环 + 容器名」的安全默认：
# 从宿主机经 127.0.0.1:8700 访问（Host: localhost:8700）可通过；改成局域网地址访问时
# 必须显式覆盖 AI_MEMORY_HTTP_ALLOWED_HOSTS —— 这是有意的摩擦，不是疏漏。
ENV AI_MEMORY_DIR=/vault \
    AI_MEMORY_STATE_DIR=/state \
    AI_MEMORY_TRANSPORT=http \
    AI_MEMORY_HTTP_HOST=0.0.0.0 \
    AI_MEMORY_HTTP_PORT=8700 \
    AI_MEMORY_HTTP_PATH=/mcp \
    AI_MEMORY_HTTP_ALLOWED_HOSTS=localhost:*,127.0.0.1:*,ai-memory:*

EXPOSE 8700
# /vault = 记忆库（事实源，必须持久）；/state = 索引 db + 指标 json。索引是纯派生
# 缓存（可重建），指标是累积趋势（不可补），两者都不该落在镜像层。声明成卷，
# 避免 run 时忘挂导致数据落在容器层。
VOLUME ["/vault", "/state"]

# 存活探针：只验监听端口在。MCP 端点是 POST + 会话语义，GET 不返回 2xx，
# 因此不去请求 /mcp（那会把「健康」误判成「故障」）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os,socket,sys;s=socket.socket();s.settimeout(3);sys.exit(1 if s.connect_ex(('127.0.0.1',int(os.environ.get('AI_MEMORY_HTTP_PORT','8700')))) else 0)"

CMD ["python", "serve_http.py"]
