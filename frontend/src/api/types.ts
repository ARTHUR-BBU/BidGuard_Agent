export type DisplayStatus =
  | 'high_risk'
  | 'needs_evidence'
  | 'optimize'
  | 'satisfied'
  | 'needs_confirmation'

export const STATUS_LABEL: Record<DisplayStatus, string> = {
  high_risk: '高风险',
  needs_evidence: '待补充',
  optimize: '可优化',
  satisfied: '已满足',
  needs_confirmation: '待确认',
}

export type ApiErrorPayload = {
  code?: string
  message?: string
  detail?: string
}

export type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: BodyInit | Record<string, unknown> | null
}

export type ProjectSummary = {
  id: string
  name: string
  deadline?: string | null
  stage?: string
}
