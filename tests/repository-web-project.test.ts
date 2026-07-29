import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const source = fs.readFileSync(
  path.resolve(process.cwd(), 'src/pages/Repository/index.tsx'),
  'utf8',
)

test('Web 页面库保留独立项目模式并支持修改项目', () => {
  assert.match(source, /private_source \|\| n\.repo_detail\?\.private_source/)
  assert.match(source, /key: 'edit-project', label: '修改当前项目'/)
  assert.match(source, /projectConfigIntent === 'edit' \? '修改项目' : '新建项目'/)
  assert.match(source, /label="项目名称" name="project_name"/)
  assert.match(source, /project_name: String\(values\.project_name/)
  assert.doesNotMatch(source, /false && configurationMode === 'web'/)
  assert.match(source, /autoComplete="one-time-code"/)
  assert.doesNotMatch(source, /VITE_CODEARTS_PRIVATE_TEST_PASSWORD/)
  assert.doesNotMatch(source, /cwgy-57373/)
})

test('CodeArts 项目同步完成后触发数据仓库同步', () => {
  assert.match(source, /triggerCodeartsAutoSync/)
  assert.match(source, /trigger_source: 'codearts_project_sync'/)
  assert.match(source, /await triggerRepositoryDataSync\(projectKey\)/)
})
