@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0.."
title Hera UAT - Generate Dashboard

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
echo  Hera UAT - Generate Dashboard
echo  This reads the most recently updated Excel report under
echo  Reports\ and builds a fresh chart-driven dashboard under
echo  Dashboards\. The source report is never modified.
echo ============================================================
echo.

py generate_dashboard.py %*
set "EXITCODE=%errorlevel%"

if not "%EXITCODE%"=="0" (
    echo.
    echo ============================================================
    echo  Dashboard generation stopped with exit code %EXITCODE%.
    echo  Scroll up for details. Press any key to close this window.
    echo ============================================================
    pause >nul
    endlocal
    exit /b %EXITCODE%
)

rem Open the dashboard that was just generated (newest .xlsx under
rem Dashboards\, skipping Excel's own "~$" lock files).
set "LATEST="
for /f "delims=" %%F in ('dir /b /o-d "Dashboards\*.xlsx" 2^>nul ^| findstr /v /b "~$"') do (
    if not defined LATEST set "LATEST=%%F"
)

echo.
echo ============================================================
if defined LATEST (
    echo  Opening Dashboards\!LATEST!
    echo ============================================================
    start "" "Dashboards\!LATEST!"
) else (
    echo  Done, but the new dashboard file could not be located to
    echo  open automatically. Check the Dashboards\ folder.
    echo ============================================================
)
echo  Press any key to close this window.
pause >nul
endlocal
