本次为**同家族暂定审阅**。33 项离线测试全部通过；另外复现了 Windows 被试 ID 大小写混用造成目录与元数据身份不一致，以及事件写入失败后仍被完整性检查计数且不报错的问题。所有验证仅使用临时合成数据，未修改项目文件或访问设备。时钟拟合的两项发现属于账本未声明的语义选择，未将它们直接判作算法错误。

```json
{
  "undeclared": [
    {
      "site": "E:/HMotionGPT_project/scripts/fit_clock_map.py:116-131",
      "decision": "所有 role=fit 的事件采用等权普通最小二乘拟合正斜率仿射映射；不根据事件定位误差加权，也不剔除离群事件。",
      "why_it_matters": "这决定偏移和漂移参数的实际含义，以及某个识别不准的共同事件如何影响整段时间映射。现有 A-019 声明了单位、事件数量和外推边界，但没有声明拟合方法与权重。该选择在代码中明确，尚未进入假设账本。",
      "class": "semantic"
    },
    {
      "site": "E:/HMotionGPT_project/scripts/fit_clock_map.py:146-156",
      "decision": "验证门槛仅比较 check 事件的最大绝对残差，等于门槛也通过；fit 事件的残差无论多大都不影响 verified_for_supplied_events。",
      "why_it_matters": "这决定验证通过究竟证明什么。离线反例中，fit 点为 (0,0)、(50,80)、(100,100)，check 点为 (75,85)，得到 a=1、b=10；fit 残差分别为 10000、-20000、10000 ms，但门槛设为 0 ms 时仍返回 verified=true，因为 check 残差为 0。代码注释确实说明由独立 check 决定验证，因此此处报告为未入账的验收语义，而非直接认定算法缺陷。A-019 应明确误差聚合方式、门槛包含等号，以及通过状态仅覆盖 check 事件。",
      "class": "semantic"
    },
    {
      "site": "E:/HMotionGPT_project/scripts/experiment_session.py:24-30,47-55",
      "decision": "ID 接受并保留大小写，但 subject/session/trial 的目录身份直接交由宿主文件系统决定；已有父目录的拼写不作一致性检查。",
      "why_it_matters": "A-001 声明联合唯一，却没有说明大小写规则。本机 Windows 上 P001 与 p001 可以共用同一被试目录，同时不同 trial 的 episode.json 保存不同 subject 字符串；按目录和按元数据分组会产生不同的被试划分。跨平台使用时，同样输入还可能形成不同目录结构。",
      "class": "interface"
    }
  ],
  "stale_rows": [
    {
      "id": "A-018",
      "site": "E:/HMotionGPT_project/implement-stage/session-capture/ASSUMPTIONS.md:24; E:/HMotionGPT_project/scripts/experiment_session.py:340",
      "declared": "status 对单项损坏记录 issues 并继续。",
      "actual": "事件文件不读取、不解析，仅以 events/*.json 的文件数计算 event_count。事件 JSON 写入中断后留下的损坏文件会被计数，且不会进入 issues。",
      "why_it_matters": "当前完整性状态无法区分成功保存的事件与写入失败留下的文件；账本声明的损坏检查范围超过了实现。详见 B-002。"
    }
  ],
  "semantic_undeclared": 2,
  "verdict": "gaps",
  "bugs": [
    {
      "id": "B-001",
      "priority": "P2",
      "site": "E:/HMotionGPT_project/scripts/experiment_session.py:47-55",
      "title": "Windows 上大小写不同的被试 ID 共享目录，却保存为不同元数据身份",
      "trigger": "依次创建 subject=P001/session=day1/trial=T001 和 subject=p001/session=day1/trial=T002；两次创建均成功。",
      "evidence": "本机临时目录复现：两个 episode 的被试父目录 samefile() 为 true，根目录只有 P001 一个被试目录，而两个 episode.json 的 subject 分别是 P001 和 p001。未修改已有项目数据。",
      "impact": "正常录入时仅改变 ID 大小写，就会造成目录归属与元数据身份不一致。后续按 subject 字符串划分训练/验证集合时，同一被试可能被拆成两个身份；按目录汇总时又会合并。若把大小写视为不同编号，则会出现相反的数据混放问题。",
      "suggested_fix": "明确统一的 ID 大小写规则并在创建时执行；复用已有 subject/session 父目录前检查身份拼写，避免依赖文件系统静默合并。补充上述 Windows 反例测试。"
    },
    {
      "id": "B-002",
      "priority": "P2",
      "site": "E:/HMotionGPT_project/scripts/experiment_session.py:33-36,91,340",
      "title": "写入失败的事件仍被 status 计为已保存事件，且不报告损坏",
      "trigger": "mark 创建事件文件后，JSON 写入中途发生 I/O 错误；write_new_json 会留下部分文件。",
      "evidence": "离线故障注入让 json.dump 写出单个字符 '{' 后抛出 OSError。随后 status 返回 event_count=1、issues=[]，虽然该事件文件不是合法 JSON，mark 也未成功完成。",
      "impact": "采集失败或存储异常后，操作者可能误认为开始、结束或同步标记已经保存；事件数量与可用事件证据不一致，而且完整性检查没有给出失败线索。此路径不需要恶意修改文件。",
      "suggested_fix": "status 逐个读取并校验事件记录，将损坏或未完成事件写入 issues，并区分有效事件数量与事件文件数量；继续保留失败文件供排查。"
    }
  ]
}
```
