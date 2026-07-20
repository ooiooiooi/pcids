@echo off
setlocal
rem CodeArts Build Windows entrypoint.  It calls only the PCIDS allow-listed adapter.
set "PCIDS_ROOT=%~dp0.."
if exist "%PCIDS_ROOT%\.venv\Scripts\python.exe" (
  "%PCIDS_ROOT%\.venv\Scripts\python.exe" "%~dp0pcids_flash.py" %*
) else (
  python "%~dp0pcids_flash.py" %*
)
exit /b %ERRORLEVEL%
