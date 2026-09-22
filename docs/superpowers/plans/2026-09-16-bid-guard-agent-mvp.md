# BidGuard Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user web MVP that ingests tender documents, proposal drafts, and reusable company evidence; creates a traceable requirement matrix; detects bid-invalidating risks and scoring gaps; supports human confirmation and incremental re-review; and proves quality with a saved evaluation harness.

**Architecture:** A React/TypeScript frontend calls a FastAPI backend. The backend owns projects, versioned files, parsing, state rules, persistence, and a durable-in-database job loop; OpenAI Agents SDK owns bounded extraction and review turns through strict tools. Every model conclusion is stored with current-version evidence, and deterministic gates prevent unsupported “satisfied” states.

**Tech Stack:** Python 3.14, uv, FastAPI, SQLAlchemy 2, Pydantic 2, SQLite, OpenAI Agents SDK, pypdf, python-docx, React, TypeScript, Vite, Vitest, Testing Library, Playwright.

**Governance update (2026-09-18):** This plan is subordinate to `docs/governance/bidguard-constitution.md` and `docs/governance/llm-position-authority-phased-development.md`. Tasks 6-7 are deterministic foundation work and may proceed. The connectivity-only smoke in Task 8 may send no business document text, but Task 9 must not send tender text to a live model until the mandatory governance gate after Task 8 is complete. Planning, code-complete, mock-tested, live-model-evaluated, and real-business-validated capabilities must be reported separately.

---

## Execution prerequisites

1. Before installing or running OpenAI-backed code, follow the `openai-platform-api-key` credential gate. A usable process-level `OPENAI_API_KEY` was detected during design, but the user must explicitly choose whether to reuse it or create a project-specific key.
2. Run all commands from `D:\arthur-agent-academy\bid-guard-agent` unless a step gives another working directory.
3. Keep the approved design open while implementing: `docs/superpowers/specs/2026-09-16-bid-guard-agent-design.md`.
4. Do not add multi-user roles, automatic bid submission, automatic signing, full proposal generation, CRM/OA integrations, or a workflow builder.
5. Commit after every task. Do not combine tasks into one large commit.

## Planned file structure

```text
bid-guard-agent/
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI factory and lifecycle
│   │   ├── settings.py             # Environment-backed configuration
│   │   ├── db.py                   # Engine, session, schema bootstrap
│   │   ├── api/
│   │   │   ├── health.py           # Health endpoint
│   │   │   ├── projects.py         # Project CRUD and summary
│   │   │   ├── documents.py        # Upload and version endpoints
│   │   │   ├── reviews.py          # Start/status/matrix/re-review endpoints
│   │   │   └── decisions.py        # Human confirmation and action endpoints
│   │   ├── domain/
│   │   │   ├── enums.py            # Stable state vocabularies
│   │   │   ├── schemas.py          # API and agent structured outputs
│   │   │   └── status_rules.py     # Deterministic display-state rules
│   │   ├── persistence/
│   │   │   ├── models.py           # SQLAlchemy entities
│   │   │   └── repositories.py     # Focused persistence operations
│   │   ├── documents/
│   │   │   ├── storage.py          # Safe local storage and hashing
│   │   │   ├── parser.py           # PDF/DOCX dispatch
│   │   │   ├── pdf.py              # Page-preserving PDF parsing
│   │   │   ├── docx.py             # Heading-preserving DOCX parsing
│   │   │   └── search.py           # Model-independent lexical retrieval
│   │   ├── agents/
│   │   │   ├── provider.py         # Model-provider boundary
│   │   │   ├── extraction.py       # Requirement extraction run
│   │   │   ├── review.py           # Evidence review run
│   │   │   ├── tools.py            # Strict retrieval/state tools
│   │   │   └── gates.py            # Citation and unsupported-pass gates
│   │   ├── services/
│   │   │   ├── projects.py
│   │   │   ├── ingestion.py
│   │   │   ├── requirements.py
│   │   │   ├── reviews.py
│   │   │   ├── decisions.py
│   │   │   └── reports.py
│   │   └── jobs/
│   │       ├── worker.py            # Persisted job polling loop
│   │       └── handlers.py          # Ingest/extract/review/re-review handlers
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── fixtures/
│   │   ├── test_health.py
│   │   ├── test_status_rules.py
│   │   ├── test_projects_api.py
│   │   ├── test_document_versions.py
│   │   ├── test_parsers.py
│   │   ├── test_search.py
│   │   ├── test_agent_gates.py
│   │   ├── test_review_jobs.py
│   │   ├── test_decisions_api.py
│   │   └── test_reports.py
│   └── evals/
│       ├── cases.jsonl
│       ├── graders.py
│       ├── run_local.py
│       └── results/.gitkeep
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── app.tsx
│   │   ├── api/client.ts
│   │   ├── api/types.ts
│   │   ├── pages/projects-page.tsx
│   │   ├── pages/new-review-page.tsx
│   │   ├── pages/review-page.tsx
│   │   ├── pages/issue-page.tsx
│   │   ├── pages/evidence-library-page.tsx
│   │   ├── components/status-badge.tsx
│   │   ├── components/requirement-table.tsx
│   │   └── styles.css
│   └── tests/
│       ├── setup.ts
│       ├── projects-page.test.tsx
│       ├── review-page.test.tsx
│       └── issue-page.test.tsx
├── e2e/
│   └── bid-review.spec.ts
├── samples/
│   └── README.md
├── scripts/
│   ├── dev.ps1
│   └── verify.ps1
├── .env.example
└── README.md
```

## Phase 1 — Runnable foundation and deterministic core

### Task 1: Scaffold backend, frontend, and one verified health path

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/settings.py`
- Create: `backend/app/api/health.py`
- Create: `backend/tests/test_health.py`
- Create: `frontend/` using Vite React TypeScript template
- Create: `.env.example`
- Create: `scripts/dev.ps1`
- Modify: `README.md`

- [x] **Step 1: Initialize the backend package**

Run:

```powershell
New-Item -ItemType Directory -Path backend
Set-Location backend
uv init --app --python 3.14
uv add fastapi "uvicorn[standard]" pydantic-settings sqlalchemy python-multipart pypdf python-docx openai-agents
uv add --dev pytest pytest-asyncio httpx ruff mypy
```

Expected: `backend/pyproject.toml` and `backend/uv.lock` exist; no OpenAI request is made.

- [x] **Step 2: Write the failing health test**

Create `backend/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_ready() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "bid-guard-api"}
```

- [x] **Step 3: Run the health test and verify failure**

Run: `uv run pytest tests/test_health.py -v`

Expected: FAIL because `app.main` does not exist.

- [x] **Step 4: Implement settings, route, and app factory**

Create `backend/app/settings.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BidGuard API"
    database_url: str = "sqlite:///./bidguard.db"
    storage_root: Path = Path("./uploads")
    model_provider: str = "openai"
    extraction_model: str = ""
    review_model: str = ""
    max_agent_turns: int = 12
    max_tool_calls: int = 40

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `backend/app/api/health.py`:

