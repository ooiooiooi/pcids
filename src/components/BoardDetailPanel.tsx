import { resolveMediaUrl } from '../utils/mediaUrl'

type BoardDetailPanelProps = {
  record: any
  burnInterfaceText: string
  communicationInterfaceText: string
  onPreviewImage?: (imageUrl?: string) => void
}

const displayText = (value: any) => {
  const text = String(value ?? '').trim()
  return text || '-'
}

const getChipClassName = (chipType: string) => {
  const normalized = String(chipType || '').trim().toUpperCase()
  if (normalized === 'ARM') return 'board-detail-chip board-detail-chip--arm'
  if (normalized === 'PIC') return 'board-detail-chip board-detail-chip--pic'
  if (normalized === 'DSP') return 'board-detail-chip board-detail-chip--dsp'
  if (normalized === 'FPGA') return 'board-detail-chip board-detail-chip--fpga'
  if (normalized === 'ALTERA-CPLD') return 'board-detail-chip board-detail-chip--cpld'
  if (normalized === '其他') return 'board-detail-chip board-detail-chip--other'
  return 'board-detail-chip'
}

const BoardDetailPanel: React.FC<BoardDetailPanelProps> = ({
  record,
  burnInterfaceText,
  communicationInterfaceText,
  onPreviewImage,
}) => {
  const imageUrl = resolveMediaUrl(record?.board_image)
  const chipTypeText = displayText(record?.chip_type)
  const metadataItems = [
    { label: '序列号', value: displayText(record?.serial_number) },
    { label: '芯片型号', value: displayText(record?.chip_model) },
    { label: '通信接口', value: displayText(communicationInterfaceText) },
    { label: '烧录接口', value: displayText(burnInterfaceText) },
  ]

  return (
    <div className="board-detail-panel">
      <div className="board-detail-media-wrap">
        {imageUrl ? (
          <button
            type="button"
            className="board-detail-media-button"
            onClick={() => onPreviewImage?.(imageUrl)}
          >
            <img src={imageUrl} alt="board" className="board-detail-image" />
          </button>
        ) : (
          <div className="board-detail-image-empty">暂无图片</div>
        )}
      </div>

      <div className="board-detail-heading-row">
        <div className="board-detail-title">{displayText(record?.name)}</div>
        <span className={getChipClassName(chipTypeText)}>{chipTypeText}</span>
      </div>

      <div className="board-detail-meta-grid">
        {metadataItems.map((item) => (
          <div key={item.label} className="board-detail-meta-item">
            <span className="board-detail-meta-label">{item.label}</span>
            <span className="board-detail-meta-value">{item.value}</span>
          </div>
        ))}
      </div>

      <div className="board-detail-description-list">
        <div className="board-detail-description-item">
          <div className="board-detail-description-label">配置说明</div>
          <div className="board-detail-description-value">{displayText(record?.config_description)}</div>
        </div>
        <div className="board-detail-description-item">
          <div className="board-detail-description-label">使用说明</div>
          <div className="board-detail-description-value">{displayText(record?.usage_description)}</div>
        </div>
      </div>
    </div>
  )
}

export default BoardDetailPanel
