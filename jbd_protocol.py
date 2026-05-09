"""JBD / Xiaoxiang / Conexpro BMS frame format and parsers.

Pure-Python, no third-party deps — kept separate from the BLE/MQTT bridge so
the protocol can be unit-tested in isolation.

Frame layout (matches BMSCommandEntity.calCmdApi in the decompiled APK):

    TX read:   DD A5 [reg] 00 [csum_hi] [csum_lo] 77        (7 bytes total)
    TX write:  DD 5A [reg] [N] [data 0..N-1] [csum_hi] [csum_lo] 77
    RX:        DD [reg] [status] [N] [data 0..N-1] [csum_hi] [csum_lo] 77

    checksum = ((~(sum(data) + N + reg)) + 1) & 0xFFFF, big-endian (hi, lo)
    status:   0x00 = OK, 0x80 = error
"""
from __future__ import annotations

import struct
from typing import Any

SVC_UUID    = "0000ff00-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"  # BMS → app
WRITE_UUID  = "0000ff02-0000-1000-8000-00805f9b34fb"  # app → BMS

FRAME_START = 0xDD
FRAME_END   = 0x77
READ_MODE   = 0xA5
WRITE_MODE  = 0x5A

REG_BASIC   = 0x03
REG_CELLS   = 0x04
REG_HW_VER  = 0x05
REG_MFR     = 0xA0

# Order matches BMSBaseInfoCMDEntity.protectionTypes[] in the APK.
PROTECTION_BITS = [
    "cell_over_voltage",
    "cell_under_voltage",
    "pack_over_voltage",
    "pack_under_voltage",
    "charge_over_temperature",
    "charge_under_temperature",
    "discharge_over_temperature",
    "discharge_under_temperature",
    "charge_over_current",
    "discharge_over_current",
    "short_circuit",
    "ic_error",
    "mosfet_software_lock",
]


def checksum(b2: int, payload: bytes) -> bytes:
    """JBD checksum.

    b2 is the byte at frame index 2 — that's the **register** for TX frames
    and the **status byte** for RX frames (which is normally 0x00). The APK's
    BMSCommandEntity uses `cmd` for the static TX helper but `bArr[2]` for the
    instance RX verifier; here we make the caller pass the right value.
    """
    s = (sum(payload) + len(payload) + b2) & 0xFFFF
    cs = ((~s) + 1) & 0xFFFF
    return struct.pack(">H", cs)


def build_read_frame(reg: int) -> bytes:
    payload = b""
    return bytes([FRAME_START, READ_MODE, reg, len(payload)]) + payload + checksum(reg, payload) + bytes([FRAME_END])


def build_write_frame(reg: int, payload: bytes) -> bytes:
    return bytes([FRAME_START, WRITE_MODE, reg, len(payload)]) + payload + checksum(reg, payload) + bytes([FRAME_END])


def verify_frame(frame: bytes) -> bool:
    if len(frame) < 7 or frame[0] != FRAME_START or frame[-1] != FRAME_END:
        return False
    payload_len = frame[3]
    if len(frame) != payload_len + 7:
        return False
    status = frame[2]
    payload = frame[4:4 + payload_len]
    return frame[4 + payload_len:4 + payload_len + 2] == checksum(status, payload)