```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ready", "service": "bid-guard-api"}
```

Create `backend/app/main.py`:

```python
from fastapi import FastAPI

from app.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="BidGuard API")
    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
```

- [x] **Step 5: Run backend checks**

Run:

```powershell
uv run pytest tests/test_health.py -v
uv run ruff check app tests
```

Expected: PASS; Ruff reports no errors.

- [x] **Step 6: Scaffold and verify the frontend**

Run from repository root:

```powershell
npm create vite@latest frontend -- --template react-ts
Set-Location frontend
npm install
npm install react-router-dom
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
npm run build
```

Expected: Vite production build succeeds.

- [x] **Step 7: Add safe environment template and development launcher**

Create `.env.example`:

```dotenv
OPENAI_API_KEY=
MODEL_PROVIDER=openai
EXTRACTION_MODEL=
REVIEW_MODEL=
DATABASE_URL=sqlite:///./bidguard.db
STORAGE_ROOT=./uploads
```

Create `scripts/dev.ps1`:

```powershell
$repoRoot = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath "uv" -ArgumentList @("run", "uvicorn", "app.main:app", "--reload", "--port", "8000") -WorkingDirectory "$repoRoot\backend" -WindowStyle Hidden
Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "--port", "5173") -WorkingDirectory "$repoRoot\frontend" -WindowStyle Hidden
Write-Host "BidGuard API: http://localhost:8000/api/health"
Write-Host "BidGuard web: http://localhost:5173"
```

Do not put real secrets in `.env.example`.

- [x] **Step 8: Update the project README with verified commands**

Add exact setup, test, and development commands for both packages. State that live Agent execution requires a separately approved key decision.

- [x] **Step 9: Commit the foundation**

```powershell
git add backend frontend scripts .env.example README.md
git commit -m "chore: scaffold BidGuard full-stack app"
```

### Task 2: Define domain states and deterministic display rules

**Files:**
- Create: `backend/app/domain/enums.py`
- Create: `backend/app/domain/schemas.py`
- Create: `backend/app/domain/status_rules.py`
- Create: `backend/tests/test_status_rules.py`

- [x] **Step 1: Write failing status-rule tests**

Create `backend/tests/test_status_rules.py`:

```python
import pytest

from app.domain.enums import DisplayStatus, EvidenceState, Severity
from app.domain.status_rules import calculate_display_status


@pytest.mark.parametrize(
    ("mandatory", "evidence", "severity", "needs_confirmation", "expected"),
    [
        (True, EvidenceState.MISSING, Severity.CRITICAL, False, DisplayStatus.HIGH_RISK),
        (True, EvidenceState.PARTIAL, Severity.WARNING, False, DisplayStatus.NEEDS_EVIDENCE),
        (False, EvidenceState.PARTIAL, Severity.OPPORTUNITY, False, DisplayStatus.OPTIMIZE),
        (True, EvidenceState.MATCHED, Severity.NONE, False, DisplayStatus.SATISFIED),
        (True, EvidenceState.UNCERTAIN, Severity.WARNING, True, DisplayStatus.NEEDS_CONFIRMATION),
    ],
)
def test_calculate_display_status(
    mandatory: bool,
    evidence: EvidenceState,
    severity: Severity,
    needs_confirmation: bool,
    expected: DisplayStatus,
) -> None:
    assert calculate_display_status(mandatory, evidence, severity, needs_confirmation) == expected


def test_missing_evidence_can_never_be_satisfied() -> None:
    result = calculate_display_status(True, EvidenceState.MISSING, Severity.NONE, False)
    assert result is not DisplayStatus.SATISFIED
```

- [x] **Step 2: Run tests and verify import failure**

Run: `uv run pytest tests/test_status_rules.py -v`

Expected: FAIL because domain modules do not exist.

- [x] **Step 3: Implement stable enums**

Create `backend/app/domain/enums.py`:

```python
from enum import StrEnum


class DocumentRole(StrEnum):
    TENDER = "tender"
    PROPOSAL = "proposal"
    COMPANY = "company"


class RequirementKind(StrEnum):
    QUALIFICATION = "qualification"
    SUBSTANTIAL = "substantial"
    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    SCORING = "scoring"


class EvidenceState(StrEnum):
    MISSING = "missing"
    PARTIAL = "partial"
    MATCHED = "matched"
    UNCERTAIN = "uncertain"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    OPPORTUNITY = "opportunity"
    NONE = "none"


class DisplayStatus(StrEnum):
    HIGH_RISK = "high_risk"
    NEEDS_EVIDENCE = "needs_evidence"
    OPTIMIZE = "optimize"
    SATISFIED = "satisfied"
    NEEDS_CONFIRMATION = "needs_confirmation"


class ReviewRunStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    REVIEWING = "reviewing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
```

- [x] **Step 4: Implement the deterministic status function**

Create `backend/app/domain/status_rules.py`:

```python
from app.domain.enums import DisplayStatus, EvidenceState, Severity


def calculate_display_status(
    mandatory: bool,
    evidence: EvidenceState,
    severity: Severity,
    needs_confirmation: bool,
) -> DisplayStatus:
    if needs_confirmation or evidence is EvidenceState.UNCERTAIN:
        return DisplayStatus.NEEDS_CONFIRMATION
    if mandatory and evidence is EvidenceState.MISSING:
        return DisplayStatus.HIGH_RISK
    if severity is Severity.CRITICAL:
        return DisplayStatus.HIGH_RISK
    if evidence in {EvidenceState.MISSING, EvidenceState.PARTIAL}:
        return DisplayStatus.OPTIMIZE if severity is Severity.OPPORTUNITY else DisplayStatus.NEEDS_EVIDENCE
    return DisplayStatus.SATISFIED
```

- [x] **Step 5: Add shared structured schemas**

