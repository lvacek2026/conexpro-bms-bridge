# conexpro-bms-bridge

🇬🇧 [English](README.md)  ·  🇨🇿 **Česky**

> **Conexpro Smart BMS Bluetooth → MQTT bridge** pro LiFePO4 baterie Conexpro
> (a jakýkoli další BMS s čipsetem JBD — Xiaoxiang, Jiabaida, LLT Power,
> Overkill Solar, Daly-rebrand, …). Self-hosted, žádný cloud, není potřeba
> Home Assistant ani mobilní aplikace.

Samostatný Docker kontejner, který přes Bluetooth Low Energy čte živá data
z **Conexpro Smart BMS** (nebo jakéhokoli BMS z rodiny JBD) a publikuje je
jako jeden JSON dokument na MQTT broker. Cokoliv potom — Node-RED, Home
Assistant, Telegraf/InfluxDB, n8n, vlastní skript — si data odebere a udělá
si s nimi co chce.

## Kompatibilní baterie

Pokud je tvoje baterie prodávaná jako **„Smart BMS, Bluetooth"** od kteréhokoli
z těchto výrobců, bridge skoro jistě funguje:

* **Conexpro** — LiFePO4 12 V / 24 V / 48 V s vestavěným Bluetooth Smart BMS
  (bridge mluví stejným Bluetooth protokolem, jaký používá oficiální
  výrobcová mobilní aplikace)
* **JBD** (Jiabaida) — původní výrobce čipsetu, všechny řady SP04S / SP15S
  / SP17S / SP21S / SP24S
* **Xiaoxiang**-brandované packy
* **LLT Power** BMS moduly
* **Overkill Solar** BMS (US distributor JBD)
* **Daly** rebrandy hardware z rodiny JBD
* Většina „JBD-SP*", „JBD-AP*", „JBD-UP*" zařízení advertisovaných přes BLE
* Jakýkoli BMS, který přichází s oficiální Bluetooth mobilní aplikací od
  výrobce a vystavuje GATT službu `0xFF00`

Pokud při skenu `bluetoothctl` vidíš v advertisementu UUID služby
`0000ff00-…`, bridge si s tím nejspíš popovídá.

Bridge se připojuje na GATT službu `0xFF00` a používá dokumentovanou JBD sadu
registrů (0x03 základní info, 0x04 napětí článků, 0x05 hardware version, 0xA0
jméno výrobce). Odzkoušeno na **Conexpro 12,8 V / 150 Ah LFP**
(JBD-SP04S034-L4S-150A, FW 2.4) — ale protokol je u celé rodiny JBD identický,
takže by mělo fungovat cokoli z výše uvedeného.

## Jak jsme protokol získali

Dokumentováno přes interoperability research na oficiálním Bluetooth rozhraní
těchto BMS, ověřeno proti zachyceným rámcům z reálné jednotky. Kompletní
byte-by-byte specifikace je v [`docs/PROTOCOL.cs.md`](docs/PROTOCOL.cs.md).

**Pouze ke čtení.** Bridge žádné řídící příkazy nevysílá. Protokolová
dokumentace pro úplnost popisuje i zapisovací opcodes (factory mód, MOSFET
spínač, sleep, balance), ale bridge je nikdy nevolá.

## Co dostaneš

Retained MQTT zpráva každých `POLL_INTERVAL` sekund (výchozí 30 s):

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
  "protections": { "cell_over_voltage": false, /* … 13 pojmenovaných flagů */ },
  "protection_active": false,
  "protection_word": 0,
  "hardware_name": "JBD-SP04S034-L4S-150A",
  "mac": "A4:C1:37:02:F8:CC",
  "name": "JBD-SP04S034-L4S-150A"
}
```

A drobná availability topic:

| Topic                       | Payload          | Retain |
|-----------------------------|------------------|--------|
| `<MQTT_TOPIC>/state`        | JSON výše        | ano    |
| `<MQTT_TOPIC>/availability` | `online` / `offline` | ano |

## Rychlý start

Co potřebuješ:

* Linux host s **BlueZ** (Raspberry Pi, x86 mini-PC, cokoliv rozumně
  novějšího). Docker Desktop na macOS / Windows **nebude fungovat** —
  Bluetooth se musí předat přímo z hostitelského kernelu.
* MQTT broker dosažitelný z hosta (Mosquitto v dalším kontejneru je v pohodě
  — výchozí konfig předpokládá `127.0.0.1:1883` anonymous, jinak změň v
  `.env`).
* JBD-rodinu BMS v dosahu BLE s **odpojenou** mobilní aplikací (vždy se může
  připojit jen jeden BLE klient).

```bash
git clone https://github.com/lvacek2026/conexpro-bms-bridge.git
cd conexpro-bms-bridge
cp .env.example .env

