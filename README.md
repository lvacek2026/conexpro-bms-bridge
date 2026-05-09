# conexpro-bms-bridge

> **Conexpro Smart BMS Bluetooth → MQTT bridge** for Conexpro LiFePO4 batteries
> (and any other JBD-chipset BMS — Xiaoxiang, Jiabaida, LLT Power, Overkill
> Solar, Daly-rebrand, …). Self-hosted, no cloud, no Home Assistant required,
> no phone app needed once running.

Standalone Docker container that reads live telemetry from a **Conexpro Smart
BMS** (or any JBD-family BMS) over Bluetooth Low Energy and publishes a single
JSON document to your MQTT broker. Anyone — Node-RED, Home Assistant,
Telegraf/InfluxDB, n8n, custom code — can then subscribe and use the data.

## Compatible batteries

If your battery is sold as **"Smart BMS, Bluetooth"** by any of these vendors,
this bridge almost certainly works with it:

* **Conexpro** — LiFePO4 12 V / 24 V / 48 V series with built-in Bluetooth
  Smart BMS (the Xiaoxiang / Little Elephant Android app is what they ship to
  read it; this bridge speaks the same wire protocol)
* **JBD** (Jiabaida) — original chipset manufacturer, all SP04S / SP15S /
  SP17S / SP21S / SP24S families
