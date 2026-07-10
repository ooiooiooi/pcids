# Debug Session: al321-flash-run

Status: OPEN

## Goal

Run the AL321 Flash programming flow once in the current environment and capture runtime evidence for the failure point.

## Hypotheses

1. Automatic driver switching fails before `hw_server` / `xsdb` / `program_flash` execute because no FTDI-compatible INF can be resolved for `USB\VID_0403&PID_6014`.
2. With `AL321_AUTO_DRIVER_SWITCH=0`, AMD tools may already work with the current `WinUSB` driver and the failure point will move to `xsdb` enumeration or `program_flash`.
3. `xsdb` can connect to `hw_server`, but the serial-number and target uniqueness checks reject the board selection.
4. `program_flash` starts successfully but fails later due to FSBL, boot mode, or QSPI topology issues.

## Evidence Log

- Runtime parameters recovered from task database:
  - `BOOT.bin`: `D:\workspace\pcids\uploads\task_runs\114\BOOT.bin`
  - `FSBL`: `C:\Users\pc\Desktop\CAN.elf`
  - `BURNER_SN`: `210512180081`
- `program_flash -help` succeeds with `D:\vitis\Vitis\2020.2\bin\program_flash.bat`.
- `hw_server` is listening on `TCP:127.0.0.1:3121`.
- `xsdb.bat` exits with code `0` against the scan script, but returns no `__PCIDS_TARGET__...` lines.
- Direct flash attempt under the current driver fails with:
  - `Connected to hw_server @ TCP:127.0.0.1:3121`
  - `ERROR: Unable to detect JTAG cable`
- Automatic driver switching was repaired and verified:
  - Initial state: `WinUSB / oem32.inf`
  - Switched state: `FTDIBUS / oem21.inf`
  - Restored state: `WinUSB / oem32.inf`
- After switching to `FTDIBUS`, `xsdb` enumerates the cable and targets:
  - `serial=210512180081`
  - `target_name=PS TAP / PMU / PL`
- `program_flash -jtagtargets -url TCP:127.0.0.1:3121` now returns:
  - `Target 0 : jsn-JTAG-HS1-210512180081`
  - `Device 0: jsn-JTAG-HS1-210512180081-14750093-0`
- Direct flash attempt with the repaired invocation now reaches the flash stage:
  - `Connected to hw_server @ TCP:127.0.0.1:3121`
  - `Retrieving Flash info...`
  - `ERROR: Flash Operation Failed`

## Hypothesis Status

1. Automatic driver switching fails before AMD tools execute because no FTDI-compatible INF can be resolved for `USB\VID_0403&PID_6014`.
   - Confirmed false after the script fix. Automatic switching now resolves and applies an FTDI bus driver.
2. With `AL321_AUTO_DRIVER_SWITCH=0`, AMD tools may already work with the current `WinUSB` driver and the failure point will move to `xsdb` enumeration or `program_flash`.
   - Confirmed false. AMD tools do not recognize the cable under the current `WinUSB` driver.
3. `xsdb` can connect to `hw_server`, but the serial-number and target uniqueness checks reject the board selection.
   - Confirmed false after switching to `FTDIBUS`. Targets are enumerated successfully.
4. `program_flash` starts successfully but fails later due to FSBL, boot mode, or QSPI topology issues.
   - Now plausible. The repaired flow reaches `Retrieving Flash info...` and then fails with `Flash Operation Failed`.
