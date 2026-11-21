# CODE27 API-Erweiterung — Gesamtstatus (Phase 9)

Stand: 2026-09-24 (aktualisiert nach Stabilitäts-Auditdurchlauf, WP-B bis WP-K). Konsolidierte Übersicht über die im API-Inventar
(`../audit-native-app-20260912/API-Inventar-native-app.md`) geplante und in den
Paketen 1–4 umgesetzte Arbeit. Ersetzt nicht die einzelnen Paket-Dokumente (dort
steht die Begründung, hier der aktuelle Gesamtstand) - Details, Testfälle und
Nachträge stehen in den Paket-Dokumenten unter `../audit-native-app-20260912/`.
Dieses Audit-Verzeichnis ist bewusst nicht Teil des Repositories (per
`.gitignore` ausgeschlossen), die Pfade sind daher als Ablageorte genannt, nicht
als Links:

- `PAKET-1-UMSETZUNG.md` — Grundlagen & Korrekturen (Phasen 1–3)
- `PAKET-2-UMSETZUNG.md` + `PAKET-2-CARD.md` — Charaktere & Kommunikation (Phasen 4–6)
- `PAKET-3-UMSETZUNG.md` — Musik & RFID (Phase 7)
- `PAKET-4-PHASE-8.md` — IP-Recovery (Phase 8)
- [CAPABILITIES.md](CAPABILITIES.md) — Geräte-Feature-Flags
- [RFID-BLUEPRINT.md](RFID-BLUEPRINT.md) — RFID/NFC-Automation

## Phasenstatus

| Phase | Inhalt | Status |
|---|---|---|
| 1 | Audit-Abgleich mit dem Code | ✅ erledigt |
| 2 | API-Grundlagen (Multipart, Binärabruf, Gerätefehler, capabilities, music_storage/bluetooth_state) | ✅ erledigt |
| 3 | Korrektur bestehender Funktionen | ✅ erledigt |
| 4 | Charakterübersicht & Portraits | ✅ erledigt (Backend + Card) |
| 5 | Bildchat | ✅ Backend erledigt; Card-Anzeige/-Senden bewusst zurückgestellt (siehe unten) |
| 6 | Stimmen, Memory Cards, Diary | ✅ erledigt, außer dynamischer Stimmenliste (blockiert, siehe unten) |
| 7 | Musikbibliothek & RFID | ✅ erledigt |
| 8 | LAN-IP-Recovery | ✅ erledigt |
| 9 | Gesamtabnahme & Dokumentation | 🔶 dieses Dokument + Live-Test-Checkliste unten; **Live-Ausführung durch den Nutzer noch offen** |

## Konfiguration

**Einrichtung** (einmalig): Host/Port + Pairing-Code (unverändert seit vor
Paket 1 - kein nachgewiesener Fehler, also nicht angefasst, siehe
PAKET-1-UMSETZUNG.md).

**Optionen** (Einstellungen → CODE27-Gerät → Konfigurieren):

| Option | Standard | Bedeutung |
|---|---|---|
| Polling-Intervall | 15s | Haupt-Poll-Zyklus |
| Konversations-Agent für Codies Sätze / Codie-Tool-Bridge | leer (keine Tools) | Firmware ≥ 1.6.0: Codies Modell ruft `home_command`, `spell_cast`, `spell_list`. Die frühere Assist-Bridge (Codewort, Chat-Mitlesen, Bereinigung) ist entfernt. |
| **LAN-IP-Recovery über die Cloud** | **aus** | Neu in Phase 8. Nennt `codie-remote.prod.code27.co` explizit in der Beschreibung. |

## Entitäten (neu seit Paket 1, nach Plattform)

- **sensor**: Charakterübersicht, Musikspeicher belegt (%), Freigeschaltete
  Funktionen (capabilities)
- **binary_sensor**: Bluetooth verbunden
- **image**: ein Portrait pro Charakter-Slot (dynamisch, ein/aus wie die
  Alarm-Switches)
- **media_player**: Medienbrowser (`BROWSE_MEDIA`) und `play_media`
  ergänzt am bestehenden Player

