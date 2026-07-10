import { useEffect, useMemo, useState } from 'react'
import EllipsisText from './EllipsisText'
import { resolveMediaUrl } from '../utils/mediaUrl'

export function firstFilled(...values: Array<any>) {
  for (const value of values) {
    if (value === null || value === undefined) continue
    const text = String(value).trim()
    if (text) return text
  }
  return ''
}

export function getUserDisplayName(user?: Record<string, any> | null, fallback?: string) {
  return firstFilled(user?.display_name, user?.username, fallback, '-') || '-'
}

export function getUserAvatarUrl(user?: Record<string, any> | null) {
  return resolveMediaUrl(firstFilled(
    user?.avatar_url,
    user?.avatar,
    user?.portrait,
    user?.photo,
    user?.head_img,
    user?.user_avatar_url,
    user?.inviter_avatar_url,
  ) || '')
}

export function getNameInitials(name: string) {
  const text = String(name || '').trim()
  if (!text) return '--'
  return text.slice(-2).toUpperCase()
}

type UserAvatarProps = {
  user?: Record<string, any> | null
  fallbackName?: string
  size?: number
  title?: string
}

export const UserAvatar: React.FC<UserAvatarProps> = ({
  user,
  fallbackName,
  size = 24,
  title,
}) => {
  const displayName = getUserDisplayName(user, fallbackName)
  const avatarUrl = getUserAvatarUrl(user)
  const [imageBroken, setImageBroken] = useState(false)

  useEffect(() => {
    setImageBroken(false)
  }, [avatarUrl])

  const canRenderImage = Boolean(avatarUrl) && !imageBroken
  const fontSize = useMemo(() => {
    if (size <= 24) return 12
    if (size <= 30) return 13
    return Math.max(14, Math.floor(size / 2.4))
  }, [size])
  const avatarName = firstFilled(user?.username, fallbackName, user?.display_name, displayName)
  const initials = useMemo(() => getNameInitials(avatarName), [avatarName])

  return (
    <span
      title={title || displayName}
      style={{
        width: size,
        height: size,
        minWidth: size,
        borderRadius: '50%',
        overflow: 'hidden',
        background: canRenderImage ? '#eef2ff' : 'linear-gradient(135deg, #4c57f8 0%, #6b74ff 100%)',
        color: '#fff',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize,
        fontWeight: 700,
        lineHeight: 1.1,
        letterSpacing: initials.length > 1 ? '-0.04em' : 0,
        verticalAlign: 'middle',
        boxShadow: canRenderImage ? 'inset 0 0 0 1px rgba(76, 87, 248, 0.12)' : '0 8px 16px rgba(76, 87, 248, 0.2)',
        flexShrink: 0,
        padding: size <= 24 ? 1 : 2,
        boxSizing: 'border-box',
      }}
    >
      {canRenderImage ? (
        <img
          src={avatarUrl}
          alt={displayName}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={() => setImageBroken(true)}
        />
      ) : (
        initials
      )}
    </span>
  )
}

type UserIdentityProps = {
  user?: Record<string, any> | null
  fallbackName?: string
  avatarSize?: number
  gap?: number
  secondaryText?: string
  nameColor?: string
  nameWeight?: number
}

const UserIdentity: React.FC<UserIdentityProps> = ({
  user,
  fallbackName,
  avatarSize = 24,
  gap = 8,
  secondaryText,
  nameColor = '#2b2f36',
  nameWeight = 500,
}) => {
  const displayName = getUserDisplayName(user, fallbackName)

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap, minWidth: 0, lineHeight: 1 }}>
      <UserAvatar user={user} fallbackName={fallbackName} size={avatarSize} />
      {secondaryText ? (
        <span style={{ minWidth: 0, display: 'inline-flex', flexDirection: 'column' }}>
          <EllipsisText value={displayName} style={{ color: nameColor, fontWeight: nameWeight, lineHeight: 1.2 }} />
          <EllipsisText value={secondaryText} style={{ color: '#8b9098', fontSize: 12, marginTop: 2 }} />
        </span>
      ) : (
        <EllipsisText
          value={displayName}
          style={{
            lineHeight: `${avatarSize}px`,
            color: nameColor,
            fontWeight: nameWeight,
          }}
        />
      )}
    </span>
  )
}

export default UserIdentity
