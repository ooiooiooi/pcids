import { useEffect, useState } from 'react'
import { BorderOutlined, CloseOutlined, MinusOutlined, SwitcherOutlined } from '@ant-design/icons'

const DesktopWindowControls: React.FC = () => {
  const windowControls = window.electronAPI?.windowControls
  const [isMaximized, setIsMaximized] = useState(false)

  useEffect(() => {
    if (!windowControls) return

    let active = true
    const syncMaximizedState = () => {
      void windowControls.isMaximized().then((maximized) => {
        if (active) setIsMaximized(maximized)
      })
    }

    syncMaximizedState()
    window.addEventListener('resize', syncMaximizedState)
    return () => {
      active = false
      window.removeEventListener('resize', syncMaximizedState)
    }
  }, [windowControls])

  if (!windowControls) return null

  const handleToggleMaximize = async () => {
    setIsMaximized(await windowControls.toggleMaximize())
  }

  return (
    <div className="desktop-window-controls" aria-label="窗口控制">
      <button type="button" title="最小化" aria-label="最小化" onClick={windowControls.minimize}>
        <MinusOutlined />
      </button>
      <button
        type="button"
        title={isMaximized ? '还原' : '最大化'}
        aria-label={isMaximized ? '还原' : '最大化'}
        onClick={() => void handleToggleMaximize()}
      >
        {isMaximized ? <SwitcherOutlined /> : <BorderOutlined />}
      </button>
      <button type="button" className="desktop-window-controls__close" title="关闭" aria-label="关闭" onClick={windowControls.close}>
        <CloseOutlined />
      </button>
    </div>
  )
}

export default DesktopWindowControls