class FrameAssembler:
    """Stitches BLE notification chunks back into complete JBD frames.

    Default GATT MTU is 23 (20 bytes payload), so a 30+ byte basic-info frame
    arrives as multiple notifications.
    """

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buf.extend(data)
        out: list[bytes] = []
        while True:
            i = self.buf.find(FRAME_START)
            if i < 0:
                self.buf.clear()
                break
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 4:
                break
            payload_len = self.buf[3]
            total = 4 + payload_len + 3
            if len(self.buf) < total:
                break
            if self.buf[total - 1] != FRAME_END:
                del self.buf[0]
                continue
            frame = bytes(self.buf[:total])
            del self.buf[:total]
            out.append(frame)
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Per-register parsers
# ──────────────────────────────────────────────────────────────────────────────
def parse_basic(payload: bytes) -> dict[str, Any]:
    """Register 0x03 — basic info."""
    if len(payload) < 23:
        raise ValueError(f"basic-info payload too short ({len(payload)} bytes)")

    total_voltage_v   = struct.unpack(">H", payload[0:2])[0] / 100.0
    current_a         = struct.unpack(">h", payload[2:4])[0] / 100.0
    remaining_ah      = struct.unpack(">h", payload[4:6])[0] / 100.0
    nominal_ah        = struct.unpack(">h", payload[6:8])[0] / 100.0
    if remaining_ah < 0:
        remaining_ah += 655.36
    if nominal_ah < 0:
        nominal_ah += 655.36

    cycle_count       = struct.unpack(">H", payload[8:10])[0]
    prod_packed       = struct.unpack(">H", payload[10:12])[0]
    production_date   = f"{2000 + (prod_packed >> 9)}-{(prod_packed >> 5) & 0x0F:02d}-{prod_packed & 0x1F:02d}"

    balance_lo        = struct.unpack(">H", payload[12:14])[0]
    balance_hi        = struct.unpack(">H", payload[14:16])[0]
    balance_mask_32   = (balance_hi << 16) | balance_lo

    protection_word   = struct.unpack(">H", payload[16:18])[0]
    protections = {name: bool((protection_word >> bit) & 1) for bit, name in enumerate(PROTECTION_BITS)}

    fw_byte           = payload[18]
    fw_version        = f"{fw_byte >> 4}.{fw_byte & 0x0F}"

    soc_pct           = payload[19]
    fet_state         = payload[20]
    cell_count        = payload[21]
    ntc_count         = payload[22]

    humidity_offset   = 23 + ntc_count * 2
    if len(payload) > humidity_offset and payload[humidity_offset] == 0x88:
        # APK switches scaling for current/capacity when humidity sentinel is present.
        current_a    = struct.unpack(">h", payload[2:4])[0] / 10.0
        remaining_ah = struct.unpack(">h", payload[4:6])[0] / 10.0

    temperatures_c: list[float] = []
    for i in range(ntc_count):
        off = 23 + i * 2
        if off + 1 >= len(payload):
            break
        raw_kx10 = struct.unpack(">H", payload[off:off + 2])[0]
        temperatures_c.append(round((raw_kx10 - 2731) / 10.0, 1))

    balance_states = [bool((balance_mask_32 >> i) & 1) for i in range(cell_count)]

    out: dict[str, Any] = {
        "voltage_v":          round(total_voltage_v, 2),
        "current_a":          round(current_a, 2),
        "power_w":            round(total_voltage_v * current_a, 1),
        "remaining_ah":       round(remaining_ah, 2),
        "capacity_ah":        round(nominal_ah, 2),
        "soc_pct":            soc_pct,
        "cycle_count":        cycle_count,
        "production_date":    production_date,
        "firmware_version":   fw_version,
        "cell_count":         cell_count,
        "ntc_count":          ntc_count,
        "temperatures_c":     temperatures_c,
        "charging_mosfet":    bool(fet_state & 0x01),
        "discharging_mosfet": bool(fet_state & 0x02),
        "balance_states":     balance_states,
        "balance_active":     any(balance_states),
        "protections":        protections,
        "protection_active":  any(protections.values()),
        "protection_word":    protection_word,
    }

    extra_off = humidity_offset
    if len(payload) > extra_off + 7:
        out["humidity"]            = payload[extra_off]
        out["alter"]               = struct.unpack(">H", payload[extra_off + 1:extra_off + 3])[0]
        out["learned_capacity_ah"] = round(struct.unpack(">H", payload[extra_off + 3:extra_off + 5])[0] / 100.0, 2)
        out["balance_current_a"]   = round(struct.unpack(">h", payload[extra_off + 5:extra_off + 7])[0] / 100.0, 2)
    return out


def parse_cells(payload: bytes) -> dict[str, Any]:
    """Register 0x04 — N × u16 BE in millivolts."""
    if len(payload) % 2 != 0:
        raise ValueError(f"cell payload not aligned ({len(payload)} bytes)")
    cells_v = [struct.unpack(">H", payload[i:i + 2])[0] / 1000.0 for i in range(0, len(payload), 2)]
    if not cells_v:
        return {"cells_v": [], "cell_min_v": None, "cell_max_v": None, "cell_delta_v": None}
    cmin, cmax = min(cells_v), max(cells_v)
    return {
        "cells_v":      [round(v, 3) for v in cells_v],
        "cell_min_v":   round(cmin, 3),
        "cell_max_v":   round(cmax, 3),
        "cell_delta_v": round(cmax - cmin, 3),
        "cell_min_idx": cells_v.index(cmin),
        "cell_max_idx": cells_v.index(cmax),
    }


def parse_hw_version(payload: bytes) -> dict[str, Any]:
    try:
        return {"hardware_name": payload.decode("gb2312", errors="replace").strip("\x00 ")}
    except Exception:
        return {"hardware_name_hex": payload.hex()}


def parse_manufacturer(payload: bytes) -> dict[str, Any]:
    try:
        return {"manufacturer": payload.decode("gb2312", errors="replace").strip("\x00 ")}
    except Exception:
        return {"manufacturer_hex": payload.hex()}


REG_PARSERS = {
    REG_BASIC:  ("basic",        parse_basic),
    REG_CELLS:  ("cells",        parse_cells),
    REG_HW_VER: ("hw_version",   parse_hw_version),
    REG_MFR:    ("manufacturer", parse_manufacturer),
}


def wrap_response(reg: int, payload: bytes, status: int = 0) -> bytes:
    """Build a fake RX frame (used by tests). Checksum uses *status*."""
    return bytes([FRAME_START, reg, status, len(payload)]) + payload + checksum(status, payload) + bytes([FRAME_END])