## Services (17 gesamt, 10 davon neu seit Paket 1)

| Service | Neu? | Zweck |
|---|---|---|
| `create_alarm` / `update_alarm` / `delete_alarm` | nein | unverändert |
| `send_chat_message` | erweitert | + optionales `language` |
| `send_chat_image` | **neu** | Bild (+Text) an aktiven Charakter senden |
| `set_character_voice` | **neu** | dauerhafte Stimmzuweisung |
| `update_memory_card` | **neu** | Erinnerung bearbeiten (markiert dauerhaft als bearbeitet!) |
| `get_memory_cards` / `get_diary_entries` / `get_diary_entry` | **neu** | Lese-Services mit Antwortdaten, bewusst keine Sensor-Entities |
| `upload_music` / `delete_music_file` | **neu** | TF-Karte verwalten, Basis für die RFID-Automation |
| `set_music_play_mode` | **neu** | Wiedergabemodus setzen (list_loop/single_loop/shuffle/sequence) |
| `set_music_sound_effect` | **neu** | Soundeffekt/Equalizer-Preset setzen |
| `notify` / `notify_clear` / `speak` | nein | unverändert |

## Bekannte Grenzen (konsolidiert)

- **Dynamische Stimmenliste nicht abrufbar** (`ability_group_keys_path`) -
  der Audit kennt nur den Config-Key-Namen, nie den aufgelösten REST-Pfad.
  `set_character_voice` funktioniert, aber die `voice_id` muss der Nutzer
  sich anderweitig besorgen.
- **Bildchat nur im Backend**, nicht in der Card - ein sicherer
  Bildproxy für `<img>`-Tags braucht signierte URLs
  (`homeassistant.components.http.auth.async_sign_path`), die ohne echte
  HA-Instanz nicht zuverlässig zu testen waren.
- **Diary bleibt vollständig lesend** - `favorite`/`reaction`/`delete` sind
  auf der Geräte-Firmware live widerlegt (HTTP 404), absichtlich nicht
  angeboten.
- **`music_storage` hat keinen REST-Fallback**, nur WS-Push - nach einem
  Neustart der Integration leer bis zum ersten Push (typischerweise sofort
  nach WS-Connect).
- **Medienbrowser-Ordnergruppierung** geht von relativen Pfaden aus
  (`"Rockkapella/song.mp3"`) - nur an einem Beispiel bestätigt.
- **Keine Feature-Gatinglogik anhand von `capabilities`** - bewusste
  Entscheidung, siehe [CAPABILITIES.md](CAPABILITIES.md). Flags sind
  sichtbar, aber nichts prüft sie aktiv.
- **Rolling-Upload-Streaming, Cloth-Verwaltung, Turret-Steuerung, alte
  Gestenprotokolle, Änderung des Pairing-Verfahrens**: bewusst nie in
  diesen Plan aufgenommen (siehe ursprüngliche Audit-Kategorisierung) -
  fehlende Tests, kein konkreter Bedarf oder keine erreichbaren Endpunkte.

## Edge-Case-Durchsicht (Phase 9)

Code-seitig durchgesehen (kein Ersatz für einen Live-Test, siehe
Checkliste unten):

- **Neustart/Reload**: alle neuen dynamischen Entity-Gruppen (Portraits pro
  Slot) folgen exakt dem bereits produktiv laufenden Alarm-Switch-Muster
  (`switch.py`) - Abmeldung über `entry.async_on_unload(coordinator.async_add_listener(...))`,
  stabile `unique_id`s über Reloads hinweg. Kein neuer Task/Listener, der
  nicht schon über bestehende `async_on_unload`-Pfade abgemeldet wird.
- **Verbindungsabbruch**: durchgängig über `Code27ConnectionError` +
  optionale Phase-8-Recovery abgedeckt (siehe PAKET-4-PHASE-8.md).