Create `backend/app/domain/schemas.py` with exact models used by both API and Agent code:

```python
from pydantic import BaseModel, Field

from app.domain.enums import EvidenceState, RequirementKind, Severity


class SourceCitation(BaseModel):
    document_version_id: int
    page_number: int | None = None
    section_path: str | None = None
    quote: str = Field(min_length=1, max_length=2000)


class RequirementCandidate(BaseModel):
    text: str = Field(min_length=3)
    kind: RequirementKind
    mandatory: bool
    requested_evidence: list[str] = Field(default_factory=list)
    citation: SourceCitation


class RequirementBatch(BaseModel):
    requirements: list[RequirementCandidate]


class AssessmentCandidate(BaseModel):
    requirement_id: int
    evidence_state: EvidenceState
    severity: Severity
    needs_confirmation: bool
    reasoning: str = Field(min_length=3, max_length=4000)
    evidence: list[SourceCitation] = Field(default_factory=list)
    recommendation: str = Field(default="", max_length=4000)
```

- [x] **Step 6: Run tests and commit**

```powershell
uv run pytest tests/test_status_rules.py -v
uv run ruff check app tests
git add backend/app/domain backend/tests/test_status_rules.py
git commit -m "feat: define review states and status rules"
```

Expected: all tests pass.

### Task 3: Add the traceable persistence model

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/persistence/models.py`
- Create: `backend/app/persistence/repositories.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_persistence.py`
- Modify: `backend/app/main.py`

- [x] **Step 1: Write a failing traceability test**

Create `backend/tests/test_persistence.py`:

```python
from app.domain.enums import DocumentRole, RequirementKind
from app.persistence.models import BidProject, Document, DocumentVersion, Requirement


def test_requirement_points_to_exact_tender_version(db_session) -> None:
    project = BidProject(name="数据治理平台", deadline_at=None)
    document = Document(project=project, role=DocumentRole.TENDER, display_name="招标文件.pdf")
    version = DocumentVersion(document=document, version_number=1, sha256="a" * 64, storage_path="tender/a.pdf")
    requirement = Requirement(
        project=project,
        source_version=version,
        source_page=12,
        source_quote="必须提供授权委托书",
        text="提供签字盖章的授权委托书",
        kind=RequirementKind.SUBSTANTIAL,
        mandatory=True,
    )
    db_session.add(requirement)
    db_session.commit()

    assert requirement.source_version.sha256 == "a" * 64
    assert requirement.source_page == 12
```

- [x] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_persistence.py -v`

Expected: FAIL because persistence models do not exist.

- [x] **Step 3: Implement database session lifecycle**

Create `backend/app/db.py`:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
```

- [x] **Step 4: Implement exact SQLAlchemy entities**

Create `backend/app/persistence/models.py`. Include these tables and foreign keys:

```text
bid_projects
documents -> bid_projects (nullable only for reusable company evidence)
document_versions -> documents
document_chunks -> document_versions
requirements -> bid_projects + source document_versions
review_runs -> bid_projects
assessments -> requirements + review_runs
evidence_links -> assessments + document_versions
action_items -> requirements
decisions -> requirements
audit_events -> bid_projects
review_jobs -> bid_projects
```

Use SQLAlchemy 2 `Mapped` fields. Store enum values as strings, timestamps in UTC, and uniqueness constraints on `(document_id, version_number)`, `(document_version_id, chunk_index)`, and `(requirement_id, review_run_id)`.

Minimum required fields:

```python
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BidProject(Base):
    __tablename__ = "bid_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    deadline_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bid_projects.id", ondelete="CASCADE"), index=True)
    source_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    source_page: Mapped[int | None]
    source_section: Mapped[str | None] = mapped_column(String(500))
    source_quote: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30))
    mandatory: Mapped[bool]
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    active: Mapped[bool] = mapped_column(default=True)


class ReviewRun(Base):
    __tablename__ = "review_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("bid_projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(String(80))
    model_provider: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str] = mapped_column(String(200))
    resumable_requirement_id: Mapped[int | None]
    started_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    completed_at: Mapped[datetime | None]


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("requirement_id", "review_run_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    review_run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), index=True)
    evidence_state: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(30))
    display_status: Mapped[str] = mapped_column(String(40))
    needs_confirmation: Mapped[bool]
    reasoning: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    current: Mapped[bool] = mapped_column(default=True, index=True)


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    page_number: Mapped[int | None]
    section_path: Mapped[str | None] = mapped_column(String(500))
    quote: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String(40))
    valid: Mapped[bool] = mapped_column(default=True, index=True)
```

Define all relationships named exactly as used by tests: `BidProject.documents`, `Document.project`, `Document.versions`, `DocumentVersion.document`, `Requirement.project`, and `Requirement.source_version`.

- [x] **Step 5: Add isolated test database fixture**

Create `backend/tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
```

- [x] **Step 6: Bootstrap schema in FastAPI lifespan**

Modify `backend/app/main.py` to call `Base.metadata.create_all(engine)` inside an async lifespan context before serving requests.

- [x] **Step 7: Run persistence tests and commit**

```powershell
uv run pytest tests/test_persistence.py -v
uv run pytest -q
git add backend/app/db.py backend/app/main.py backend/app/persistence backend/tests
git commit -m "feat: add traceable BidGuard data model"
```

## Phase 2 — Projects, files, versions, and searchable evidence

### Task 4: Implement project and company-evidence APIs

**Files:**
- Create: `backend/app/api/projects.py`
- Create: `backend/app/services/projects.py`
- Create: `backend/tests/test_projects_api.py`
- Modify: `backend/app/main.py`

- [x] **Step 1: Write failing project API tests**

Cover `POST /api/projects`, `GET /api/projects`, and `GET /api/projects/{id}`. Assert that a new project returns zero counts for all five display statuses and preserves an optional ISO-8601 deadline.

Example assertion:

```python
response = client.post("/api/projects", json={"name": "市级云平台运维", "deadline_at": "2026-10-01T09:00:00Z"})
assert response.status_code == 201
assert response.json()["status_counts"] == {
    "high_risk": 0,
    "needs_evidence": 0,
    "optimize": 0,
    "satisfied": 0,
    "needs_confirmation": 0,
}
```

- [x] **Step 2: Run tests and verify 404 failures**

Run: `uv run pytest tests/test_projects_api.py -v`

Expected: FAIL because project routes are absent.

- [x] **Step 3: Implement project service and routes**

Use Pydantic request/response models in `domain/schemas.py`. Keep SQL in `services/projects.py`, not in route functions. Sort project lists by nearest deadline, then newest creation time.

- [x] **Step 4: Run tests and commit**

```powershell
uv run pytest tests/test_projects_api.py -v
git add backend/app/api/projects.py backend/app/services/projects.py backend/app/domain/schemas.py backend/app/main.py backend/tests/test_projects_api.py
git commit -m "feat: add bid project API"
```

### Task 5: Add safe, versioned document upload

**Files:**
- Create: `backend/app/documents/storage.py`
- Create: `backend/app/api/documents.py`
- Create: `backend/app/services/ingestion.py`
- Create: `backend/tests/test_document_versions.py`
- Modify: `backend/app/main.py`

- [x] **Step 1: Write failing storage and version tests**

Create five named tests with these exact assertions:

- `test_rejects_executable_extension`: uploading `payload.exe` returns HTTP 415 and creates no `Document` row;
- `test_same_bytes_do_not_create_duplicate_version`: uploading identical bytes twice returns the same version id, version number `1`, and `created=false` on the second request;
- `test_changed_bytes_create_next_version`: uploading different bytes to the same document returns version number `2` and keeps version `1` queryable;
- `test_storage_path_stays_inside_configured_root`: a filename containing `..\\` still resolves beneath the configured storage root;
- `test_company_document_can_be_reused_without_project_id`: company evidence is created without a project id and can be selected by two projects without duplicating the stored file.

- [x] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_document_versions.py -v`

