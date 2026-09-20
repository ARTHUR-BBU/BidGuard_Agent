# Task 8 Constitution Impact

> 状态：OpenAI 主通道与 EasyRouter 显式备用通道已通过独立复审；EasyRouter live smoke 已成功
>
> 变更：建立模型供应商边界与受限连通性 smoke

## Constitution impact: Yes

Task 8 新增了模型供应商配置和 Agents SDK 的运行配置边界。宪法第 13 节明确要求：引入新模型供应商或改变模型调用边界时，必须显式记录影响。本次扩展允许配置中明确写出的 `openai` 或 `easyrouter` 供应商；不支持的供应商会以稳定错误码拒绝，不会静默切换或降级到其他模型。EasyRouter 通过 OpenAI 兼容接口接入，但不因此宣称能力等价。

## 未改变的正式权力

- 本任务没有创建 Evidence、Claim、Assessment 或 DisplayStatus。
- 本任务没有读取、检索或发送招标文件、投标响应、企业材料或其他业务文档文本。
- 本任务没有新增 Agent 工具、写权限、外部执行能力或跨项目访问能力。
- 模型配置错误只阻止 Agent 执行；健康检查、文件浏览和确定性 API 不依赖模型配置。
- `OPENAI_API_KEY` 仅由 Agents SDK 从受控进程环境使用；代码不复制、记录、打印或提交密钥。
- EasyRouter 仅使用 `EASYROUTER_API_KEY`、显式 `EASYROUTER_BASE_URL` 和显式 EasyRouter 模型配置；Key 不复制、记录、打印或提交。
- EasyRouter 只作为显式 provider 选择使用；本任务不实现 OpenAI → EasyRouter 自动故障切换。自动切换规则属于后续独立任务，必须另行审查和批准。

## 新增边界与门禁

1. `resolve_model_name` 只接受 `extraction` 或 `review`，缺失模型以 `MODEL_NOT_CONFIGURED` fail closed；EasyRouter 使用独立的 `EASYROUTER_EXTRACTION_MODEL` / `EASYROUTER_REVIEW_MODEL`。
2. `build_run_config` 只接受显式 `openai` 或 `easyrouter`，未知供应商以 `MODEL_PROVIDER_UNSUPPORTED` fail closed；EasyRouter 缺 Key 或非法 HTTPS 地址分别以 `MODEL_API_KEY_NOT_CONFIGURED` / `MODEL_BASE_URL_INVALID` fail closed。
3. 模型名和 SDK `RunConfig` / `OpenAIProvider` 的组装集中在 provider 模块，Agent 模块不自行选择供应商。
4. 运行配置固定 15 秒模型调用超时和 0 次 SDK 模型重试；调用方必须显式提供 Agent 最大轮数。
5. smoke 命令固定一次 Agent turn、无工具、无业务文本，并只输出 `OK` 或不含异常详情的安全错误码。
6. smoke 支持 `--provider` 与 `--model` 的显式一次性覆盖；默认仍读取 OpenAI 配置。失败不得暴露 API Key、授权头、环境变量、URL 或供应商异常原文。

## 测试证据

- 缺少 extraction/review 模型时拒绝执行。
- extraction 与 review 模型独立解析。
- 未知供应商拒绝且不回退。
- `RunConfig` 使用安装版本 `openai-agents 0.22.2` 的实际 `Runner.run_sync` 兼容路径。
- 伪 Runner 成功路径返回结构化结果，避免把异步 coroutine 当成结果。
- smoke 缺配置时只打印 `ERROR MODEL_NOT_CONFIGURED`，不打印环境值或请求头。
- EasyRouter 离线合同测试验证了 OpenAI 兼容 `OpenAIProvider(base_url=..., api_key=..., use_responses=False)`、独立模型配置、缺 Key、非法地址和显式 smoke 参数；未包含业务文本。
- EasyRouter live smoke 已由项目负责人指定模型后以 `--provider easyrouter --model deepseek-v4-flash` 执行并输出 `OK`；这只证明最小通道可用，不证明业务质量。

## 能力声明

完成本任务表示“模型供应商和一次无业务文本的 Agent 连通性边界”代码已实现，并且 EasyRouter 的最小通道 smoke 已成功；不表示 EasyRouter 真实模型业务质量已验收，也不表示 BidGuard 已经可以进行业务文档审核。
