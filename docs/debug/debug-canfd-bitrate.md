[OPEN] CAN FD bitrate parsing

## Scope
- Reproduce and fix CAN FD bitrate parsing for ZLG USBCANFD-200U.
- Validate arbitration/data bitrate field precedence and unit normalization.
- Preserve evidence for backend validation and real-device connection behavior.

## Hypotheses
1. `init_channel()` does not prioritize `arb_baud_rate` / `arb_bitrate`, so the backend misses the actual CAN FD arbitration bitrate field.
2. `_normalize_can_bitrate()` only accepts integer-like input and rejects `500kbps` / `2Mbps`.
3. Data bitrate normalization fails for the same unit-parsing reason as arbitration bitrate.
4. The reported error is raised before `ZCAN_SetValue`, so fixing normalization should allow SDK calls to proceed.

## Evidence Plan
- Inspect current bitrate normalization and CAN FD config field selection.
- Add regression tests for numeric, digit-string, kbps, Mbps, empty, and invalid-unit inputs.
- Run backend and frontend unit tests, then verify real-device connection with 500kbps + 2Mbps + BRS.

## Status
- Session opened.