Expected: FAIL because upload service is absent.

- [x] **Step 3: Implement storage safety**

In `storage.py`:

```python
ALLOWED_SUFFIXES = {".pdf", ".docx"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_storage_path(root: Path, digest: str, original_name: str) -> Path:
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("unsupported document type")
    target = (root / digest[:2] / f"{digest}{suffix}").resolve()
    if root.resolve() not in target.parents:
        raise ValueError("unsafe storage path")
    return target
```

Reject empty files and content larger than `MAX_UPLOAD_BYTES`. Never use the client filename as a directory component.

- [x] **Step 4: Implement upload endpoints**

Add:

```text
POST /api/projects/{project_id}/documents?role=tender|proposal
POST /api/company-evidence
GET  /api/projects/{project_id}/documents
```

Return document id, version id, version number, SHA-256, parse status, and upload time. Re-uploading identical bytes returns the existing version with `created=false`.

- [x] **Step 5: Run tests and commit**

```powershell
uv run pytest tests/test_document_versions.py -v
uv run pytest -q
git add backend/app/documents backend/app/api/documents.py backend/app/services/ingestion.py backend/app/main.py backend/tests/test_document_versions.py
git commit -m "feat: add versioned document uploads"
```

### Task 6: Parse PDF and DOCX while preserving evidence locations

**Files:**
- Create: `backend/app/documents/parser.py`
- Create: `backend/app/documents/pdf.py`
- Create: `backend/app/documents/docx.py`
- Create: `backend/tests/fixtures/sample-tender.pdf`
- Create: `backend/tests/fixtures/sample-proposal.docx`
- Create: `backend/tests/test_parsers.py`

- [x] **Step 1: Write parser contract tests**

Define the parser result in `domain/schemas.py`:

```python
class ParsedChunk(BaseModel):
    chunk_index: int
    page_number: int | None
    section_path: str | None
    text: str = Field(min_length=1)


class ParsedDocument(BaseModel):
    chunks: list[ParsedChunk]
    failed_pages: list[int] = Field(default_factory=list)
    needs_ocr: bool = False
```

Tests must assert PDF page numbers survive, DOCX heading paths survive, blank pages are omitted, and a PDF with fewer than 30 visible characters per page returns `needs_ocr=True` rather than fabricated text.

- [x] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_parsers.py -v`

Expected: FAIL because parsers are absent.

- [x] **Step 3: Implement parsers**

Use `pypdf.PdfReader` for PDFs and `python-docx` for DOCX. Normalize whitespace but do not remove section numbers. Limit chunks to approximately 3,000 characters with 300-character overlap, and never mix text from different PDF pages in one chunk.

- [x] **Step 4: Persist parsed chunks**

Update `services/ingestion.py` so parse success replaces chunks only for the current `DocumentVersion`. On failure, preserve the uploaded file and write an error message to the version record.

- [x] **Step 5: Run tests and commit**

```powershell
uv run pytest tests/test_parsers.py -v
git add backend/app/documents backend/app/domain/schemas.py backend/app/services/ingestion.py backend/tests
git commit -m "feat: parse bid documents with traceable locations"
```

### Task 7: Add model-independent evidence search

**Files:**
- Create: `backend/app/documents/search.py`
- Create: `backend/tests/test_search.py`

- [x] **Step 1: Write failing lexical-search tests**

Test that a query for `信息系统项目管理师 项目经理` ranks the chunk containing both terms above unrelated chunks, filters by document role, and returns source version/page metadata.

- [x] **Step 2: Implement deterministic ranking**

Implement a small, testable lexical ranker:

```python
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text)}


def overlap_score(query: str, text: str) -> float:
    query_terms = tokenize(query)
    if not query_terms:
        return 0.0
    text_terms = tokenize(text)
    return len(query_terms & text_terms) / len(query_terms)
```

`search_chunks` must retrieve candidates from the database by allowed document version ids, score them, discard zero scores, and return at most the requested limit.

- [x] **Step 3: Verify and commit**

```powershell
uv run pytest tests/test_search.py -v
git add backend/app/documents/search.py backend/tests/test_search.py
git commit -m "feat: add model-independent evidence retrieval"
```

## Phase 3 — Agents SDK extraction and review core

### Task 8: Add model-provider boundary and live-access smoke command

**Files:**
- Create: `backend/app/agents/provider.py`
- Create: `backend/scripts/smoke_agent.py`
- Create: `backend/tests/test_provider.py`
- Modify: `backend/app/settings.py`

- [x] **Step 1: Write configuration tests without calling a model**

Test that missing `EXTRACTION_MODEL` or `REVIEW_MODEL` produces a clear startup/configuration error only when Agent execution is requested, not when health endpoints run.

- [x] **Step 2: Implement provider resolution**

Expose:

```python
def resolve_model_name(purpose: Literal["extraction", "review"], settings: Settings) -> str:
    model = settings.extraction_model if purpose == "extraction" else settings.review_model
    if not model:
        raise ModelConfigurationError(f"{purpose} model is not configured")
    return model
