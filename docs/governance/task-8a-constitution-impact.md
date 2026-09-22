# Task 8A Constitution Impact

> 状态：程序化 LLM 治理门禁已实现，尚未向真实模型发送业务文档文本
>
> 范围：调用账本、Coverage、ReviewContext、运行预算、项目与企业资料授权、招标包成员和冲突阻断

## Constitution impact: Yes

Task 8A 把宪法中关于“谁能看什么、模型能做什么、失败如何留下痕迹”的要求落成了服务端对象和测试。它是进入 Task 9 真实业务文本调用前的必要门禁，不等于真实模型审核质量已经验收。

## 已落地的硬边界

- `ReviewContext` 由服务端创建，固定项目、ReviewRun、招标版本、企业资料版本、可见 chunk 和运行预算。模型提供的项目或版本标识只能缩小范围，不能扩大范围。
- `Coverage` 记录已纳入和未检查的范围；部分失败或待确认必须写出未检查范围，不能伪装成完整检查。
- `LLMCallRecord` 只保存 provider/model、Prompt 版本与哈希、对象 ID、版本/片段范围、Coverage、结果、稳定 reason code、工具摘要、耗时、重试、用量和停止原因；默认不保存完整 Prompt 或文档正文，也不保存密钥。
- `ProjectCompanyEvidence` 记录企业资料由谁、何时、为何选入项目；构建上下文时再次核对资料确实是 company 文档，跨项目或非企业资料 fail closed。
- `TenderPackageMember` 记录主文件、附件、补遗、澄清和模板的纳入/排除、效力顺序和冲突状态。未解决冲突不进入模型上下文，必须转人工确认。
- `CallBudget` 和 `AgentRuntimeLimits` 对轮次、工具调用、重试、超时、token、费用和批量设置正向上限；达到上限后停止，不自动提高。

## 尚未实现且明确禁止冒充完成的内容

- 没有创建 Requirement、EvidenceLink、Claim、Assessment 或 DisplayStatus。
- 没有实现要求提取 Agent，也没有把招标文件、投标响应或企业资料文本发送给 OpenAI、EasyRouter 或其他模型。
- 没有启用供应商自动故障切换；OpenAI 与 EasyRouter 仍必须显式选择。
- 没有宣称完整招标包覆盖、真实模型业务质量或可直接提交投标。

## 测试证据

- Task 8A 专项治理测试覆盖 Coverage fail-closed、Prompt 哈希与敏感字段缺失、预算耗尽、项目/版本越权、企业资料授权、补遗冲突、调用账本持久化、版本删除保护、输入对象授权和治理表创建。
- 全量测试通过 **244 项**；Ruff（新增范围）与 Mypy 通过；真实 `backend/bidguard.db` 未触碰，哈希仍为 `B43DDC1DF8FFE938F838EABDDCC5A214B9BCC762EBB2E5A2C2B81E14A338B9CB`。
- 独立攻击式复审最终结论：Critical 0、Important 0、Minor 0，`Ready = Yes`。
- 这些测试证明的是程序化治理控制，不是模型输出质量。Task 9 仍必须先经过独立复审，再做单文件、有界、可回放的真实提取评测。
