# Geräte-Capabilities

`GET /data/user/system` liefert ein `capabilities`-Objekt mit Feature-Flags
(bestätigt in
[API-Inventar-native-app.md](../audit-native-app-20260912/API-Inventar-native-app.md#14-hardware--gerätefunktionen-kamera-mikrofon-display-sensoren-akku-bluetooth-gesten)).
Seit Paket 1 steckt es unverändert in `coordinator.data.system["capabilities"]`
und ist seit Phase 9 zusätzlich über den Diagnose-Sensor **"Freigeschaltete
Funktionen"** sichtbar (Attribut `capabilities`, State = Anzahl aktivierter
Flags).

## Wichtig: kein Feature in HASE ist aktuell an diese Flags gekoppelt

Auf dem einzigen bisher getesteten Gerät waren praktisch alle relevanten
Flags aktiv - es gab also nie einen Anlass *und* keine Möglichkeit, echtes
Gating gegenzuprüfen. Statt ungeprüfte Bedingungen einzubauen ("wenn Flag X
fehlt, verstecke Entity Y"), die an einem einzigen, vollständig
freigeschalteten Testgerät nie ausgelöst worden wären, macht HASE die Flags
nur sichtbar. Ein Gerät mit abweichenden Capabilities zeigt sich stattdessen
so:

- **Fehlt eine REST-Route komplett** (z.B. weil eine ältere Firmware ein
  Feature gar nicht kennt): der jeweilige Aufruf schlägt mit einem klaren
  Fehler fehl (i.d.R. HTTP 404 → `Code27Error("http_404")`, bei Services
  als `ServiceValidationError` sichtbar), nicht mit einem stillen Absturz.
- **Liefert die Route eine leere Liste** (z.B. keine Alarme, keine Memos):
  die jeweilige dynamische Entity-Gruppe (Alarm-Switches, Portrait-Images)
  bleibt einfach leer - kein Fehler, keine Entities, aber auch kein Crash.

## Bekannte Flags (live beobachtet, 2026-09-12) und ihr HASE-Bezug

| Flag | Beobachteter Wert | HASE-Bezug |
|---|---|---|
| `character.memory` | `true` | Memory-Card-Services (Paket 2) |
| `chat.audio_debug` | `false` | Kein HASE-Feature nutzt das |
| `lab.alarm` | `true` | Alarm-Switches (`switch.py`) |
| `lab.light_editor` | `true` | Umgebungslicht-Entity, Lichtszenen |
| `lab.memo` | `true` | Notizen-Todo-Liste |
| `lab.system` | `false` | Kein HASE-Feature nutzt das |
| `mode.lab` | `true` | Kein HASE-Feature nutzt das direkt |
| `mode.music` | `true` | Media-Player, Medienbrowser (Paket 3) |
| `voice.group` | `true` | Stimmenzuweisung (`set_character_voice`, Paket 2) |
| `admin.*` (alle) | `false` | Kein HASE-Feature nutzt das |
| `feature.face_tracking_turret` | `true` | Kein HASE-Feature (separates Hardware-Zubehör, siehe Audit Abschnitt 14) |

**Falls du ein Gerät mit abweichenden Capabilities hast** und dabei ein
Feature unerwartet fehlschlägt (statt sauber "nicht verfügbar" zu melden):
das ist ein echter Fund wert, das Verhalten zu melden - dann kann gezieltes
Gating für genau diesen Fall ergänzt werden, statt spekulativ für alle
denkbaren Kombinationen vorzusorgen.
