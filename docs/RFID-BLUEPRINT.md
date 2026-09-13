# RFID/NFC-Tag → Audiodatei abspielen (Toniebox-artig)

Blueprint: [`blueprints/automation/hase/rfid_play_music.yaml`](../blueprints/automation/hase/rfid_play_music.yaml)

Idee (aus dem API-Audit bestätigt, siehe
[API-Inventar-native-app.md](../audit-native-app-20260912/API-Inventar-native-app.md#kategorisierung),
Abschnitt "Toniebox-artige RFID-Integration"): ein RFID/NFC-Tag wird
gescannt → eine feste Audiodatei wird auf das CODE27-Gerät hochgeladen und
sofort abgespielt. Braucht **keine** neuen CODE27-Endpunkte, nur die
bestehenden `hase.upload_music`/`hase.delete_music_file`-Services (Paket 3)
und Home Assistants eigenes Tag-System.

## Warum Home Assistants Tag-System statt einer bestimmten RFID-Marke?

Das Blueprint setzt bewusst nicht auf ein bestimmtes RFID/NFC-Lesegerät auf,
sondern auf den `tag`-Trigger, den Home Assistant selbst mitbringt
(Einstellungen → Tags). Jedes Lesegerät, das bereits den Dienst `tag.scan`
aufruft, funktioniert damit - ESPHome-basierte Leser, die Companion-App
eines Smartphones (NFC), viele fertige Zigbee/Z-Wave-Tag-Lösungen. Damit
bleibt das Blueprint unabhängig davon, welche konkrete Hardware du nutzt.

## Einrichtung

1. **Tag registrieren**: Einstellungen → Tags → Tag hinzufügen, dann einmal
   mit deinem Lesegerät scannen, damit Home Assistant die ID kennt.
2. **Audiodatei bereitlegen**: eine Datei auf dem Home-Assistant-Host, z.B.
   unter `/config/www/hase/tag_dino.mp3`. Der Ordner muss unter
   `homeassistant: allowlist_external_dirs:` in der `configuration.yaml`
   erlaubt sein - Home Assistant liest sonst aus Sicherheitsgründen keine
   beliebigen Pfade aus einer Automation heraus.
3. **Blueprint importieren**: Einstellungen → Automatisierungen → Blueprints
   → Blueprint importieren, die Rohdatei-URL des obigen Pfads einfügen (oder
   die Datei direkt nach `<config>/blueprints/automation/hase/` kopieren,
   falls dieses Repository nicht bereits dein Home-Assistant-Konfigurations-
   verzeichnis selbst ist).
4. **Eine Automation pro Tag anlegen**: Automatisierung aus dem Blueprint
   erstellen, CODE27-Gerät, Tag und Audiodatei auswählen. Für mehrere Tags
   (mehrere Figuren/Karten) das Blueprint mehrfach mit jeweils anderem Tag
   und anderer Datei instanziieren.

## Voraussetzungen und bekannte Grenzen

- **Musikmodus muss auf dem Gerät selbst offen sein** - sonst schlägt das
  Abspielen mit einer klaren `MODE_INACTIVE`-Fehlermeldung fehl (im
  Automatisierungs-Log sichtbar), nicht mit einem stillen Nichtstun.
- **Jeder Scan lädt die Datei neu hoch** (kein Cache) - unkritisch für
  kleine Dateien, aber bei sehr häufigem Scannen derselben Karte unnötiger
  Datenverkehr. Eine Cache-Variante (nur hochladen, wenn noch nicht
  vorhanden) ist bewusst nicht Teil dieses einfachen Blueprints - der
  Audit-Fund, dass Löschen der gerade laufenden Datei die Wiedergabe nicht
  unterbricht, macht ein "Rolling Upload"-Muster möglich, ist hier aber
  nicht umgesetzt, um das Blueprint einfach und nachvollziehbar zu halten.
- **Kein automatisches Aufräumen**: hochgeladene Dateien bleiben auf der
  TF-Karte liegen. Bei Bedarf manuell (oder in einer eigenen Automation) mit
  `hase.delete_music_file` entfernen.
