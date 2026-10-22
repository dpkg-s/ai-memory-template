> 本页由仓库主页拆分而来，内容与拆分前逐字一致。返回 [主页](../README.md)。

# 用 Obsidian 可视化记忆网络

因为记忆是 **Markdown + 双链**，直接用 Obsidian 打开记忆库目录，就能得到完整的知识图谱：

- **图谱视图（Graph View）** —— 所有笔记的链接网络一目了然，哪些项目关联最紧密、哪些笔记是枢纽节点，一眼可见
- **局部图谱（Local Graph）** —— 选中一条笔记，只看它的出链与反向链接
- **孤岛高亮** —— Obsidian 自动标记没有链接的笔记，与 `memory_orphans` 工具互补

```
记忆网络示意：

              运维实录
                 ↑
    ┌──────── 项目_A ────────┐
    │            ↑           │
    │      用户画像 ←── 记忆索引
    │            ↓           │
    └──────── 项目_B ────────┘
                 ↓
           近期工作动态
```

不装 Obsidian 也没关系 —— `memory_graph` 与 `memory_orphans` 在 AI 对话里就能直接查询链接关系。

---

## 多设备同步与版本控制

记忆库就是一堆 Markdown 文件，天然适合 git。给它接上远程仓库，即可实现**多设备无缝同步 + 完整历史记录**。

安装脚本会把 `rules/Git版本控制规范.md` 复制进记忆库目录。**AI 打开记忆库后搜索「git」就能发现并自动遵守这套规范**，无需人工提醒。

### 给 AI 的规范

**每次 `memory_write` 完成后自动执行：**

```bash
cd ~/ai-memory
git add -A
git commit -m "{source}: {动作} - {简要说明}"
git push
```

| 约定 | 说明 |
|------|------|
| commit 格式 | `{source}: {动作} - {简要说明}` |
| 示例 | `codex: 更新项目状态 - 完成数据采集工具 v4` |
| source 标识 | 每个 AI 用自己的标识：`workbuddy`、`codex`、`claude` |
| push | commit 后立即 push，保证远程最新 |

**每次对话开始时先拉取：**

```bash
cd ~/ai-memory && git pull
```

### 首次设置

```bash
# 1. 初始化本地 git
cd ~/ai-memory
git init
git add -A
git commit -m "chore: 初始化记忆库"

# 2. 在 GitHub 创建私有仓库（不要勾选 README）

# 3. 关联远程并推送
git remote add origin https://github.com/你的用户名/ai-memory.git
git branch -M master
git push -u origin master
```

> 记忆库常含个人工作记录，**建议使用私有仓库**。

### 第二台电脑

```bash
git clone https://github.com/你的用户名/ai-memory.git ~/ai-memory
```

clone 完即可使用，所有 AI 的记忆自动同步。

---
