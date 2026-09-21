# Task 10 Constitution Impact

> 状态：受控证据工具和 unsupported-pass gate 已实现并通过确定性测试；尚未运行真实业务模型审核
>
> 范围：单一 ReviewContext 下的证据读取、候选 Assessment、人工确认请求和 ActionItem

## Constitution impact: Yes

Task 10 让 Agent 获得了有限的读工具和候选写工具，因此改变了 LLM 可触达的业务边界。工具全部由服务端 `ReviewContext` 闭包绑定，模型不能通过参数扩大项目、版本或企业资料范围。

## 已落地的硬边界

- `get_document_page` 只返回授权版本指定页的紧凑片段，不返回整份文档；跨项目、未授权版本和未授权企业资料直接拒绝。
- `search_proposal_evidence` 只搜索当前 ReviewContext 已授权且属于当前项目的 proposal 版本。
- `search_company_evidence` 只搜索当前项目明确选入且仍 active 的 company evidence；系统不会因为资料存在于数据库就自动使用。
- `save_assessment` 必须验证 Requirement 属于当前项目且仍 active；`MATCHED` 没有 Evidence 时触发 `UnsupportedPassError`。
- Assessment 引用必须来自 active scope、proposal/company 文档，页码或章节必须存在，quote 必须真实出现在对应 chunk 中。
- 正式 `display_status` 由 `calculate_display_status` 服务端计算，忽略模型可能提出的状态文本。
- `request_user_confirmation` 只创建可审计的待确认请求，不替代文档 Evidence，不自动改变正式状态。
- `create_action_item` 只创建 open ActionItem 和审计事件，不代表问题已经解决。
- 工具通过 Agents SDK `function_tool` 暴露，但数据库会话和 ReviewContext 不作为模型参数传入，权限由服务端闭包持有。

## 尚未实现且明确禁止冒充完成的内容

- 尚未实现完整 Review Agent 编排、后台任务、人工决定 API 或增量复核。
- 尚未执行真实业务文本模型调用，也没有证明 Assessment 的真实业务准确率。
- 尚未实现自动提交、签章、审批、付款或外部系统写入。
- 检索分数只是候选排序信号，不是事实、相关性结论或“已满足”依据。

## 测试证据

- Task 10 专项测试 **6 项通过**，覆盖项目范围、页面读取、proposal/company 检索、未授权企业资料、unsupported pass、过期证据、服务端状态计算、确认请求、ActionItem 和 SDK tool wrapper。
- 全量测试通过 **267 项**；Ruff 与 Mypy 通过。
- 本阶段仍只证明程序化工具边界，不证明真实模型业务质量。
