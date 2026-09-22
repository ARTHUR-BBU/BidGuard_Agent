# Spec: Task 13 人工决定、行动项与增量复核

## Objective

把 Task 12 留下的 `awaiting_confirmation` 接成人可以负责、可追溯、不可越权的正式决定，并让 proposal、company evidence、tender 版本变化触发有边界的增量复核。

Task 13 只负责“记录人的决定、计算影响范围、重新排队必要审核”。它不把人工点击当作文档证据，也不允许旧 Assessment 被覆盖或伪装成当前结论。

## User-visible success criteria

- `POST /api/requirements/{id}/decisions` 支持 `confirm`、`deny`、`not_applicable`，解释必填且有长度上限；
- 每个决定保存责任主体、时间、项目、Requirement、当前 Assessment/ReviewRun 依据和相关版本范围，并产生审计事件；
- Agent 创建的待确认事项不能被删除或静默关闭；新决定只能追加历史记录；
- `POST /api/action-items/{id}/complete` 只能完成属于当前项目的 open ActionItem，并记录完成时间与审计事件；
- 完成行动项或人工决定不会直接把正式状态改成 `satisfied`，新的文档事实必须经过版本化和重新核查；
- 新 proposal 版本只使引用旧 proposal 的当前 Assessment 进入待复核；
- 失效/替换的 company evidence 使所有引用该版本的当前 Assessment 进入待复核；
- 新 tender 版本停用旧 Requirement 矩阵，保留历史，并为新版本重新提取/审核；
- `POST /api/projects/{project_id}/re-review` 在创建 ReviewJob 前持久化受影响 Requirement ID；
- 未受影响 Requirement 的当前 Assessment 保持当前；旧 Assessment 不删除。

## Governance boundaries

- Constitution impact: Yes. 本任务正式引入人工决定和版本影响计算；必须同步 Constitution Impact 文档。
- 人工决定不是 EvidenceLink，不适用页码/quote 门禁；要求书面随标的材料仍必须上传到当前 proposal/附件版本。
- 服务端计算受影响范围的最低集合；模型只能扩大候选范围，不能缩小必须复核的范围。
- `confirm` 表示责任主体作出受规则允许的确认，不等于无证据通过；`deny` 和 `not_applicable` 都必须保留解释。
- 旧 Requirement、Assessment、EvidenceLink 和 Decision 只可失效/归档，不可物理删除来“刷新状态”。

## Planned API

```text
POST /api/requirements/{requirement_id}/decisions
POST /api/action-items/{action_item_id}/complete
POST /api/projects/{project_id}/re-review
```

## Testing strategy

- 决定合同测试：缺解释、非法枚举、跨项目 Requirement、Agent pending 保护、审计记录和历史追加；
- 行动项合同测试：只能完成 open 项、跨项目拒绝、重复完成幂等/拒绝和审计记录；
- 影响范围测试：proposal、company、tender 三类版本变化分别验证受影响与不受影响集合；
- 集成测试：re-review 先保存 affected IDs，再创建 queued ReviewJob；
- 回归测试：全量 pytest、Ruff、Mypy。

## Non-goals

- 不实现多租户身份系统；当前使用受信任本地工作区的操作者标识；
- 不实现自动上传、自动改写投标文件、自动签章或自动提交；
- 不实现分布式增量计算、向量 RAG 或模型自主决定影响范围；
- 不宣称真实业务模型质量已经验证。

