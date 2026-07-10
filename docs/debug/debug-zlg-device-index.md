[OPEN] ZLG USBCANFD-200U device index mapping

## Scope
- Reproduce and fix ZLG USBCANFD-200U runtime device index detection.
- Preserve evidence for `ZCAN_GetDeviceInfoEx` return code mismatch and legacy fallback behavior.
- Validate automated tests and real-device enumeration/connection behavior.

## Hypotheses
1. `ZCAN_GetDeviceInfoEx` returns a non-`STATUS_OK` code for a valid opened device, and current logic drops valid probe results.
2. Legacy `ZCAN_GetDeviceInf` is either not bound or bound with an incompatible structure, preventing fallback enumeration.
3. Probe handles are not closed on every branch, which can poison later open/init attempts.
4. Multi-device mapping logic matches the wrong SDK index because it does not consistently compare normalized serial numbers.

## Evidence Plan
- Inspect current ctypes bindings and runtime probe code.
- Add minimal instrumentation only if static inspection cannot fully explain runtime behavior.
- Add regression tests for EX-success, fallback-success, dual-failure, multi-device mapping, and handle cleanup.

## Status
- Session opened.
