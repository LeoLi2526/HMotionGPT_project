# 多被试三模态采集与数据处理

对应 PROJECT_GOAL.yaml 的 N1/N2：同一被试在一个试次内同时佩戴前臂肌电臂环、戒指和第一视角相机；不同被试依次进行，跨时段和重戴建立新 session。当前臂环已可实时读取；戒指和相机的实时接口仍待实物验证，因此程序当前支持其原始文件导入，不代表三路已经实现实时同步。

2026-11-09 更新：戒指新增BLE v2实时采集入口、原始帧导入、IMU派生与同episode状态检查，已离线联调，操作见[ERING_CAPTURE.md](./ERING_CAPTURE.md)。戒指实物当前不在场，真实蓝牙和同步仍未验收；相机仍为原件导入。下文先前“戒指时间字段待核对”现已取得协议定义，实际设备输出仍待核对。

## 设备与软件身份（2026-11-05）

- 当前设备：思知瑞 8 通道前臂 sEMG 臂环，用户指认对应[1.0 上位机教程](https://sichiray-tech.yuque.com/dm0eyv/chanpin/bflmvgotg46wszkm)。接收器标签“8通道肌电臂环适配器”。没有可读取的硬件 SKU、修订号或固件版本，不能从 CH340 芯片或教程标题推定这些信息。用内部设备编号和照片/佩戴记录追踪同一实物，后续有厂家证明再补型号。
- 当前程序：用户提供的 `思知瑞调试助手.exe`，Windows 产品名 `serialplot`，原始文件名 `serialplot.exe`；文件和产品版本字段均为 `0.0.0.0`。二进制包含 `10.0.0` 字符串，但仅据字符串不能确认运行版本。实际打开“帮助 → About”只显示厂商网址，没有版本号。可复现身份使用程序 SHA-256 `aea87eca302653a56971e6efdee0de2e9cf67a48b68a4a32b2c106bea19dea96`。它的界面对应 1.0 教程中的调试助手，不能称作已确认的硬件 1.0 或软件 2.0。
- 已实测控制：agent 通过桌面工具完成鼠标选择、键盘写路径、打开串口、开始/停止记录及关闭串口。串口只能由一个程序占用；调试助手和命令行采集器轮流使用。

## 时间字段的实际含义

| 来源 | 格式 | 可用于什么 | 边界 |
|---|---|---|---|
| 臂环当前 8/9/14 列文本 | 整数，无明确设备时钟 | 保留通道值与原始顺序 | 不能当作已获取逐样本设备时间 |
| 调试助手 seconds only | Unix 整秒 | 会话大致时段 | 同一秒多行共用时间 |
| 调试助手 milliseconds | Unix 毫秒整数 | 更细的主机记录时间 | 不等于设备采样时间；实测有重复时间及成批到达 |
| 项目采集器 | 每次读取完成的 UTC ns、monotonic ns、字节偏移 | 在同一主机追踪接收顺序、延迟异常和数据归属 | ns 是存储单位，不是同步精度承诺；同一读取批次内各行不可伪造不同设备时间 |
| 相机视频 | 原始容器、逐帧 PTS（待实际文件验证） | 视频自身时间轴 | 不用文件修改时间代替拍摄时刻 |
| 戒指 | 原始输出和时间字段待核对 | 保留原始文件后解码 | 未核实前时钟标 unknown |

桌面控制导出 `data/sichiray/evidence/gui_ms_control_20261105.csv`：2,290 行九列，时间从 `1793869221972` 到 `1793869254972`，跨度 33 秒；相邻行差最小 0、 中位数 2、最大 1315 ms，22 处相邻时间相同。SHA-256 `ba5b73f248bf4dced61c098af6d68322c6aa9f9a94cd5c34de9794320fe2eb78`。这说明更细的显示单位仍不能保证均匀的逐样本时刻。GUI 有 seconds with precision 选项，本次未单独验证。

更正：GUI 选项实际是“暂停时录制”（控件 `cbRecordPaused`），表示绘图暂停时仍可记录；先前把它读成“暂停录制”并用于解释空文件没有依据。`screenshot_correct1` 只有表头的事实保留，原因待定。

## 每个被试的执行流程

1. 给被试固定编号，例如 P001；每个不同日期/时段或重新佩戴建立新 session，例如 20261105_PM01。试次编号 T001 仅在该 session 内递增。记录同意范围、任务、左右手、佩戴位置/方向、设备内部编号、电极接触与异常，不用名字或自动递增截图名承担主索引。
2. 新建 episode，再启动三设备。臂环由项目采集器保存原始串口；相机/戒指若不能实时接入，则各自录制并在同一 episode 中导入原文件。每个原文件与实际试次的归属由采集操作者核对，不能按导入时间猜测。
3. 在试次开始、中间和结束安排能在有关数据流中明确识别的共同事件，记录事件编号及识别位置。优先利用相机可见且惯性数据可检测的动作；仅肌电模式下是否能可靠识别同一事件需试验。命令行 mark 只记录操作者/agent处理事件的主机时间，不构成设备同步证据。
4. 完成任务后停止采集，检查文件、数据量、模式、连续性、缺失模态和异常。失败试次保留并记录原因，重做建立新 trial，不覆盖。换被试或重戴后重复静止基线与事件检查。
5. 核对并复制 camera/ring 原始文件至该 episode。把所有原始文件做备份与校验；实验原始数据保持在被忽略的数据目录，不公开到 Git。

## 处理和同步

原始层保留字节、接收批次时间、原始视频/戒指文件及配置；派生层另存可重建 CSV、异常记录、标签和时钟映射。解析时记录 raw offset，将跨读取批次的一行归于其换行符所在批次的接收完成时间，起止半行和未知格式单独报告。模式变化不能把不同列数拼成同一通道表。

先确认实际信号的单位、设备滤波和采样方式，再决定滤波、归一化与特征。当前 8 列约 67 行/秒、合并模式约 25 行/秒是输出率，不能把它们标成 500 Hz 完整原始 EMG；也不能通过插值恢复尚未收到的高频肌电成分。保留原值，跨被试比较先在各自 session 内记录和核对基线，所有变换参数写入派生结果配置。

每台设备保留自身原始时轴，以共同事件拟合 `t_episode = a * t_device + b`。至少两个不同时间的事件用于估计偏移和漂移，另留独立事件检查误差。误差门槛由试采任务冻结后显式设置；只有拟合点没有检查点时不得标同步已验证。发生时钟重置、掉线、模式变化或明显漂移时分段建映射。GUI 的毫秒主机时间可以作为接收端时轴，但其传输延迟仍需留出事件验证。

训练/验证/测试按被试或 session 分组，先固定划分再切窗口；不能把同一连续试次的相邻窗口随机分散到不同集合并宣称跨被试泛化。标注保留原始事件时轴和转换版本。当前程序的完整性状态不会自动把“文件齐全”升级成“三模态同步通过”。

## 可执行入口

在项目根目录使用 `.venv\Scripts\python.exe scripts/experiment_session.py`，或由 agent 直接执行同样命令。先关闭占用该串口的调试助手连接。示例中的 ID/任务/佩戴侧/同意范围须替换为实际记录；端口从本机 `config/host.local.yaml` 核对。

```powershell
.\.venv\Scripts\python.exe scripts/experiment_session.py create --subject P001 --session 20261105_PM01 --trial T001 --task grasp --wear-side right --consent-scope project_collection --notes 'record actual placement here'
$episodePath = 'data/episodes/P001/20261105_PM01/T001'
.\.venv\Scripts\python.exe scripts/experiment_session.py mark --episode $episodePath --event preparation_complete
.\.venv\Scripts\python.exe scripts/experiment_session.py capture --episode $episodePath --capture-id armband01 --port COMx --seconds 60
.\.venv\Scripts\python.exe scripts/experiment_session.py derive --episode $episodePath --capture-id armband01 --derive-id decode01
.\.venv\Scripts\python.exe scripts/experiment_session.py attach --episode $episodePath --modality camera --attachment-id clip01 --file E:/actual/path/video.mp4
.\.venv\Scripts\python.exe scripts/experiment_session.py attach --episode $episodePath --modality ring --attachment-id ring01 --file E:/actual/path/ring.csv
.\.venv\Scripts\python.exe scripts/experiment_session.py status --episode $episodePath
```

`capture`按明确时长自动停止，也保留中断和失败记录。`mark`可在另一个命令窗口与采集同时执行。重复 ID 会被拒绝；复采更换 capture-id，重算更换 derive-id。被试、session、trial 编号保留首次拼写，后续仅大小写不同的拼写会被拒绝，避免跨电脑混淆身份。`status`分别报告事件文件数和校验有效的事件数，损坏事件保留并列出问题。`attach`只做原件归档，不猜测戒指协议或解码视频；数据解释由后续适配器完成。

时钟映射工具为 `scripts/fit_clock_map.py`：输入 JSON 中写明 `modality`、`source_clock`、`source_unit: seconds`、`target_clock: episode_relative_seconds`，以及 `anchors` 数组；每个事件含 `event_id`、`source_seconds`、`target_seconds`、`role: fit/check`。执行 `python scripts/fit_clock_map.py anchors.json --output new_map.json --max-error-ms <显式门槛>`。采用全部 fit 事件等权最小二乘，不自动去除异常点；fit 和 check 两组的最大绝对残差均须不大于门槛（包含等号）。输出不得覆盖既有文件；退出成功只表示报告写出，须检查报告的验证状态。没有独立 check 或 check 处于拟合区间外时不通过，且通过只适用于所提供事件。

## 本机验收

工程试次 `data/episodes/DEVICECHECK/20261105_PM/T001` 已实际运行 create、mark、capture、derive。10 秒采集得到 668 条八列文本和 1 个尾片段；原始字节 SHA-256 `82eb11ddaf35c3843f4b27349b6d199d9eebb83e3eab6df99697e1aa033e230f`。该试次仅为臂环工程验证，camera/ring 尚未提供，不计入正式三模态被试试次或 N2 的完整 episode 数量。
