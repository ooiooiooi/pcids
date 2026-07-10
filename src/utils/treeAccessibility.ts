export const patchTreeHiddenInputs = (container: HTMLElement | null, baseName: string) => {
  if (!container) return

  const hiddenInputs = container.querySelectorAll<HTMLInputElement>('input[aria-label="for screen reader"]')

  hiddenInputs.forEach((input, index) => {
    const suffix = index === 0 ? '' : `-${index + 1}`
    if (!input.id) {
      input.setAttribute('id', `${baseName}-sr${suffix}`)
    }
    if (!input.name) {
      input.setAttribute('name', `${baseName}Sr${suffix}`)
    }
    if (!input.autocomplete) {
      input.setAttribute('autocomplete', 'off')
    }
  })
}
