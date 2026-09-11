@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
title Hera UAT - Run All Tests

where py >nul 2>nul
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  ERROR: Python launcher "py" was not found on this machine.
    echo  Install Python ^(from python.org, "Add py launcher" option^)
    echo  and try again.
    echo ============================================================
    pause
    exit /b 1
)

echo ============================================================
echo  Hera UAT - Run All Tests
echo  This will launch every folder's test script in order.
echo  Each script stays interactive: MFA login, environment choice,
echo  and Pass/Fail/Skip/Inconclusive confirmations still work
echo  exactly as when run by hand.
echo ============================================================
echo.

py run_all_tests.py %*
set "EXITCODE=%errorlevel%"

echo.
echo ============================================================
if "%EXITCODE%"=="0" (
    echo  Run finished. See the summary above and the Excel report
    echo  path it printed under Reports\.
) else (
    echo  Run stopped with exit code %EXITCODE%. Scroll up for details.
)
echo  Press any key to close this window.
echo ============================================================
pause >nul
endlocal
