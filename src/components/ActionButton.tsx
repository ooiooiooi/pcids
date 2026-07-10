import { Button, Space } from 'antd'
import type { ButtonProps, SpaceProps } from 'antd'

const joinClassNames = (...classNames: Array<string | false | null | undefined>) =>
  classNames.filter(Boolean).join(' ')

export const PagePrimaryButton: React.FC<ButtonProps> = ({ className, type, ...props }) => (
  <Button
    type={type || 'primary'}
    className={joinClassNames('ui-page-primary-button', className)}
    {...props}
  />
)

export const PageSecondaryButton: React.FC<ButtonProps> = ({ className, type, ...props }) => (
  <Button
    type={type || 'default'}
    className={joinClassNames('ui-page-secondary-button', className)}
    {...props}
  />
)

type ActionLinkButtonProps = ButtonProps & {
  danger?: boolean
}

export const ActionLinkButton: React.FC<ActionLinkButtonProps> = ({ className, danger, type, ...props }) => (
  <Button
    type={type || 'link'}
    danger={danger}
    className={joinClassNames('ui-action-link', danger && 'ui-action-link-danger', className)}
    {...props}
  />
)

type ActionButtonGroupProps = SpaceProps & {
  compact?: boolean
}

export const ActionButtonGroup: React.FC<ActionButtonGroupProps> = ({ className, compact, size, ...props }) => (
  <Space
    size={size || (compact ? 12 : 16)}
    className={joinClassNames('ui-action-group', compact && 'ui-action-group-compact', className)}
    {...props}
  />
)
