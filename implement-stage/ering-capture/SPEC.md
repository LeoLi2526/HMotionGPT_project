# eRing 接入规格

2026-11-09；对应 PROJECT_GOAL.yaml N1/N2。用户提供 hardware.md / protocol.md，戒指不在身边；本轮实现可运行的协议、BLE采集入口及既有episode平台集成，以合成设备验证，不进行实际扫描、连接或升级。

effort=balanced；ask=never；assurance=draft。

F0：真实命令入口将文档帧样例导入独立ring采集目录，生成可追溯原始记录与IMU CSV（无IMU时空CSV不冒充数据）。
F1：严格v2外层/已知载荷校验、分片与未知type无损保留，文档向量及破坏性回归测试。
F2：Bleak扫描与连接、加密配对、串行write-with-response、按request_id匹配、配置/状态/诊断/时间查询、定时live，失败保留。
F3：可选补传页、正确计数与落盘后游标提交、重复去重、断连/错误不提交、历史代隔离。
F4：接入experiment_session的ring-scan/ring-capture/ring-import/ring-derive/status，生成六轴原值和标称样本时间，既有臂环测试不回归；独立假设审阅。

不实现音频下载/ACK、执行器、OTA、自动配置改写；保留收到的相关消息。相机仍为原件导入。独立设备并行命令共享episode但各占用唯一capture目录；不宣称硬件同步或实时三设备一键启动。

验收：python -B -m unittest discover -s tests -v；CLI帮助、文档样例导入/派生/status；真实BLE待设备到场。