```

Keep provider construction behind `build_run_config(settings)`. The first implementation may return the default OpenAI run configuration; do not spread provider checks into Agent modules.

- [x] **Step 3: Add the bounded smoke command**

`backend/scripts/smoke_agent.py` must run one Agent turn with no tools and print only `OK` when a non-empty structured response returns. It must not print keys, headers, or environment values.

- [x] **Step 4: Run unit tests**

Run: `uv run pytest tests/test_provider.py -v`

Expected: PASS without network access.

- [x] **Step 5: After explicit REVIEW_MODEL configuration, run one live smoke check**

Run: `uv run python scripts/smoke_agent.py`

Expected: `OK`. If access fails, record the exact safe error and stop; do not silently switch providers.

This is a connectivity-only smoke. It sends no tender, proposal, company evidence, user fact, or other business document text, and it does not count as model-quality acceptance.

- [x] **Step 6: Commit**

```powershell
git add backend/app/agents/provider.py backend/app/settings.py backend/scripts/smoke_agent.py backend/tests/test_provider.py
git commit -m "feat: add configurable agent model provider"
```

### Task 8A: Pass the mandatory LLM governance gate

This task is required before Task 9 can send any business document text to a live model. It implements the engineering controls defined by the two governance documents rather than relying on Prompt instructions.

**Files:**
- Create: `backend/app/agents/contracts.py`
- Create: `backend/app/agents/context.py`
- Create: `backend/app/services/model_calls.py`
- Create: `backend/tests/test_llm_governance.py`
- Modify: `backend/app/domain/schemas.py`
- Modify: `backend/app/persistence/models.py`
- Modify: `backend/app/persistence/schema.py`
- Modify: `backend/app/settings.py`
- Modify: `.env.example`

- [x] **Step 1: Write failing governance contract tests**

Cover prompt versioning, stable reason codes, unified Coverage, safe call-ledger persistence, positive bounded configuration, project-scoped `ReviewContext`, explicitly selected company evidence, and rejection of model-supplied project/version ids that attempt to widen the server scope.

- [x] **Step 2: Add durable governance objects**

Add a one-to-many LLM call ledger for each `ReviewRun`, including provider/model, Prompt version/hash, authorized input object ids and coverage, attempts, timing, outcome, usage when reported, gate counts, tool-call summary, and stop reason. Do not store API keys or full sensitive document text by default.

Define versioned `Coverage`, Evidence Fact/Claim boundary, shared reason-code types, and a server-created `ReviewContext`. Add a persistent relation recording which company evidence is authorized for a project.

Add a persistent tender-package membership model that records included and excluded main files, attachments, addenda and clarifications, plus their effective/precedence/conflict state. If the current MVP intentionally remains single-tender-file, persist that limitation as Coverage instead of implying that the full tender package was checked.

- [x] **Step 3: Resolve identity and history rules before extraction**

Specify and test Requirement identity for repeated wording at different source locations and for cross-version lineage. Define tender-package precedence and conflict behavior: an unresolved addendum/clarification conflict fails closed to human confirmation. Define archival/deletion behavior so deleting a file cannot silently erase the evidence chain. Human decisions must later be able to reference actor, source Assessment, applicable versions, and superseded state.

- [x] **Step 4: Validate runtime limits before model use**

Validate timeout, retry, batch, turn, tool-call, token and cost bounds when Agent execution is requested. Health, file browsing and deterministic APIs must remain available when model configuration is absent. Never silently switch providers or increase limits.

- [x] **Step 5: Migrate safely and test without touching the real development database**

Extend the supported SQLite migration path for the new governance objects. Tests must use isolated databases, preserve valid old rows, fail closed on unsafe conflicts, remain idempotent, and leave `backend/bidguard.db` unchanged.

- [x] **Step 6: Pass the governance gate**

Before Task 9 live extraction, verify:

```text
LLM call ledger persists a safe record
Coverage records read and unread scope
Prompt version and reason codes are stable
Project/company evidence authorization is enforced by code
ReviewContext prevents model-controlled scope expansion
Tender-package membership and included/excluded scope are persisted
Addendum/clarification precedence or unresolved conflict is explicit
Budget, timeout and stop semantics are tested
No business text has been sent during unit or mock tests
```

Commit the governance foundation separately. A mock-only pass may be described as “programmatic governance controls implemented”; it is not real-model quality acceptance.

### Task 9: Extract traceable atomic requirements

**Files:**
- Create: `backend/app/agents/extraction.py`
- Create: `backend/app/services/requirements.py`
- Create: `backend/app/agents/gates.py`
- Create: `backend/tests/test_agent_gates.py`
- Create: `backend/tests/test_requirement_extraction.py`

- [x] **Step 1: Write citation-gate tests**

Test that a requirement candidate is rejected when its source version differs from the active tender version, its page does not exist, its quote is absent from the cited chunk, or the quote is empty. Test that a valid candidate is accepted and assigned a stable SHA-256 fingerprint derived from normalized requirement text plus source location.

- [x] **Step 2: Implement citation gate**

Create `validate_requirement_candidate(candidate, active_version, chunks) -> ValidatedRequirement`. The function performs no model calls and raises typed `CitationGateError` values with codes `wrong_version`, `missing_page`, `quote_not_found`, or `empty_quote`.

- [x] **Step 3: Implement extraction Agent**

Define one `Agent[None]` with `output_type=RequirementBatch`. Instructions must state:

```text
Extract only requirements stated in the supplied tender excerpt.
Split compound statements into atomic requirements when each can be checked independently.
Classify each requirement using the provided enum.
Quote the smallest sufficient source passage and preserve its page/section.
Do not infer company facts, compliance, or scoring outcomes.
Return an empty list when the excerpt contains no bidder requirement.
```

Use `Runner.run` once per bounded page/section batch. Pass model name through `resolve_model_name("extraction", settings)` and set a maximum turn count.

- [x] **Step 4: Normalize and save requirements**

`services/requirements.py` must validate every candidate, deduplicate on fingerprint, save rejected-candidate audit events, and never delete historical requirements. A new tender version marks old requirements inactive before saving the new matrix.

- [x] **Step 5: Run mocked contract tests**

Mock only the model response boundary, not validation or persistence. Assert that invalid citations never reach the database and duplicate candidates create one active requirement.

- [ ] **Step 6: After Task 8A passes, run one bounded live extraction test**

Use `backend/tests/fixtures/sample-tender.pdf`. Verify at least one known requirement appears with the correct page and exact quote. Save the safe result summary under `backend/evals/results/extraction-smoke.json`.

While the product supports only one active tender file, label this result as a **single-file bounded extraction evaluation**. It must not be reported as complete tender-package coverage.

- [x] **Step 7: Commit**

```powershell
uv run pytest tests/test_agent_gates.py tests/test_requirement_extraction.py -v
git add backend/app/agents backend/app/services/requirements.py backend/tests backend/evals/results/extraction-smoke.json
git commit -m "feat: extract cited tender requirements"
```

### Task 10: Build evidence tools and unsupported-pass gate

**Files:**
- Create: `backend/app/agents/tools.py`
- Extend: `backend/app/agents/gates.py`
- Create: `backend/tests/test_review_tools.py`

- [x] **Step 1: Write failing tool tests**

Test exact tool contracts:

```text
get_document_page(document_version_id, page_number)
search_proposal_evidence(project_id, query, limit)
search_company_evidence(project_id, query, limit)
save_assessment(requirement_id, candidate)
request_user_confirmation(requirement_id, question, reason)
create_action_item(requirement_id, title, recommendation)
```

Assert project scoping prevents reading another project, company evidence search includes only documents explicitly selected for the project, and `save_assessment` rejects `MATCHED` when the evidence list is empty.

- [x] **Step 2: Implement read tools**

Wrap service functions with the SDK tool decorator. Each tool returns compact structured data containing version id, page/section, quote, and score. Do not return an entire document.

- [x] **Step 3: Implement write tools with gates**

Before saving an assessment:

```python
def validate_assessment(candidate: AssessmentCandidate, active_versions: set[int]) -> None:
    if candidate.evidence_state is EvidenceState.MATCHED and not candidate.evidence:
        raise UnsupportedPassError("matched assessment requires evidence")
    if any(item.document_version_id not in active_versions for item in candidate.evidence):
        raise StaleEvidenceError("assessment cites an inactive document version")
