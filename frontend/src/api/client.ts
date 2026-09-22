import type { ApiErrorPayload, RequestOptions } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly userMessage: string

  constructor(status: number, payload: ApiErrorPayload = {}) {
    const userMessage = payload.message ?? payload.detail ?? '暂时无法完成请求，请稍后再试。'
    super(userMessage)
    this.name = 'ApiError'
    this.status = status
    this.code = payload.code ?? 'request_failed'
    this.userMessage = userMessage
  }
}

async function readPayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return response.json().catch(() => ({}))
  }
  return response.text().catch(() => '')
}

function toRequestBody(body: RequestOptions['body']): BodyInit | undefined {
  if (body === null || body === undefined) return undefined
  if (body instanceof FormData || typeof body === 'string' || body instanceof Blob) {
    return body
  }
  return JSON.stringify(body)
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const body = toRequestBody(options.body)
  const headers = new Headers(options.headers)
  if (body && !(body instanceof FormData) && !headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    body,
    headers,
  })
  const payload = await readPayload(response)
  if (!response.ok) {
    const errorPayload = typeof payload === 'object' && payload !== null ? payload as ApiErrorPayload : {}
    throw new ApiError(response.status, errorPayload)
  }
  return payload as T
}

export const apiClient = {
  get<T>(path: string, options?: Omit<RequestOptions, 'body' | 'method'>) {
    return request<T>(path, { ...options, method: 'GET' })
  },
  post<T>(path: string, body?: RequestOptions['body'], options?: Omit<RequestOptions, 'body' | 'method'>) {
    return request<T>(path, { ...options, method: 'POST', body })
  },
}
