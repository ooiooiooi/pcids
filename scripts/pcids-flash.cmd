@echo off
setlocal
rem CodeArts Build Windows entrypoint. It works both in a source checkout and
rem in the installed Electron application's resources\\flash-adapter directory.
set "PCIDS_ROOT=%~dp0.."
set "PCIDS_BACKEND_EXE=%PCIDS_ROOT%\backend\pcids_backend.exe"
if not defined PCIDS_BUNDLED_TOOLS_DIR if exist "%PCIDS_ROOT%\tools\burners" set "PCIDS_BUNDLED_TOOLS_DIR=%PCIDS_ROOT%\tools\burners"
if exist "%PCIDS_BACKEND_EXE%" (
  "%PCIDS_BACKEND_EXE%" --run-script "%~dp0pcids_flash.py" %*
) else if exist "%PCIDS_ROOT%\.venv\Scripts\python.exe" (
  "%PCIDS_ROOT%\.venv\Scripts\python.exe" "%~dp0pcids_flash.py" %*
) else (
  python "%~dp0pcids_flash.py" %*
)
exit /b %ERRORLEVEL%
