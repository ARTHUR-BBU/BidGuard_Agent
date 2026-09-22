import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusBadge } from '../src/components/status-badge'
import type { DisplayStatus } from '../src/api/types'

describe('StatusBadge', () => {
  const statuses: Array<[DisplayStatus, string]> = [
    ['high_risk', '高风险'],
    ['needs_evidence', '待补充'],
    ['optimize', '可优化'],
    ['satisfied', '已满足'],
    ['needs_confirmation', '待确认'],
  ]

  it.each(statuses)('renders %s as readable text and a semantic style', (status, label) => {
    render(<StatusBadge status={status} />)

    const badge = screen.getByText(label)
    expect(badge).toBeVisible()
    expect(badge).toHaveAttribute('data-status', status)
    expect(badge).toHaveClass(`status-badge--${status}`)
  })
})
