@echo off
rem ============================================================
rem  Remote key scanner - lists every key the Xiaomi remote can send
rem
rem  Keep this file PURE ASCII. cmd.exe reads .bat files in the
rem  system codepage (GBK on this machine), so UTF-8 Chinese here
rem  gets mangled, and any ">" inside a rem line is treated as a
rem  rem redirect and creates stray 0-byte files.
rem
rem  Usage:
rem    1) double-click this file
rem    2) press every button on the remote one by one
rem    3) the console lists each key; Esc or 180s ends the scan
rem
rem  Output log: tools\remote_keys_scan.log
rem ============================================================
setlocal

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0scan_remote_keys.py" --seconds 180

echo.
echo Scan finished. Log: %~dp0remote_keys_scan.log
pause
