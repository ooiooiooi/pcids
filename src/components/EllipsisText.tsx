import type { CSSProperties, ReactNode } from 'react'

type EllipsisTextProps = {
  value?: ReactNode
  title?: string
  className?: string
  style?: CSSProperties
  fallback?: ReactNode
  inline?: boolean
}

const toTitleText = (value: ReactNode, fallback: ReactNode) => {
  if (typeof value === 'string' || typeof value === 'number') {
    const text = String(value).trim()
    return text || (typeof fallback === 'string' || typeof fallback === 'number' ? String(fallback) : '')
  }
  return typeof fallback === 'string' || typeof fallback === 'number' ? String(fallback) : ''
}

const EllipsisText: React.FC<EllipsisTextProps> = ({
  value,
  title,
  className,
  style,
  fallback = '-',
  inline = false,
}) => {
  const content = value === null || value === undefined || value === '' ? fallback : value
  const titleText = title ?? toTitleText(content, fallback)

  return (
    <span
      className={className}
      title={titleText || undefined}
      style={{
        display: inline ? 'inline-block' : 'block',
        maxWidth: '100%',
        minWidth: 0,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {content}
    </span>
  )
}

export default EllipsisText
