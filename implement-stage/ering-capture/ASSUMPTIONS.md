# eRing 假设账本

ASK=never；资料中的协议约束作为技术规范，不是用户授权。

| ID | 决策 | Class | Source |
|---|---|---|---|
| E01 | 本轮通过离线fixture和假BLE验证软件；无真实硬件，任何50Hz、固件2.0.0、量程均以文档/响应分别标识，不写实测 | semantic | user |
| E02 | ring采集存episode/ring_captures/<capture-id>，独占创建；导入是带raw_hex的JSONL完整帧，不是旧CSV自动猜格式；原始记录和派生独立 | interface | default |
| E03 | BLE采用Bleak，显式选择设备地址、服务UUID过滤；默认配对以满足加密；一次连接失败终止并保留，下一次用新capture ID；只有执行ring-scan或ring-capture才发现设备，capture连接前会查找指定地址；本轮不调用真实发现 | interface | default; sweep更正文意 |
| E04 | 完整帧、原始通知分片、主机UTC/monotonic接收时间及异常均保留；坏帧不进入有效样本，未知type通过外层校验后原样保留 | semantic | default |
| E05 | IMU整数不覆盖；匹配同一设备采集代和sample_config_id才换算g/dps；first_sample_us+i*period_us明确标为估计设备样本时间，不当逐样本实测时间 | semantic | default |
| E06 | 设备capture_id不同于平台capture-id；同一物理设备内按(capture_id,sequence)去重，跨采集目录不自动合并。实时/补传只忽略REPLAY位与request_id差异，其他内容冲突报告错误 | semantic | default |
| E07 | 可选补传从SYNC_STATUS.oldest开始，每次最多32帧、有限页数；不跨被试复用游标。历史帧留在该capture的backlog证据，不能默认归属当前被试或正式试次 | semantic | default |
| E08 | 补传仅在成功REPLAY_DONE、实际帧计数匹配及原始帧fsync后写入原子页游标；无结果/错误/CRC/断连/计数不一致不提交；已保存数据保留。当前仅保存本次目录游标，不实现跨目录自动续传 | semantic | default |
| E09 | TIME_SYNC保留主机发送/接收UTC及单调时、device_rx/tx、capture_id和往返信息；仅记录近似映射依据，不自动授予同步通过；重启代与历史代不得借用当前映射 | semantic | default |
| E10 | 首期只查询配置，不改采样率；LIVE_CONTROL固定500ms、只控制通知不停止本地采集。采集时长是live观察时长，不含握手/补传 | interface | default |
| E11 | status的新ring采集存在须有效非空非REPLAY IMU样本及原始文件完整性通过；历史补传、握手响应/未知帧不能证明当前试次运动数据存在。synchronization始终unverified，旧attach入口保留文件存在语义并明确区分；有有效实时IMU时声明device_monotonic_us但UTC映射仍unverified | semantic | default |
| E12 | 小MTU写入固定20B以内（14B数据）优先兼容；全部请求串行，等待与type/request_id/flags相符响应；无自动重发执行器、ACK或OTA | interface | default |
| E13 | 默认派生只取非REPLAY IMU，补传样本需显式include-replay且标明归属未验证；动作标签和历史帧归属由操作者核对 | semantic | default |
| E14 | 接收解码拒绝主机请求，向量A只用于请求编码/重组。协议未明确禁止零样本IMU批，解析接受但不计有效样本；MORE保留而不作为完成信号。此条为worker交付后补录，非事前账本 | semantic | default |
| E15 | BLE采集manifest记录协议/Python/Bleak版本和可获取的Git提交及工作区是否有改动；无法读取时显式unknown，不把软件版本当固件版本。dirty只作提示，不保证未提交代码可从Git重建 | interface | default |
| E16 | 零周期IMU批保留原值和原始period，但派生device_time_us留空、标invalid_nominal_period且返回非零；不将无效周期算成重复数值样本时间 | semantic | sweep |
| E17 | 失败/中断采集若原始完整性通过仍可导出已有有效原值；CSV非空不表示采集完整，失败QC保留且派生命令非零 | semantic | sweep |
| E18 | 连续性QC检查首样本与前批标称终点严格相等；配置周期比较容差1微秒。仅表示偏离标称时间模型，不能单凭此证明丢样 | semantic | sweep |
| E19 | 完整组装但校验失败的帧须进入原始/validation统计；不完整或非法分片保存于notifications及错误日志，不能统计为完整帧 | semantic | sweep |
| E20 | MORE原样保留，普通响应与REPLAY_DONE匹配忽略该位；补传数据计数仍只允许flags恰为REPLAY | interface | sweep |
| E21 | 仅对REPLAY_REQUEST的STALE_CURSOR重新查询SYNC_STATUS，记录原游标和新oldest覆盖区间，从新oldest继续；失败页不提交。replay-pages上限计请求尝试数，包含STALE尝试，防止无限追赶；到上限时记录未完成后继续live | semantic | sweep |
