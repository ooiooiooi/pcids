import { Select } from 'antd'

export const LIST_PAGE_SIZE_OPTIONS = [10, 30, 50]

function buildPageSizeSelectOptions(options: number[]) {
  return options.map((size) => ({
    label: `${size}条/页`,
    value: size,
  }))
}

type RenderListPaginationTotalOptions = {
  pageSizeOptions?: number[]
  disablePageSizeChange?: boolean
}

export function renderListPaginationTotal(
  total: number,
  pageSize: number,
  onPageSizeChange: (size: number) => void,
  options?: RenderListPaginationTotalOptions,
) {
  const pageSizeOptions = options?.pageSizeOptions?.length ? options.pageSizeOptions : LIST_PAGE_SIZE_OPTIONS

  return (
    <span className="list-pagination-total">
      <Select
        className="list-page-size-select"
        classNames={{ popup: { root: 'list-page-size-select-dropdown' } }}
        value={pageSize}
        options={buildPageSizeSelectOptions(pageSizeOptions)}
        onChange={onPageSizeChange}
        disabled={options?.disablePageSizeChange}
        popupMatchSelectWidth={false}
        style={{ width: 92 }}
        suffixIcon={<span style={{ fontSize: 10, color: '#8e98aa' }}>▾</span>}
      />
      <span className="list-pagination-total-count">共 {total} 条</span>
    </span>
  )
}
