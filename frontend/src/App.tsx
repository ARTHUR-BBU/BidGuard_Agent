import type { ReactNode } from 'react'
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { STATUS_LABEL, type DisplayStatus } from './api/types'
import { StatusBadge } from './components/status-badge'
import './App.css'

const navItems = [
  { to: '/projects/new', label: '新建核查', icon: '+' },
  { to: '/projects', label: '投标项目', icon: '▦' },
  { to: '/evidence', label: '企业资料库', icon: '⌁' },
]

const displayStatuses = Object.keys(STATUS_LABEL) as DisplayStatus[]

function PageIntro({ eyebrow, title, description, children }: {
  eyebrow: string
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <section className="page-intro">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p className="page-description">{description}</p>
      {children}
    </section>
  )
}

function ProjectsPage() {
  return (
    <>
      <PageIntro
        eyebrow="工作台 / 投标项目"
        title="先看清，再决定是否提交。"
        description="每个项目都会保留材料来源、核查状态和需要人工确认的事项。"
      >
        <Link className="primary-button" to="/projects/new">新建一次核查 <span aria-hidden="true">→</span></Link>
      </PageIntro>
      <section className="workspace-section" aria-labelledby="projects-empty-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">项目列表</p>
            <h2 id="projects-empty-title">还没有投标项目</h2>
          </div>
          <span className="quiet-label">数据以后台为准</span>
        </div>
        <div className="empty-state">
          <span className="empty-state__mark" aria-hidden="true">01</span>
          <div>
            <h3>从一份招标文件开始</h3>
            <p>上传招标文件、投标文件，再选择需要核验的企业资料。系统会在后续步骤中逐条留下证据。</p>
          </div>
          <Link className="text-link" to="/projects/new">开始新核查 <span aria-hidden="true">↗</span></Link>
        </div>
      </section>
      <section className="workspace-section status-guide" aria-labelledby="status-guide-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">统一语言</p>
            <h2 id="status-guide-title">审核状态怎么读</h2>
          </div>
          <span className="quiet-label">五种后台状态</span>
        </div>
        <div className="status-list">
          {displayStatuses.map((status) => (
            <div className="status-list__item" key={status}>
              <StatusBadge status={status} />
              <span>{status === 'high_risk' ? '需要优先处理' : status === 'needs_confirmation' ? '等待负责人确认' : '由证据和规则共同决定'}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  )
}

function NewReviewPage() {
  return <PageIntro eyebrow="新建核查" title="准备一份新的投标核查" description="这里将按项目资料、投标文件、企业资料三个步骤收集材料。" />
}

function EvidencePage() {
  return <PageIntro eyebrow="企业资料库" title="企业资料库" description="集中管理资质、业绩和人员等可复用的企业材料。每次使用都会绑定具体版本。" />
}

function ReviewPage() {
  return <PageIntro eyebrow="项目核查" title="审核总览即将到来" description="这里会呈现真实的审核阶段、风险排序和逐条要求矩阵。" />
}

function RequirementPage() {
  return <PageIntro eyebrow="要求详情" title="逐条查看证据" description="这里会把招标要求、投标响应、企业证据和人工确认放在同一条证据链上。" />
}

function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="主导航">
        <Link className="brand" to="/projects" aria-label="BidGuard 首页">
          <span className="brand-mark" aria-hidden="true">B</span>
          <span><strong>BidGuard</strong><small>evidence-led review</small></span>
        </Link>
        <div className="sidebar-rule" />
        <nav className="main-nav">
          <p className="nav-label">工作台</p>
          {navItems.map((item) => (
            <NavLink
              className={({ isActive }) => `nav-item${isActive ? ' nav-item--active' : ''}`}
              key={item.to}
              to={item.to}
            >
              <span className="nav-item__icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="environment-dot" aria-hidden="true" />
          <span>证据优先模式</span>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <span className="topbar__context">BidGuard / MVP</span>
          <span className="topbar__status"><span className="environment-dot" aria-hidden="true" />后台连接待配置</span>
        </header>
        <div className="page-content">
          <Routes>
            <Route path="/" element={<Navigate to="/projects" replace />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/new" element={<NewReviewPage />} />
            <Route path="/projects/:id/review" element={<ReviewPage />} />
            <Route path="/requirements/:id" element={<RequirementPage />} />
            <Route path="/evidence" element={<EvidencePage />} />
            <Route path="*" element={<Navigate to="/projects" replace />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  )
}

export default App
