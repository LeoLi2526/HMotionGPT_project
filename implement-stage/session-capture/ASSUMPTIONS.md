# 假设账本

ASK mode: never

| ID | 未确定问题 | 选择 | Class | Source |
|---|---|---|---|---|
| A-001 | ID 与唯一性作用域 | subject/session/trial 联合唯一；每段限 ASCII 字母、数字、下划线、短横线，拒绝 Windows 保留名；同 session 可跨 subject 复用。保留首次输入拼写；create 在每级现有父目录扫描 casefold 匹配，发现不同拼写即拒绝，跨平台一致；不统一转小写，不改既有 capture IDs | interface | default; sweep (B001 review fix) |
| A-002 | 同意范围的验证 | 必填非空文本，仅记录操作者声明，不自动认证同意书 | semantic | default |
| A-003 | 时间含义 | mark 为 CLI 处理时的 UTC/monotonic ns，不回填操作者按键时刻；键盘或 agent 事件均不证明硬件同步；monotonic 只在同机同次启动可比 | semantic | user |
| A-004 | 行时间定义 | LF 终止行；主机接收完成时间取包含 LF 的 chunk；同 chunk 可共享时间；不插值、不创造设备采样时间 | semantic | default |
| A-005 | 列宽含义 | 接受 8/9/14 整数列，命名 value_1..14，空列补空而非 0；不宣称物理单位或第9列为时钟；模式变化仅指列宽变化 | semantic | default |
| A-006 | 片段与首行 | 尾部无 LF 视为片段；首行起点未知，若语法有效仍保留但标 start_boundary_unverified；其他坏行单独报告，不能据此计算丢包率 | semantic | default |
| A-007 | 实采重试 | 每次 capture 需唯一 capture ID；失败目录及日志保留，重试用新 ID；一 episode 可有多次 capture；derive 显式选择 ID | interface | default |
| A-008 | 多进程控制 | capture 独占其 ID，mark 每次独立 UUID 文件，可与 capture 并行；附件/派生/episode 均独占创建；不修改共享 manifest | interface | default |
| A-009 | 附件时钟 | 复制原件并记录 SHA-256、大小、原始文件名和导入主机时间；不把文件 mtime 或导入时间当设备时间，设备时钟来源固定 unknown | semantic | user |
| A-010 | status 完整性含义 | 模态存在须非空且原始文件校验匹配；不代表信号有效/同步完成；空文件、失败采集、unknown 时钟、未验证同步单列 QC | semantic | default |
| A-011 | 派生不可覆盖 | 每次 derive 需唯一输出 ID；保留原 raw/chunks，记录来源 hash；chunk 不连续/时间字段非法时拒绝推断并返回错误 | interface | default |
| A-012 | session 时间 | session 为操作者提供的时段 ID；记录创建 UTC ns；不推定名称编码日期或限制被试跨日参与 | semantic | default |
| A-013 | CLI 附件与事件接口 | attach 必填 --attachment-id，每个模态内唯一；mark --event 必填、--source 默认 agent（可选 keyboard）；event UUID 文件 | interface | default |
| A-014 | 采集参数与错误 | 显式 COM 正整数编号，baud 默认 115200（沿用既有工具候选值），--seconds/--duration 必填有限正数；直接调用既有 capture，stdout/stderr 入日志，失败 result.json 保留且退出非零 | interface | default |
| A-015 | 派生行序及片段报告 | row_index 从0开始计原始物理行（含坏行）；raw_offset 为字节偏移，raw_bytes 含 LF/CRLF；首行无效时另标 possible_leading_fragment，与 invalid 报告可重叠 | interface | default |
| A-016 | 失败采集的解析 | 仅 capture result.json 存在后可 derive；即使失败，raw/chunks 完整且 hash 正确仍允许解析并标 capture_incomplete；缺 chunk 不插值 | semantic | default |
| A-017 | 主机时间倒退 | UTC 可因校时倒退，保留原值并标 QC；monotonic 必须不下降；未知设备时间在 CSV 留空，设备时钟来源 unknown | semantic | default |
| A-018 | status 接口 | 只读 JSON stdout；状态检查完成返回0，即使有缺模态/QC；单项损坏记录 issues 并继续；至少一份非空且校验通过的原始文件才计模态 present；采集无 result 为 pending_or_incomplete。event_file_count 计 events/*.json 文件数，event_count 只计逐个校验通过的事件：JSON 对象、schema_version 整数1、event_id 与文件名 stem 一致且非空、event 非空字符串、host UTC/monotonic 为非负整数（不接受 bool）、hardware_sync 严格为 false、source 为 agent/keyboard、notes 为字符串、time_semantics/monotonic_scope 非空字符串；损坏记录 issues/invalid_events，保留文件且继续检查 | interface | default; sweep (B002 review fix) |
| A-019 | 外部独立工具 fit_clock_map 的单位与验收边界 | 主 agent 在委派前已声明：source 单位为 seconds，target 为 episode_relative_seconds；fit 至少包含2个不同源时间，check 至少1个；门槛必须显式提供，无 check 或外推不通过；source 事件身份由操作者核对。此约束对应外部 worker 的独立代码，无本模块内单一实现位置；本条仅补录委派前约束，不宣称已验证外部实现 | semantic | default(delegation contract before implementation) |

依赖 survey：复用现有 sichiray_capture.capture/INTEGER；其余使用 argparse、csv、json、hashlib、pathlib、unittest 等标准库，无新增框架或安装。

审阅补充（Source: sweep）：A-020（semantic）时钟映射采用全部 fit 事件等权普通最小二乘，不自动加权或剔除离群点，定位错误由操作者核对。A-021（semantic）独立审阅揭示仅检查 check 残差可能在 fit 严重不一致时仍通过；现决定同时要求 fit 和 check 的最大绝对残差均不大于显式门槛（包含等号），仍必须有独立 check 且不得外推。该门槛仅针对提供的事件，不能证明整段或三设备同步。A-022（interface）编号大小写冲突一律拒绝，保留首次建立的拼写；见 A-001 修订。
