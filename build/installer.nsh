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

; electron-builder calls this hook before removing $INSTDIR. Defining it also
; replaces the template's default recursive removal, so keep that final RMDir.
!macro customRemoveFiles
  IfFileExists "$INSTDIR\data\*.*" 0 pcids_remove_install_files
    ReadRegStr $1 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" "Common AppData"
    StrCpy $0 "$1\PCIDS-InstallDataBackup"
    CreateDirectory "$0"
    DetailPrint "Preserving PCIDS data before uninstall or upgrade..."
    ExecWait '"$SYSDIR\robocopy.exe" "$INSTDIR\data" "$0" /MIR /COPY:DAT /DCOPY:DAT /R:1 /W:1' $9
    ${If} $9 >= 8
      MessageBox MB_ICONSTOP "Failed to preserve PCIDS data. Uninstall has been stopped. Robocopy exit code: $9$\r$\nSource: $INSTDIR\data$\r$\nBackup: $0"
      Abort
    ${EndIf}
    ${If} ${FileExists} "$INSTDIR\data\app_data.db"
      ${IfNot} ${FileExists} "$0\app_data.db"
        MessageBox MB_ICONSTOP "PCIDS database backup verification failed: $0\app_data.db"
        Abort
      ${EndIf}
    ${EndIf}
  pcids_remove_install_files:
  RMDir /r "$INSTDIR"
!macroend

!macro customInstall
  ; Keep CodeArts pipeline commands independent from the user-selected
  ; installation folder. This is a machine variable because the Windows
  ; CodeArts Agent commonly runs under a different service account.
  WriteRegExpandStr HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PCIDS_FLASH_ADAPTER" "$INSTDIR\resources\flash-adapter\pcids-flash.cmd"
  DetailPrint "Configured PCIDS_FLASH_ADAPTER for CodeArts Agent."
  WriteRegExpandStr HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PCIDS_INSTALL_ADAPTER" "$INSTDIR\resources\install-adapter\pcids-install.cmd"
  DetailPrint "Configured PCIDS_INSTALL_ADAPTER for CodeArts Agent."

  ; Restore data saved by the previous-version uninstaller before creating
  ; package defaults. This staging directory is used only during upgrade or
  ; uninstall; normal application runtime stays entirely below $INSTDIR\data.
  StrCpy $1 "$INSTDIR\data"
  CreateDirectory "$1"
  ReadRegStr $3 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" "Common AppData"
  StrCpy $2 "$3\PCIDS-InstallDataBackup"
  IfFileExists "$2\*.*" 0 pcids_restore_complete
    DetailPrint "Restoring PCIDS data saved before upgrade..."
    ExecWait '"$SYSDIR\robocopy.exe" "$2" "$1" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1' $9
    ${If} $9 >= 8
      MessageBox MB_ICONSTOP "Failed to restore PCIDS data after upgrade. Robocopy exit code: $9$\r$\nBackup: $2$\r$\nTarget: $1"
      Abort
    ${EndIf}
    ${If} ${FileExists} "$2\app_data.db"
      ${IfNot} ${FileExists} "$1\app_data.db"
        MessageBox MB_ICONSTOP "PCIDS database restore verification failed: $1\app_data.db"
        Abort
      ${EndIf}
    ${EndIf}
    RMDir /r "$2"
  pcids_restore_complete:

  ; Keep all writable configuration and business data below the operator-
  ; selected installation directory.
  DetailPrint "Granting workstation users access to PCIDS data: $1"
  ExecWait '"$SYSDIR\icacls.exe" "$1" /grant "*S-1-5-32-545:(OI)(CI)M" /T /C' $9
  ${If} $9 != 0
    MessageBox MB_ICONSTOP "Failed to set PCIDS data directory permissions. Exit code: $9$\r$\nPath: $1"
    Abort
  ${EndIf}
  IfFileExists "$1\agent-discovery.yaml" +3 0
    SetOutPath "$1"
    File /oname=agent-discovery.yaml "${PROJECT_DIR}\backend\config\agent-discovery.yaml"
  DetailPrint "LAN Agent discovery config: $1\agent-discovery.yaml"

  StrCpy $0 "$1\logs"
  CreateDirectory "$0"
  DetailPrint "Runtime logs: $0"

  DetailPrint "Burner drivers are installed separately with install-burner-drivers.ps1."
!macroend

!macro customUnInstall
  DeleteRegValue HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PCIDS_FLASH_ADAPTER"
  DeleteRegValue HKLM "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "PCIDS_INSTALL_ADAPTER"
  !insertmacro CloseRunningPcidsProcesses
!macroend
