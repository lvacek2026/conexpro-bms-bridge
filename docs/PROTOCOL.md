# Conexpro / JBD / Xiaoxiang BMS — BLE protocol reference

🇬🇧 **English**  ·  🇨🇿 [Česky](PROTOCOL.cs.md)

> Documented through interoperability research on the official Bluetooth
> interface of these BMS, then validated against captured frames from a
> production unit. Conexpro / Xiaoxiang / Jiabaida / LLT Power / Overkill
> Solar are all rebrands of the same JBD chipset and speak this exact
> protocol.

## TL;DR

| | |
|---|---|
| **GATT service** | `0000ff00-0000-1000-8000-00805f9b34fb` |
| **Notify (BMS → app)** | `0000ff01-0000-1000-8000-00805f9b34fb` |
| **Write (app → BMS)** | `0000ff02-0000-1000-8000-00805f9b34fb` |
| **Frame start** | `0xDD` |
| **Frame end** | `0x77` |
| **Read mode** | `0xA5` |
| **Write mode** | `0x5A` |

```
TX read:    DD A5 [reg] 00 [csum_hi] [csum_lo] 77        (7 bytes)
TX write:   DD 5A [reg] [N] [data 0..N-1] [csum_hi] [csum_lo] 77
RX:         DD [reg] [status] [N] [data 0..N-1] [csum_hi] [csum_lo] 77
            └─ status: 0x00 OK, 0x80/0x81 error
```

**Checksum:**

| Direction | Formula |
|-----------|---------|
| TX (app → BMS) | `((~(sum(data) + N + reg)) + 1) & 0xFFFF`, big-endian (hi, lo) |
| RX (BMS → app) | `((~(sum(data) + N + status)) + 1) & 0xFFFF`, big-endian (hi, lo) |

> The two formulas look identical because for a successful response
> `status == 0x00` is at the same byte offset as `reg` is in a TX frame
> (offset 2). Compute the checksum over `byte[2] + byte[3] + payload`. The
> distinction matters when the BMS returns an error (`status == 0x80`/
> `0x81`) — verifying with the register byte instead of the status byte will
> wrongly reject the frame.

For an empty TX payload (`N=0`) the checksum collapses to `(-reg) & 0xFFFF`.
So a "read register 0x03" frame is always `DD A5 03 00 FF FD 77`.

A complete frame is split across multiple BLE notifications because the
default ATT MTU is 23 bytes (= 20 bytes payload). Re-frame on the receiver
by locking onto `0xDD` and using the length byte at offset 3.

## Read registers

| Reg | Purpose | Payload |
|-----|---------|---------|
| `0x03` | Basic info — voltage, current, SOC, FET, balance, protections, temperatures | see below |
| `0x04` | Cell voltages | `N × u16 BE` in mV |
| `0x05` | Hardware version | ASCII / GB2312 string |
| `0xA0` | Manufacturer name | GB2312 string (some firmwares respond `0x81`) |
| `0x2E` | NTC details | per-NTC raw values |
| `0xAA` | Protection event counters (cumulative) | structured |
| `0xAB` | Charge / discharge history | structured |
| `0xF6` | Per-cell internal resistance | `N × u16` |
| `0xFA` | All EEPROM parameters | full settings dump |

The bridge (or your custom client) really only needs `0x03` + `0x04` for
live telemetry. `0x05` and `0xA0` are nice-to-have once at connect.

## Register `0x03` — basic info payload

