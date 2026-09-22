import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from '../src/App'

describe('BidGuard application shell', () => {
  it('exposes the three primary destinations and navigates to a reserved route', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByRole('link', { name: '投标项目' })).toBeVisible()
    expect(screen.getByRole('link', { name: '企业资料库' })).toBeVisible()
    expect(screen.getAllByRole('link', { name: /新建核查/ }).length).toBeGreaterThan(0)

    await user.click(screen.getByRole('link', { name: '企业资料库' }))

    expect(screen.getByRole('heading', { name: '企业资料库' })).toBeVisible()
  })
})