* **Xiaoxiang** branded packs
* **LLT Power** BMS modules
* **Overkill Solar** BMS (US distributor of JBD)
* **Daly** rebrands marked compatible with the Xiaoxiang app
* Most "JBD-SP*", "JBD-AP*", "JBD-UP*" advertised over BLE
* Any battery whose phone app is [Xiaoxiang](https://play.google.com/store/apps/details?id=com.xiaoxiang.battery)
  or [Little Elephant / 小象](https://play.google.com/store/apps/details?id=com.jiabaida.little_elephant)

The bridge connects to GATT service `0xFF00` and uses the documented JBD
register set (0x03 basic info, 0x04 cell voltages, 0x05 hardware version,
0xA0 manufacturer name). Verified live on a **Conexpro 12.8 V / 150 Ah LFP**
(JBD-SP04S034-L4S-150A, FW 2.4) — but the protocol is identical across all
JBD-family BMS, so any of the above should work.

## How the protocol was obtained

Reverse-engineered from the
[`xiaoxiang`](https://play.google.com/store/apps/details?id=com.xiaoxiang.battery)
and
[`Little Elephant / 小象`](https://play.google.com/store/apps/details?id=com.jiabaida.little_elephant)
Android apps (decompiled with `jadx`, traced through `BluetoothUtil` and
`BMSCommandEntity`). All BMS vendor rebrands sold under those apps use the
same wire format, GATT UUIDs, and frame encoding. Full byte-by-byte spec is
in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

**Read-only.** No control commands are sent to the BMS. The protocol document
covers writable opcodes (factory mode, MOSFET switch, sleep, balance) for
completeness, but the bridge never invokes them.

## What you get

A retained MQTT message every `POLL_INTERVAL` seconds (default 30 s):

```json
{
  "ts": 1778364378,
  "voltage_v": 13.80,
  "current_a": 0.0,
  "power_w": 0.0,
  "remaining_ah": 151.56,
  "capacity_ah": 151.82,
  "soc_pct": 100,
  "cycle_count": 14,
  "production_date": "2023-07-17",
  "firmware_version": "2.4",
  "cell_count": 4,
  "ntc_count": 3,
  "temperatures_c": [20.0, 18.8, 18.7],
  "cells_v": [3.458, 3.465, 3.456, 3.425],
  "cell_min_v": 3.425, "cell_max_v": 3.465, "cell_delta_v": 0.040,
  "cell_min_idx": 3,   "cell_max_idx": 1,
  "charging_mosfet": true,
  "discharging_mosfet": true,
  "balance_states": [false, false, false, false],
  "balance_active": false,
  "protections": { "cell_over_voltage": false, /* … 13 named flags */ },
  "protection_active": false,
  "protection_word": 0,
  "hardware_name": "JBD-SP04S034-L4S-150A",
  "mac": "A4:C1:37:02:F8:CC",
  "name": "JBD-SP04S034-L4S-150A"
}
```

Plus a tiny availability topic:

| Topic                    | Payload          | Retain |
|--------------------------|------------------|--------|
| `<MQTT_TOPIC>/state`     | the JSON above   | yes    |
| `<MQTT_TOPIC>/availability` | `online` / `offline` | yes |

## Quick start

You need:

* a Linux host with **BlueZ** (a Raspberry Pi, an x86 mini-PC, anything
  modern). Docker Desktop on macOS / Windows **won't** work — Bluetooth has
  to be passed through from the host kernel.
* an MQTT broker reachable from the host (Mosquitto in another container
  works fine — the default config assumes `127.0.0.1:1883` anonymous, change
  in `.env` if yours is elsewhere).
* a JBD-family BMS in BLE range with the phone app **disconnected** (only
  one BLE central can talk to the BMS at a time).

```bash
git clone https://github.com/lvacek2026/conexpro-bms-bridge.git
cd conexpro-bms-bridge
cp .env.example .env

# (optional) discover your BMS MAC on the host
bluetoothctl scan on   # let it run ~30 s, look for "JBD-…" or your model
# edit .env → set BMS_MAC=AA:BB:CC:DD:EE:FF

docker compose up -d
docker compose logs -f
```

You should see something like:

```
INFO Connecting to JBD-SP04S034-L4S-150A (A4:C1:37:02:F8:CC) …
INFO Connected. Notifications open. Polling every 30.0s.
```

Verify on MQTT:

```bash
mosquitto_sub -h 127.0.0.1 -t 'bms/main/state' -C 1 | jq .
```

## Configuration

All config is in `.env`. The defaults work out of the box for a single JBD
BMS publishing to a local anonymous Mosquitto.

| Variable | Default | Notes |
|----------|---------|-------|
| `BMS_MAC` | *(empty)* | Pin a specific battery. Recommended once known. |
| `BMS_NAME_PREFIX` | `JBD` | Used only when `BMS_MAC` is empty. |
| `POLL_INTERVAL` | `30` | Seconds between full reads. |
| `CONN_RETRY` | `10` | Backoff after disconnect/error. |
| `READ_TIMEOUT` | `5` | Per-register response timeout. |
| `REGISTERS` | `basic,cells,hw_version` | Comma-separated. Names: `basic` (0x03), `cells` (0x04), `hw_version` (0x05), `manufacturer` (0xA0). Hex (`0x03`) also accepted. The bridge auto-disables a register after the BMS rejects it once. |
| `MQTT_HOST` | `127.0.0.1` | Broker address. |
| `MQTT_PORT` | `1883` | |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | *(empty)* | |
| `MQTT_TOPIC` | `bms/main` | State → `<topic>/state`, availability → `<topic>/availability`. |
| `MQTT_QOS` | `0` | |
| `MQTT_RETAIN` | `true` | |
| `HA_DISCOVERY` | `false` | If `true`, publishes Home Assistant MQTT-discovery configs (one device with ~17 entities) so HA auto-creates sensors. |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | HA's discovery prefix. |
| `HA_DEVICE_NAME` | `Conexpro BMS` | Shown in HA. |
| `LOG_LEVEL` | `INFO` | `DEBUG` dumps every BLE frame in hex. |

## Architecture

```
┌─────────────────────┐                    ┌──────────────┐
│ conexpro-bms-bridge │ ─── BLE GATT ────► │ JBD BMS      │
│ (Python + bleak)    │   service 0xFF00   │ (Conexpro,   │
│ network_mode: host  │                    │  Xiaoxiang,  │
│ privileged: true    │                    │  LLT, …)     │
└──────────┬──────────┘                    └──────────────┘
           │
           │ JSON every POLL_INTERVAL
           ▼
   ┌───────────────┐
   │ MQTT broker   │  topic: <MQTT_TOPIC>/state    (retained)
   │ (yours)       │         <MQTT_TOPIC>/availability  (LWT)
   └───────────────┘
```

The container runs with `network_mode: host` because BlueZ is socket-based
and exposing it through Docker's NAT is more pain than it's worth. Same
pattern as
[Theengs Gateway](https://github.com/theengs/gateway) and similar BLE bridges.

## Consuming the data

* **Node-RED** — `mqtt in` node subscribed to `<MQTT_TOPIC>/state` with
  `Output: a parsed JSON object`. There's a ready-to-import flow at
  [`examples/node-red-flow.json`](examples/node-red-flow.json) — it
  subscribes, exposes the latest snapshot in `flow.bms_latest`, and shows a
  one-line node status with live SoC / V / A.
* **Home Assistant** — set `HA_DISCOVERY=true` and HA will create a "Conexpro
  BMS" device with voltage / current / SoC / per-cell stats / MOSFET / fault
  binary sensors. No YAML required.
* **InfluxDB / Grafana** — point Telegraf's `[[inputs.mqtt_consumer]]` at
  `<MQTT_TOPIC>/state` with JSON parsing; every numeric field becomes a
  measurement.
* **Anything else** — the topic is plain JSON. Subscribe with `mosquitto_sub`,
  Python `paho-mqtt`, MQTT.js, your call.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `No BMS found, retrying …` (forever) | Phone app still connected to the BMS, or BMS is sleeping | Close the app; for a sleeping BMS, briefly load it (turn on a light, plug in a charger) so it advertises |
| `Connected: yes` in `bluetoothctl info` but bridge can't connect | Stale GATT session held by BlueZ from a previous run | `bluetoothctl disconnect <MAC>` once, bridge will re-establish |
| `Bad frame for 0xNN: …` repeatedly | Cell delta on the BMS firmware that doesn't follow JBD docs to the letter | Open an issue with the hex dump; might need a per-firmware quirk |
| Container starts but never publishes | MQTT broker unreachable from the host | Check `MQTT_HOST` — `127.0.0.1` only works if the broker is on the same host |
| Slot for `manufacturer` (0xA0) silently disappears | Some firmwares return error 0x81 for that register. Bridge logs it once at INFO and skips it | Expected. `hardware_name` from 0x05 has the same info. |

## Hacking on the protocol

* Full byte-by-byte spec: [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
* Frame parser is `jbd_protocol.py` in this folder. It's pure stdlib so you
  can `python3 -c 'from jbd_protocol import parse_basic; …'` to poke at
  captured frames without spinning up the bridge.

## Why not [`fl4p/batmon-ha`](https://github.com/fl4p/batmon-ha)?

`batmon-ha` is excellent and supports more BMS families, but it's
distributed as a Home Assistant add-on (its `Dockerfile` uses `ARG
BUILD_FROM` and the entrypoint reads HA supervisor config). Standalone
deployment outside HA needs custom wrapping. This bridge is purpose-built
for JBD-family BMS, ~400 lines, no HA assumption. If you run HA you're
welcome to use either; if you don't, this one is simpler.

## Contributing

Bug reports and PRs welcome. Especially helpful:

* Hex dumps of frames from BMS firmware variants where parsing fails
* Confirmation of new vendor rebrands (open an issue: "works with my
  XYZ battery, advertised name `XYZ-…`, MAC pattern `…`")
* Translations of the README into other languages

## License

[MIT](LICENSE) — do whatever you want with it. No warranty; this talks to
high-current battery hardware over an unauthenticated wireless protocol, use
your judgement.

## See also

* [JBD BMS Protocol Documentation (community)](https://gitlab.com/bms-tools/bms-tools/-/blob/master/JBD_REGISTER_MAP.md)
* [`fl4p/batmon-ha`](https://github.com/fl4p/batmon-ha) — Home Assistant add-on, supports more BMS families
* [Theengs Gateway](https://github.com/theengs/gateway) — BLE-advertisement decoder (different protocol; complementary)
* [`victron-ble`](https://github.com/keshavdv/victron-ble) — same idea for Victron MPPT/Smart Battery Sense/Orion BLE devices

## Keywords

Conexpro Smart BMS, Conexpro Bluetooth, Conexpro LiFePO4, Conexpro 12V 24V
48V, JBD BMS MQTT, Xiaoxiang BMS Linux, Xiaoxiang Raspberry Pi, JBD BLE
bridge, Smart BMS Bluetooth Docker, JBD-SP04S, JBD-SP15S, JBD-SP17S,
JBD-SP21S, LiFePO4 monitor MQTT, Home Assistant Conexpro, Node-RED Smart BMS,
LFP battery telemetry, BMS reverse engineering, BLE GATT 0xFF00 0xFF01
0xFF02, JBD frame DD A5, jadx Xiaoxiang, Little Elephant 小象 BMS, IoT camper
LiFePO4, RV battery monitor, motorhome BMS, off-grid solar BMS.
