import { STATUS_LABEL, type DisplayStatus } from '../api/types'

type StatusBadgeProps = {
  status: DisplayStatus
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${status}`} data-status={status} role="status">
      <span className="status-badge__dot" aria-hidden="true" />
      {STATUS_LABEL[status]}
    </span>
  )
}
