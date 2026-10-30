@echo off
REM MiVoice - verify the remote OK key -> send chain
REM
REM Double-click this file. Follow the on-screen steps.
REM It prints a verdict at the end, so you do not have to read the log yourself.
REM
REM Keep this file PURE ASCII (cmd.exe reads .bat in the system codepage).
REM Do NOT put ">" inside rem/echo lines - it becomes a redirect.
setlocal

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0verify_remote_send.py"
echo.
pause
endlocal
