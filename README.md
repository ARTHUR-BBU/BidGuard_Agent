# BidGuard Agent

> 中文说明 | [English README](README.en.md)

BidGuard Agent 是一个面向政府采购和企业招标团队的提交前质量门。它的目标不是承诺中标，而是把招标要求、投标响应、企业资料和审核依据组织成可追溯的要求矩阵，帮助团队发现缺失、矛盾、歧义和潜在失分点。

## 当前状态

已完成：

- Task 1—5：可运行工程、领域状态、可追溯数据库、项目 API、安全版本化上传；
- Task 6：保留页码/章节和 Coverage 的 PDF/DOCX 解析；
- Task 7：有项目和版本边界的确定性证据检索；
- Task 8：OpenAI 与 EasyRouter 的显式模型通道；
- Task 8A：ReviewContext、Coverage、预算、授权、冲突阻断和调用台账；
- Task 9 程序化部分：带引用门禁的要求提取、历史保留、失败记账。
- Task 10 程序化部分：受控页面读取、投标/企业证据检索、Assessment 候选、人工确认请求和 ActionItem 工具。

验证结果：后端全量测试 `268 passed`，Ruff 和 Mypy 通过；Task 9 独立复核 `Ready = Yes`，Task 10 已完成程序化工具门禁。

尚未完成：真实业务文本的模型质量评测、完整招标包覆盖、完整 Review Agent 编排、人工决定闭环、完整前端审核流程和报告导出。当前不能宣传为自动投标、自动签章或中标保证工具。

## 核心原则

> 招标原文定义要求，当前证据支撑事实，LLM 负责理解与质疑，程序负责验证与制衡，人工负责承诺与最终提交。

模型不是权限系统、数据库、事实裁判或提交责任主体。正式状态由服务端规则计算，模型输出必须经过项目、版本、引用、Coverage 和权限门禁。

## 架构位置

```text
文件版本 → 可定位解析 → 项目范围检索 → ReviewContext
        → 受限 Agent → 引用门禁 → Requirement
        → 受控证据工具 → Assessment 候选 → 人工确认/复核
```

详细阶段复盘见：[Task 1—9 阶段复盘](docs/development-review-task1-9.md)。

治理总纲：

- [BidGuard Constitution](docs/governance/bidguard-constitution.md)
- [LLM 定位、权限与分阶段开发规范](docs/governance/llm-position-authority-phased-development.md)
- [开发日记](docs/development-diary.md)

## 准备环境

需要 Python 3.14、[uv](https://docs.astral.sh/uv/) 和 Node.js LTS。

模型 API Key 只通过受控环境配置。健康检查、文件解析和确定性检索不需要 API Key。真实业务文本调用必须经过单独的范围、凭据和评测确认。

## 后端

```powershell
cd backend
uv sync
uv run pytest -q
uv run ruff check app tests
uv run mypy app
uv run uvicorn app.main:app --reload --port 8000
```

健康检查：<http://localhost:8000/api/health>

## 前端

```powershell
cd frontend
npm install
npm run build
npm run dev -- --port 5173
```

开发页面：<http://localhost:5173>

## 一次启动前后端

```powershell
.\scripts\dev.ps1
```

## 目录说明

- `backend/app/documents/`：安全存储、解析和确定性检索；
- `backend/app/agents/`：模型供应商、上下文、治理、要求提取和后续工具；
- `backend/app/services/`：项目、上传、要求持久化等业务服务；
- `backend/tests/`：确定性、对抗式和 Agent 合约测试；
- `docs/governance/`：项目宪法、LLM 规范和 Constitution impact 记录；
- `docs/development-review-task1-9.md`：阶段性开发复盘。

## 开发边界

当前是单用户、受信任本地工作区 MVP。多租户认证、外部系统写入、自动提交、签章、审批、付款和自动供应商切换都需要单独的权限设计、人工确认和治理审查。
