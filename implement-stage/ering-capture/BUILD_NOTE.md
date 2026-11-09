# eRing 实现记录

起点6d1d099；本轮设备不在场。原文归档于被忽略的data/vendor/ering/2026-11-09。四个文档合成帧的长度与CRC、向量A的MTU23分片重组已验证；不构成真实BLE证据。

执行者为当前Codex，worker继承当前模型；具体运行身份以工具返回为准，不推定跨家族独立性。验收与review将在完成后追加。

## 执行证据

- 原始资料两文件已逐字节归档；4个完整文档帧的外层CRC/长度通过，MTU23分片重组通过。请求向量A不是设备响应，只能用于编码/分片测试。
- F0实际CLI：create → ring-import → ring-derive → status，离线样本目录 `data/episodes/SYNTHETIC/ERING_OFFLINE_20261109/VECTORS01`。首轮混入A请求的导入被拒绝并保存失败；当时存储worker尚未完成派生接口，后续命令报告ImportError。第二次改用B/C/D设备帧、独立ID后全流程成功；无IMU向量，status正确保持ring缺失。失败目录未覆盖。
- F1：协议worker28项测试通过，覆盖完整向量、MTU23、坏CRC/版本/flags/载荷、已知非持久伪补传、未知type、signed IMU/u24 PPG、错误重组。
- F2/F3：主agent10项测试通过，覆盖真实CLI、模拟GATT的串行写入/交错通知、TIME_SYNC证据、错误/超时/断连、补传重复计数/未知type及失败不提交游标。初次时间测试要求模拟RTT严格大于0，在本机时钟分辨率下为0，已改为非负；无代码把ns单位当ns精度。
- 存储worker26项测试通过，含重复/冲突/历史代配置、损坏及中断保留、fsync和原子替换失败不推进游标、未知类型与默认排除补传。
- 端到端模拟BLE演示 `data/episodes/SYNTHETIC/ERING_OFFLINE_20261109/FAKEBLE01`：18条完整帧、1次重复、去重后2个IMU样本（1实时、1补传）；默认派生仅1个实时样本。此数据仅为synthetic软件证据。
- Bleak 3.1.0已在既有.venv安装并导入；未实际扫描。官方API来源记录于docs/ERING_CAPTURE.md。当前命令帮助含4个ring入口。
- 中间整套回归103项通过，便携性检查通过；最终回归及review将在下方补充。
- 补齐中断QC后，在项目.venv中完整回归104项通过；再增加软件版本元数据，相关10项BLE/CLI集成测试通过。没有运行实际蓝牙扫描，只有依赖导入与假设备。

## 范围与状态

代码由主agent及两个继承当前模型的worker完成，reasoning请求high；工具未报告独立模型家族。reviewer为全新上下文，路由器host-default选择codex/gpt-6-astra/xhigh，实际工具模型继承当前模型，reasoning显式xhigh，记录为同家族暂定审阅。

F0–F4软件项已接入，无伪硬件输出或生产stub；硬件配对、采样率、掉线恢复、三模态同步待设备到场。未实现相机实时驱动、音频下载/ACK、执行器或OTA；这些不是本轮入口。首次F0和后续模块在并行开发中有重叠，未声称严格逐项串行开发；记录真实验收顺序。

## 审阅修复

第一轮原文REVIEW_ROUND1.json记录3项未声明semantic选择、3项过时账本表述、5项P2问题。已补充E16–E21并修正E03；修复零周期时间、ring_derived状态汇总、接收端完整坏CRC帧归档/计数、STALE_CURSOR重查询与有限重试、MORE响应匹配。新测试实际破坏通知中的CRC字节，避免原先在模拟发送端就被拒绝而漏测接收路径。默认历史归属边界保留。

修复后项目.venv整套111项测试通过（协议28、存储28、BLE/CLI15、既有40）。同一合成演示用新derive-id decode02再次派生，旧产物不覆盖；新status汇总ring_derived及其QC。无真实设备测试；第二轮审阅结果另附。

第二轮完整原文保存在SILENT_ASSUMPTION_SWEEP.json：verdict=clean，semantic_undeclared=0，无未解决bugs或stale rows。审阅者独立重放第一轮5个反例，并执行71项eRing及20项既有session测试；五项缺陷均标resolved。review_independence=same-family、acceptance_status=provisional，不能升级为硬件或跨家族独立验收。F0–F4软件检查已通过，真实设备相关依赖仍未满足。