| Offset | Size | Field | Scale / notes |
|---|---|---|---|
| 0 | u16 BE | total pack voltage | × 0.01 V |
| 2 | i16 BE | pack current | × 0.01 A *(signed; negative = discharging by JBD convention)* |
| 4 | i16 BE | remaining capacity | × 0.01 Ah *(if negative, add 655.36)* |
| 6 | i16 BE | nominal capacity | × 0.01 Ah *(same wrap)* |
| 8 | u16 BE | cycle count | |
| 10 | u16 BE | production date | bits 15..9 = year - 2000, bits 8..5 = month, bits 4..0 = day |
| 12 | u16 BE | balance state, cells 0..15 | bit per cell |
| 14 | u16 BE | balance state, cells 16..31 | bit per cell |
| 16 | u16 BE | protection state | see bitmap below |
| 18 | u8 | software version | high nibble = major, low nibble = minor |
| 19 | u8 | RSOC | % |
| 20 | u8 | FET state | bit0 = charge MOSFET on, bit1 = discharge MOSFET on |
| 21 | u8 | cell count `N` | |
| 22 | u8 | NTC count `M` | |
| 23 | M × u16 BE | NTC temps | Kelvin × 10. °C = (raw − 2731) / 10 |

**Optional tail** (present on newer firmwares — start offset = `23 + 2*M`):

| +0 | u8 | humidity / sentinel | if `0x88`, switch current/capacity scaling from /100 to /10 |
| +1 | u16 BE | alarm word | implementation-specific |
| +3 | u16 BE | learned capacity | × 0.01 Ah |
| +5 | i16 BE | balance current | × 0.01 A |

### Protection bitmap (16 bits)

Byte 17 is the low byte (bits 0..7), byte 16 is the high byte (bits 8..15):

| Bit | Flag |
|-----|------|
| 0   | cell over-voltage |
| 1   | cell under-voltage |
| 2   | pack over-voltage |
| 3   | pack under-voltage |
| 4   | charge over-temperature |
| 5   | charge under-temperature |
| 6   | discharge over-temperature |
| 7   | discharge under-temperature |
| 8   | charge over-current |
| 9   | discharge over-current |
| 10  | short circuit |
| 11  | IC error |
| 12  | MOSFET software lock |

## Register `0x04` — cell voltages

`N × u16 BE`, each in **millivolts**. Number of cells is implicit from the
payload length (`len / 2`), and matches the cell count reported by `0x03`.

Example response payload for 4S at 3.32 V/3.322 V/3.315 V/3.321 V:

```
0CF8 0CFA 0CF3 0CF9
```

## Write commands (documented for completeness — not used by the read-only bridge)

| Reg | rwMode | Action | Payload |
|-----|--------|--------|---------|
| `0x00` | `0x5A` | Enter factory / EEPROM mode | `56 78` |
| `0x01` | `0x5A` | Exit factory mode | `00 00` to save changes, `28 28` to discard |
| `0x06` | `0x5A` | Pair with password | `[len][ASCII password bytes]` |
| `0x0A` | `0x5A` | MOSFET / control sub-command | first byte = sub-cmd: `01` reset capacity, `02` clear records, `03` reboot, `04` clear protection, `05` sleep, `06` deep sleep, `07` open balance |
| `0xFB` | `0x5A` | Switch (charge / discharge enable) | `[which][state]` — which: `0`=discharge, `1`=charge, `2`=predischarge; state: `0`=open (on), `1`=close (off) |

> **None of these are sent by this bridge.** They are listed only for
> protocol completeness. Writing settings requires entering factory mode
> (`0x00 56 78`) first; forgetting the matching exit (`0x01 00 00` to
> commit, `0x01 28 28` to discard) leaves the BMS in a transient state.
> Test against a non-critical bench unit before touching anything live.

## Reference Python parser

A standalone, dependency-free implementation is at
[`../jbd_protocol.py`](../jbd_protocol.py). It builds and verifies frames,
reassembles BLE-MTU chunks, and parses `0x03`, `0x04`, `0x05`, and `0xA0`.
Useful for ad-hoc debugging:

```python
from jbd_protocol import build_read_frame, FrameAssembler, parse_basic, verify_frame
print(build_read_frame(0x03).hex())  # → "dda50300fffd77"
```

## Validation

The protocol described here was verified end-to-end on a production
**Conexpro 12.8 V / 150 Ah LFP** (advertised name `JBD-SP04S034-L4S-150A`,
firmware 2.4) over hci0 BlueZ on a Raspberry Pi. All field semantics, the
checksum quirk, register support set, and frame fragmentation behaviour
match the documentation above.
