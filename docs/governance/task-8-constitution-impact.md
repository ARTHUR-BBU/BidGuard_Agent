# Task 8 Constitution Impact

> 状态：代码实现已通过独立复审（Critical 0 / Important 0 / Minor 0，Ready = Yes）；live smoke 待明确模型配置
>
> 变更：建立模型供应商边界与受限连通性 smoke

## Constitution impact: Yes

Task 8 新增了模型供应商配置和 Agents SDK 的运行配置边界。宪法第 13 节明确要求：引入新模型供应商或改变模型调用边界时，必须显式记录影响。本任务目前只允许配置中明确写出的 `openai` 供应商；不支持的供应商会以稳定错误码拒绝，不会静默切换或降级到其他模型。

## 未改变的正式权力

- 本任务没有创建 Evidence、Claim、Assessment 或 DisplayStatus。
- 本任务没有读取、检索或发送招标文件、投标响应、企业材料或其他业务文档文本。
- 本任务没有新增 Agent 工具、写权限、外部执行能力或跨项目访问能力。
- 模型配置错误只阻止 Agent 执行；健康检查、文件浏览和确定性 API 不依赖模型配置。
- `OPENAI_API_KEY` 仅由 Agents SDK 从受控进程环境使用；代码不复制、记录、打印或提交密钥。

## 新增边界与门禁

1. `resolve_model_name` 只接受 `extraction` 或 `review`，缺失模型以 `MODEL_NOT_CONFIGURED` fail closed。
2. `build_run_config` 只接受显式 `openai`，未知供应商以 `MODEL_PROVIDER_UNSUPPORTED` fail closed。
3. 模型名和 SDK `RunConfig` / `OpenAIProvider` 的组装集中在 provider 模块，Agent 模块不自行选择供应商。
4. 运行配置固定 15 秒模型调用超时和 0 次 SDK 模型重试；调用方必须显式提供 Agent 最大轮数。
5. smoke 命令固定一次 Agent turn、无工具、无业务文本，并只输出 `OK` 或不含异常详情的安全错误码。
6. smoke 失败不得暴露 API Key、授权头、环境变量、URL 或供应商异常原文。

## 测试证据

- 缺少 extraction/review 模型时拒绝执行。
- extraction 与 review 模型独立解析。
- 未知供应商拒绝且不回退。
- `RunConfig` 使用安装版本 `openai-agents 0.22.2` 的实际 `Runner.run_sync` 兼容路径。
- 伪 Runner 成功路径返回结构化结果，避免把异步 coroutine 当成结果。
- smoke 缺配置时只打印 `ERROR MODEL_NOT_CONFIGURED`，不打印环境值或请求头。
- 真实 live smoke 已按安全路径尝试，但因未配置 `REVIEW_MODEL` 返回 `ERROR MODEL_NOT_CONFIGURED`，未发出 API 请求；待项目负责人明确模型名后再运行一次。

## 能力声明

完成本任务只表示“模型供应商和一次无业务文本的 Agent 连通性边界”代码已实现并通过离线测试；不表示真实模型已验收，不表示模型具备投标审查质量，也不表示 BidGuard 已经可以进行业务文档审核。
