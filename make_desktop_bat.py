#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成桌面一键启动器 (.bat) —— ETF 动横阶梯轮动报告
================================================================
规则 (踩坑沉淀):
  1) .bat 必须 GBK(ANSI) 编码, 否则中文路径在 cmd 下乱码;
  2) 提示文本全 ASCII, 不用 emoji;
  3) Python 路径含 `\\binaries` 时, 用 Write/echo 直写会把 `\\b` 吞成退格符 0x08,
     所以必须用 Python 以 raw string 生成, 并 assert 无 0x08;
  4) 写死 venv 全路径, 禁用裸 python (PATH 首个 C:\\Python314 无 pandas);
  5) 成功 -> 窗口自动关闭; 失败 -> 打印错误并 pause 让人看见。

用法:
  python make_desktop_bat.py                    # 默认装到 E:\\桌面
  python make_desktop_bat.py --desktop "C:/Users/王旭/Desktop"
"""

import argparse
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\王旭\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
SCRIPT = r"C:\Users\王旭\.workbuddy\skills\etf-roc-ladder\scripts\etf_rotation.py"

BAT_TMPL = (
    "@echo off\r\n"
    "chcp 936 >nul\r\n"
    "setlocal\r\n"
    f'set "PY={PY}"\r\n'
    f'set "SCRIPT={SCRIPT}"\r\n'
    "if not exist \"%PY%\" (\r\n"
    "  echo [ERROR] Python venv not found:\r\n"
    "  echo   %PY%\r\n"
    "  echo   Please check the install path.\r\n"
    "  pause\r\n"
    "  exit /b 1\r\n"
    ")\r\n"
    "if not exist \"%SCRIPT%\" (\r\n"
    "  echo [ERROR] Script not found:\r\n"
    "  echo   %SCRIPT%\r\n"
    "  pause\r\n"
    "  exit /b 1\r\n"
    ")\r\n"
    '"%PY%" "%SCRIPT%"{extra} %*\r\n'
    "if errorlevel 1 (\r\n"
    "  echo.\r\n"
    "  echo [ERROR] The report script failed. Messages above.\r\n"
    "  pause\r\n"
    "  exit /b 1\r\n"
    ")\r\n"
    "exit /b 0\r\n"
)

# 只保留一个入口: 在线实时版(--live)。断网/接口全挂时会自动回退本地离线数据,
# 因此不再需要单独的"离线版" bat。想强制离线: 命令行跑 etf_rotation.py（不传 --live）。
VARIANTS = [
    ("ETF轮动一键报告.bat", " --live"),
]

DEFAULT_DESKTOPS = [r"E:\桌面", r"C:\Users\王旭\Desktop"]


def write_bat(path: str, extra: str) -> int:
    data = BAT_TMPL.format(extra=extra).encode("gbk")
    assert b"\x08" not in data, "生成内容里出现 0x08 退格符, 路径转义有误!"
    with open(path, "wb") as f:
        f.write(data)
    raw = open(path, "rb").read()
    assert b"\x08" not in raw, "回读发现 0x08, 文件损坏!"
    assert PY.encode("gbk") in raw, "回读发现 Python 路径不完整!"
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--desktop', default=None)
    ap.add_argument('--name', default=None, help='只生成指定名字的一个 bat')
    a = ap.parse_args()

    desk = a.desktop
    if not desk:
        desk = next((d for d in DEFAULT_DESKTOPS if os.path.isdir(d)), None)
    if not desk or not os.path.isdir(desk):
        raise SystemExit(f'桌面目录不存在: {desk}')

    items = ([(a.name, ' --live')] if a.name else VARIANTS)
    for name, extra in items:
        path = os.path.join(desk, name)
        n = write_bat(path, extra)
        print(f'[OK] 已生成: {path}  ({n} bytes, GBK, 无 0x08, 参数:{extra.strip() or "(离线)"})')
    print('[OK] 回读校验通过: Python 路径完整、无退格符')


if __name__ == '__main__':
    main()
