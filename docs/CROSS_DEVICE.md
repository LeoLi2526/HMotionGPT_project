# 跨设备接力与真实设备访问

本项目有两种不同的“接力”：**继续同一聊天**，以及**让另一台电脑获得项目文件和设备访问**。Git 保存项目规范、方案、代码和交接状态；它不保存聊天历史，也不自动同步 USB/蓝牙外设、论文 PDF 或大型原始数据。

## 方式 A：另一台设备继续控制当前主机

如果目标是从另一台设备继续当前聊天、当前电脑仍连接着设备，可在支持远程控制的 Mac/Windows Codex 桌面应用中通过 **Settings → Connections → Control this Mac or PC** 开启远程连接，再从同账号、同 workspace 的受支持设备配对。聊天、文件和外设操作仍在当前主机执行；主机需要在线且保持可用。官方远程控制说明当前明确列出 Mac/Windows 主机；本项目当前执行主机为 Linux，不预设其桌面预览版具有相同入口。Linux 主机可按 SSH 远端项目路径检查。

官方说明：[Remote connections](https://learn.chatgpt.com/docs/remote-connections)。

## 方式 B：把同一聊天和工作位置移到设备所在电脑

如果戒指、臂环或相机接在另一台电脑上，需要让 Codex 在**那台电脑或可访问它的 SSH 主机**运行。先在目标主机保存同一 Git 仓库为 Codex 项目，并在桌面应用中连接该主机；再于原聊天底部选择运行位置，选目标主机并执行 **Hand off**。官方文档说明 Hand off 会转移聊天和 Git 状态，并在目标主机创建或复用工作树。必须先连接目标主机并保存匹配的项目；不能从正在请求 Hand off 的同一聊天自行迁移。

若使用 SSH 主机，先在桌面应用所在电脑的 `~/.ssh/config` 配置明确主机别名，确认 SSH 可连接；在远端安装并登录 Codex，确保登录 shell 的 `PATH` 包含 `codex`。通过 **Settings → Connections → SSH** 添加主机并选择远端项目目录。不要把 SSH 密钥或访问令牌提交到仓库。

官方步骤：[连接 SSH 主机与 Hand off](https://learn.chatgpt.com/docs/remote-connections#connect-to-an-ssh-host)。

### 当前聊天的 Hand off 限制（2026-11-03 实测）

从 Linux `amax` 将本项目研究聊天 Hand off 到 Windows `DESKTOP-5QG35AD` 时，Codex 在 `prepare-host-transfer` 阶段返回“分页聊天目前还不能在另一台主机上继续”；文件传输和主机切换未开始。反向迁移测试聊天也返回相同错误。OpenAI 的 [App Server 文档](https://learn.chatgpt.com/docs/app-server)说明新建持久聊天默认使用分页历史，但目前未提供将现有分页聊天转换为可跨主机 Hand off 聊天的正式步骤。不要把重试标记理解为已解决。

若只需在 Windows 上继续讨论，打开原有 Linux 远端聊天即可，命令仍在 Linux 执行。若需访问 Windows 本地外设，使用下方“方式 C”在 Windows 项目中接续已提交的项目状态；新聊天不继承原聊天的完整历史。

### 不接外设的最小转移测试

1. 在两台电脑上使用同一 ChatGPT 账号和 workspace；目标电脑取得本公开仓库，并在 Codex 中保存为项目。先运行 `python3 scripts/check_portability.py`，确认项目技能副本可用。
2. 在桌面应用的 **Settings → Connections** 中连接目标主机。若目标电脑提供受支持的远程控制入口，按配对流程连接；若它作为 SSH 主机，按上文的 SSH 步骤连接。看不到入口时先核对应用版本、平台和账号可用性，不要假定同账号就会自动出现另一台电脑。
3. 在原主机的项目中创建一个单独的“跨设备接力测试”聊天，让它读取 `PROJECT_HANDOFF.md` 并报告 `hostname`、`git rev-parse --short HEAD`。这样不用移动主要研究聊天。
4. 在测试聊天底部选择运行位置 → 目标主机 → **Hand off**。在目标电脑打开同一测试聊天，再让 agent 报告 `hostname`、当前项目路径和 Git 提交，并复述 N1 的状态。
5. 只有当聊天上下文仍在、`hostname` 已变为目标主机、项目提交可核对时，才算完成“聊天与执行位置转移”测试。未接外设时无需声称设备访问已通过。

这套测试使用本地/SSH 主机；Codex 的 Hand off 当前不支持把聊天转到 Codex cloud 环境。云端可以作为其他任务的可选运行位置，但不替代目标电脑的实际设备连接。

## 方式 C：新聊天接续项目状态

当 Hand off 不可用时，在另一台电脑克隆[公开仓库](https://github.com/LeoLi2526/HMotionGPT_project)：

```bash
git clone https://github.com/LeoLi2526/HMotionGPT_project.git
cd HMotionGPT_project
python3 scripts/check_portability.py
```

在 Codex 中打开克隆的项目，并让新聊天从 `PROJECT_HANDOFF.md`、`AGENTS.md`、`PROJECT_GOAL.yaml` 与 `ROADMAP.md` 接续。新聊天能继承**已提交的项目状态**，但不是原聊天的完整上下文；需要完整对话接续时使用方式 A 或 B。项目级技能和工具现在以普通目录保存，Windows 检出不依赖 Git 的符号链接功能。

## 设备所在主机的准备

1. 从 `config/host.example.yaml` 复制到 `config/host.local.yaml`，填写目标主机实际的设备位置、接口与访问状态。后者被 Git 忽略。
2. 检查目标主机能否读取设备或原始导出。USB、串口、蓝牙、相机驱动及账号权限由该主机提供；同账号登录并不会把另一台电脑的外设转发过来。
3. 对未知设备先做只读盘点和静止样本采集，保留固件/软件版本、时间戳、配置与失败日志。遇到 agent 无法解决的设备问题，停止受影响节点并向用户询问。
4. 仅上传可公开的规范、代码和必要的小型索引。内部会议原件、论文 PDF、大型原始数据和模型权重按各自主机的存储清单迁移；不要把本机绝对路径写成通用路径。

**已验证范围**：Linux 项目便携性检查通过。Windows 项目已克隆到 `C:\Users\LeoLi\Downloads\HMotionGPT_project`，2026-11-03 检查发现技能副本仅存在 CRLF/LF 换行差异，检查脚本已改为对 UTF-8 文本按统一换行比较，仍需在 Windows 拉取新版脚本后复测。聊天 Hand off 实测失败；实际外设访问尚未测试。