```

Calculate display status with `calculate_display_status`; ignore any display-status text proposed by the model.

- [x] **Step 4: Run tests and commit**

```powershell
uv run pytest tests/test_review_tools.py tests/test_agent_gates.py -v
git add backend/app/agents backend/tests/test_review_tools.py
git commit -m "feat: add guarded bid evidence tools"
```

### Task 11: Implement bounded review orchestration

**Files:**
- Create: `backend/app/agents/review.py`
- Create: `backend/app/services/reviews.py`
- Create: `backend/tests/test_review_orchestration.py`

- [ ] **Step 1: Write failing orchestration tests**

Cover these paths:

1. matched evidence saves `satisfied`;
2. missing mandatory evidence saves `high_risk`;
3. partial scoring evidence saves `optimize`;
4. ambiguous requirement creates a decision request and saves `needs_confirmation`;
5. tool-call or turn limit saves partial progress rather than discarding completed assessments.

- [ ] **Step 2: Define the review Agent**

Instructions must require this loop:

```text
For the assigned requirement, first read its source citation.
Search proposal evidence, then selected company evidence.
Open the most relevant source pages before judging.
If evidence is insufficient, record missing/partial evidence; do not infer facts.
If the result depends on an unverified company fact or ambiguous wording, request user confirmation.
Save exactly one current assessment and at most one action item for the requirement.
```

The Agent receives one requirement or a small fixed batch. It cannot choose arbitrary project ids.

- [ ] **Step 3: Implement service orchestration**

Create a `ReviewContext` containing project id, active document version ids, database-session factory, and current review-run id. Process requirements in batches of at most 10, commit after each batch, and update counters on `ReviewRun`.

- [ ] **Step 4: Verify bounded failure behavior**

Simulate a failure after the first saved assessment. Assert the first result remains committed and the run status becomes `partial_failure` with a resumable requirement cursor.

- [ ] **Step 5: Commit**

```powershell
uv run pytest tests/test_review_orchestration.py -v
git add backend/app/agents/review.py backend/app/services/reviews.py backend/tests/test_review_orchestration.py
git commit -m "feat: orchestrate bounded evidence reviews"
```

## Phase 4 — Persisted jobs, human decisions, and incremental re-review

### Task 12: Add a persisted in-process job worker

**Files:**
- Create: `backend/app/jobs/worker.py`
- Create: `backend/app/jobs/handlers.py`
- Create: `backend/app/api/reviews.py`
- Create: `backend/tests/test_review_jobs.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing job recovery tests**

Test that starting a review creates a queued job, a worker atomically claims one job, completed results persist, and jobs left `running` by a stopped process return to `queued` on application startup with an incremented attempt count.

- [ ] **Step 2: Implement job table repository methods**

Add repository operations `enqueue_job`, `claim_next_job`, `mark_job_complete`, `mark_job_failed`, and `requeue_interrupted_jobs`. Claim and state transition occur in one transaction.

- [ ] **Step 3: Implement handlers**

Handlers execute exact stages:

```text
parse current files
extract requirements if tender matrix is stale
review active requirements
set awaiting_confirmation when unresolved decisions exist
set completed when all eligible requirements have assessments
```

Persist stage names so the frontend never shows a fake percentage.

- [ ] **Step 4: Add review API**

Add:

```text
POST /api/projects/{project_id}/reviews
GET  /api/projects/{project_id}/reviews/latest
GET  /api/projects/{project_id}/requirements
GET  /api/requirements/{requirement_id}
```

Starting a duplicate review with the same active file versions returns the existing active job rather than creating another.

- [ ] **Step 5: Wire worker into FastAPI lifespan**

Start one asyncio worker task on startup; on shutdown, signal it to stop after its current database operation. Requeue interrupted jobs before accepting requests.

- [ ] **Step 6: Test and commit**

```powershell
uv run pytest tests/test_review_jobs.py -v
git add backend/app/jobs backend/app/api/reviews.py backend/app/main.py backend/tests/test_review_jobs.py
git commit -m "feat: persist and resume review jobs"
```

### Task 13: Add human decisions, action items, and incremental scope

