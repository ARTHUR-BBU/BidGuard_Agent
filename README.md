# BidGuard Agent

BidGuard Agent 面向参与政府采购或企业招标的中小型软件与 IT 服务公司，帮助团队先把投标文件中的合规要点、风险和待确认事项看清楚。

现在已具备一个可验证的前后端开发骨架：后端健康检查和 React 前端都能独立启动。具体业务审核能力将在后续任务中逐步加入。

## 准备环境

请先安装：

- Python 3.14 与 [uv](https://docs.astral.sh/uv/)
- Node.js（建议使用当前 LTS 版本）

如需为未来的 live Agent 运行配置模型，在仓库根目录复制 `.env.example` 的内容到 `backend/.env.local`，再按另行批准的方式填写 API Key。当前健康检查和前端构建不会调用模型，也不需要 API Key。

## 后端

```powershell
cd backend
uv sync
uv run pytest tests/test_health.py -v
uv run ruff check app tests
uv run uvicorn app.main:app --reload --port 8000
```

启动后可访问 <http://localhost:8000/api/health>，应返回服务已就绪的信息。

### 本地数据库升级

后端启动时会自动检查本地 SQLite 数据库。早期开发版数据库如果只是缺少文档内容指纹和对应的唯一性约束，BidGuard 会在一个事务中补齐字段、回填公司资料的内容指纹，并增加防重复约束；已是新结构的数据库不会被重复修改。

自动升级不会擅自删除或合并记录。如果旧数据存在同一项目同类文件重复、同一公司资料内容重复、公司资料并非恰好只有一个版本，或文档归属关系不合法，应用会在启动阶段停止，并列出需要处理的记录，而不是等到上传接口报错。正式或重要数据仍建议在升级前备份。这个轻量升级只面向当前 MVP 的 SQLite 旧结构；PostgreSQL 等服务型数据库不会执行 SQLite 专用语句，后续正式部署应使用独立的版本化迁移工具。

## 前端

```powershell
cd frontend
npm install
npm run build
npm run dev -- --port 5173
```

开发页面地址为 <http://localhost:5173>。

## 当前前端验证

当前尚未配置自动化前端测试脚本；该能力会在后续的 UI 测试任务中加入。现在前端可执行的验证方式是：

```powershell
cd frontend
npm run build
```

## 一次启动前后端

在仓库根目录运行：

```powershell
.\scripts\dev.ps1
```

该命令会在后台启动后端和前端，并打印两个访问地址。

