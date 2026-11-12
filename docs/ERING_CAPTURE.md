# eRing 接入既有臂环采集平台

2026-11-09。设备参数与资料校验值见 [eRing参数卡](../knowledge_base/devices/ering.md)。本轮戒指不在身边：新增代码有离线协议/模拟BLE/命令入口验证，真实配对、持续输出和同步精度待实机验收。现有臂环命令保留，相机仍支持原件导入。

## 接入范围

在 `scripts/experiment_session.py` 增加 `ring-scan`、`ring-capture`、`ring-import`、`ring-derive`，共用既有create/mark/status。

- BLE v2按服务UUID查找，操作者显式选择地址，连接时尝试配对以满足加密。设备仅支持一个连接，实采前释放手机App占用。
- 查询CAPABILITIES、TIME_SYNC、SAMPLE_CONFIG、SYNC_STATUS及DIAGNOSTICS；按请求ID和类型匹配，允许主动消息交错。记录实际回复，不把文档版本当实物版本。
- 串行Write With Response，固定不超过20B的GATT写入以兼容MTU23；严格重组分片/校验完整帧。无效数据保留但不进入有效样本，未知v2类型保留完整帧。
- LIVE_CONTROL使用文档支持的500ms批量延迟，指定秒数为live观察时长，不含握手/可选补传。退出时停止通知并断开；停止通知不停止戒指内部采集。
- 可选有限页补传：统计本页收到的帧次数（包括重复），落盘去重，成功页及计数吻合后才提交游标。默认不取历史页；不跨试次共享游标，也不自动跨目录续传。
- 不提供采样配置修改、音频下载/删除ACK、马达或LED效果、OTA命令入口。

## 安装与离线使用

