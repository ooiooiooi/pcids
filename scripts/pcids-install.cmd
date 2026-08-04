@echo off
setlocal
rem CodeArts Build Windows entrypoint for scripted OS application installation.
rem It works from a source checkout and from resources\install-adapter.
set "PCIDS_ROOT=%~dp0.."
set "PCIDS_BACKEND_EXE=%PCIDS_ROOT%\backend\pcids_backend.exe"
if not defined PCIDS_BUNDLED_TOOLS_DIR if exist "%PCIDS_ROOT%\tools\burners" set "PCIDS_BUNDLED_TOOLS_DIR=%PCIDS_ROOT%\tools\burners"
if exist "%PCIDS_BACKEND_EXE%" (
  "%PCIDS_BACKEND_EXE%" --run-script "%~dp0pcids_install.py" %*
) else if exist "%PCIDS_ROOT%\.venv\Scripts\python.exe" (
  "%PCIDS_ROOT%\.venv\Scripts\python.exe" "%~dp0pcids_install.py" %*
) else (
  python "%~dp0pcids_install.py" %*
)
exit /b %ERRORLEVEL%
