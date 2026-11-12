# 戒指裸板工程采集与上位机导入

2026-11-12。本阶段服务于 N1 原始流、保存和重放联调。用户确认戒指尚无外壳、当前有线连接电脑；臂环暂不可连接。佩戴轴向标定、跨设备时间对齐和正式被试采集暂不开展。

有线供电本身不代表 IMU 损坏。裸板数据可用于验证通信、原帧解析、运动响应和时间字段，但不能代表稳定佩戴下的手指动作。

## 当前可执行流程

在项目根目录使用平台采集优先：它保存完整配置回复、接收时间和请求日志，默认不补传历史。先在 eRingWeb 点“断开”，两者不能同时占用戒指。

```powershell
.\.venv\Scripts\python.exe scripts/experiment_session.py create --subject DEVICECHECK --session 20261112_RING --trial T003 --task ring_board_check --consent-scope user_authorized_hardware_debug --wear-side unknown --purpose engineering --ring-mount bare_board --ring-power tethered
$episodePath = 'data/episodes/DEVICECHECK/20261112_RING/T003'
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-scan --seconds 8
# 替换成扫描并确认属于本设备的地址；当前主机信息保存在 config/host.local.yaml。
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-capture --episode $episodePath --capture-id ring01 --address '实际地址' --seconds 30
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-derive --episode $episodePath --capture-id ring01 --derive-id decode01
.\.venv\Scripts\python.exe scripts/experiment_session.py status --episode $episodePath
```

示例编号只在未使用时有效；重试使用新的 trial/capture-id/derive-id，不覆盖旧记录。无需接臂环才能完成上述工程流程。

## 使用 eRingWeb 1.0.6 时

实际验证过“扫描指环 → 连接 → 暂停实时 → 断开 → 诊断、本机档案与回放 → 导出原帧 JSONL / 导出样本 CSV”。本次连接后软件自动开启实时，随后观察到自动补传历史；未手动点击补传。不要把“暂停实时”理解为已停掉全部后台通信，导出前应断开连接并固定所选本机数据集。

同时保留 JSONL、CSV，以及程序 `data/<device>/<dataset>/` 中的 `events.jsonl` 和 `cursor.json`；后两项只作为辅助证据，不直接作为已校准的时钟映射。可用平台 `attach --modality ring` 复制附件并记录哈希。JSONL 导入示例：

```powershell
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-import --episode $episodePath --capture-id web01 --file '实际导出的ering_frames.jsonl'
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-derive --episode $episodePath --capture-id web01 --derive-id web_decode01
```

平台兼容自身 `raw_hex` 和上位机 `frame_hex`，原始输入完整另存 `source.jsonl`，按字节重新解码和校验。冲突的双字段输入拒绝处理。不要只留 CSV：本次厂商 CSV 没有 replay 标记、逐帧接收时间和完整采样配置回复，无法独立区分历史归属。

本次导出含 2,552 帧、38,142 个六轴样本：其中 2,420 帧 / 36,162 样本为历史补传（采集代149、150）；132帧 / 1,980 样本为非补传（采集代155）。平台默认只派生后者。全部CSV整数和 `sample_us` 与独立解码逐项相符；非补传仍不自动证明属于当前被试或录制窗口，厂商档案导入的 `attribution` 保持 `unknown`。

## 时间字段的解释

| 字段 | 含义与处理 |
|---|---|
| 固件 `capture_id` | 设备采集代；不是被试编号，也不是平台 capture-id 目录名。换代隔离处理。 |
| `first_sample_us` / `batch_timestamp_us` | 原帧中的设备批次时间锚点，保留整数，不当作 UTC。 |
| `period_us` | 批内标称间隔，本机目前返回20,000µs。 |
| `device_time_us` / 厂商 `sample_us` | 批首锚点加样本索引×标称间隔；不是独立测得的每样本时刻。 |
| `host_utc_ns` / `host_monotonic_ns` | 平台直接采集可保存的接收时间；厂商帧导出缺失时留空，不补为导入时间。 |
| `time_epoch` | 平台在同采集代内遇到配置切换或原始锚点回退时增加的处理段号；不自动判断设备复位原因。 |
| 厂商事件 `utc_offset_us` | 上位机计算的偏移候选。本次原样保存，未验证误差/漂移，不应用于统一时间。 |

批界残差为：下一批原始首时刻减去上一批首时刻、样本数和标称周期推算的下一批起点。`nominal_batch_boundary_mismatch` 仅表示残差非零；真正原始锚点回退才报 `device_time_backwards`。本次厂商非补传段残差中位数−480µs，无原始锚点回退。此前第三段裸板记录另有−19,424µs残差，原因未定，不能直接归因为丢样、复位或确定采样频率。

## 多被试、多时段的保存边界

继续沿用 `data/episodes/<subject>/<session>/<trial>/`。同一被试每次到访/时间段建新 session，每次任务尝试建新 trial；重复采集单独编号。三个设备可以独立保存，再导入同一试次，前提是明确记录设备、采集代、录制窗口和被试归属，不能把“同一个导出文件”当作“同一个被试”。跨档案合并还需检查 `(设备, capture_id, sequence)` 重复与冲突。

新建试次默认 `purpose=engineering`。裸板即便被误写为 research，`status` 也返回 `engineering_only`；其他试采/研究记录返回 `pending_validation`，旧记录缺失字段为 unknown。当前工具没有正式训练准入验收，`training_eligible` 始终为 false；文件齐全、CSV非空或声明“已标定”均不能自动获得批准。

目前保存原始字节、原始时间、软件版本、文件哈希、失败日志及采集条件。等外壳/固定佩戴完成后再安排轴向与重戴标定；等臂环恢复连接、相机具备录制能力后再验证共同事件、时钟偏移和漂移。现阶段不生成三流对齐文件，也不计入正式多被试样本量或 N2 完整 episode。

实际证据见 [当日联调记录](../knowledge_base/devices/ering_n1_2026-11-12.md)。T001 为此前平台三段，T002 为本次上位机导出验证，均只保存在本机忽略目录。
