#!/usr/bin/env python3
"""Conexpro / JBD / Xiaoxiang BMS → MQTT bridge (read-only).

Connects to a JBD-family BMS over BLE GATT, polls the documented register
set, parses the binary frames, and publishes a JSON document to MQTT.

Designed to be reusable: anyone with a JBD/Xiaoxiang/Conexpro/LLT/Daly-rebrand
BMS that uses the standard 0xFF00 service can clone the folder, set their MAC
in .env, run `docker compose up -d`, and consume the data wherever they like.

Spec: docs/conexpro-bms-protocol.md (or the README in this folder).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import paho.mqtt.client as mqtt

from jbd_protocol import (
    NOTIFY_UUID, WRITE_UUID,
    REG_PARSERS, REG_BASIC, REG_CELLS, REG_HW_VER, REG_MFR,
    FrameAssembler,
    build_read_frame, verify_frame,
)

REGISTER_NAMES = {
    REG_BASIC:  "basic",
    REG_CELLS:  "cells",
    REG_HW_VER: "hw_version",
    REG_MFR:    "manufacturer",
}
REGISTER_BY_NAME = {v: k for k, v in REGISTER_NAMES.items()}


@dataclass
class Config:
    bms_mac:         Optional[str] = None
    bms_name_prefix: str           = "JBD"
    poll_interval:   float         = 30.0
    conn_retry:      float         = 10.0
    read_timeout:    float         = 5.0
    registers:       list[int]     = field(default_factory=lambda: [REG_BASIC, REG_CELLS, REG_HW_VER])
    mqtt_host:       str           = "127.0.0.1"
    mqtt_port:       int           = 1883
    mqtt_username:   Optional[str] = None
    mqtt_password:   Optional[str] = None
    mqtt_topic:      str           = "bms/main"
    mqtt_qos:        int           = 0
    mqtt_retain:     bool          = True
    ha_discovery:    bool          = False
    ha_prefix:       str           = "homeassistant"
    ha_device_name:  str           = "Conexpro BMS"
    log_level:       str           = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        regs_env = os.getenv("REGISTERS", "basic,cells,hw_version").strip()
        registers: list[int] = []
        for token in [t.strip() for t in regs_env.split(",") if t.strip()]:
            if token in REGISTER_BY_NAME:
                registers.append(REGISTER_BY_NAME[token])
            else:
                try:
                    registers.append(int(token, 0))
                except ValueError:
                    logging.warning("Unknown register %r in REGISTERS env, ignoring", token)
        if not registers:
            registers = [REG_BASIC, REG_CELLS, REG_HW_VER]

        return cls(
            bms_mac         = os.getenv("BMS_MAC") or None,
            bms_name_prefix = os.getenv("BMS_NAME_PREFIX", "JBD"),
            poll_interval   = float(os.getenv("POLL_INTERVAL", "30")),
            conn_retry      = float(os.getenv("CONN_RETRY", "10")),
            read_timeout    = float(os.getenv("READ_TIMEOUT", "5")),
            registers       = registers,
            mqtt_host       = os.getenv("MQTT_HOST", "127.0.0.1"),
            mqtt_port       = int(os.getenv("MQTT_PORT", "1883")),
            mqtt_username   = os.getenv("MQTT_USERNAME") or None,
            mqtt_password   = os.getenv("MQTT_PASSWORD") or None,
            mqtt_topic      = os.getenv("MQTT_TOPIC", "bms/main").rstrip("/"),
            mqtt_qos        = int(os.getenv("MQTT_QOS", "0")),
            mqtt_retain     = os.getenv("MQTT_RETAIN", "true").lower() == "true",
            ha_discovery    = os.getenv("HA_DISCOVERY", "false").lower() == "true",
            ha_prefix       = os.getenv("HA_DISCOVERY_PREFIX", "homeassistant"),
            ha_device_name  = os.getenv("HA_DEVICE_NAME", "Conexpro BMS"),
            log_level       = os.getenv("LOG_LEVEL", "INFO").upper(),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Home Assistant MQTT auto-discovery
# ──────────────────────────────────────────────────────────────────────────────
HA_SENSORS = [
    # (key in JSON document, friendly name, unit, device_class, state_class, icon)
    ("voltage_v",         "Voltage",          "V",   "voltage",     "measurement", None),
    ("current_a",         "Current",          "A",   "current",     "measurement", None),
    ("power_w",           "Power",            "W",   "power",       "measurement", None),
    ("soc_pct",           "State of charge",  "%",   "battery",     "measurement", None),
    ("remaining_ah",      "Remaining",        "Ah",  None,          "measurement", "mdi:battery-50"),
    ("capacity_ah",       "Capacity",         "Ah",  None,          "measurement", "mdi:battery-high"),
    ("cycle_count",       "Cycles",           None,  None,          "total_increasing", "mdi:counter"),
    ("cell_min_v",        "Cell min",         "V",   "voltage",     "measurement", "mdi:battery-low"),
    ("cell_max_v",        "Cell max",         "V",   "voltage",     "measurement", "mdi:battery-high"),
    ("cell_delta_v",      "Cell delta",       "V",   "voltage",     "measurement", "mdi:delta"),
    ("firmware_version",  "Firmware",         None,  None,          None,          "mdi:chip"),
    ("hardware_name",     "Hardware",         None,  None,          None,          "mdi:identifier"),
    ("production_date",   "Manufactured",     None,  None,          None,          "mdi:calendar"),
]
HA_BINARY_SENSORS = [
    # (key, name, device_class, icon)
    ("charging_mosfet",    "Charge MOSFET",      "power",      "mdi:battery-charging"),
    ("discharging_mosfet", "Discharge MOSFET",   "power",      "mdi:battery-arrow-down"),
    ("balance_active",     "Balancing",          None,         "mdi:scale-balance"),
    ("protection_active",  "Protection",         "problem",    "mdi:shield-alert"),
]


def publish_ha_discovery(mqttc: mqtt.Client, cfg: Config) -> None:
    """Publish Home Assistant MQTT-discovery configs (retained)."""
    if not cfg.ha_discovery:
        return

    state_topic = f"{cfg.mqtt_topic}/state"
    avail_topic = f"{cfg.mqtt_topic}/availability"
    safe_id = cfg.mqtt_topic.replace("/", "_")

    device = {
        "identifiers": [f"conexpro_bms_{safe_id}"],
        "name":        cfg.ha_device_name,
        "manufacturer":"JBD / Conexpro",
        "model":       "JBD-family BMS (BLE)",
    }

    for key, name, unit, dev_cls, state_cls, icon in HA_SENSORS:
        cfg_topic = f"{cfg.ha_prefix}/sensor/{safe_id}/{key}/config"
        payload: dict[str, Any] = {
            "name":              name,
            "unique_id":         f"{safe_id}_{key}",
            "state_topic":       state_topic,
            "availability_topic":avail_topic,
            "value_template":    f"{{{{ value_json.{key} }}}}",
            "device":            device,
        }
        if unit:    payload["unit_of_measurement"] = unit
        if dev_cls: payload["device_class"]        = dev_cls
        if state_cls: payload["state_class"]       = state_cls
        if icon:    payload["icon"]                = icon
        mqttc.publish(cfg_topic, json.dumps(payload), qos=1, retain=True)

    for key, name, dev_cls, icon in HA_BINARY_SENSORS:
        cfg_topic = f"{cfg.ha_prefix}/binary_sensor/{safe_id}/{key}/config"
        payload = {
            "name":              name,
            "unique_id":         f"{safe_id}_{key}",
            "state_topic":       state_topic,
            "availability_topic":avail_topic,
            "value_template":    f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
            "device":            device,
        }
        if dev_cls: payload["device_class"] = dev_cls
        if icon:    payload["icon"]         = icon
        mqttc.publish(cfg_topic, json.dumps(payload), qos=1, retain=True)


# ──────────────────────────────────────────────────────────────────────────────
# BLE polling worker
# ──────────────────────────────────────────────────────────────────────────────
class BMSPoller:
    def __init__(self, cfg: Config, mqttc: mqtt.Client) -> None:
        self.cfg = cfg
        self.mqttc = mqttc
        self.assembler = FrameAssembler()
        self.client: Optional[BleakClient] = None
        self._frame_q: asyncio.Queue[bytes] = asyncio.Queue()
        self._stop = asyncio.Event()
        # Registers that the BMS firmware has rejected (status 0x80/0x81). After
        # the first rejection we drop them silently to keep logs clean.
        self._unsupported_regs: set[int] = set()

    def stop(self) -> None:
        self._stop.set()

    async def _discover(self) -> Optional[BLEDevice]:
        if self.cfg.bms_mac:
            logging.info("Looking for configured BMS MAC %s …", self.cfg.bms_mac)
            return await BleakScanner.find_device_by_address(self.cfg.bms_mac, timeout=15.0)

        prefix = self.cfg.bms_name_prefix
        logging.info("Scanning for BMS by name prefix %r …", prefix)
        devices = await BleakScanner.discover(timeout=10.0)
        candidates = [d for d in devices if d.name and (not prefix or d.name.startswith(prefix))]
        if not candidates:
            return None
        logging.info("Found candidates: %s", [(d.address, d.name) for d in candidates])
        return candidates[0]

    def _on_notify(self, _char: int, data: bytearray) -> None:
        for frame in self.assembler.feed(bytes(data)):
            self._frame_q.put_nowait(frame)

    async def _read_register(self, reg: int) -> Optional[bytes]:
        while not self._frame_q.empty():
            self._frame_q.get_nowait()
        if self.client is None:
            return None
        await self.client.write_gatt_char(WRITE_UUID, build_read_frame(reg), response=False)
        deadline = asyncio.get_event_loop().time() + self.cfg.read_timeout
        while True:
            timeout_left = deadline - asyncio.get_event_loop().time()
            if timeout_left <= 0:
                logging.warning("Timeout waiting for register 0x%02X", reg)
                return None
            try:
                frame = await asyncio.wait_for(self._frame_q.get(), timeout=timeout_left)
            except asyncio.TimeoutError:
                logging.warning("Timeout waiting for register 0x%02X", reg)
                return None
            if not verify_frame(frame):
                logging.warning("Bad frame for 0x%02X: %s", reg, frame.hex())
                continue
            if frame[1] != reg:
                logging.debug("Got reg 0x%02X while expecting 0x%02X", frame[1], reg)
                continue
            status = frame[2]
            if status != 0:
                if reg not in self._unsupported_regs:
                    logging.info("Register 0x%02X (%s) returned status 0x%02X — firmware doesn't support it; will skip silently from now on",
                                 reg, REGISTER_NAMES.get(reg, "?"), status)
                    self._unsupported_regs.add(reg)
                return None
            payload_len = frame[3]
            return frame[4:4 + payload_len]

    async def _read_all(self) -> Optional[dict[str, Any]]:
        merged: dict[str, Any] = {"ts": int(time.time())}
        for reg in self.cfg.registers:
            if reg in self._unsupported_regs:
                continue
            if reg not in REG_PARSERS:
                logging.warning("No parser for register 0x%02X, skipping", reg)
                continue
            name, parser = REG_PARSERS[reg]
            payload = await self._read_register(reg)
            if payload is None:
                continue
            try:
                merged.update(parser(payload))
            except Exception as e:
                logging.warning("Parse error for 0x%02X (%s): %s", reg, name, e)
                logging.debug("Payload: %s", payload.hex())
        return merged if len(merged) > 1 else None

    async def _publish_state(self, doc: dict[str, Any]) -> None:
        topic = f"{self.cfg.mqtt_topic}/state"
        payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        logging.debug("Publish %s = %s", topic, payload)
        info = self.mqttc.publish(topic, payload, qos=self.cfg.mqtt_qos, retain=self.cfg.mqtt_retain)
        info.wait_for_publish(timeout=5)

    async def _publish_availability(self, status: str) -> None:
        topic = f"{self.cfg.mqtt_topic}/availability"
        try:
            self.mqttc.publish(topic, status, qos=1, retain=True).wait_for_publish(timeout=5)
        except Exception:
            pass

    def _on_disconnect(self, _client: BleakClient) -> None:
        logging.info("Disconnected from BMS")

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            device = await self._discover()
            if device is None:
                logging.warning("No BMS found, retrying in %ss", self.cfg.conn_retry)
                await asyncio.sleep(self.cfg.conn_retry)
                continue

            logging.info("Connecting to %s (%s) …", device.name, device.address)
            try:
                async with BleakClient(device, disconnected_callback=self._on_disconnect) as client:
                    self.client = client
                    self.assembler = FrameAssembler()
                    while not self._frame_q.empty():
                        self._frame_q.get_nowait()
                    await client.start_notify(NOTIFY_UUID, self._on_notify)
                    logging.info("Connected. Notifications open. Polling every %ss.", self.cfg.poll_interval)
                    await self._publish_availability("online")

                    while client.is_connected and not self._stop.is_set():
                        doc = await self._read_all()
                        if doc:
                            doc["mac"] = device.address
                            doc["name"] = device.name
                            await self._publish_state(doc)
                        await asyncio.sleep(self.cfg.poll_interval)
            except BleakError as e:
                logging.warning("BLE error: %s", e)
            except Exception as e:
                logging.exception("Unexpected error in BLE loop: %s", e)
            finally:
                self.client = None
                await self._publish_availability("offline")

            if not self._stop.is_set():
                logging.info("Reconnecting in %ss …", self.cfg.conn_retry)
                await asyncio.sleep(self.cfg.conn_retry)


def make_mqtt_client(cfg: Config) -> mqtt.Client:
    client_id = f"conexpro-bms-bridge-{os.getpid()}"
    try:
        c = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):
        c = mqtt.Client(client_id=client_id)
    if cfg.mqtt_username:
        c.username_pw_set(cfg.mqtt_username, cfg.mqtt_password or "")
    c.will_set(f"{cfg.mqtt_topic}/availability", "offline", qos=1, retain=True)
    c.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
    c.loop_start()
    return c


async def main_async() -> None:
    cfg = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("Conexpro BMS bridge starting. State topic=%s/state, registers=%s",
                 cfg.mqtt_topic, [f"0x{r:02X}" for r in cfg.registers])

    mqttc = make_mqtt_client(cfg)
    publish_ha_discovery(mqttc, cfg)
    poller = BMSPoller(cfg, mqttc)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, poller.stop)

    try:
        await poller.run_forever()
    finally:
        mqttc.loop_stop()
        mqttc.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        sys.exit(0)
