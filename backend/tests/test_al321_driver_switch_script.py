import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tools" / "burners" / "AL321" / "drivers" / "switch-al321-driver.ps1"


class Al321DriverSwitchScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_records_original_driver_state_before_switch(self):
        self.assertIn('[ValidateSet("amd", "winusb", "recover-pending")]', self.content)
        self.assertIn("InstanceId = [string]$device.InstanceId", self.content)
        self.assertIn("OriginalInf = [string]$current.Inf", self.content)
        self.assertIn("OriginalService = [string]$current.Service", self.content)
        self.assertIn('State = "pending_restore"', self.content)
        self.assertIn("Save-State $state", self.content)

    def test_already_active_amd_driver_is_treated_as_success_without_forcing_ftdi_back_to_winusb(self):
        self.assertIn('$current.Service -eq "FTDIBUS"', self.content)
        self.assertIn('$current.PublishedInfPath -eq $resolvedAmdInfPath', self.content)
        self.assertIn('AL321 already uses the required FTDI/AMD driver; no driver switch or WinUSB restore is needed.', self.content)
        self.assertIn('OriginalService = "WinUSB"', self.content)
        self.assertIn('State = "amd_active"', self.content)
        self.assertIn("AL321 driver already uses AMD/Digilent driver", self.content)
        self.assertIn("exit 0", self.content)

    def test_recovery_deletes_state_file_only_after_successful_validation(self):
        self.assertIn("Restart-And-ValidateDevice $instanceId | Out-Null", self.content)
        self.assertIn("if ($State.OriginalService -and $after.Service -ne [string]$State.OriginalService)", self.content)
        self.assertIn("Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop", self.content)
        validate_index = self.content.index("Restart-And-ValidateDevice $instanceId | Out-Null")
        delete_index = self.content.rindex("Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop")
        self.assertLess(validate_index, delete_index)

    def test_recovery_failure_and_interrupted_recovery_preserve_state_file(self):
        self.assertIn("Unable to resolve the original INF for recovery. State file preserved for manual recovery.", self.content)
        self.assertIn('throw "Pending recovery requires the original AL321 device to be present: $instanceId"', self.content)
        self.assertIn('if ($Mode -eq "recover-pending") {', self.content)

    def test_script_is_compatible_with_windows_powershell_5(self):
        self.assertNotIn("??", self.content)
        self.assertIn("function ConvertTo-StringOrEmpty", self.content)

    def test_driver_switch_can_fallback_to_pnputil_without_devcon(self):
        self.assertIn("function Invoke-PnpUtilDriverInstall", self.content)
        self.assertIn("function Invoke-NewDevForceUpdate", self.content)
        self.assertIn("& pnputil.exe /add-driver $InfPath /install | Out-Host", self.content)
        self.assertIn('Write-Warning "devcon.exe was not found; forcing driver selection via UpdateDriverForPlugAndPlayDevices."', self.content)
        self.assertIn("[Pcids.Al321.NewDev]::UpdateDriverForPlugAndPlayDevices", self.content)
        self.assertIn('Write-Warning ("UpdateDriverForPlugAndPlayDevices did not complete cleanly: {0}. Continuing with the pnputil-installed driver package." -f $_.Exception.Message)', self.content)
        self.assertIn('Write-Warning "devcon update failed with exit code $LASTEXITCODE; continuing with the pnputil-installed driver package."', self.content)

    def test_driver_switch_requires_exactly_one_present_compatible_device(self):
        self.assertIn("function Assert-OnlyTargetCompatibleDevicePresent", self.content)
        self.assertIn('throw "Automatic AL321 driver switching requires exactly one present $HardwareId device; found $($devices.Count)."', self.content)
        self.assertIn("Assert-OnlyTargetCompatibleDevicePresent $HardwareId $InstanceId | Out-Null", self.content)

    def test_shared_ftdi_driver_switch_requires_a_stable_serial_and_never_restores_generic_winusb(self):
        self.assertIn("function Test-IsStableUsbSerial", self.content)
        self.assertIn("BURNER_SN must be a stable hardware serial", self.content)
        self.assertIn("Refusing to restore a generic WinUSB binding for shared FTDI VID_0403&PID_6014", self.content)
        self.assertIn("Refusing to switch a shared FTDI VID_0403&PID_6014 device currently bound to WinUSB", self.content)

    def test_amd_inf_resolution_scans_d_vitis_tool_and_cable_driver_locations(self):
        self.assertIn('"D:\\vitis\\Vitis\\*"', self.content)
        self.assertIn('"D:\\vitis\\Vivado\\*"', self.content)
        self.assertIn('Join-Path $installRoot "data\\xicom\\cable_drivers"', self.content)
        self.assertIn('Add-UniquePath $roots (Join-Path $env:WINDIR "INF")', self.content)

    def test_ftdi_device_rejects_xpcwinusb_and_generic_winusb_candidates(self):
        self.assertIn('if ($category -eq "ftdi") {', self.content)
        self.assertIn('xpcwinusb.inf / 03FD Xilinx cable drivers do not match 0403:6014 FTDI devices.', self.content)
        self.assertIn('This INF belongs to a libwdi / WinUSB package, not an FTDI cable driver.', self.content)

    def test_resolution_error_mentions_skip_switch_and_explicit_inf_override(self):
        self.assertIn('set AL321_AUTO_DRIVER_SWITCH=0 to skip automatic switching', self.content)
        self.assertIn('set AL321_AMD_DRIVER_INF to that file path', self.content)
        self.assertIn('Current device VID/PID: $TargetHardwareId', self.content)
        self.assertIn('The current AL321 is an FTDI / WinUSB device. Vitis xpcwinusb.inf is a 03FD Xilinx cable driver and cannot be used for this device.', self.content)

    def test_multiple_candidate_infs_are_reported_without_guessing(self):
        self.assertIn('Multiple AL321 cable driver INFs match the current device. PCIDS will not guess.', self.content)
        self.assertIn('Candidate INF paths:', self.content)

    def test_ftdi_candidates_can_prefer_newer_ftdibus_driverver(self):
        self.assertIn("function Get-InfDriverVersionInfo", self.content)
        self.assertIn("function Select-BestAcceptedCandidate", self.content)
        self.assertIn("Sort-Object Score, Version, Date, Path -Descending", self.content)


if __name__ == "__main__":
    unittest.main()