- **Fehlende Portraits**: `get_character_portrait()` liefert `None`, wenn
  weder Custom-Thumbnail noch Stock-Portrait existieren (live bestätigter
  Fall, z.B. Slot 4 auf dem Testgerät) - die `image`-Entity zeigt dafür
  kein Bild statt eines Fehlers. Ob Home Assistants `image`-Plattform das
  als "unavailable" oder als leeres Bild darstellt, ist nicht ohne
  laufende HA-Instanz zu verifizieren.
- **Leere TF-Karte**: `Code27MusicStorageSensor` liefert `None`
  ("unbekannt") ohne Daten, `0` bei geladener aber leerer Karte: Medienbrowser
  zeigt eine leere Liste statt eines Fehlers.
- **Eingeschränkte Capabilities**: siehe [CAPABILITIES.md](CAPABILITIES.md) -
  bewusst nur sichtbar gemacht, nicht durchgesetzt.

## Testsuite

```powershell
python -B -m pytest -q -p no:cacheprovider
```

Die Testabhängigkeiten werden aus `requirements-test.txt` installiert, einschließlich
`voluptuous`, MCP und uvicorn. CI führt die vollständige automatisch gesammelte
Suite ohne Testausschlüsse aus. Home Assistant wird für die lokalen Tests durch
gezielte Adapter ersetzt; die echten Bibliotheken und der lokale MCP-HTTP-Roundtrip
werden ausgeführt. Der separate Import-/Lifecycle-Smoke testet reale HASE-Module
mit dem echten Schema-Validator, einschließlich Transportstart und -shutdown.

Der Abschluss-Audit hat die MCP-Fehler als Regressionen des Transportumbaus bestätigt,
nicht als fehlende-Dependency-Altlast. Der SDK-konforme Transport ist wiederhergestellt.
Abschlusslauf 25.09.2026: **1.286 Tests und 543 Subtests bestanden**, keine Fehler.
Details und neue Gegenproben: [Abschlusskorrekturen](CLOSING-AUDIT-2026-09-24.md).

## Live-Test-Checkliste (Phase 9 - vom Nutzer auszuführen)

Konsolidiert aus allen Paket-Dokumenten, nichts davon wurde in dieser
Session gegen das echte Gerät geprüft:

- [ ] `image.py`: Portrait-Entities zeigen tatsächlich Bilder; ein Slot ohne
      Portrait zeigt einen sinnvollen Leerzustand statt eines Fehlers.
- [ ] `send_chat_image`-Service mit einer echten Datei (Pfad-Erlaubnis,
      1MB-Limit, tatsächlicher Multipart-Request).
- [ ] `get_memory_cards`/`get_diary_entries`/`get_diary_entry` liefern über
      Entwicklerwerkzeuge → Dienst aufrufen tatsächlich Antwortdaten.
- [ ] `set_character_voice` mit einer echten `voice_id`.
- [ ] Medienbrowser zeigt die tatsächliche Ordnerstruktur sinnvoll
      (Gerät steht laut Nutzer bereits auf MEDIA-Modus).
- [ ] Ein Titel aus dem Medienbrowser ausgewählt spielt tatsächlich.
- [ ] `upload_music` mit `play: true` lädt hoch und spielt sofort;
      `delete_music_file` danach räumt korrekt auf.
- [ ] Musikspeicher- und Bluetooth-Sensoren zeigen plausible Werte.
- [ ] RFID-Blueprint mit einem echten Tag und einer echten Datei.
- [ ] Ein simulierter IP-Wechsel (z.B. DHCP-Lease kurz ändern) wird mit
      aktivierter `remote_recovery_enabled`-Option behoben.
- [ ] Zweiter Neustart nach all den neuen Entities erzeugt weiterhin keine
      doppelten Entities/Listener (letzter bestätigter Stand davon war vor
      Paket 1 - seither viele neue Entities dazugekommen).
- [ ] Card: Charakterübersicht, Portraits, Musik-Sektion ein-/ausblenden,
      Chat ein-/ausblenden, gegenseitiger Ausschluss Chat/Musik - alles
      bereits mit einem synthetischen Browser-Test geprüft (siehe
      PAKET-2-CARD.md), aber nicht in einem echten Dashboard mit echtem
      Theme/echter Fensterbreite.
