# Debug Session: xds510plus-burn

- Status: OPEN
- Goal: Restore XDS510plus probe usability on Windows, validate TI tool connectivity with `xds510plus_f28335.ccxml`, and reach a successful flash path.
- Constraints: Do not rely on fixed VID/PID as the primary identity; use physical location as the main online signal.

## Symptoms

- The device present at `Port_#0002.Hub_#0002` appears as `USB\VID_0547&PID_1020\6&23A967E&0&2`.
- Windows reports `CM_PROB_FAILED_INSTALL`.
- Dynamic driver package import fails with signature error `0xE000022F`.
- TI tooling exists locally, but probe enumeration is blocked before flash can start.

## Hypotheses

1. Windows Code Integrity is blocking the patched `sdusb2em` package because the INF has no signed catalog.
2. The hardware at the target physical port is compatible with the Spectrum Digital stack, but the shipped INF does not include the currently exposed hardware ID.
3. The hardware is not functionally compatible with the Spectrum Digital stack, so even if the driver installs, TI tooling may still fail to connect.
4. The current `.ccxml` is structurally valid for `F28335`, and the next blocker after driver recovery will be target connectivity rather than config parsing.
5. Future successful flashing must depend on burner-to-task mapping and physical presence checks, not on a fixed VID/PID signature.

## Evidence Log

- 2026-07-01: Confirmed local TI tools exist under `C:\ti`.
- 2026-07-01: Confirmed device location path resolves to `Port_#0002.Hub_#0002`.
- 2026-07-01: Confirmed patched driver import fails with `0xE000022F` in `setupapi.dev.log`.
- 2026-07-01: Confirmed current shell is not elevated (`IsAdmin=False`), so boot policy and driver strategy changes cannot be applied from the current session.
- 2026-07-01: Confirmed original Spectrum Digital package includes signed catalog `sdusb2em_ntamd64.cat`.
- 2026-07-01: Confirmed original signed `sdusb2em.inf` is already in Driver Store as `oem49.inf`, but it does not bind to the current `USB\VID_0547&PID_1020` device.
- 2026-07-01: Enabled Windows test signing in an elevated session.
- 2026-07-01: Confirmed test signing alone is insufficient for an unsigned INF; Windows still rejects a package with no catalog.
- 2026-07-01: Generated a local test-signed catalog and successfully staged/published the patched driver package as `oem72.inf` / `oem72.cat`.
- 2026-07-01: Device state recovered to `Spectrum Digital XDS510USB-PLUS`, class `SDUSBEmulators`, problem `CM_PROB_NONE`.
- 2026-07-01: UniFlash bundled DSLite lacked `SD510USBPLUS_JSC_Connection.xml`; CCS 8 targetdb contains the required connection and driver files.
- 2026-07-01: CCS 8 `DSLite.exe` can parse and initialize the `XDS510USB-PLUS JSC` configuration, but fails during target connection with `C28xx: Error connecting to the target`.
- 2026-07-01: Alternate non-PLUS `XDS510USB` configuration fails earlier with `C28xx: Error initializing emulator`, confirming the PLUS JSC path is closer to correct.

## Planned Next Steps

1. Collect elevated-environment evidence for boot policy and test-signing status.
2. Attempt a controlled driver-install path suitable for the current Windows integrity policy.
3. Re-check PnP state at the target physical location after any environment repair.
4. Validate TI connectivity with the existing `.ccxml`.
5. Only after evidence confirms the path, adjust code or configuration if still needed.
