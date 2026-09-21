# BidGuard Agent

> [中文说明](README.md) | English README

BidGuard Agent is a pre-submission quality gate for teams preparing government-procurement and enterprise bids. It is not a promise of winning. Its purpose is to turn tender requirements, proposal drafts, company evidence, and review rationale into a traceable requirement matrix that exposes missing material, contradictions, ambiguity, and scoring gaps.

## Current status

Completed:

- Tasks 1–5: runnable project foundation, domain states, traceable persistence, project APIs, and safe versioned uploads;
- Task 6: PDF/DOCX parsing with page/section locations and explicit coverage;
- Task 7: deterministic evidence retrieval scoped to a project and authorized document versions;
- Task 8: explicit OpenAI and EasyRouter model-provider boundaries;
- Task 8A: `ReviewContext`, coverage, budgets, authorizations, conflict blocking, and a model-call ledger;
- Task 9 programmatic scope: cited requirement extraction, history preservation, and failure logging.
- Task 10 programmatic scope: guarded page reads, proposal/company evidence search, candidate Assessments, confirmation requests, and ActionItem tools.

Verification: `261 passed` backend tests, Ruff and Mypy pass, and the independent Task 9 review returned `Ready = Yes`.

Not completed: real-business model-quality evaluation, complete tender-package coverage, full Review Agent orchestration, human-decision workflow, the complete review UI, and report export. The current MVP must not be described as an automatic submission, signing, or winning-guarantee product.

## Core principle

> Tender text defines requirements; current evidence supports facts; the LLM interprets and challenges; deterministic software validates and constrains; humans own commitments and final submission.

The model is not the permission system, database, factual adjudicator, or submission authority. Formal status is calculated by the server, and model output must pass project, version, citation, coverage, and authorization gates.

## Architecture position

```text
Document version → traceable parsing → scoped retrieval → ReviewContext
                → bounded Agent → citation gate → Requirement
                → guarded evidence tools → Assessment candidate → human confirmation/review
```

Read the detailed [Task 1–9 development review](docs/development-review-task1-9.md).

Governance documents:

- [BidGuard Constitution](docs/governance/bidguard-constitution.md)
- [LLM positioning, authority, and phased development standard](docs/governance/llm-position-authority-phased-development.md)
- [Development diary](docs/development-diary.md)

## Prerequisites

Python 3.14, [uv](https://docs.astral.sh/uv/), and an LTS Node.js release.

Model API keys are supplied only through a controlled environment. Health checks, file parsing, and deterministic retrieval do not require an API key. Any real business-text call requires separate scope, credential, and evaluation approval.

## Backend

```powershell
cd backend
uv sync
uv run pytest -q
uv run ruff check app tests
uv run mypy app
uv run uvicorn app.main:app --reload --port 8000
```

Health endpoint: <http://localhost:8000/api/health>

## Frontend

```powershell
cd frontend
npm install
npm run build
npm run dev -- --port 5173
```

Development page: <http://localhost:5173>

## Start backend and frontend together

```powershell
.\scripts\dev.ps1
```

## Repository map

- `backend/app/documents/`: safe storage, parsing, and deterministic retrieval;
- `backend/app/agents/`: provider boundary, context, governance, extraction, and future tools;
- `backend/app/services/`: project, upload, and requirement persistence services;
- `backend/tests/`: deterministic, adversarial, and Agent contract tests;
- `docs/governance/`: constitution, LLM standard, and Constitution impact records;
- `docs/development-review-task1-9.md`: stage-by-stage development review.

## Development boundaries

The current MVP is a single-user, trusted local workspace. Multi-tenant authentication, external-system writes, automatic submission, signing, approval, payment, and automatic provider failover require separate authority design, human confirmation, and governance review.
