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
  ; Keep CodeArts pipeline commands independent from the user-selected
  ; installation folder. This is a machine variable because the Windows
  ; CodeArts Agent commonly runs under a different service account.
  WriteRegExpandStr HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PCIDS_FLASH_ADAPTER" "$INSTDIR\resources\flash-adapter\pcids-flash.cmd"
  DetailPrint "Configured PCIDS_FLASH_ADAPTER for CodeArts Agent."

  ; The LAN Agent discovery configuration is deliberately external to the
  ; application package.  Preserve an operator-edited file on upgrades.
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" "Common AppData"
  StrCpy $1 "$0\PCIDS"
  CreateDirectory "$1"
  IfFileExists "$1\agent-discovery.yaml" +3 0
    SetOutPath "$1"
    File /oname=agent-discovery.yaml "${PROJECT_DIR}\backend\config\agent-discovery.yaml"
  DetailPrint "LAN Agent discovery config: $1\agent-discovery.yaml"

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
  DeleteRegValue HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PCIDS_FLASH_ADAPTER"
  !insertmacro CloseRunningPcidsProcesses
!macroend
