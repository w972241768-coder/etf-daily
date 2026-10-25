"""重写桌面一键启动 bat（去掉已废弃的 --live；GBK 写入并校验无 0x08 退格污染）。"""
import os

PATH = r"E:\桌面\ETF轮动一键报告.bat"

BAT = r"""@echo off
chcp 936 >nul
setlocal
set "PY=C:\Users\王旭\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "SCRIPT=C:\Users\王旭\.workbuddy\skills\etf-roc-ladder\scripts\etf_rotation.py"
if not exist "%PY%" (
  echo [ERROR] Python venv not found:
  echo   %PY%
  echo   Please check the install path.
  pause
  exit /b 1
)
if not exist "%SCRIPT%" (
  echo [ERROR] Script not found:
  echo   %SCRIPT%
  pause
  exit /b 1
)
"%PY%" "%SCRIPT%" %*
if errorlevel 1 (
  echo.
  echo [ERROR] The report script failed. Messages above.
  pause
  exit /b 1
)
exit /b 0
"""

if __name__ == "__main__":
    data = BAT.encode("gbk")
    assert b"\x08" not in data, "backslash-b escape bug!"
    assert b"--live" not in data
    with open(PATH, "wb") as f:
        f.write(data)
    print(f"bat updated: {PATH} ({len(data)} bytes, no --live, no 0x08)")
