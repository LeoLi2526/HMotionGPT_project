# 思知瑞前臂肌电臂环：连接前的数据获取准备

**对应节点**：N1 / R001。当前只完成 Windows 软件与离线采集入口准备；尚未读取项目实物。设备型号、串口、协议版本和时间戳单位须在实机连接后确认。

## 已准备的内容

- 用户提供的《思知瑞调试助手.zip》已解压到本机被 Git 忽略的 `data/vendor/sichiray_debug/`。主程序为 `思知瑞调试助手.exe`，SHA-256 为 `aea87eca302653a56971e6efdee0de2e9cf67a48b68a4a32b2c106bea19dea96`。该文件与《EEG_EMG_Collection.zip》中的 `EMG_Upper/emgsdk_demo.exe` 字节相同；无需安装两份上位机。Windows 显示该程序未作数字签名；软件未在本机启动或连接臂环。
- 已在被忽略的 `.venv/` 中安装 `pyserial 3.5`，并准备了只读串口采集程序 [`scripts/sichiray_capture.py`](../scripts/sichiray_capture.py)。程序不向串口发送应用命令，保存每个收到的字节及每次读取的主机时间。
- [厂商教程](https://sichiray-tech.yuque.com/dm0eyv/chanpin/iledy42eabd6dkxl)描述通过蓝牙接收器/串口连接，专业模式可保存文件，双击设备按钮可启动 HEX 数据。压缩包内旧脚本则用 `115200` 波特率读每行 8 个整数；这是**待实机核对的候选配置**，不能视为 HEX 协议已确认。

## 接入实物后的首次操作

1. 记录臂环铭牌、软件/固件版本、接收器型号及前臂佩戴位置。按厂商教程配对并开启数据输出。若需要注册码，应通过厂家或已有授权取得；不要将注册码写入 Git。
2. 在项目根目录运行 `\.venv\Scripts\python.exe scripts\sichiray_capture.py ports`。对照接收器设备信息和连接前后的端口变化选择 `COMx`；本机已有的 COM3/4/7/8 均未归属到项目臂环，不能按旧脚本固定选 COM3/5。
3. 关闭占用同一串口的上位机，再进行一次 60 秒静止、握拳/放松、手臂转动的短采。用实际端口替换 `COMx`：

   ```powershell
   .\.venv\Scripts\python.exe scripts\sichiray_capture.py capture --port COMx --baud 115200 --seconds 60 --label first_contact
   ```

   若实际端口需要其他波特率，按设备/接收器说明改 `--baud`。程序不会自动扫描或试开所有端口。
4. 程序打印会话目录；用该目录执行 `\.venv\Scripts\python.exe scripts\sichiray_capture.py inspect <会话目录>`。检查收到的字节数、8 整数文本行数或候选 98 字节 HEX 帧数。即使检查结果是“未知格式”，也保留原始文件供协议核对。
5. 另行用厂商上位机的专业模式保存一个短样本，并记录导出设置、文件格式与软件版本。若上位机和采集程序使用同一串口，分别运行。对比两种输出后再确定正式采集路径。

## 输出和判定边界

每次采集写入被 Git 忽略的 `data/sichiray/<UTC时间>_<标签>/`：`serial_raw.bin` 是原始字节，`receive_chunks.jsonl` 记录字节偏移、长度及主机读取完成时间，`metadata.json` 记录端口、配置、状态、字节数和 SHA-256。主机读取时间**不是**每个 EMG 样本的采样时刻；离线检查中的 HEX 帧、长度字节和大端时间戳也只是厂商文档结构的候选匹配。实机样本到手后还需确认实际数据格式、设备时钟单位、通道值的有符号性、EMG 帧内时序与丢包率。

若采集为 0 字节，先检查端口归属、蓝牙接收器、设备输出模式、串口占用和波特率，并保留该次 `metadata.json`。若只有旧式 8 整数文本行，仍可保留肌电读数，但它不提供文档所述的设备时间戳及 IMU；不能以主机读取时间冒充同步后的设备时间。

在另一台电脑重新准备时，从项目根目录执行 `python -m venv .venv` 和 `.\.venv\Scripts\python.exe -m pip install -r requirements-sichiray.txt`；调试助手压缩包需在该主机的获准位置单独取得，不随公开仓库同步。
