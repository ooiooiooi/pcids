!include "LogicLib.nsh"

!macro CloseRunningPcidsProcesses
  DetailPrint "Closing running PCIDS processes..."
  ExecWait '"$SYSDIR\taskkill.exe" /F /T /IM "程控安装部署系统.exe"' $8
  ExecWait '"$SYSDIR\taskkill.exe" /F /T /IM "pcids_backend.exe"' $8
  Sleep 1000
!macroend

!macro customInit
  !insertmacro CloseRunningPcidsProcesses
!macroend

!macro customInstall
  StrCpy $0 "$INSTDIR\logs"

  CreateDirectory "$0"
  DetailPrint "Granting write access to runtime log directory..."
  ExecWait '"$SYSDIR\icacls.exe" "$0" /grant "*S-1-5-32-545:(OI)(CI)M" /T /C' $9
  ${If} $9 != 0
    MessageBox MB_ICONSTOP "Failed to set log directory permissions. Exit code: $9$\r$\nPath: $0"
    Abort
  ${EndIf}

  DetailPrint "Burner drivers are installed separately with install-burner-drivers.ps1."
!macroend

!macro customUnInstall
  !insertmacro CloseRunningPcidsProcesses
!macroend