**Files:**
- Create: `backend/app/services/decisions.py`
- Create: `backend/app/api/decisions.py`
- Create: `backend/tests/test_decisions_api.py`
- Create: `backend/tests/test_incremental_review.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing decision tests**

Test `confirm`, `deny`, and `not_applicable` decisions with a required explanation. Assert Agent-created pending decisions cannot be silently deleted and every user decision creates an audit event.

- [ ] **Step 2: Write failing incremental-scope tests**

Test:

- a new proposal version invalidates assessments citing the old proposal version;
- an expired company certificate invalidates every assessment that cites it;
- unaffected requirements remain current;
- a changed tender version marks the entire old requirement matrix inactive.

- [ ] **Step 3: Implement decision API**

Add:

```text
POST /api/requirements/{id}/decisions
POST /api/action-items/{id}/complete
POST /api/projects/{id}/re-review
```

`re-review` calculates and stores the affected requirement ids before enqueueing a job.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest tests/test_decisions_api.py tests/test_incremental_review.py -v
git add backend/app/services/decisions.py backend/app/api/decisions.py backend/app/main.py backend/tests
git commit -m "feat: add human decisions and incremental reviews"
```

## Phase 5 — User-facing workflow

### Task 14: Build typed frontend API client and application shell

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Replace: `frontend/src/app.tsx`
- Replace: `frontend/src/styles.css`
- Create: `frontend/src/components/status-badge.tsx`
- Create: `frontend/tests/setup.ts`
- Modify: `frontend/vite.config.ts`

- [ ] **Step 1: Configure Vitest and write a failing status-badge test**

Assert all five backend display statuses render the approved Chinese labels and use text plus color, not color alone.

- [ ] **Step 2: Implement exact frontend types**

Mirror backend wire values without redefining business rules:

```typescript
export type DisplayStatus =
  | 'high_risk'
  | 'needs_evidence'
  | 'optimize'
  | 'satisfied'
  | 'needs_confirmation';

export const STATUS_LABEL: Record<DisplayStatus, string> = {
  high_risk: '高风险',
  needs_evidence: '待补充',
  optimize: '可优化',
  satisfied: '已满足',
  needs_confirmation: '待确认',
};
```

- [ ] **Step 3: Implement API error handling**

`api/client.ts` must throw a typed `ApiError` containing status, code, and user-safe message. It must support JSON and `FormData` requests and never include secrets in browser code.

- [ ] **Step 4: Build the shell**

Create a restrained sidebar with only: `新建核查`, `投标项目`, and `企业资料库`. Add React Router routes for `/projects`, `/projects/new`, `/projects/:id/review`, `/requirements/:id`, and `/evidence`.

- [ ] **Step 5: Verify and commit**

```powershell
npm test -- --run
npm run build
git add frontend
git commit -m "feat: add BidGuard application shell"
```

### Task 15: Build project creation and upload flow

**Files:**
- Create: `frontend/src/pages/projects-page.tsx`
- Create: `frontend/src/pages/new-review-page.tsx`
- Create: `frontend/src/pages/evidence-library-page.tsx`
- Create: `frontend/tests/projects-page.test.tsx`
- Create: `frontend/tests/new-review-page.test.tsx`

- [ ] **Step 1: Write failing page tests**

Test that projects sort by deadline, the new-review page requires a tender before proposal upload, only PDF/DOCX are accepted, selected company evidence is visible, and submit is disabled while required material is absent.

- [ ] **Step 2: Implement project list**

Show project name, deadline, real backend stage, and counts for high risk, needs evidence, and needs confirmation. Do not render a fake percentage.

- [ ] **Step 3: Implement new-review wizard**

Use three explicit steps: project details, tender/proposal files, company evidence selection. After successful creation and uploads, start a review and navigate to `/projects/{id}/review`.

- [ ] **Step 4: Implement evidence library**

Support upload, current version, expiry date, and display name. Do not add folders, tags, sharing, or bulk integrations in the MVP.

- [ ] **Step 5: Verify and commit**

```powershell
npm test -- --run
npm run build
git add frontend/src frontend/tests
git commit -m "feat: add project and evidence upload workflow"
```

### Task 16: Build review overview and requirement matrix

**Files:**
- Create: `frontend/src/pages/review-page.tsx`
- Create: `frontend/src/components/requirement-table.tsx`
- Create: `frontend/tests/review-page.test.tsx`

- [ ] **Step 1: Write failing review-page tests**

Test ordered summary cards, filter behavior, source location display, job-stage polling, partial-failure warning, and row navigation to requirement detail.

- [ ] **Step 2: Implement real-stage polling**

Poll the latest review endpoint while status is active. Display `等待材料`, `解析中`, `提取要求中`, `核查中`, or `等待确认` exactly as returned. Stop polling on completed, partial failure, or failed.

- [ ] **Step 3: Implement matrix filters**

Provide filters for all five statuses, requirement kind, and “待我处理”. Default sorting: high risk, needs confirmation, needs evidence, optimize, satisfied.

- [ ] **Step 4: Verify and commit**

```powershell
npm test -- --run frontend/tests/review-page.test.tsx
npm run build
git add frontend/src/pages/review-page.tsx frontend/src/components/requirement-table.tsx frontend/tests/review-page.test.tsx
git commit -m "feat: add review matrix and live task stages"
```

### Task 17: Build issue detail and human-action flow

**Files:**
- Create: `frontend/src/pages/issue-page.tsx`
- Create: `frontend/tests/issue-page.test.tsx`

- [ ] **Step 1: Write failing issue-detail tests**

Assert one page shows tender source, proposal response, company evidence, Agent reasoning, impact, recommendation, actions, and history. Assert a pending confirmation requires an explanation before submission.

- [ ] **Step 2: Implement evidence panels**

Each quote links to exact document version/page metadata. Missing evidence is shown explicitly; never render an empty panel as success.

- [ ] **Step 3: Implement actions**

Support upload supplemental evidence, assign/update action item, confirm fact, deny fact, mark not applicable with reason, retain risk, and start re-review.

- [ ] **Step 4: Verify responsive behavior and commit**

```powershell
npm test -- --run frontend/tests/issue-page.test.tsx
npm run build
git add frontend/src/pages/issue-page.tsx frontend/tests/issue-page.test.tsx
git commit -m "feat: add evidence-first issue resolution"
```

## Phase 6 — Reporting, security, evaluation, and acceptance

### Task 18: Add traceable report export

