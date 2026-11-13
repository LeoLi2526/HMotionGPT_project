"""Interactive local launcher; explicit choices, engineering capture only."""
import asyncio
from datetime import datetime
import sys
from pathlib import Path
import dual_capture
import ering_capture
from serial.tools import list_ports


def ask(label, default=''):
    value = input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip()
    return value or default


def choose(label, values):
    if not values:
        raise RuntimeError(label + '：没有找到设备，请连接后重试。')
    print(label)
    for i, (text, _) in enumerate(values, 1):
        print(f'  {i}. {text}')
    while True:
        value = ask('输入序号')
        if value.isdigit() and 1 <= int(value) <= len(values):
            return values[int(value) - 1][1]
        print('请输入列表中的序号。')


def main():
    print('臂环 + 戒指工程采集\n请让厂商软件断开设备；保持臂环为8列文本模式。')
    print('按电脑接收时间整理两路数据；尚未验证设备采样同步精度。\n')
    port = choose('选择臂环接收器（通常为 USB-SERIAL CH340）',
                  [(f'{p.device}: {p.description}', p.device) for p in list_ports.comports()])
    print('正在搜索戒指……')
    found = asyncio.run(ering_capture.scan(5))
    address = choose('选择戒指', [(f"{d['name']} ({d['address']})", d['address']) for d in found['devices']])
    subject = ask('被试编号，仅英文、数字或下划线', 'DEVICECHECK')
    session = ask('会话编号（不同时间段请另建会话）', datetime.now().strftime('%Y%m%d_%H%M%S'))
    trial = ask('试次编号', 'T001')
    task = ask('任务描述', '双设备工程检查')
    seconds = ask('戒指目标记录秒数，臂环另外预留30秒', '30')
    mount = choose('戒指当前安装状态', [('裸板', 'bare_board'), ('已佩戴固定', 'worn'), ('未确认', 'unknown')])
    power = choose('戒指供电', [('连线供电', 'tethered'), ('电池', 'battery'), ('未确认', 'unknown')])
    side = choose('臂环佩戴侧', [('左前臂', 'left'), ('右前臂', 'right'), ('未佩戴或未确认', 'unknown')])
    scope = ask('已获同意的数据使用范围（元数据记录）', '项目工程采集与分析')
    code = dual_capture.main(['record', '--subject', subject, '--session', session, '--trial', trial,
        '--task', task, '--seconds', seconds, '--port', port, '--address', address,
        '--ring-mount', mount, '--ring-power', power, '--wear-side', side, '--consent-scope', scope])
    print('\n' + ('双路工程检查通过。' if code == 0 else '本次未通过工程检查；请查看试次内日志与报告。'))
    print('同步精度仍未验证。原始记录不会被覆盖。')
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'已停止：{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
