@echo off
REM MiVoice - Xiaomi remote key calibration tool
REM
REM Double-click this file. It asks you to press each remote button.
REM Press the physical keyboard "N" key to skip an item. Press Esc to quit.
REM
REM Keep this file PURE ASCII (cmd.exe reads .bat in the system codepage).
REM Do NOT put ">" inside rem/echo lines - it becomes a redirect.
setlocal

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0map_remote_keys.py"
echo.
echo Done. Log file: %~dp0remote_keymap.log
pause
endlocal
