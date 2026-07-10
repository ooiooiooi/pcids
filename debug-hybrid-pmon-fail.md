# Debug Session: hybrid-pmon-fail
- **Status**: [OPEN]
- **Issue**: 混合协同 TFTP+串口流程中，`reboot` 后长时间停在“等待 AUTO 窗口并发送 Ctrl+U 打断自启动 / 等待串口进入 PMON 命令行”，最终仍失败，用户怀疑关键阶段串口状态不可见、判定不可信。
- **Debug Server**: N/A
- **Log File**: N/A

## Reproduction Steps
1. 在前端发起 `sylixos_ls2k_ftp_serial_flash` 混合协同任务。
2. 观察串口阶段事件流，从 `发送 q`、`发送 reboot` 到 `等待 AUTO 窗口并发送 Ctrl+U 打断自启动`。
3. 关注是否出现 PMON 可交互证据，以及是否在失败事件中看见 reboot 后串口摘要。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | 事件流虽然增加了回显记录，但前端没有展示 `_lines` 字段，导致用户看不到 reboot 后串口证据。 | High | Low | Rejected：前端详情页直接展示 `task.result`，而后端 `ExecutionMonitor.render()` 会把 `_lines` 展平成多行文本。 |
| B | `_interrupt_pmon_auto_boot()` 实际拿到了串口输出，但 `monitor.record()` 的触发条件或传参使关键信息没有上报。 | High | Low | In Progress：已补充 `bytes/classified_state/saw_*` 观测，并写回最终尝试摘要，待复现验证。 |
| C | `reboot` 后确实没有出现 `AUTO/ctrl-u` 关键窗口，当前等待逻辑卡满 30s 属于真实现场，而不是日志丢失。 | Medium | Low | Pending |
| D | 串口读到的是分片/乱码，`_tail_nonempty_lines()` 或事件摘要截断导致关键行被过滤掉。 | Medium | Low | In Progress：已增加 `<无串口回显>` 占位和字节数统计，待复现验证。 |
| E | 前端任务日志接口返回了这些事件，但页面轮询/映射层丢弃了部分运行中事件。 | Medium | Medium | Rejected：SSE 直接回传 `task.result`，轮询层没有丢弃 `_lines` 的单独逻辑。 |
| F | `q` 后没有真正回到可执行命令环境，`reboot` 发出前目标仍未就绪或已失去回显。 | High | Low | In Progress：已新增 `q 前初始串口回显 / q 后串口回显 / 重启前串口回显` 三段观测，待复现验证。 |

## Log Evidence
- [已确认] `ExecutionMonitor.render()` 会把 `_lines` 逐行拼进文本结果；前端详情页只渲染 `detailTask.result`，不存在单独吞掉 `_lines` 的分支。
- [已添加观测] `backend/routers/tasks.py` 现在会在 `reboot`、`AUTO/Ctrl+U`、`PMON 探活` 三阶段上报 `bytes`、`classified_state`、`saw_auto/saw_ctrl_u_prompt/saw_enter_prompt/saw_probe_*`，并在空回显时写出 `<无串口回显>`。
- [已添加观测] 为验证“第一步是否就没对”，新增 `q 前初始串口回显`、`q 后串口回显`、`重启前串口回显` 三段证据，且同样写入最终尝试摘要。

## Verification Conclusion
- 等用户复现后，根据新增运行时证据判断是“未收到串口回显”、“收到回显但未命中 AUTO 窗口”，还是“已进入 PMON 启动但探活失败”。
