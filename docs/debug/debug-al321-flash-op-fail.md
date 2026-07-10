# Debug Session: al321-flash-op-fail

Status: OPEN

## Goal

Diagnose why AL321 ZynqMP Flash programming reaches `Retrieving Flash info...` and then fails with `ERROR: Flash Operation Failed`.

## Current Evidence

- Task log shows `program_flash -help` succeeds.
- Task log shows automatic driver switching succeeds far enough for AMD tools to enumerate the cable.
- Task log shows `xsdb` enumeration succeeds.
- Task log shows `program_flash -jtagtargets -url TCP:127.0.0.1:3121` succeeds and resolves:
  - `target_name=jsn-JTAG-HS1-210512180081-14750093-0`
  - `target_id=2`
  - `device_name=xczu15`
- The latest task log reaches:
  - `Connected to hw_server @ TCP:127.0.0.1:3121`
  - `Available targets and devices:`
  - `Target 0 : jsn-JTAG-HS1-210512180081`
  - `Device 0: jsn-JTAG-HS1-210512180081-14750093-0`
  - `Retrieving Flash info...`
  - `ERROR: Flash Operation Failed`

## Falsifiable Hypotheses

1. The selected FSBL ELF does not match the current board hardware design, so `program_flash` can connect to the target but fails when initializing Flash access.
2. The board boot mode or PSU-side initialization state prevents QSPI access, so `program_flash` reaches Flash discovery and then aborts.
3. The configured `-flash_type qspi-x4-single` does not match the actual QSPI topology, so Flash info retrieval fails after target selection succeeds.
4. The target is correct, but AMD tools require additional runtime evidence from the exact `program_flash` output or supplementary XSDB/board state to distinguish PSU init failure from Flash topology mismatch.

## Next Step

Add minimal instrumentation around the AL321 Flash flow so the task log captures the exact `program_flash` failure context needed to confirm or reject the hypotheses above.
