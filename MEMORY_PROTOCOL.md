# 共享记忆写入协议 v1

本文是 MCP 与客户端钩子共用的行为契约。个性化说明可以引用本协议，不必另起一套口径。

## 会话开始

读取身份档案，再搜索「近期工作动态」。已在当前会话完成时，不重复加载；恢复会话或压缩后按需重建上下文。

## 写入触发

应写：用户明确要求记住；技术决策；已修复的问题；实质性项目进展；用户明确表达的新偏好。
不写：普通问答、临时琐事、没有行动的计划、密钥和敏感凭据。AI 推断可以作为候选记录，但必须标为 `verified=false`，不得写成已确认事实。

## 标准写入流程

1. 明确事件、结论、影响范围和后续事项；选职责最贴近的现有笔记。
2. 用 `memory_search` 查重；命中候选后用 `memory_read` 核对现有内容。
3. 同义内容更新原条目；新结论取代旧结论时标明 `supersedes`，并将旧条目标为 `deprecated`。冲突未解决时显式记录，不静默覆盖。
4. 按内容选择 `mem_type`、`scope/project`、`confidence`、`verified`、`source_context`。并发修改已知时使用 `expected_version`。
5. 用 `memory_write` 写入；写后核对返回结果，失败时明确报告，不能声称已保存。
6. 写入项目笔记后，读取「近期工作动态」，在当日区块追加 `- [x] 一句话摘要`，并更新「项目状态」表中对应行。

## 钩子职责

- SessionStart：注入协议并加载身份与近期动态。
- SubagentStart：向 Codex 子 agent 注入同一协议，要求先检索相关背景；交付时说明是否写入。多个 agent 可能并发修改同一条目时，交由主 agent 合并，避免覆盖。
- SubagentStop：子 agent 出现实质性进展但未说明记忆已同步时，触发一次续跑检查。
- Claude Code：SessionStart 注入启动规则；UserPromptSubmit 提醒本轮按触发条件判断；SubagentStart/Stop 覆盖 Claude 子 agent；Stop 检查本轮是否遗漏写回。
- UserPromptSubmit：让写入触发条件持续可见；只提示需要判断的行为，不把每条消息都写入。
- Stop：结束前复核本轮是否命中写入条件；未保存时继续执行完整流程，已保存或不符合条件时直接结束。
- 钩子只提供触发与检查；具体内容仍按上述查重、分类、更新和回读流程处理。

## memory_commit：标准提交入口

自动化与 Agent 写记忆时优先调用 `memory_commit`。普通条目提交标题和正文；项目进展额外提交 `timeline_summary`、`project_name`、`project_status`、`project_focus`，由服务端更新单篇笔记并同步「近期工作动态」与「项目状态」表。

`memory_commit` 会先查重再写入。同名笔记存在时，先用 `memory_read` 读取并合并，然后携带当前 `expected_version` 重试；发现近似候选时，先读取候选、判断是否合并或标注关系，再传 `duplicate_reviewed=true` 确认已审查。未经审查不得绕过提示直接写。并发版本冲突时重新读取、合并后重试。

<!-- hook-context:start -->
记忆库协议：新会话先读取「AI身份档案」，再搜索「近期工作动态」。只有明确要求记住、技术决策、问题修复、实质项目进展或新偏好才写；普通问答不写，密钥不写。写前 memory_search 查重，命中后 memory_read，优先更新旧条目；推断内容 verified=false。写项目笔记后读取并更新「近期工作动态」当日行及项目状态。写入失败必须如实说明。
<!-- hook-context:end -->
