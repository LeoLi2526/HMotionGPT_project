# 项目交接摘要

**更新日**：2026-11-02。此文件帮助另一台设备上的 agent 快速接续；细节以链接文件和原始证据为准。聊天历史不由 Git 仓库保存。

## 当前目标与阶段

- 唯一目标入口：[PROJECT_GOAL.yaml](./PROJECT_GOAL.yaml)。研究三模态人类 EGO 操作示范能否在可接受的采集成本下改善机器人 VLA 的真实操作学习。
- 当前阶段：设备联调与小规模试采规划。尚无本项目设备同步实测或机器人闭环成功率结论。
- 里程碑：[ROADMAP.md](./ROADMAP.md) 中 N0 已通过、N1 准备开始；详细任务和规划值见 [实验方案](./refine-logs/EXPERIMENT_PLAN.md)与[执行表](./refine-logs/EXPERIMENT_TRACKER.md)。

## 已核实与待获取

- 摄像机暂定 DJI Osmo Nano；前臂设备为思知瑞 8 通道 EMG 臂环。已整理厂商参数，见 [设备知识库](./knowledge_base/devices/README.md)。厂商参数是实验材料；原始导出、时间戳和同步性能仍需实机核验。
- 戒指型号、原始 IMU 和时间字段待核实。N1 还需臂环原始导出、Nano 样片、机器人动作接口与两项可复位任务的信息。
- 两篇论文的来源索引在 [PAPER_SOURCES.md](./knowledge_base/PAPER_SOURCES.md)；论文 PDF 只在已有主机的本地知识库，不随 Git 同步。大型原始数据、模型权重与本机设备路径同样不随 Git 同步。
- 真实设备测试先确认目标主机能访问设备，再做只读接口盘点和静止采样；机器人运动或控制另行明确安全边界。

## 新主机接力顺序

1. 按 [跨设备指南](./docs/CROSS_DEVICE.md)连接现有主机、使用 Hand off，或克隆公开仓库；先运行 `python3 scripts/check_portability.py`。
2. 读取 `AGENTS.md`、`PROJECT_GOAL.yaml`、`ROADMAP.md` 和本文件，核对当前 Git 分支与远端提交。
3. 在目标主机填写被 Git 忽略的 `config/host.local.yaml`，依据实际设备权限执行 N1 检查；记录原始文件、配置、失败日志与来源。
4. 节点准备开始、通过或阻塞时向用户汇报；随后更新本文件并提交、推送可共享的进展。
