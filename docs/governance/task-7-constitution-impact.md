# Task 7 Constitution Impact

> 状态：Task 7 修订已通过独立复审（Critical 0 / Important 0 / Minor 0，Ready = Yes）
>
> 变更：模型无关证据候选检索的安全边界修订

## Constitution impact: Yes

Task 7 原本只按调用方传入的 `allowed_document_version_ids` 检索候选块。独立复审发现，这会把跨项目授权责任完全留给调用方，无法满足宪法第 4.4、7.3、10 节要求的服务端项目与资料边界。因此本次修订显式改变了检索候选进入服务端门禁前的授权约束：`project_id` 必须由调用方提供，且服务端验证每一个允许的 `DocumentVersion` 都属于该项目；任一版本不存在、属于其他项目或 ID 格式不合法，检索均以稳定的 `search_scope_invalid` 错误 fail closed。

本次同时加强了 Coverage 边界。`coverage_complete` 不再由 `parse_status == "parsed"` 单独推断，而是防御性检查 Task 6 持久化的 `parse_coverage`：缺少覆盖对象、存在失败页、OCR 页、非空白覆盖问题或 `needs_ocr` 时均不得宣称完整。Task 6 明确标记的 `blank_page` 是“已检查但无正文”，因此单独存在时仍可以是完整覆盖；`partial_failure` 永远返回候选但标记为不完整。

## 未改变的正式权力

- 检索结果仍然只是 `SearchResult` / Evidence candidate，不是文档 `EvidenceLink`。
- 不创建 Requirement、Claim、Assessment，不计算 `DisplayStatus`，不产生“已满足”。
- 不允许跨项目、未授权版本或未解析内容进入候选上下文。
- 不调用 LLM，不扩大模型可见范围、工具权限或外部执行能力。
- 不推断 current/latest 版本；调用方仍必须提供当前 ReviewContext 允许的版本集合。
- 不修改历史文档版本、解析结果或真实数据库。

## 新增服务端门禁

1. `project_id` 是强制范围参数，不能由模型输入或隐式推断。
2. `allowed_document_version_ids` 必须全部存在并属于 `project_id`；集合中任意无效项都会拒绝整次检索。
3. SQL 查询再次限定 `Document.project_id == project_id`，形成验证与读取双重边界。
4. 只读取 `parsed` / `partial_failure` 版本；pending、parsing、failed、needs-OCR 版本不可检索。
5. `coverage_complete` 由持久化 Coverage 防御性复算，缺失或异常按不完整处理。
6. 检索结果保留版本、页、章节、角色、解析状态和覆盖完整性元数据。
7. 可检索的 `parsed` 版本必须保存四个页面覆盖列表；DOCX 也必须明确保存四个空列表。可宣称完整的 PDF 页数和 DOCX 章节数必须为正数，且统计范围自洽。

## 测试证据

- 正常 lexical 检索、角色过滤、版本限定、稳定排序、limit、空查询和 zero-score。
- 不存在版本 ID fail closed。
- 跨项目版本 ID fail closed，并使用稳定错误码 `search_scope_invalid`。
- `parsed` + 缺失 Coverage、失败页 Coverage、OCR Coverage 均不得标记完整。
- 仅有 `blank_page` Coverage issue 按 Task 6 语义视为已完成覆盖。
- 合法 `partial_failure` 可返回候选但始终不完整。
- 全部结果不创建 EvidenceLink 或正式状态。

## 能力声明

完成本修订只表示“有项目范围和覆盖边界的确定性候选检索”代码已实现并通过确定性测试；不表示 LLM 已接入，不表示证据相关性已自动判定，也不表示真实投标业务质量已经验证。
