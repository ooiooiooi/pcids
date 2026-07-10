import React from 'react'
import { Modal, Popconfirm } from 'antd'
import type { ModalProps, PopconfirmProps } from 'antd'
import { ExclamationCircleFilled } from '@ant-design/icons'

type ActionConfirmProps = {
  title: React.ReactNode
  description?: React.ReactNode
  onConfirm: () => void | Promise<void>
  confirmLoading?: boolean
  okText?: string
  cancelText?: string
  children: React.ReactNode
  disabled?: boolean
} & Omit<PopconfirmProps, 'title' | 'description' | 'onConfirm' | 'children' | 'okText' | 'cancelText'>

const ActionConfirm: React.FC<ActionConfirmProps> = ({
  title,
  description,
  onConfirm,
  confirmLoading = false,
  okText = '确认',
  cancelText = '取消',
  children,
  disabled = false,
  ...rest
}) => (
  <Popconfirm
    title={title}
    description={description}
    onConfirm={onConfirm}
    okText={okText}
    cancelText={cancelText}
    icon={<ExclamationCircleFilled className="pcids-confirm-icon" />}
    overlayClassName="pcids-confirm-popover"
    okButtonProps={{ danger: true, loading: confirmLoading }}
    disabled={disabled}
    {...rest}
  >
    {children}
  </Popconfirm>
)

export default ActionConfirm

type ActionConfirmDialogProps = {
  open: boolean
  title: React.ReactNode
  description?: React.ReactNode
  children?: React.ReactNode
  onConfirm: () => void | Promise<void>
  onCancel: () => void
  confirmLoading?: boolean
  okText?: string
  cancelText?: string
} & Omit<ModalProps, 'open' | 'title' | 'onOk' | 'onCancel' | 'okText' | 'cancelText' | 'children'>

export const ActionConfirmDialog: React.FC<ActionConfirmDialogProps> = ({
  open,
  title,
  description,
  children,
  onConfirm,
  onCancel,
  confirmLoading = false,
  okText = '确认',
  cancelText = '取消',
  ...rest
}) => (
  <Modal
    open={open}
    title={title}
    onOk={onConfirm}
    onCancel={onCancel}
    okText={okText}
    cancelText={cancelText}
    className="pcids-confirm-dialog"
    okButtonProps={{ danger: true, loading: confirmLoading }}
    centered
    maskClosable={!confirmLoading}
    width={336}
    {...rest}
  >
    {children || description ? <div className="pcids-confirm-dialog__desc">{children || description}</div> : null}
  </Modal>
)
