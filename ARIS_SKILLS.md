# ARIS 项目级技能

已将 [Auto-claude-code-research-in-sleep](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) 的 **Codex 适配版 87 个技能**内置到本项目，固定上游提交 `c824602097ce939c52a151da7d7c1e24fc7021a4`（2026-11-01 获取）。上游 MIT 许可见 `.aris/vendor/aris/LICENSE`。完整名称见 `.aris/installed-skill-names.txt`。

## 调用

在本项目的 Codex 聊天中直接点名技能，例如：

- `用 $research-refine 细化三模态 token 融合创新点`
- `用 $novelty-check 核查戒指、前臂 EMG、Osmo Nano 第一视角视觉的最接近工作`
- `用 $experiment-plan 更新试采后的实验方案`
- `用 $experiment-audit 审计完成的实验`

新的 Codex 聊天会从 `.agents/skills/` 发现这些技能；当前已经开始的聊天可能需要重新打开项目或开启新聊天以刷新技能清单。也可以明确要求读取 `.agents/skills/<名称>/SKILL.md`。

## 目录与依赖

- `.agents/skills/`：87 个技能入口及 `shared-references`，均为普通目录副本；Windows 检出无需符号链接权限。
- `.aris/vendor/aris/`：项目内固定版本的技能原文、脚本、模板、资源和可选 MCP 服务代码。
- `.aris/tools/`：固定版本脚本目录的普通副本；`.aris/installed-skills.txt` 记录项目级来源。

仅复制和注册了项目内文件，**没有执行上游脚本、注册 MCP、安装 Python 包或配置外部 API**。需要网络搜索、独立审阅、GPU、通知等能力的技能，在调用时应按其说明检查依赖；缺失时明确报告，不能把未运行的审阅写成已通过。

项目内 subagent 的调用时机、模型档位、并发、升级和证据回收见 [`SUBAGENT_POLICY.yaml`](./SUBAGENT_POLICY.yaml)。技能若对 reviewer 路线、独立上下文或审阅追踪有专门要求，按技能说明执行并记录实际模型；不要因为项目内已安装技能，就报告一个未实际运行的 reviewer 结论。

## 更新

当前版本已固定。更新时先在临时目录获取新的上游提交，比对 `.aris/vendor/aris/`、`.agents/skills/` 和 `.aris/tools/`，检查许可证、脚本与技能变更，再同步副本、更新版本记录并运行 `python3 scripts/check_portability.py`。不要直接覆盖项目中的研究资料。
