@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem  AL321 WinUSB driver install helper (wdi-simple.exe)
rem  - Re-launches itself as Administrator if needed
rem  - Tries to locate wdi-simple.exe in common locations:
rem      1) Project layout:   .\tools\burners\AL321\drivers\wdi-simple.exe
rem      2) Packaged layout:  .\resources\tools\burners\AL321\drivers\wdi-simple.exe
rem ============================================================

cd /d "%~dp0"

rem Check admin
net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo [INFO] Requesting administrator privileges...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList ''"
  exit /b 0
)

set "WDI="
if exist "%cd%\tools\burners\AL321\drivers\wdi-simple.exe" set "WDI=%cd%\tools\burners\AL321\drivers\wdi-simple.exe"
if not defined WDI if exist "%cd%\resources\tools\burners\AL321\drivers\wdi-simple.exe" set "WDI=%cd%\resources\tools\burners\AL321\drivers\wdi-simple.exe"

if not defined WDI (
  echo [ERROR] 找不到 wdi-simple.exe。
  echo         请确认当前目录下存在：
  echo         - tools\burners\AL321\drivers\wdi-simple.exe
  echo         或
  echo         - resources\tools\burners\AL321\drivers\wdi-simple.exe
  echo.
  pause
  exit /b 2
)

set "LOG_DIR=%cd%\logs"
set "LOG_FILE=%LOG_DIR%\al321_driver_install.log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
del /f /q "%LOG_FILE%" >nul 2>&1

echo AL321 WinUSB manual install > "%LOG_FILE%"
echo driver_installer="%WDI%" >> "%LOG_FILE%"
echo command="%WDI%" --name "PCIDS AL321" --vid 0x0403 --pid 0x6014 --type 0 --silent --timeout 120000 >> "%LOG_FILE%"

echo [INFO] Using: "%WDI%"
echo [INFO] Installing WinUSB for VID_0403&PID_6014 ...
"%WDI%" --name "PCIDS AL321" --vid 0x0403 --pid 0x6014 --type 0 --silent --timeout 120000
set "EXIT_CODE=%errorlevel%"

echo exit_code=%EXIT_CODE% >> "%LOG_FILE%"
echo [INFO] ExitCode=%EXIT_CODE%
echo [INFO] Log: "%LOG_FILE%"

echo.
echo [NEXT] 请拔插一次 AL321，然后重新执行烧录脚本验证是否还报 openFPGALoader JTAG 错误。
pause
exit /b %EXIT_CODE%

