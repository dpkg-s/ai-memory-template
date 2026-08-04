# Git 版本控制规范

> 记忆库必须启用 git 版本控制，确保多设备同步和历史可追溯。

## AI 必须遵守的规则

### 每次写入后自动提交

完成 `memory_write` 后，必须立即执行：

```bash
cd ~/ai-memory
git add -A
git commit -m "{source}: {动作} - {简要说明}"
git push
```

| 项 | 说明 |
|----|------|
| source | `codex` / `claude` / `workbuddy`，标识哪个 AI 提交的 |
| 动作 | `新增` / `更新` / `修复` / `归档` 等 |
| 示例 | `codex: 更新项目_xxx - 完成API对接` |

### 每次开工前拉取

对话开始时执行：

```bash
cd ~/ai-memory && git pull
```

## 首次设置（由主人或 AI 完成）

```bash
cd ~/ai-memory
git init
git add -A
git commit -m "chore: 初始化记忆库"

# 在 GitHub 创建私有仓库后：
git remote add origin https://github.com/用户名/ai-memory.git
git branch -M master
git push -u origin master
```

## 多台电脑

```bash
git clone https://github.com/用户名/ai-memory.git ~/ai-memory
```

## 注意事项

- 不要在记忆库中提交敏感信息（token、密码等）
- 如果 push 失败（网络问题），不要阻塞，下次 push 时补上
- 冲突时优先保留远程版本，本地变更手动合并
