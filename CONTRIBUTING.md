# Contributing

面向要**改代码、发版本**的人。只想安装或使用，看 [README.md](README.md) 就够了。

---

## 三仓拓扑

```
ai-memory-docker
  └── template/ (submodule) ── dpkg-s/ai-memory-template   ← 本仓
        └── web-console/ (submodule) ── dpkg-s/ai-memory-web
```

本仓是**唯一产出安装器 exe 的仓**。web 仓的 Release 会从本仓 `latest` 拉同一份 exe 转挂，
docker 仓的两个镜像 tag 跟随本仓与 web 的版本。

### 发布顺序是硬约束

**本仓先 → ai-memory-web 后 → ai-memory-docker 跟随。**

反序的后果：web 仓的 Release 会从本仓 `latest` 拿到**上一个版本**的安装器，而 exe 文件名
不带版本号，下载的人无从发现。

## 版本号规则

major 位固定不动，**只叠加**：

| 变更 | 位 |
| --- | --- |
| 修 bug / 文档 / CI / 重构 | `+0.0.1` |
| 用户可见的新能力 | `+0.1` |

三仓版本号**不同轨**，谁的代码变了谁升，不追求数字对齐，靠 README 的版本对照表说明兼容性。

### 版本源

根目录 `VERSION` 只有一行（如 `3.1.2`），是本仓**唯一机器可读版本源**。`release.yml` 与
发布门禁共读它，不要在两处各写一份。

### 一个版本号要同时出现在三处

```
tag 名  ==  VERSION 文件  ==  CHANGELOG.md 首条
```

三者不一致时 `release.yml` 的 `Verify version consistency` 会在**跑测试之前**判红。

## 发版流程

```bash
# 1. 改三处版本号
echo 3.1.3 > VERSION
# CHANGELOG.md 顶部加条目
# （若要附新安装器：python build_exe.py gui，产物落 dist/）

# 2. 发布前门禁（只读，退出码 1 = 禁止打 tag）
python ~/.workbuddy/skills/ai-memory-release-flow/scripts/check_release.py \
  --repo core --proxy http://127.0.0.1:7897

# 3. 提交并打 annotated tag
git commit -am "chore: bump to 3.1.3"
git tag -a v3.1.3 -m "v3.1.3"

# 4. 推送（tag 会触发 release.yml）
git push origin master --follow-tags

# 5. 等 CI 5 个 job 全绿，再去 web 仓发对应版本
```

### Release 正文写什么

**只写使用者能感知的**：升级后看到什么不同、要不要动手、有没有破坏性变更、怎么校验附件。

从 `CHANGELOG.md` 里**筛**，不是复制。禁止出现：仓间归属、子模块指针、commit SHA、
`release.yml` / workflow / CI job、发布顺序、`gh release upload`、构建环境、字节数。

纯内部变更（只动了 CI、只改了测试）照实写「**本版无功能变更**」三行结束。

骨架见 `~/.workbuddy/skills/ai-memory-release-flow/references/release-body-template.md`。

### 附件规范

| 附件 | 谁产 | 命名 |
| --- | --- | --- |
| 安装器 | 本仓 `dist/ai-memory-setup.exe`（入库） | `ai-memory-setup.exe` + `.sha256` |

同名 `.sha256` 必须与二进制同页发布，内容是 `sha256sum` 格式（只记裸文件名），用户可
`sha256sum -c` 直接校验。

## 历史欠账口径

| 欠账 | 现状 | 处置 |
| --- | --- | --- |
| `2.1.0` / `1.0.0` / `1.1.0` 无 tag 无 Release | 只在 CHANGELOG 里 | **不补发**，README 声明「未单独发布」 |
| 早期 Release 无 `.sha256` | 已把校验值写进正文 | 不追溯补文件 |

不补发的理由：GitHub 不允许修改 `published_at`，补发旧版会让 Releases 页面出现
「旧版本比新版本更晚」的倒挂，比缺版本更误导。

## 本地开发

```bash
pip install -r requirements-dev.txt
ruff check server.py memory_index.py yaml_io.py text_utils.py locks.py \
  metrics.py setup_link.py setup_vault.py ai_memory_setup.py ai_memory_gui.py \
  build_exe.py tests/ scripts/

# 测试是脚本式的，不是 pytest：每个文件自带临时 vault，退出码 0 = 通过
for f in tests/test_*.py; do python "$f" || echo "FAIL $f"; done
for f in tests/selftest_*.py; do python "$f"; done
```

CI 用同一套遍历逻辑（`for f in tests/test_*.py`），所以**新增回归测试不需要改 CI**，
文件名匹配 `test_*.py` 即会被执行。`tests/concurrency_worker.py` 是子进程工人，故意不匹配。

## 几条容易踩的坑

- **`mcp` 版本上界是硬约束**：`mcp>=1.30,<2`。2.x 把 `FastMCP` 更名为 `MCPServer`，
  import 会直接失败。`requirements.txt` 里的上界不要放宽。
- **`dist/ai-memory-setup.exe` 入库是有意为之**：跨仓转挂要求两边是同一份字节。
  但**不允许同时入库两个 exe**。
- **改 `web/` 保存即生效**（uvicorn `StaticFiles` 每请求读盘）；改 `web_console.py`
  等 Python 必须重启实例。
- **`git add -A` 会用子模块当前 HEAD 覆盖手工登记的 gitlink**：动过 submodule 后先
  `git add web-console`，再 `git add` 普通文件。
