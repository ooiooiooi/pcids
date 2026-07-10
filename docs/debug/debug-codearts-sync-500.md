# Debug Session: codearts-sync-500

- Status: OPEN
- Symptom: 点击“同步 CodeArts”后前端弹出“服务异常”，DevTools 中 `sync` 请求显示 `Internal Server Error`
- Expected: 云端删除文件后执行全量同步，后端应正常完成本地记录收敛并返回成功或明确业务错误

## Hypotheses

1. `/repositories/codearts/sync` 的响应体在成功路径下不满足 `Response` 模型，导致 FastAPI 响应序列化阶段报 500。
2. 删除旧仓库记录时仍有未覆盖的异常点，之前的本地文件清理容错没有覆盖真实抛错位置。
3. CodeArts 远端返回了异常目录项或字段值，使同步后半段的聚合/序列化逻辑报错。
4. `sync` 后串联触发的后续接口失败，被前端误判为同步接口自身失败。

## Plan

1. 启动独立调试会话并记录运行时日志。
2. 仅对 `sync` 及相关响应路径做最小插桩。
3. 复现一次问题并收集证据，确认真实抛错位置。
4. 基于证据做最小修复。
5. 重启前后端并验证业务流程与端口状态。

## Evidence

- 失败复现项目：`proj_e72b904d58554f23966d5e691cf8339e`
- 失败前端表现：`同步当前项目失败`，并弹出 `服务异常，请重启软件`
- 失败请求：`POST /api/repositories/codearts/sync`
- 失败根证据：删除 `repositories.id = 26` 时触发 `sqlite3.IntegrityError: FOREIGN KEY constraint failed`
- 外键来源：`tasks.repository_id -> repositories.id`、`records.repository_id -> repositories.id`

## Root Cause

- 云端文件被删除后，全量同步会尝试删除本地旧的 `Repository` 行。
- 历史烧录任务和履历记录仍然保留对这些 `Repository` 行的外键引用。
- SQLite 外键约束为 `NO ACTION`，因此删除旧仓库行时直接抛 500。
- 之前已修复“本地缓存文件删除失败会导致同步 500”的问题，但没有处理“历史任务/履历外键引用导致删除失败”的路径。

## Fix

1. 在删除旧 `Repository` 前，先解绑 `BurningTask.repository_id`。
2. 在删除旧 `Repository` 前，先解绑 `Record.repository_id`。
3. 对 `Record` 额外回填 `project_key`，避免解绑后历史记录失去项目归属。
4. 保留此前的本地文件清理容错逻辑。

## Verification

- 单元验证：`python -m unittest backend.tests.test_repository_codearts_sync backend.tests.test_repository_codearts_status backend.tests.test_repository_location_state`
- 结果：`Ran 9 tests ... OK`
- 修后重启：后端成功绑定 `8000`，前端成功绑定 `5173`
- 修后浏览器验证：
  - 项目 `程控安装部署系统 / proj_e72b904d58554f23966d5e691cf8339e`
  - `POST /api/repositories/codearts/sync`
  - 状态码：`200`
  - 页面提示：`CodeArts 同步成功，共同步 8 个文件`
  - 未再出现 `服务异常，请重启软件`
