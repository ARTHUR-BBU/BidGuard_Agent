# Spec: Task 12 持久化审核任务 Worker

## Objective

把 Task 11 的一次性审核编排放进一个可持久化、可恢复的后台任务系统。用户提交一次审核后，后端创建 ReviewJob；Worker 从数据库领取任务、记录阶段、执行审核，并在进程重启后把中断任务重新排队。

本任务服务于单用户本地 MVP，不引入 Redis、Celery 或新的外部队列。数据库中的 `ReviewJob` 是任务事实来源，FastAPI lifespan 只负责启动和停止一个进程内 asyncio Worker。

## Assumptions

1. 当前应用仍是单用户、受信任本地工作区；本任务不新增登录、多租户或权限系统。
2. 当前项目每个角色只有一个 Document，但一个 Document 可以有多个不可变版本；审核使用当前选定的 tender 版本和当前项目文件版本指纹。
3. 文件解析和 Requirement 提取沿用已有确定性解析、Task 9 引用门禁和 Task 11 `run_review`，Worker 不自行改变正式状态。
4. 真实模型调用仍然由配置控制；测试使用注入式 handler/runner，不因启动 Worker 自动消耗 API 额度。

## User-visible success criteria

- `POST /api/projects/{project_id}/reviews` 创建一个 queued ReviewJob，并返回 job/run 标识；相同项目和活动文件版本已有 queued/running job 时返回已有任务。
- Worker 原子领取一个 queued job，将它变成 running 并增加 `attempt_count`。
- Worker 在 `parsing`、`extracting`、`reviewing`、`awaiting_confirmation`、`completed` 或 `failed` 阶段之间留下真实状态，不生成虚假的百分比。
- 进程启动时，遗留的 running job 会回到 queued，错误原因被保留，下一次领取时 attempt_count 增加。
- 已完成的 ReviewRun、Assessment 和调用台账在 Worker 退出或重新启动后仍然存在。
- `GET /api/projects/{project_id}/reviews/latest` 返回最新任务状态；要求列表和要求详情接口返回当前可追溯 Assessment/Evidence 信息。

## Commands

```powershell
cd backend
uv run pytest tests/test_review_jobs.py -v
uv run pytest -q
uv run ruff check app tests
uv run mypy app
```

## Project structure

- `backend/app/jobs/worker.py`：原子领取、重排、运行循环和停止信号；
- `backend/app/jobs/handlers.py`：解析、要求提取、受控审核和最终状态处理；
- `backend/app/api/reviews.py`：审核启动、任务状态、要求列表和要求详情 API；
- `backend/app/persistence/models.py`：ReviewJob 的任务关联、版本指纹和尝试信息；
- `backend/app/persistence/schema.py`：旧 SQLite 的安全增量迁移；
- `backend/app/main.py`：FastAPI lifespan 中启动和停止一个 Worker；
- `backend/tests/test_review_jobs.py`：入队、领取、恢复、幂等和 API 合约测试。

## Code style and contract example

任务状态转换必须由服务端函数集中执行：

```python
job = claim_next_job(session)
if job is None:
    return False
job.stage = "reviewing"
session.commit()
```

模型输出不能直接修改 `ReviewJob.status`；只有 Worker/handler 在保存结果和审计事件后才能推进任务状态。

## Testing strategy

- 单元测试：状态转换、原子领取、重复启动去重、失败重排和 attempt_count；
- 集成测试：FastAPI lifespan 启动/停止、审核 API 和数据库持久化；
- 回归测试：全量后端 pytest、Ruff、Mypy；
- 不在本任务中宣称真实模型业务质量，真实模型调用必须继续经过 Task 8A/Task 11 门禁。

## Boundaries

- Always：任务状态先写数据库；每次领取和状态转移在一个事务中；停止时不删除任务；失败时保留错误和已完成结果；
- Ask first：新增外部队列、改变数据库持久化模型、自动重试供应商、扩大到多租户部署；
- Never：静默丢弃 running 任务、伪造完成百分比、把部分完成标成 completed、绕过 ReviewContext 或自动提交投标。

## Architecture decisions

### 选择数据库持久化 + 单进程 Worker

这符合当前单用户 MVP 的部署现实，能够证明“有记性、可恢复”的核心能力，同时避免在还没有真实任务量证据前引入 Redis/Celery 的运维成本。

### 选择阶段状态而不是虚假百分比

当前系统无法证明每个文档和每个模型调用的准确完成比例，因此前端只展示真实阶段和任务状态，不显示看似精确但没有证据来源的百分比。

## Non-goals

- 不实现分布式 Worker、优先级队列、租约心跳或多租户隔离；
- 不实现人工决定 API、增量复核和报告导出；
- 不自动切换模型供应商；
- 不把 Job 完成等同于投标合规或可直接提交。

## Open questions for later tasks

- Task 13 如何把 `awaiting_confirmation` 接到人工决定和增量复核；
- 真实材料回归集如何进入 Worker 的评测和监控；
- 任务并发和分布式部署是否有足够业务证据支撑扩容。
