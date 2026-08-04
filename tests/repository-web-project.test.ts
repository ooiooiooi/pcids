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
  assert.match(source, /configurationMode === 'web'\s*\?\s*\{\}\s*:\s*\{ project_name:/)
  assert.match(source, /configurationMode !== 'web'\s*\?\s*\(/)
  assert.doesNotMatch(source, /无需填写；保存后会从制品仓库页面同步/)
  assert.match(source, /syncResult\?\.data\?\.project_name/)
  assert.doesNotMatch(source, /false && configurationMode === 'web'/)
  assert.match(source, /autoComplete="one-time-code"/)
  assert.doesNotMatch(source, /devops_url/)
  assert.doesNotMatch(source, /DevOps 域名/)
  assert.doesNotMatch(source, /VITE_CODEARTS_PRIVATE_TEST_PASSWORD/)
  assert.doesNotMatch(source, /cwgy-57373/)
})

test('Web 页面库展示详情版本和时间，并在下载失败时给出轻提示', () => {
  assert.match(source, /label: '发布版本'/)
  assert.match(source, /fileDetail\.versionName/)
  assert.match(source, /fileDetail\.created_time/)
  assert.match(source, /fileDetail\.modified_time/)
  assert.match(source, /content: '下载制品失败'/)
})

test('CodeArts 项目同步完成后触发数据仓库同步', () => {
  assert.match(source, /triggerCodeartsAutoSync/)
  assert.match(source, /trigger_source: 'codearts_project_sync'/)
  assert.match(source, /await triggerRepositoryDataSync\(projectKey\)/)
})

test('repository mode only shows its own projects and stays empty without one', () => {
  assert.match(source, /const \[repositoryMode, setRepositoryMode\] = useState<CodeartsRepositoryMode>\('web'\)/)
  assert.match(source, /if \(!key\) return \[\]/)
  assert.match(source, /projects\.find\(\(project\) => project\.repositoryMode === repositoryMode\)/)
  assert.match(source, /currentProject\.repositoryMode === repositoryMode/)
  assert.match(source, /initialProjectContextRef\.current = \{ projectKey: '', projectName: '' \}/)
  assert.match(source, /setRepositoryMode\(nextMode\)/)
  assert.match(source, /setRepositoryProjectContext\(\{ projectKey: '', projectName: '' \}\)/)
})

test('new project follows the currently selected repository mode', () => {
  assert.match(source, /const configurationMode: CodeartsRepositoryMode = configurationModeOverride \|\| repositoryMode/)
  assert.match(source, /repository_mode: configurationMode === 'web' \? 'private' : configurationMode/)
  assert.match(source, /configurationMode === 'web'\s*\?\s*\{ private_source: 'web' \}/)
  assert.match(source, /configurationMode === 'private'\s*\?\s*\{ private_source: 'api', private_repo_id: repoIds\[0\] \|\| '' \}/)
  assert.match(source, /:\s*\{ private_source: null, repo_ids: repoIds \}/)
})

test('failed first sync rolls back the newly-created project', () => {
  assert.match(source, /let createdProjectId = ''/)
  assert.match(source, /configResult\?\.data\?\.created/)
  assert.match(source, /await repositoryApi\.rollbackNewCodeartsProject\(createdProjectId\)/)
})
