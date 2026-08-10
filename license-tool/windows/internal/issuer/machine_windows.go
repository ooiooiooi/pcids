//go:build windows

package issuer

import (
	"encoding/json"
	"errors"
	"os/exec"
	"strings"
	"syscall"

	"golang.org/x/sys/windows/registry"
)

func readMachineGuid() string {
	key, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Cryptography`,
		registry.QUERY_VALUE|registry.WOW64_64KEY,
	)
	if err != nil {
		return ""
	}
	defer key.Close()
	value, _, err := key.GetStringValue("MachineGuid")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(value)
}

func platformMachineComponents() (map[string]string, error) {
	if machineGuid := readMachineGuid(); machineGuid != "" {
		return map[string]string{"machine_guid": machineGuid}, nil
	}

	script := `$ErrorActionPreference = 'SilentlyContinue'
$system = Get-CimInstance Win32_ComputerSystemProduct
$bios = Get-CimInstance Win32_BIOS
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
@{
  smbios_uuid = [string]$system.UUID
  bios_serial = [string]$bios.SerialNumber
  volume_serial = [string]$drive.VolumeSerialNumber
} | ConvertTo-Json -Compress`
	command := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	output, err := command.Output()
	if err != nil {
		return nil, errors.New("无法读取 Windows 机器标识")
	}
	var components map[string]string
	if err := json.Unmarshal(output, &components); err != nil {
		return nil, errors.New("Windows 机器标识格式无效")
	}
	for key, value := range components {
		if strings.TrimSpace(value) == "" {
			delete(components, key)
		}
	}
	if len(components) == 0 {
		return nil, errors.New("Windows 机器标识为空")
	}
	return components, nil
}