**Files:**
- Create: `backend/app/services/reports.py`
- Create: `backend/app/api/reports.py`
- Create: `backend/tests/test_reports.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing report tests**

Assert the report includes bound document-version ids, unresolved high risks, unresolved confirmations, scoring opportunities, citations, generation time, and review-run id. Assert historical assessments do not appear as current.

- [ ] **Step 2: Implement HTML report**

Generate semantic HTML from database records with no model call. Escape user content. The first export endpoint is:

```text
GET /api/projects/{project_id}/reports/latest.html
```

- [ ] **Step 3: Verify and commit**

```powershell
uv run pytest tests/test_reports.py -v
git add backend/app/services/reports.py backend/app/api/reports.py backend/app/main.py backend/tests/test_reports.py
git commit -m "feat: export traceable bid review reports"
```

### Task 19: Enforce security and operational limits

**Files:**
- Create: `backend/app/security.py`
- Create: `backend/tests/test_security.py`
- Modify: `backend/app/settings.py`
- Modify: `backend/app/main.py`
- Modify: `.env.example`

- [ ] **Step 1: Write security regression tests**

Cover path traversal, oversized upload, unsupported extension, another-project document access, invalid project id in tools, secret-shaped values in logs, duplicate active job, and Agent turn/tool caps.

- [ ] **Step 2: Add request and logging protections**

Use a request id, bounded request body, allowed CORS origins from settings, and a log filter that redacts values matching `sk-` and authorization headers. Do not log uploaded document text by default.

- [ ] **Step 3: Add operational settings**

Add environment variables for maximum upload bytes, maximum Agent turns, maximum tool calls, maximum automatic retries, review batch size, and allowed frontend origins. Validate positive bounds in `Settings`.

- [ ] **Step 4: Run full backend checks and commit**

```powershell
uv run pytest -q
uv run ruff check app tests evals
uv run mypy app
git add backend/app backend/tests/test_security.py .env.example
git commit -m "feat: enforce BidGuard safety boundaries"
```

### Task 20: Build the saved Agent evaluation harness

**Files:**
- Create: `backend/evals/cases.jsonl`
- Create: `backend/evals/graders.py`
- Create: `backend/evals/run_local.py`
- Create: `backend/evals/results/.gitkeep`
- Create: `backend/tests/test_graders.py`

- [ ] **Step 1: Define case format and initial cases**

Each JSONL case contains:

```json
{
  "id": "mandatory-authorization-missing",
  "tender_fixture": "sample-tender.pdf",
  "proposal_fixture": "sample-proposal.docx",
  "expected_requirement_quotes": ["授权委托书"],
  "expected_statuses": {"M-01": "high_risk"},
  "forbidden_claims": ["已提供授权委托书"],
  "required_tool_calls": ["search_proposal_evidence"]
}
```

Add at least five initial cases: missing authorization, missing certificate, partial scoring cases, ambiguous onsite commitment, and fully supported delivery period.

- [ ] **Step 2: Implement deterministic graders**

Create graders for requirement recall, citation resolvability, citation relevance by exact fixture span, high-risk detection, unsupported-pass count, forbidden-claim count, and required-tool-call presence.

- [ ] **Step 3: Implement runner isolation and output**

The runner creates a fresh temporary database and storage directory for every case, validates required environment variables before live execution, writes `evals/results/latest.json`, and exits non-zero when a release-blocking metric fails.

- [ ] **Step 4: Test graders and commit**

```powershell
uv run pytest tests/test_graders.py -v
uv run python evals/run_local.py --dry-run
git add backend/evals backend/tests/test_graders.py
git commit -m "test: add saved BidGuard agent evaluations"
```

### Task 21: Add browser E2E and one-command verification

**Files:**
- Create: `e2e/bid-review.spec.ts`
- Create: `playwright.config.ts`
- Create: `package.json` at repository root for E2E scripts
- Create: `scripts/verify.ps1`
- Modify: `README.md`

- [ ] **Step 1: Install and configure Playwright**

Run:

```powershell
npm init -y
npm install -D @playwright/test
npx playwright install chromium
```

Configure Playwright to start backend and frontend web servers and use one worker for stateful local tests.

- [ ] **Step 2: Write the end-to-end test**

The test must:

1. create a project;
2. upload tender and proposal fixtures;
3. select company evidence;
4. start a stubbed deterministic review mode;
5. wait for real backend status `completed`;
6. open a high-risk requirement;
7. verify source, evidence, reasoning, and action are visible;
8. submit a human decision;
9. trigger re-review;
10. download and inspect the HTML report.

Use stubbed deterministic Agent output for CI. Keep live-model quality in the separate eval harness.

- [ ] **Step 3: Add one-command verification**

Create `scripts/verify.ps1`:

```powershell
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location "$repoRoot\backend"
uv run pytest -q
uv run ruff check app tests evals
Pop-Location

Push-Location "$repoRoot\frontend"
npm test -- --run
npm run build
Pop-Location

Push-Location $repoRoot
npx playwright test
Pop-Location
```

- [ ] **Step 4: Run full verification**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`

Expected: backend tests, lint, frontend tests, frontend build, and Playwright all pass.

- [ ] **Step 5: Run one real public-sample evaluation**

After credential approval, run the evaluation harness on a real public tender sample. Confirm the result file records model, tool calls, token usage, failures, and all release-blocking metrics.

- [ ] **Step 6: Update README and commit**

Document setup, credential choice, local run, test commands, data handling, current limitations, and the difference between deterministic CI and live-model evals.

```powershell
git add e2e playwright.config.ts package.json package-lock.json scripts/verify.ps1 README.md
git commit -m "test: verify the complete BidGuard MVP workflow"
```

## Final release checklist

- [ ] The complete project/upload/review/decision/re-review/report flow works without database edits.
- [ ] Every current high-risk and satisfied state resolves to active-version citations.
- [ ] Unsupported “satisfied” results and fabricated company facts remain zero in saved eval results.
- [ ] Closing the browser does not stop the backend job.
- [ ] Interrupted jobs resume without losing completed assessments.
- [ ] A changed proposal or expired certificate invalidates only affected results.
- [ ] The frontend shows real backend stages and clearly separates high risk from scoring opportunity.
- [ ] `scripts/verify.ps1` passes from a clean checkout after documented setup.
- [ ] `backend/evals/results/latest.json` exists for the chosen release model.
- [ ] One approximately 200-page public tender package completes under configured turn/tool/cost caps.
- [ ] The approved design document and README match implemented behavior and limitations.
