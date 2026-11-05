# Session capture 实现规格

目标：支持 PROJECT_GOAL.yaml 的 N1/N2 会话元数据、原始流和可追溯 episode，供多被试、跨时段试采使用。

输入：subject/session/trial、task、佩戴侧、notes、consent scope；显式 COM、duration；相机/戒指原始文件；现有臂环 raw/chunks。

输出：默认 `data/episodes/<subject>/<session>/<trial>/episode.json`；不可覆盖的采集、附件和派生目录；事件 JSON；CSV 和 QC JSON 报告。CLI stdout 为 JSON。

验收命令：`python -B -m unittest discover -s tests -p test_experiment_session.py -v`。

Base commit：`0b74d1f8bdaf9f51b29712cb7855c2e5601382b5`（开始时 HEAD 已核对）。

范围：仅 scripts/experiment_session.py、tests/test_experiment_session.py 和本目录。复用 scripts/sichiray_capture.py；不改串口逻辑。不打开 COM、不启动 GUI、不推送；本任务只离线测试。主 agent 负责硬件取证、最终 review、共享文档与整合提交。

参数：effort=balanced，ask=never，assurance=draft。依赖：Python 标准库；实采另需现有 pyserial。仿真只用于测试，不能成为实测结论。
