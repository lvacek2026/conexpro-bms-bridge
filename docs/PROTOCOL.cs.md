# Conexpro / JBD / Xiaoxiang BMS — reference BLE protokolu

🇬🇧 [English](PROTOCOL.md)  ·  🇨🇿 **Česky**

> Dokumentováno přes interoperability research na oficiálním Bluetooth
> rozhraní těchto BMS, ověřeno proti zachyceným rámcům z reálné jednotky.
> Conexpro / Xiaoxiang / Jiabaida / LLT Power / Overkill Solar jsou všechno
> rebrandy stejného JBD čipsetu a mluví přesně tímto protokolem.

## TL;DR

| | |
|---|---|
| **GATT služba** | `0000ff00-0000-1000-8000-00805f9b34fb` |
| **Notify (BMS → app)** | `0000ff01-0000-1000-8000-00805f9b34fb` |
| **Write (app → BMS)** | `0000ff02-0000-1000-8000-00805f9b34fb` |
| **Začátek rámce** | `0xDD` |
| **Konec rámce** | `0x77` |
| **Read mód** | `0xA5` |
| **Write mód** | `0x5A` |

```
TX read:    DD A5 [reg] 00 [csum_hi] [csum_lo] 77        (7 bajtů)
TX write:   DD 5A [reg] [N] [data 0..N-1] [csum_hi] [csum_lo] 77
RX:         DD [reg] [status] [N] [data 0..N-1] [csum_hi] [csum_lo] 77
            └─ status: 0x00 OK, 0x80/0x81 chyba
```

**Checksum:**

| Směr | Vzorec |
|------|--------|
| TX (app → BMS) | `((~(sum(data) + N + reg)) + 1) & 0xFFFF`, big-endian (hi, lo) |
| RX (BMS → app) | `((~(sum(data) + N + status)) + 1) & 0xFFFF`, big-endian (hi, lo) |

> Vzorce vypadají identicky, protože pro úspěšnou odpověď `status == 0x00` je
> na stejném byte offsetu jako `reg` v TX rámci (offset 2). Spočti checksum
> přes `byte[2] + byte[3] + payload`. Rozdíl má vliv jen když BMS vrátí
> chybu (`status == 0x80` / `0x81`) — verifikace přes register byte místo
> status byte tehdy špatně rámec odmítne.

Pro prázdný TX payload (`N=0`) se checksum redukuje na `(-reg) & 0xFFFF`.
Takže rámec „čti registr 0x03" je vždy `DD A5 03 00 FF FD 77`.

Kompletní rámec je rozdělen do více BLE notifikací, protože výchozí ATT MTU
je 23 bajtů (= 20 bajtů payloadu). Reframuj na příjmu hledáním `0xDD` a
použitím length bajtu na offsetu 3.

## Registry pro čtení

| Reg | Účel | Payload |
|-----|------|---------|
| `0x03` | Základní info — napětí, proud, SoC, FET, balance, ochrany, teploty | viz níže |
| `0x04` | Napětí článků | `N × u16 BE` v mV |
| `0x05` | Hardware verze | ASCII / GB2312 řetězec |
| `0xA0` | Jméno výrobce | GB2312 řetězec (některé firmware vrátí `0x81`) |
| `0x2E` | Detaily NTC | per-NTC raw hodnoty |
| `0xAA` | Čítače událostí ochran (kumulativní) | strukturováno |
| `0xAB` | Historie nabíjení/vybíjení | strukturováno |
| `0xF6` | Vnitřní odpor jednotlivých článků | `N × u16` |
| `0xFA` | Všechny EEPROM parametry | full settings dump |

Bridge (nebo tvůj vlastní klient) reálně potřebuje jen `0x03` + `0x04` pro
živou telemetrii. `0x05` a `0xA0` jsou nice-to-have jednorázově při connectu.

## Registr `0x03` — payload základního info

