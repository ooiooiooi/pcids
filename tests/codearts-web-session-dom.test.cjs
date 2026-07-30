const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')

const runtimePath = path.resolve(
  process.cwd(),
  'tools/codearts_release_debugger/browser_runtime/codearts_web_session.js',
)
const {
  expandRepositoryFolder,
  findDownloadAddressLink,
  isRootFilesListPayload,
  projectMetadataFromPage,
  selectRepositoryFile,
} = require(runtimePath)
const { chromium } = require(
  path.resolve(
    process.cwd(),
    'tools/codearts_release_debugger/browser_runtime/node_modules/playwright',
  ),
)

test('only the first project root page can seed a full sync snapshot', () => {
  assert.equal(
    isRootFilesListPayload({ projectId: 'project-1', pageNo: 1 }, 'project-1'),
    true,
  )
  assert.equal(
    isRootFilesListPayload(
      { projectId: 'project-1', parentId: 'folder-1', pageNo: 1 },
      'project-1',
    ),
    false,
  )
  assert.equal(
    isRootFilesListPayload({ projectId: 'project-1', pageNo: 2 }, 'project-1'),
    false,
  )
  assert.equal(
    isRootFilesListPayload({ projectId: 'another-project', pageNo: 1 }, 'project-1'),
    false,
  )
})

test('按目录树选择文件后定位右侧“下载地址”链接', async (t) => {
  const browser = await chromium.launch({ headless: true })
  t.after(() => browser.close())
  const page = await browser.newPage()
  await page.route('https://example.test/cloudartifact/v1/files/file-123/info', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'success', result: { version: 'latest' } }),
    })
  })
  await page.setContent(`
    <base href="https://example.test/">
    <nav class="devui-breadcrumb">首页 / 程控安装部署系统 / 制品仓库 / 软件发布库</nav>
    <div role="tree" style="position:absolute;left:20px;top:50px;width:500px">
      <div role="treeitem">
        <button class="tree-switcher" onclick="document.getElementById('hongmeng').hidden=false">+</button>
        <span>鸿蒙</span>
        <div id="hongmeng" hidden>
          <div role="treeitem">
            <button class="tree-switcher" onclick="document.getElementById('al321').hidden=false">+</button>
            <span>AL321</span>
            <div id="al321" hidden>
              <div role="treeitem" data-file-id="file-123" onclick="
                  fetch('/cloudartifact/v1/files/file-123/info');
                  setTimeout(() => {
                    document.getElementById('detail').innerHTML =
                      '<div><span>下载地址</span><span class=&quot;flex-grow-0 flex-shrink truncate devui-link&quot; title=&quot;https://example.test/cloudartifact/v1/files/download?filename=BOOT_with_bit.bin&quot; onclick=&quot;const link=document.createElement(\\'a\\');link.download=\\'BOOT_with_bit.bin\\';link.href=\\'data:application/octet-stream;base64,ZmlybXdhcmU=\\';link.click()&quot;>https://example.test/cloudartifact/v1/files/download?filename=BOOT_with_bit.bin</span></div>'
                  }, 1200)
                ">
                <span>BOOT_with_bit.bin</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <main id="detail" style="position:absolute;left:700px;top:100px"></main>
  `)

  const project = await projectMetadataFromPage(page, 'project-id')
  assert.equal(project.name, '程控安装部署系统')
  assert.equal(project.source, 'page_breadcrumb')

  const navigation = []
  await expandRepositoryFolder(page, '鸿蒙', 'AL321', navigation)
  await expandRepositoryFolder(page, 'AL321', 'BOOT_with_bit.bin', navigation)
  await selectRepositoryFile(page, {
    artifactName: 'BOOT_with_bit.bin',
    artifactId: 'file-123',
  }, navigation)

  const downloadLink = await findDownloadAddressLink(page)
  assert.ok(downloadLink)
  assert.match(downloadLink.source, /下载地址/)
  assert.equal(downloadLink.element.tag, 'SPAN')
  assert.match(downloadLink.element.class_name, /devui-link/)
  assert.deepEqual(
    navigation.filter(item => item.stage === 'folder').map(item => item.outcome),
    ['opened', 'opened'],
  )
  assert.equal(navigation.at(-1).info_status, 200)

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    downloadLink.locator.click(),
  ])
  assert.equal(download.suggestedFilename(), 'BOOT_with_bit.bin')
})
