import fs from 'node:fs/promises'
import path from 'node:path'

const src = 'C:\\Users\\pc\\Desktop\\tftpd32.exe'
const dst = 'D:\\workspace\\pcids\\.runtime\\tftpd32.exe'
const resultPath = 'D:\\workspace\\pcids\\.runtime\\copy_tftpd32_result.json'

async function main() {
  const result = {
    src,
    dst,
    src_exists: false,
    dst_exists: false,
    copied: false,
  }

  try {
    await fs.access(src)
    result.src_exists = true
    await fs.mkdir(path.dirname(dst), { recursive: true })
    await fs.copyFile(src, dst)
    result.dst_exists = true
    result.copied = true
  } catch (error) {
    result.error = error instanceof Error ? error.message : String(error)
    try {
      await fs.access(dst)
      result.dst_exists = true
    } catch {}
  }

  await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8')
}

main().catch(async (error) => {
  const result = {
    src,
    dst,
    src_exists: false,
    dst_exists: false,
    copied: false,
    fatal_error: error instanceof Error ? error.message : String(error),
  }
  await fs.mkdir(path.dirname(resultPath), { recursive: true })
  await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8')
  process.exit(1)
})
