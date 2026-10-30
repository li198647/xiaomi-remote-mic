@echo off
setlocal
title Alt Hold Test
echo This will switch to WorkBuddy, wait 3 seconds, then HOLD Alt for 5 seconds.
echo Watch the WorkBuddy microphone indicator.
echo.
"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" "%~dp0test_alt_hold.py" %*
echo.
pause
endlocal