已有项目隔离环境安装 `requirements-ering.txt`，本机验证可导入Bleak 3.1.0。协议解析和文件导入只需标准库；蓝牙入口另需适配器、系统权限和实际设备。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ering.txt
.\.venv\Scripts\python.exe scripts/experiment_session.py --help
```

BLE API采用 [BleakClient](https://bleak.readthedocs.io/en/latest/api/client.html) 的异步连接、配对、通知和响应式写入，以及 [BleakScanner](https://bleak.readthedocs.io/en/latest/api/scanner.html) 的服务过滤/设备发现。依赖安装成功不代表硬件连接成功。

`ring-import` 输入为UTF-8 JSONL，每行至少有 `raw_hex`，表示一条完整的设备→主机v2应用帧（不含GATT分片头）。可以有整数 `host_utc_ns`、`host_monotonic_ns`；缺失时留空，不用导入时间补成历史接收时间。原件复制保存后再解析。主机请求不能当成设备响应导入，旧CSV也不自动猜测协议。

```powershell
$episodePath = 'data/episodes/P001/20261109_PM01/T001'
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-import --episode $episodePath --capture-id ring_import01 --file E:/actual/path/ring_frames.jsonl
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-derive --episode $episodePath --capture-id ring_import01 --derive-id ring_decode01
.\.venv\Scripts\python.exe scripts/experiment_session.py status --episode $episodePath
```

## 戒指到场后的实时采集

先按多被试说明创建episode，记录佩戴手指、左右手、朝向和重戴情况。扫描结果只证明附近有广播，仍须连接查询身份。

```powershell
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-scan --seconds 8
# 用扫描得到并已确认属于目标戒指的地址替换实际地址。
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-capture --episode $episodePath --capture-id ring01 --address '实际地址' --seconds 60
.\.venv\Scripts\python.exe scripts/experiment_session.py ring-derive --episode $episodePath --capture-id ring01 --derive-id ring_decode01
```

同一episode内，臂环采集可在另一命令窗口并行启动，使用原 `capture --port ... --seconds ...`。两者各写自己的独占目录，主机时钟可比较，但软件启动时间不同且传输延迟不同，仍须共同事件对齐。这里没有承诺同一硬件触发或三设备一键同步开始。

若需检查缓存，给ring-capture加 `--replay-pages 2` 等明确请求尝试上限（包括过期游标尝试）。本轮从设备最老可读游标开始，不清缓存；达到上限时记录未取完状态并继续实时采集。遇到STALE_CURSOR时重查SYNC_STATUS，记录请求游标与新oldest揭示的覆盖区间，从新oldest继续，不提交失败页。中途断连/CRC失败/计数不一致不推进游标；失败记录保留，重试使用新capture-id。本轮不实现跨目录自动续传，避免将旧被试数据静默并入新试次。

## 保存与派生含义

目录为 `episode/ring_captures/<capture-id>/`：

- `raw_frames.jsonl`：每次完整帧的原始字节、接收时间和上下文，包括重复与无效帧。
- `notifications.jsonl`：原始蓝牙分片；`events.jsonl`：请求、时间交换、错误与live窗口。
- `validation.jsonl`：逐帧解析/完整性结果；`capture.json`：采集状态、统计、文件校验值和软件证据范围。BLE采集记录Python/Bleak/协议版本、可读的Git提交与工作区改动提示；它们不替代实物固件查询回复。
- 可选 `cursor.json`：成功持久落盘的补传页下一游标。导入另存 `source.jsonl` 原件。

派生写入 `episode/ring_derived/<derive-id>/imu.csv` 和 `qc.json`；不覆盖原文件。保存六轴整数、设备采集代/帧序号/样本索引、配置/校准版本、主机接收时间，以及按标称周期估计的设备样本时间。只有相同设备capture_id、sample_config_id有无冲突配置时，才另存g和°/s；无配置时转换列为空。

`capture-id`是平台目录名；`capture_id`是固件采集代。它们和被试编号均不是同一个概念。导入数据只证明文件格式，不能自动认证其来自真实设备。

默认派生排除补传样本；显式 `--include-replay` 可生成含补传CSV，但保留归属未知标记。若同一帧先补传后实时收到，按去重键保留一份并优先用实时接收记录。相同键但内容不一致为冲突，不悄悄选一个数值。

设备微秒时钟不是UTC；批内 `first_sample_us+i*period_us` 不是逐样本实测时刻。TIME_SYNC保留两端发收时间，仅为当前采集代提供后续近似映射依据；尚未自动应用到CSV，也未通过三流同步验收。按键/共同动作可以作为后续相机和臂环对齐的候选事件，但需实际验证可辨认性。

零周期批保留原始计数和周期，但派生时间列留空并报告非零退出码。连续性检查将下一批首样本与前批标称终点严格比较，周期与配置的比较容差为1微秒；相应QC只说明偏离标称模型，不能单凭它宣称真实丢样。失败/中断采集在原始完整性通过时仍允许导出已有有效样本，并保留失败QC及非零退出码；CSV存在或非空不代表采集成功。

`status`对新ring采集要求原始校验通过且存在有效实时IMU样本；只有响应、未知帧或历史补传不会让当前试次ring变为存在。另汇总ring_derived中的派生样本数、退出码、QC和损坏报告；存在性与质量分开。旧attach仍仅说明原件存在。设备时轴已知不等于UTC映射或三模态同步已验证。

## 实机最小验收

1. 确认广播、配对、实际固件/能力、配置和存储状态；短采静止与转动动作。
2. 检查六轴方向、量程、实际样本数/时间跨度、重复/缺口、有效配置及接收延迟。
3. 断连和重连后检查缓存页计数、重复去重、游标不越过失败页；跨重启不沿用旧时钟映射。
4. 臂环与戒指同episode并行试采，再加入相机；用开始/中间/结束共同事件拟合并独立检查误差。

目前以上为待办，不计入N2完整三模态试次数。软件验证详情见 [BUILD_NOTE](../implement-stage/ering-capture/BUILD_NOTE.md)，所有会影响数据解释的选择见 [ASSUMPTIONS](../implement-stage/ering-capture/ASSUMPTIONS.md)。


## 首次实机检查（2026-11-12）

现有命令已成功扫描、连接和读取主板，固件报告2.1.5；15秒/30秒观察分别保存765/1515个IMU样本。传输完整性通过，但两段接近静止，动作响应未验证；批次时间相对标称周期有约-480微秒偏差，两个采集代分开处理。上位机eRingWeb1.0.6仅完成静态核查，未运行或验证其导出兼容。完整证据见[首次实采](../knowledge_base/devices/ering_n1_2026-11-12.md)，不将采样成功视为N2同步通过。
