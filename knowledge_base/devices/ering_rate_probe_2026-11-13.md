# 现有戒指主板调频接口实机验证

2026-11-13；N1/R001。用户转述开发者：现有主板可以实现50–200Hz IMU，无需换电子元器件。该信息作为开发者能力声明保留；本轮验证的是当前固件和已知接口是否能启用，并非测定硬件上限。

用户明确授权接着主板进行调试。设备报告固件2.1.5、硬件修订字段2，读取配置为PPG25Hz、IMU50Hz、通道掩码7、config_id=1、±4g/±500°/s。

## 实际操作及结论

1. 使用已知BLE v2服务连接，读取CAPABILITIES和SAMPLE_CONFIG。
2. 停止实时通知，以已读配置构造协议记载的12字节SAMPLE_CONFIG写入，作为不改变数值的对照。
3. 设备返回`ERROR(code=1, origin_type=3, detail=0)`；所附协议将code1定义为UNSUPPORTED。回复请求编号与发出请求一致，帧校验有效。
4. 原查询回复payload与写入payload逐字节一致，均为`19003200070001000400f401`。因此不是把100/200Hz请求失败误记为原配置写入失败。
5. 对照未通过，停止后续100/200Hz请求；两挡均**未实际发送、未采集、未验收**。没有猜测其他命令、修改PPG/量程、刷固件或操作音频/执行器。
6. 回查确认原配置仍为50Hz。断开后另开一次12秒实时采集，保存53有效应用帧、615实时六轴样本，0坏帧/重复/冲突/历史补传。41个IMU批次均为period_us=20000，采集代159。结束释放BLE。

可以证明：当前2.1.5固件不接受本次按照旧文档构造的SAMPLE_CONFIG原样写回；50Hz实时采集仍正常。不能证明：主板不支持100/200Hz、所有接口都不能改频率、必须换板，或已验证高频工作。PPG上位机选项也不能替代IMU调频证据。

下一步需要开发者提供适用于当前硬件和固件的IMU设置命令、配置入口，或对应固件版本/工程。取得后再验证100/200Hz配置回读、实际原始样本与批次时间、样本连续性和恢复流程。当前不盲猜命令、不自行刷入不匹配固件。时间同步与佩戴标定状态不变。

## 文件和可复核证据

本机忽略目录`data/episodes/DEVICECHECK/20261113_RING_RATE/T001/`：

- `ring_captures/rate_probe01/`：查询、原样写回、拒绝、恢复回查的完整帧及请求日志；probe_report.json记录探测失败及原配置确认。没有IMU样本属于对照阶段提前停止，不隐藏此失败。
- 原样写回探测raw_frames.jsonl SHA-256：`56acb8dda113e4210a179cf0fdf518eb6fc82492922d30b5a015811a665a9188`。
- `ring_captures/post_probe_50/`：之后独立的50Hz采集。raw_frames.jsonl SHA-256：`e42857e64341693d672a2e399ea70534d9068f1a3e74fb5bc7db635a706b9be8`。
- `ring_derived/post_probe_50_decode/`：615样本及QC；`rate_probe_validation.json`记录payload相等、请求编号匹配和时间字段；`status.json`保留失败探测和成功后测两个记录。

## 有界探测工具

新增`scripts/ering_rate_probe.py`，显式指定目标地址和未使用输出目录。仅使用文档中的配置和实时通知命令；先验证50Hz基线与原样写回，再按100、200Hz顺序请求。仅接受明确回读匹配的设置；清理时回查并必要时恢复初始数值。通信失败时恢复可能无法完成，工具必须报告未确认，不能声称已恢复。不会更新固件或补传历史。

```powershell
# 仅在获得新版接口说明后决定是否复用本工具；当前接口已拒绝，不重复忙试。
.\.venv\Scripts\python.exe scripts/ering_rate_probe.py --address '已确认的设备地址' --output '新的工程证据目录' --seconds 8
```

5项新增合成测试覆盖接受两档后恢复、拒绝后回读、采集异常后恢复、原样写回拒绝即停止、关联配置错误处理；戒指相关回归总计83项通过。这些测试验证软件行为，不是100/200Hz实机证据。正式采集程序保持原有行为；可选探测工具不是日常采集入口。

## 用户要求自行继续核查后的上位机内部审计

用户没有取得开发者的具体设置方式，要求继续自行查找。已检查所提供上位机包及项目、桌面、下载目录中的相关名称和工程入口，没有找到匹配戒指的固件工程。该检索有目录和名称范围，不代表扫描整台电脑；上位机VERSION.json明确标记firmware_included=false。

进一步只读解析eRingWeb.exe的PyInstaller归档，并反汇编Python3.12代码对象，未导入或执行提取模块、未改上位机文件。归档结构参照[PyInstaller官方读取实现](https://raw.githubusercontent.com/pyinstaller/pyinstaller/develop/PyInstaller/archive/readers.py)及[PYZ格式读取实现](https://raw.githubusercontent.com/pyinstaller/pyinstaller/develop/PyInstaller/loader/pyimod01_archive.py)。证据与EXE哈希保存在本机忽略目录`data/vendor/ering/2026-11-13/static_audit/`。

实际核实到：

- `tools.ering_lab.protocol_v2`的PPG_RATE_CONFIGS构造三个预设：PPG25/50/100Hz，IMU参数均为50Hz；config_id分别1/2/3，量程4g/500°/s。
- `tools.ering_web.bridge.RingBridge.set_ppg_rate`使用上述预设和SAMPLE_CONFIG命令；`server`仅提供ppg_rate入口，所查bridge/client/server/protocol模块没有IMU调频入口。
- `SampleConfig.encode`确实使用`<HHBBHHH`，字段顺序与本次探测完全一致，reserved=0。不是因为项目编码采用了与上位机不同的配置布局而产生拒绝。
- `RingBridge.set_live`仍使用`<BBH`和500ms参数，没有更短批量延迟设置入口。
- `can_set_ppg_rate`要求固件版本至少2.1.1且features bit0为1；本板实测features=510、bit0为0，因此该上位机会禁用PPG调挡。它不说明IMU硬件上限，也不单独解释设备端全部拒绝机制。

当前交付软件中没有找到可直接启用IMU100/200Hz的隐藏入口。结合实机拒绝证据，仍缺对应固件实现/设置方法；不能通过只改电脑端期望频率实现真实高频采样。开发者关于现有板50–200Hz能力的声明保留，不改为硬件不支持。没有尝试未知命令或用PPG挡位冒充IMU调频。
