# Task 13 Constitution Impact

> 状态：人工决定、行动项和增量复核已实现并通过确定性回归；真实业务模型质量仍未验收。
>
> 范围：Human Decision、Agent pending confirmation、ActionItem 完成、版本影响计算和受影响范围复核。

## Constitution impact: Yes

Task 13 正式引入人工决定权和版本变化后的复核语义，因此改变了“谁可以影响后续流程”和“哪些 Assessment 仍然是当前结论”的工程边界。但它没有把人工点击变成文档证据，也没有扩大 LLM 或外部执行权限。

## 已落地的硬边界

- 人工决定只能是 `confirm`、`deny` 或 `not_applicable`，解释和责任人必填；
- 每次决定都追加新记录，不覆盖旧决定，并写入项目审计事件；
- Agent 的 `pending` 确认请求单独持久化，不能被静默删除；
- 有决定历史的 Requirement 不能物理删除，只能停用并保留历史；
- ActionItem 完成只记录人的处理动作，不直接改变正式 DisplayStatus；
- 书面材料要求仍必须由当前 proposal/附件版本中的 Evidence 支撑，Human Decision 不冒充 EvidenceLink；
- proposal/company/tender 版本影响由服务端根据版本和 EvidenceLink 计算，模型不能缩小最低复核集合；
- 受影响 Assessment 标记为非当前，旧证据和旧要求保留；
- tender 变化停用旧 Requirement 矩阵，新的审核任务从新 tender 版本提取当前矩阵；
- re-review 在创建 ReviewJob 前把 affected Requirement ID 持久化到 ReviewRun。

## 尚未实现且明确禁止冒充完成的内容

- 尚未实现多租户身份、生产级操作者认证和细粒度权限；
- 尚未实现自动改写、上传、签章、审批或投标提交；
- 尚未证明真实业务文本的模型质量、复核召回率或成本；
- 人工接受风险仍不等于要求已满足，也不能替代招标要求的书面证据。

## 测试证据

- Task 13 专项测试 8 项通过；
- 后端全量测试 287 项通过；
- Ruff 通过；
- Mypy 对 44 个源文件检查通过。
