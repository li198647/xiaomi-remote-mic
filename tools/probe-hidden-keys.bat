@echo off
REM MiVoice - hidden keys probe (Volume+ / Volume- / Back)
REM
REM Double-click this file. It first lists what the remote HID descriptor
REM declares (no key press needed), then listens for real key presses.
REM
REM Keep this file PURE ASCII (cmd.exe reads .bat in the system codepage).
REM Do NOT put ">" inside rem/echo lines - it becomes a redirect.
setlocal

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -u "%~dp0probe_hidden_keys.py" %*
echo.
pause
endlocal
