# Task 11 Constitution Impact

> 状态：受控审核编排已实现并通过确定性测试；尚未完成真实业务模型质量验收。
>
> 范围：单一 ReviewContext 下，按要求逐项运行受限 Review Agent，保存候选 Assessment，创建人工确认请求，并在失败时保留可恢复进度。

## Constitution impact: Yes

Task 11 把 Task 10 的“有门禁工具箱”接入了受控审核循环，因此扩大了 Agent 的行动编排能力，但没有扩大它的权限边界。项目、招标版本、企业资料、引用真实性、正式状态和运行预算仍由服务端掌握。

## 已落地的硬边界

- 每次 Agent 只接收一个明确的 `Requirement` 和其原文引用，不接收任意项目 ID 或任意要求列表。
- ReviewContext 由服务端创建，并额外纳入当前项目可用的 proposal 版本；公司证据仍必须来自显式授权范围。
- Agent 必须先读要求原文，再检索投标证据和公司证据，并打开相关页面后才能提出候选判断。
- `save_assessment` 仍由工具门禁和服务端 `calculate_display_status` 控制；模型不能直接写入正式状态。
- `MATCHED` 无证据仍然触发 `unsupported-pass` 阻断；引用必须属于当前 ReviewContext 且原文真实存在。
- 歧义或未核实企业事实会创建可审计的人工确认请求，并将正式状态保持为 `needs_confirmation`。
- 每批最多处理 10 项要求；每批提交一次，单项失败时提交已完成结果，并记录 `resumable_requirement_id`。
- 失败进度通过 `review_progress_updated` 和 `review_requirement_failed` 审计事件留下，不能静默丢弃已经保存的 Assessment。
- 每次模型尝试都写入不含提示词和原文的调用台账，记录 scope、Coverage、结果原因和停止原因。

## 尚未实现且明确禁止冒充完成的内容

- 尚未证明真实业务文本上的审核准确率、召回率或模型稳定性。
- 尚未实现后台持久化任务 worker、人工决定 API、增量复核和完整前端审核流程。
- 尚未实现自动提交、签章、审批、付款、外部系统写入或自动供应商故障切换。
- `ReviewResult` 和审计进度只代表程序执行进度，不代表招标已经合规，也不代表可以直接提交。

## 测试证据

- Task 11 专项测试 **5 项通过**，覆盖匹配、缺失、部分评分、歧义确认和中途超时恢复。
- 全量后端测试 **273 项通过**；Ruff 与 Mypy 通过。
- 本阶段仍未调用真实业务模型，测试使用注入式 Runner 和确定性证据图谱。