# (volitelné) najdi si MAC adresu BMS na hostiteli
bluetoothctl scan on   # nech běžet ~30 s, hledej "JBD-…" nebo svůj model
# uprav .env → nastav BMS_MAC=AA:BB:CC:DD:EE:FF

docker compose up -d
docker compose logs -f
```

V logu by se mělo objevit:

```
INFO Connecting to JBD-SP04S034-L4S-150A (A4:C1:37:02:F8:CC) …
INFO Connected. Notifications open. Polling every 30.0s.
```

Ověření na MQTT:

```bash
mosquitto_sub -h 127.0.0.1 -t 'bms/main/state' -C 1 | jq .
```

## Konfigurace

Všechno je v `.env`. Výchozí hodnoty fungují pro jeden JBD BMS publikující
na lokální anonymní Mosquitto.

| Proměnná | Výchozí | Poznámka |
|----------|---------|----------|
| `BMS_MAC` | *(prázdné)* | Pinuje konkrétní baterii. Doporučeno jakmile MAC znáš. |
| `BMS_NAME_PREFIX` | `JBD` | Použito jen když je `BMS_MAC` prázdné. |
| `POLL_INTERVAL` | `30` | Sekundy mezi čteními. |
| `CONN_RETRY` | `10` | Backoff po disconnect/error. |
| `READ_TIMEOUT` | `5` | Timeout odpovědi pro jeden registr. |
| `REGISTERS` | `basic,cells,hw_version` | Comma-separated. Jména: `basic` (0x03), `cells` (0x04), `hw_version` (0x05), `manufacturer` (0xA0). Hex (`0x03`) také funguje. Bridge si registr automaticky vyřadí, pokud ho BMS odmítne. |
| `MQTT_HOST` | `127.0.0.1` | Adresa brokeru. |
| `MQTT_PORT` | `1883` | |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | *(prázdné)* | |
| `MQTT_TOPIC` | `bms/main` | State → `<topic>/state`, availability → `<topic>/availability`. |
| `MQTT_QOS` | `0` | |
| `MQTT_RETAIN` | `true` | |
| `HA_DISCOVERY` | `false` | Když `true`, publikuje Home Assistant MQTT-discovery configy (jedno zařízení s ~17 entitami) a HA si automaticky vytvoří senzory. |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | HA discovery prefix. |
| `HA_DEVICE_NAME` | `Conexpro BMS` | Zobrazí se v HA. |
| `LOG_LEVEL` | `INFO` | `DEBUG` vypisuje každý BLE rámec v hex. |

## Architektura

```
┌─────────────────────┐                    ┌──────────────┐
│ conexpro-bms-bridge │ ─── BLE GATT ────► │ JBD BMS      │
│ (Python + bleak)    │   služba 0xFF00    │ (Conexpro,   │
│ network_mode: host  │                    │  Xiaoxiang,  │
│ privileged: true    │                    │  LLT, …)     │
└──────────┬──────────┘                    └──────────────┘
           │
           │ JSON každých POLL_INTERVAL
           ▼
   ┌───────────────┐
   │ MQTT broker   │  topic: <MQTT_TOPIC>/state         (retained)
   │ (tvůj)        │         <MQTT_TOPIC>/availability  (LWT)
   └───────────────┘
