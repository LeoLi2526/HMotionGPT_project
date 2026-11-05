# CLI 交付与主 agent 验证

工作目录 `E:/HMotionGPT_project`，Python 标准库；只有真实 capture 需要既有 pyserial。所有成功命令 stdout 为一份 JSON。错误退出非零；status 中的缺模态/QC 通过 JSON 返回，检查本身成功时退出0。没有 GUI、后台驻留进程或自动打开设备动作。

以下是供操作者执行的 PowerShell 示例，本实现任务没有运行硬件 capture。subject/task/佩戴侧/consent scope 必须使用真实记录；DEVICECHECK 用于设备检查，不能算人体试验样本。

```powershell
Set-Location E:/HMotionGPT_project
$createdEpisode = python -B scripts/experiment_session.py create --subject DEVICECHECK --session 20261105_PM --trial T002 --task device_check --wear-side unknown --consent-scope "device check only" --notes "operator verification"
$episodePath = ($createdEpisode | ConvertFrom-Json).episode
python -B scripts/experiment_session.py mark --episode $episodePath --event capture_requested --source agent
python -B scripts/experiment_session.py capture --episode $episodePath --capture-id arm01 --port COM6 --seconds 3 --baud 115200
python -B scripts/experiment_session.py mark --episode $episodePath --event capture_returned --source agent
python -B scripts/experiment_session.py derive --episode $episodePath --capture-id arm01 --derive-id parsed01
python -B scripts/experiment_session.py status --episode $episodePath
```

`--duration 3` 是 `--seconds 3` 的别名。每次重试用新的 trial/capture-id/derive-id；已有目录一律拒绝覆盖。capture 为阻塞命令；记录采集中事件时，在另一终端/agent CLI 调用 mark 即可。失败原始数据和日志保留，不能重用失败 capture ID。不要让两次 capture 或 GUI 同时占用同一串口。

相机和戒指导出的原始文件可分别导入；附件不修改源文件。示例路径需要替换为真实存在的原始文件：

```powershell
python -B scripts/experiment_session.py attach --episode $episodePath --modality camera --attachment-id video01 --file E:/recordings/original.mp4
python -B scripts/experiment_session.py attach --episode $episodePath --modality ring --attachment-id ring01 --file E:/recordings/ring_raw.csv
```

已有实采 episode 的只读检查：

```powershell
python -B scripts/experiment_session.py status --episode E:/HMotionGPT_project/data/episodes/DEVICECHECK/20261105_PM/T001
```

输出布局：

- `episode.json`：被试、时段、trial、task、佩戴侧、同意范围、notes、创建主机时间。
- `events/<uuid>.json`：事件名、来源、UTC ns/monotonic ns，`hardware_sync=false`。它记录 CLI 处理时刻，不能代表物理按键发生时刻或设备同步。
- `captures/<capture-id>/request.json`, `result.json`, `stdout.log`, `stderr.log`：启动参数、返回结果和失败日志。
- `captures/<capture-id>/source/<backend-session>/`：既有采集器输出的 `serial_raw.bin`, `receive_chunks.jsonl`, `metadata.json`，不改格式。
- `attachments/<camera|ring>/<attachment-id>/raw<suffix>`, `attachment.json`：复制原件、原名、大小、SHA-256 和导入时间；设备时钟来源 unknown。
- `derived/<derive-id>/rows.csv`：0-based 原始物理 row_index、字节偏移、原始行字节数、列宽、chunk 索引、行接收完成 UTC/monotonic ns、空 device_time_ns、unknown 时钟来源、QC、value_1..14。保留整数文本，包括前导零；未使用的列留空。
- `derived/<derive-id>/invalid_lines.json`, `fragments.json`, `qc.json`：独立无效行、片段、模式变化与混合宽度统计。首行可能是片段，因此即使语法有效也标 start_boundary_unverified；首行无效可能同时出现在 invalid 和 fragment 报告。

跨 chunk 行使用收到 LF 的 chunk 时间，CRLF 的 LF 可在后一 chunk；同 chunk 多行共享读取完成时间。无换行尾片段不作为有效行，即使包含8/9/14整数。不能把行率当设备采样率，不能把第9列默认当设备时钟，不能凭三模态 presence 推断信号可用或同步通过。status 不自动载入外部 fit_clock_map.py 的结果，后续由主 agent 整合验证边界。

独立验证命令（本任务已执行，17 tests / exit0）：

```powershell
python -B -m unittest discover -s tests -p test_experiment_session.py -v
```

所有 fixture 均为 synthetic，只用于软件行为验证。独立 sweep 与真实设备/同步验收由主 agent 负责；结果状态见 BUILD_NOTE.md 和 SILENT_ASSUMPTION_SWEEP.json。
