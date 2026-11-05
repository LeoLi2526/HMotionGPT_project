# 构建记录

effort=balanced / ask=never / assurance=draft。对齐 N1/N2 可追溯 episode；非硬件验证。

| Rung | 功能 | 验收命令 | Tier | 状态 |
|---|---|---|---|---|
| F0 | CLI create → episode.json | `python -B -m unittest discover -s tests -p test_experiment_session.py -v` | MUST | PASS |
| F1 | mark / attach / 拒绝覆盖 | 同上（累计测试） | MUST | PASS |
| F2 | 复用 capture 与失败记录 | 同上（累计测试） | MUST | PASS |
| F3 | derive 原始行、片段、混合宽度、chunk 时间 | 同上（累计测试） | MUST | PASS |
| F4 | status / CLI 集成 | 同上（累计测试） | MUST | PASS |

## 执行记录

- F0：累计 3 tests PASS，真实 CLI subprocess 创建 metadata；临时 fixture 清理。首次验收 exit 0。
- F1：工具重启前 patch 已落盘；重启后核对文件并重跑，累计 5 tests PASS / exit 0。
- F2：累计 8 tests PASS / exit 0。直接复用现有 capture；测试只替换 require_serial 为 FakePort，无真实串口访问。
- F3：累计 12 tests PASS / exit 0。含 CRLF 跨 chunk、同 chunk 多行、8→9→14 模式变化、物理行序/偏移、首行坏片段、尾片段、UTC 倒退、hash/chunk 损坏拒绝。
- F4：累计 17 tests PASS / exit 0，耗时 3.579s。增加完整列宽但缺 LF 的尾片段、非法/倒退 monotonic、三模态状态与跨 subject 隔离、空文件/篡改/未完成采集、真实 subprocess JSON stdout 与只读验证。
- F0–F4 首次有效验收均通过，无需修复迭代；没有存活 stub。测试临时数据均标记 synthetic，放在本任务目录临时子目录并由 unittest 清理。
- 主 agent 在用户消息中报告 DEVICECHECK/20261105_PM/T001 实机 10s、668 行八列和 1 尾片段；该报告与上述离线测试分开。本执行者未打开串口，未执行实机采集。
- 完成后实际执行只读 `python -B scripts/experiment_session.py status --episode data/episodes/DEVICECHECK/20261105_PM/T001`，exit 0：armband01 completed/原始校验通过，26,721 bytes、2,005 chunks；decode01 报668有效行；camera/ring 缺失，三设备时钟 unknown，同步 unverified，保留 fragments_present。该步骤读取主 agent 的实采产物，未新增或更改 episode。
- `python -B scripts/experiment_session.py --help` exit 0，列出全部6个子命令；`git diff --check` exit 0（新增文件未暂存，主要机械证据是上述测试）。交付文件仍未提交/推送，主 agent 统一整合。

- 开始前读取 AGENTS、PROJECT_GOAL、SUBAGENT_POLICY、OPERATIONS_POLICY、ROADMAP、技能和共享说明、现有 capture/tests、设备证据和会议公开摘要。
- HEAD = 0b74d1f8bdaf9f51b29712cb7855c2e5601382b5；初始工作树干净。
- 当前执行者：Codex；运行时未提供可核实的具体型号/推理档位，不伪造身份。主 agent 负责最终审阅；不在本任务追加嵌套 reviewer 或占用硬件。
- 独占写范围不包含 Git 整合和共享文档；rung 提交留给主 agent 统一处理。

## 延后

主 agent 集成验证（2026-11-05）：新 CLI 在真实 COM6 上完成 create/mark/capture/derive/status，10 秒、668 行八列、1 个尾片段；证据位于被忽略的 `data/episodes/DEVICECHECK/20261105_PM/T001`。缺失 camera/ring 与未验证同步状态正确返回。完整单元测试共36项通过（本模块17、时钟映射16、原采集器3），便携性检查通过。GUI 实际鼠标和键盘操作已完成毫秒时间戳采样，证据及语义限制在 `docs/MULTISUBJECT_CAPTURE.md`。实现由主agent及两个继承当前模型的子agent完成；独立review请求使用 gpt-6-astra / xhigh，同家族审阅只能提供暂定意见。

- 实机采集、设备时钟与三流同步验证、重放工具、真实机器人实验、共享 docs 与最终 review 由主 agent 负责。

## 阻塞

- 最终复核：共40项测试通过（会话20、时钟17、既有采集器3），便携性检查通过；实机工程样本再次只读校验通过。
- 两轮假设审阅已完成，原文见 REVIEW_ROUND1.md / REVIEW_ROUND2.md。第一轮发现2项未声明语义、1项接口语义、1项过时账本以及2项实际缺陷；已修复大小写身份冲突、损坏事件误计数，并要求时钟拟合与检查两组残差同时满足门槛。第二轮 semantic_undeclared=0、verdict=clean；review_independence=same-family，acceptance_status=provisional。审阅者另执行37项相关离线测试和故障反例重放。
- 无软件实现阻塞，无存活 stub。F0–F4 已通过，实机臂环链路已验证；戒指/相机实时适配、三流同步与重放、完整三模态试采尚未验收。测试和同家族暂定审阅不代表这些设备节点已通过或得到研究结论。