```

Kontejner běží s `network_mode: host`, protože BlueZ je socket-based a
prostrkávat ho přes Docker NAT je víc bolesti než užitku. Stejný pattern jako
[Theengs Gateway](https://github.com/theengs/gateway) a podobné BLE bridge.

## Konzumace dat

* **Node-RED** — `mqtt in` node odebírající `<MQTT_TOPIC>/state` s
  `Output: a parsed JSON object`. Přiložený flow je v
  [`examples/node-red-flow.json`](examples/node-red-flow.json) — odebírá
  data, vystavuje poslední snapshot v `flow.bms_latest`, **zapisuje
  decimovanou time series do InfluxDB 1.x** (viz schéma níže) a na
  node-statusu ukazuje živé SoC / V / A.
* **Home Assistant** — nastav `HA_DISCOVERY=true` a HA si vytvoří zařízení
  „Conexpro BMS" s entitami pro napětí / proud / SoC / per-cell / MOSFET /
  faulty. Žádné YAML.
* **InfluxDB + Grafana** — Node-RED flow výše zapisuje do InfluxDB
  s per-metric decimací (viz „InfluxDB schéma" níže). Hotový dashboard je
  [`examples/grafana-dashboard.json`](examples/grafana-dashboard.json) —
  drop-in import (Grafana → Dashboards → New → Import → Upload JSON, pak
  vyber svůj InfluxDB datasource).
* **Telegraf přímo** — alternativně namiř Telegraf
  `[[inputs.mqtt_consumer]]` na `<MQTT_TOPIC>/state` s parsováním JSON;
  každé číselné pole se stane measurementem (ale bez decimace).
* **Cokoliv jiného** — topic je čistý JSON. Odběr přes `mosquitto_sub`,
  Pythonem `paho-mqtt`, MQTT.js, jak chceš.

### InfluxDB schéma (zapisuje přiložený Node-RED flow)

Funkce `decimate → influx writes` ve flow rozdělí jeden BMS dokument
(přicházející každých `POLL_INTERVAL`, default 30 s) do více zápisů
s různou frekvencí, abys nehromadil gigabajty redundantních samplů
`cycle_count` a přitom zachytil každý charge/discharge transient.

| Měření | Frekvence | Tagy | Pole |
|--------|-----------|------|------|
| `bms_live`  | každý tick (~30 s) | `device` | `voltage_v`, `current_a`, `power_w`, `soc_pct`, `remaining_ah`, `charging_mosfet`, `discharging_mosfet`, `balance_active`, `protection_active` (booly jako 0/1) |
| `bms_state` | každých 5 min NEBO na změně cell-delta / protection / balance | `device` | `cell_min_v`, `cell_max_v`, `cell_delta_v`, `cell_min_idx`, `cell_max_idx` |
| `bms_cells` | každých 10 min NEBO na změně >5 mV per článek | `device`, `cell_idx` | `voltage_v` (per článek) |
| `bms_temps` | každých 10 min NEBO na změně >0.5 °C | `device`, `ntc_idx` | `temp_c` (per NTC) |
| `bms_meta`  | hodinově | `device` | `cycle_count`, `capacity_ah`, `cell_count`, `ntc_count` |

Doporučené nastavení DB (jednorázově):

```bash
docker exec influxdb influx -execute "CREATE DATABASE bms WITH DURATION 90d"
docker exec influxdb influx -execute "CREATE RETENTION POLICY \"1y_hourly\" ON \"bms\" DURATION 365d REPLICATION 1"
docker exec influxdb influx -execute "CREATE CONTINUOUS QUERY cq_bms_live_hourly ON bms BEGIN SELECT mean(voltage_v) AS voltage_v, mean(current_a) AS current_a, mean(power_w) AS power_w, mean(soc_pct) AS soc_pct, mean(remaining_ah) AS remaining_ah, max(current_a) AS current_max_a, min(current_a) AS current_min_a INTO \"1y_hourly\".bms_live_1h FROM bms_live GROUP BY time(1h), * END"
```

Tím dostaneš **90 dní plné resoluce** raw dat + **1 rok hodinových
agregátů** automaticky downsamplovaných. Na 4S BMS to je hluboko pod
100 MB / rok.

## Troubleshooting

| Symptom | Pravděpodobná příčina | Náprava |
|---------|------------------------|---------|
| `No BMS found, retrying …` (donekonečna) | Mobilní app je pořád připojená, nebo BMS spí | Zavři aplikaci; spící BMS krátce probuď zátěží (rozsvítit světlo, zapojit nabíječku) aby začala advertise |
| `Connected: yes` v `bluetoothctl info`, ale bridge se nemůže připojit | Stará GATT session držená BlueZ z minulého běhu | `bluetoothctl disconnect <MAC>` jednou, bridge si pak naváže nové spojení |
| `Bad frame for 0xNN: …` opakovaně | Některý firmware se trochu odchyluje od JBD docs | Otevři issue s hex dumpem; možná potřeba per-firmware quirk |
| Kontejner naběhne, ale nepublikuje | MQTT broker není z hosta dostupný | Zkontroluj `MQTT_HOST` — `127.0.0.1` funguje jen když je broker na stejném hostu |
| Slot pro `manufacturer` (0xA0) tiše zmizí | Některé firmware vrací error 0x81. Bridge to jednou zaloguje a víc se na to neptá | Očekávané. `hardware_name` z 0x05 obsahuje totéž. |

## Hackování protokolu

* Kompletní byte-by-byte spec: [`docs/PROTOCOL.cs.md`](docs/PROTOCOL.cs.md)
  (anglicky: [`docs/PROTOCOL.md`](docs/PROTOCOL.md))
* Parser rámců je `jbd_protocol.py` ve stejné složce. Je čistě stdlib, takže
  můžeš `python3 -c 'from jbd_protocol import parse_basic; …'` a šťourat
  v zachycených rámcích bez spouštění bridge.

## Proč ne [`fl4p/batmon-ha`](https://github.com/fl4p/batmon-ha)?

`batmon-ha` je výborný a podporuje víc rodin BMS, ale je distribuován jako
Home Assistant add-on (jeho `Dockerfile` používá `ARG BUILD_FROM` a
entrypoint čte HA supervisor config). Standalone nasazení mimo HA si žádá
vlastní wrapping. Tenhle bridge je purpose-built pro JBD-rodinu, ~400 řádků,
žádné HA assumpce. Pokud HA máš, můžeš použít kterýkoli; pokud ne, tenhle
je jednodušší.

## Příspěvky

Bug reporty a PR vítány. Zvlášť užitečné:

* Hex dumpy rámců z firmware variant kde parsing selhává
* Potvrzení nových vendor rebrandů (otevři issue: „funguje s mou XYZ
  baterií, advertised name `XYZ-…`, MAC pattern `…`")
* Překlady README do dalších jazyků

## Licence

[MIT](LICENSE) — dělej si s tím co chceš. Bez záruky; tohle mluví s
vysokoproudým baterkovým hardware přes neautentizovaný wireless protokol,
přemýšlej.

## Související projekty

* [JBD BMS Protocol Documentation (community)](https://gitlab.com/bms-tools/bms-tools/-/blob/master/JBD_REGISTER_MAP.md)
* [`fl4p/batmon-ha`](https://github.com/fl4p/batmon-ha) — Home Assistant add-on, podporuje víc rodin BMS
* [Theengs Gateway](https://github.com/theengs/gateway) — BLE-advertisement decoder (jiný protokol; doplňkový)
* [`victron-ble`](https://github.com/keshavdv/victron-ble) — totéž pro Victron MPPT/Smart Battery Sense/Orion BLE zařízení

## Klíčová slova

Conexpro Smart BMS čtení Bluetooth, Conexpro LiFePO4 monitoring, Conexpro
12V 24V 48V baterie, JBD BMS Linux, JBD BLE bridge, Smart BMS Bluetooth
Docker, JBD-SP04S, JBD-SP15S, JBD-SP17S, JBD-SP21S, LiFePO4 monitor MQTT,
Home Assistant Conexpro, Node-RED Smart BMS, LFP baterie telemetrie, BLE
GATT 0xFF00 0xFF01 0xFF02, JBD frame DD A5, IoT karavan LiFePO4, obytný vůz
baterie monitoring, off-grid solární BMS, loď baterie monitoring.