| Offset | Velikost | Pole | Měřítko / poznámky |
|---|---|---|---|
| 0 | u16 BE | celkové napětí packu | × 0.01 V |
| 2 | i16 BE | proud packu | × 0.01 A *(signed; záporný = vybíjení podle JBD konvence)* |
| 4 | i16 BE | zbývající kapacita | × 0.01 Ah *(pokud záporné, přičti 655.36)* |
| 6 | i16 BE | nominální kapacita | × 0.01 Ah *(stejný wrap)* |
| 8 | u16 BE | počet cyklů | |
| 10 | u16 BE | datum výroby | bity 15..9 = rok - 2000, bity 8..5 = měsíc, bity 4..0 = den |
| 12 | u16 BE | balance state, články 0..15 | bit per článek |
| 14 | u16 BE | balance state, články 16..31 | bit per článek |
| 16 | u16 BE | protection state | viz bitmapa níže |
| 18 | u8 | software verze | high nibble = major, low nibble = minor |
| 19 | u8 | RSOC | % |
| 20 | u8 | FET stav | bit0 = charge MOSFET on, bit1 = discharge MOSFET on |
| 21 | u8 | počet článků `N` | |
| 22 | u8 | počet NTC `M` | |
| 23 | M × u16 BE | teploty NTC | Kelvin × 10. °C = (raw − 2731) / 10 |

**Volitelný tail** (přítomen u novějších firmware — start offset = `23 + 2*M`):

| +0 | u8 | vlhkost / sentinel | pokud `0x88`, přepni měřítko proudu/kapacity z /100 na /10 |
| +1 | u16 BE | alarm word | implementačně specifické |
| +3 | u16 BE | naučená kapacita | × 0.01 Ah |
| +5 | i16 BE | balance proud | × 0.01 A |

### Bitmapa ochran (16 bitů)

Byte 17 je low byte (bity 0..7), byte 16 je high byte (bity 8..15):

| Bit | Flag |
|-----|------|
| 0   | přepětí článku |
| 1   | podpětí článku |
| 2   | přepětí packu |
| 3   | podpětí packu |
| 4   | nadměrná teplota při nabíjení |
| 5   | nízká teplota při nabíjení |
| 6   | nadměrná teplota při vybíjení |
| 7   | nízká teplota při vybíjení |
| 8   | nadproud při nabíjení |
| 9   | nadproud při vybíjení |
| 10  | zkrat |
| 11  | chyba IC |
| 12  | softwarový zámek MOSFETu |

## Registr `0x04` — napětí článků

`N × u16 BE`, každé v **milivoltech**. Počet článků je implicitní z délky
payloadu (`len / 2`) a odpovídá počtu z `0x03`.

Příklad odpovědi pro 4S při 3.32 V / 3.322 V / 3.315 V / 3.321 V:

```
0CF8 0CFA 0CF3 0CF9
```

## Zápisové příkazy (dokumentováno pro úplnost — bridge je nepoužívá)

| Reg | rwMode | Akce | Payload |
|-----|--------|------|---------|
| `0x00` | `0x5A` | Vstup do factory / EEPROM módu | `56 78` |
| `0x01` | `0x5A` | Výstup z factory módu | `00 00` pro uložení změn, `28 28` pro zahození |
| `0x06` | `0x5A` | Spárování s heslem | `[len][ASCII bajty hesla]` |
| `0x0A` | `0x5A` | MOSFET / control sub-command | první bajt = sub-cmd: `01` reset capacity, `02` clear records, `03` reboot, `04` clear protection, `05` sleep, `06` deep sleep, `07` open balance |
| `0xFB` | `0x5A` | Spínač (charge / discharge enable) | `[which][state]` — which: `0`=discharge, `1`=charge, `2`=predischarge; state: `0`=open (zapnuto), `1`=close (vypnuto) |

> **Žádný z těchto příkazů bridge neposílá.** Jsou tady jen pro úplnost
> protokolu. Zápis nastavení vyžaduje nejdřív vstup do factory módu
> (`0x00 56 78`); zapomenutí matching výstupu (`0x01 00 00` pro commit,
> `0x01 28 28` pro discard) nechá BMS v přechodném stavu. Před zkoušením
> na ostré baterii to otestuj na servisní jednotce.

## Referenční Python parser

Standalone implementace bez závislostí je v [`../jbd_protocol.py`](../jbd_protocol.py).
Staví a verifikuje rámce, skládá BLE-MTU chunks, parsuje `0x03`, `0x04`,
`0x05` a `0xA0`. Užitečné pro ad-hoc debug:

```python
from jbd_protocol import build_read_frame, FrameAssembler, parse_basic, verify_frame
print(build_read_frame(0x03).hex())  # → "dda50300fffd77"
```

## Validace

Protokol popsaný výše byl ověřen end-to-end na reálné **Conexpro 12,8 V /
150 Ah LFP** (advertised name `JBD-SP04S034-L4S-150A`, firmware 2.4) přes
hci0 BlueZ na Raspberry Pi. Všechny sémantiky polí, checksum quirk,
podporované registry i fragmentace rámců sedí s dokumentací výše.
