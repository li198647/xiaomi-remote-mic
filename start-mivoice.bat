@echo off
rem ============================================================
rem  MiVoice launcher - runs mivoice.py with pythonw.exe
rem  (no console window; the app stays in the system tray)
rem
rem  Keep this file PURE ASCII. cmd.exe reads .bat files in the
rem  system codepage (GBK on this machine), so UTF-8 Chinese here
rem  gets mangled, and any ">" inside a rem line is treated as a
rem  redirect and creates stray 0-byte files.
rem
rem  Prefer the BASE pythonw.exe: this project has zero third-party
rem  dependencies (stdlib ctypes only). The pythonw.exe inside the
rem  venv is a small launcher that forks a second process, which
rem  makes Task Manager look like the app started twice.
rem
rem  Usage:
rem    1) install VB-CABLE first (see vbcable\ folder), reboot once
rem    2) Windows sound settings, Input, set default to CABLE Output
rem    3) double-click this file; a mic icon shows up in the tray
rem    4) click into the WorkBuddy input box, hold the remote voice
rem       key, speak, release; press the OK key to send
rem
rem  Troubleshooting: tray menu, view log (mivoice.log)
rem  Quit hotkey: Ctrl+Shift+M
rem ============================================================
setlocal

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
if not exist "%PY%" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=pythonw"

start "" "%PY%" "%~dp0mivoice.py"
