@echo off
REM MiVoice - Xiaomi remote HID probe
REM
REM Double-click this file. It asks you to press each remote button.
REM It shows whether volume / back keys reach Windows at all.
REM
REM Keep this file PURE ASCII (cmd.exe reads .bat in the system codepage).
REM Do NOT put ">" inside rem/echo lines - it becomes a redirect.
setlocal

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0probe_remote_hid.py"
echo.
pause
endlocal
