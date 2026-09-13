# Inventar des Ausgangsstands

Automatisch aus den vorhandenen YAML-Command-Blöcken extrahiert; ein Eintrag pro Triggerblock, Sprachvarianten zusammengefasst. Keine Laufzeit-Kompatibilitätszusage.

## Ausführungspfade

| Fundstelle | Heute | Ziel beim Umbau |
|---|---|---|
| bridge.py:652,714; :879; :1032; :1301 | Spellbook, Codewort und Korrekturen über ausführendes Assist | Alle zu typisierten Executor-Operationen; kein freier Text-Fallback |
| bridge.py:1482 | vacuum.clean_area | Executor-Vacuum-Handler, Bereich prüfen |
| bridge.py:1550 | Provider-Discovery per Service | Executor-interne kontrollierte Discovery; keine Backend-Serviceparameter |
| bridge.py:1702 | Kartenwechsel | Executor als Teil der Reinigung |
| bridge.py:1776 | Provider-Reinigung | Executor; Provider-Konfiguration vertrauenswürdig laden |
| bridge.py:1825 | light.turn_on/off, alle | Explizite erlaubte Ziel-IDs |
| bridge.py:1225 | ai_task.generate_data für Textbereinigung | Optionaler Backend-/Infrastrukturzugriff; kein fachlicher Ausführungspfad, Ergebnis weiterhin untrusted |
| bridge.py:503 | Hinweis quittieren direkt am CODE27 | Explizite lokale Frontend-Funktion; nicht als allgemeine HA-Steuerung tarnen |
| bridge.py:1327,1848; coordinator.async_send_chat_reply | Chat/TTS-Ausgabe | CODE27-Frontend, Echo-/Slot-Schutz behalten |
| __init__.py und entity-Plattformen | Bestehende CODE27-Geräteservices | Geräteintegration bleibt zuständig; nicht unaufgefordert komplett in Spellbooker verschieben |

## Migrationsgruppen

| Gruppe | Minimale Festlegung vor Freischaltung |
|---|---|
| Licht an/aus | Etappe 2: light.set_power |
| Helligkeit/Farbe/Warmweiß | Prozentbereich, Farbmodell und Fähigkeiten definieren |
| Temperaturabfrage und Status | entity.get_state zunächst eindeutig benannte Entity; Bereichstemperatur benötigt eigene Sensor-/Aggregationsregel |
| Medien | Play/Pause/Track/Volume als getrennte Operationen; mehrere Player eindeutig behandeln |
| Covers/Schlösser | Öffnen, Entriegeln und Hochfahren unterscheiden; keine Text-Heuristik im Executor |
| Geräte an/aus, Neustart, volle/minimale Leistung | Domain-/Capability-Bindung fehlt bisher; generische Freitexte nicht automatisch migrieren |
| Klima/Lüftung | Relative Schrittweite, Einheit, Gerätegrenzen und Zieldomain festlegen |
| Vacuum | Native und Providerpfade, Map-Switch und Fehlerstatus gemeinsam kapseln |
| Szenen/Alarm | Szenenbindung/Folgewirkungen, Alarmmodus und erforderliche Authentisierung explizit; zunächst unsupported |
| Anwesenheit/Ort/Aktivität/Wetter | Zielmodell und zulässige Antwortdaten definieren; Area allein reicht nicht für jede Query |
| Uhrzeit | Bereichsfreie Query separat zulassen |

## Alle vorhandenen Trigger

### middle_earth.yaml

| Zeile | Trigger (DE / EN) | Heutiger Output / Action | Pfad |
|---|---|---|---|
| 33 | de: "entzündet die leuchtfeuer von {area} / en: "light the beacons of {area} | de: "Licht {area} auf 100 Prozent stellen / en: "Set the light in {area} to 100 percent | Assist-Freitext |
| 41 | de: "licht von eärendil {area} / en: "light of eärendil {area} | de: "Licht {area} einschalten / en: "Turn on the light in {area} | Assist-Freitext |
| 49 | de: "löscht die feuer von {area} / en: "extinguish the fires of {area} | de: "Licht {area} ausschalten / en: "Turn off the light in {area} | Assist-Freitext |
| 65 | de: "löscht die leuchtfeuer von {area} / en: "extinguish the beacons of {area} | de: "Licht {area} ausschalten / en: "Turn off the light in {area} | Assist-Freitext |
| 83 | de: "entzündet die feuer von mittelerde / en: "light the fires of middle-earth | light_all_on | Direktaktion |
| 89 | de: "löscht die feuer von mittelerde / en: "extinguish the fires of middle-earth | light_all_off | Direktaktion |
| 95 | de: "die sonne geht auf über {area} / en: "the sun rises over {area} | de: "Licht {area} auf 100 Prozent stellen / en: "Set the light in {area} to 100 percent | Assist-Freitext |
| 103 | de: "dämmerung über {area} / en: "dusk over {area} | de: "Licht {area} auf 20 Prozent stellen / en: "Set the light in {area} to 20 percent | Assist-Freitext |
| 111 | de: "licht von {area} auf {value} prozent / en: "light of {area} to {value} percent | de: "Licht {area} auf {value} Prozent stellen / en: "Set the light in {area} to {value} percent | Assist-Freitext |
| 119 | de: "farben von valinor in {area} auf {color} / en: "colors of valinor in {area} to {color} | de: "Licht {area} auf {color} stellen / en: "Set the light in {area} to {color} | Assist-Freitext |
| 129 | mellon {target} | de: "{target} öffnen / en: "Open {target} | Assist-Freitext |
| 135 | de: "öffnet die tore von {target} / en: "open the gates of {target} | de: "{target} öffnen / en: "Open {target} | Assist-Freitext |
| 143 | de: "schließt die tore von {target} / en: "close the gates of {target} | de: "{target} schließen / en: "Close {target} | Assist-Freitext |
| 151 | de: "haltet die tore von {target} / en: "hold the gates of {target} | de: "{target} stoppen / en: "Stop {target} | Assist-Freitext |
| 161 | de: "öffnet die hohen tore von {target} / en: "raise the high gates of {target} | de: "{target} hochfahren / en: "Raise {target} | Assist-Freitext |
| 169 | de: "senkt die tore von {target} / en: "lower the gates of {target} | de: "{target} herunterfahren / en: "Lower {target} | Assist-Freitext |
| 179 | de: "du kommst hier nicht vorbei {area} / en: "you shall not pass {area} | de: "Alle Schlösser in {area} verriegeln / en: "Lock all locks in {area} | Assist-Freitext |
| 187 | de: "bewacht die grenzen von {area} / en: "guard the borders of {area} | de: "Alarm in {area} scharfschalten / en: "Arm the alarm in {area} | Assist-Freitext |
| 195 | de: "die gefahr ist vorüber bei {target} / en: "the danger has passed at {target} | de: "Alarm bei {target} deaktivieren / en: "Deactivate the alarm at {target} | Assist-Freitext |
| 206 | de: "lieder der halle {area} / en: "songs of the hall {area} | de: "Musik {area} fortsetzen / en: "Resume the music in {area} | Assist-Freitext |
| 214 | de: "schweigt in der halle {area} / en: "silence in the hall {area} | de: "Musik {area} pausieren / en: "Pause the music in {area} | Assist-Freitext |
| 222 | de: "lauter sollen die lieder klingen in {area} / en: "let the songs ring louder in {area} | de: "Musik {area} lauter / en: "Increase the volume for {area} | Assist-Freitext |
| 230 | de: "leiser in der halle {area} / en: "quieter in the hall {area} | de: "Musik {area} leiser / en: "Decrease the volume for {area} | Assist-Freitext |
| 238 | de: "das nächste lied in {area} / en: "the next song in {area} | de: "nächster Titel im {area} / en: "Next track in {area} | Assist-Freitext |
| 246 | de: "das vorige lied in {area} / en: "the previous song in {area} | de: "vorheriger Titel im {area} / en: "Previous track in {area} | Assist-Freitext |
| 256 | de: "feuer von anor {area} / en: "flame of anor {area} | de: "Temperatur in {area} erhöhen / en: "Increase the temperature in {area} | Assist-Freitext |
| 264 | de: "atem des nordens {area} / en: "breath of the north {area} | de: "Temperatur in {area} verringern / en: "Decrease the temperature in {area} | Assist-Freitext |
| 272 | de: "wind über {area} / en: "wind over {area} | de: "Lüftung in {area} einschalten / en: "Turn on the ventilation in {area} | Assist-Freitext |
| 280 | de: "wie warm sind die hallen von {area} / en: "how warm are the halls of {area} | de: "Wie warm ist es in {area} / en: "What's the temperature in {area} | Assist-Freitext |
| 292 | de: "reinigt die hallen von {area} / en: "clean the halls of {area} | vacuum_clean | Direktaktion |
| 301 | de: "wer wandert durch {area} / en: "who walks through {area} | de: "Ist jemand im Bereich {area} / en: "Is anyone in {area} | Assist-Freitext |
| 308 | de: "palantír {area} / en: "palantír {area} | de: "Was ist in {area} aktiv / en: "What is active in {area} | Assist-Freitext |
| 315 | de: "wo ist {target} palantír / en: "where is {target} palantír | de: "Wo befindet sich {target} / en: "Where is {target} | Assist-Freitext |
| 324 | de: "blick in den palantír von {area} / en: "look into the palantír of {area} | de: "Was ist in {area} aktiv / en: "What is active in {area} | Assist-Freitext |
| 331 | de: "offenbare den zustand von {target} / en: "reveal the state of {target} | de: "Wie ist der Status von {target} / en: "What is the status of {target} | Assist-Freitext |
| 338 | de: "was tut {target} palantír / en: "what does {target} do palantír | de: "Was macht {target} gerade / en: "What is {target} currently doing | Assist-Freitext |
| 347 | de: "wie steht das wetter über {area} / en: "how is the weather over {area} | de: "Wie ist das Wetter in {area} / en: "What's the weather like in {area} | Assist-Freitext |
| 356 | de: "die reise ist zu ende {area} / en: "the journey is over {area} | de: "Alle Geräte im Bereich {area} ausschalten / en: "Turn off all devices in {area} | Assist-Freitext |
| 364 | de: "ein fest im auenland {area} / en: "a feast in the shire {area} | de: "Gemütliche Szene in {area} aktivieren / en: "Activate the cozy scene in {area} | Assist-Freitext |
| 372 | de: "die hallen von moria {area} / en: "the halls of moria {area} | de: "Licht {area} auf 20 Prozent stellen / en: "Set the light in {area} to 20 percent | Assist-Freitext |
| 380 | de: "der weiße baum von {area} / en: "the white tree of {area} | de: "Licht {area} auf warmweiß stellen / en: "Set the light in {area} to warm white | Assist-Freitext |

### potter_verse.yaml

| Zeile | Trigger (DE / EN) | Heutiger Output / Action | Pfad |
|---|---|---|---|
| 71 | lumos {area} | de: "Licht {area} einschalten / en: "Turn on the light in {area} | Assist-Freitext |
| 77 | nox {area} | de: "Licht {area} ausschalten / en: "Turn off the light in {area} | Assist-Freitext |
| 83 | lumos maxima {area} | de: "Licht {area} auf 100 Prozent stellen / en: "Set the light in {area} to 100 percent | Assist-Freitext |
| 89 | lumos minima {area} | de: "Licht {area} auf 20 Prozent stellen / en: "Set the light in {area} to 20 percent | Assist-Freitext |
| 105 | lumos maxima | light_all_on | Direktaktion |
| 109 | nox maxima | light_all_off | Direktaktion |
| 115 | de: "lumos {area} auf {value} prozent / en: "lumos {area} to {value} percent | de: "Licht {area} auf {value} Prozent stellen / en: "Set the light in {area} to {value} percent | Assist-Freitext |
| 123 | lumos solem {area} | de: "Licht {area} auf warmweiß stellen / en: "Set the light in {area} to warm white | Assist-Freitext |
| 132 | de: "colovaria {area} auf {color} / en: "colovaria {area} to {color} | de: "Licht {area} auf {color} stellen / en: "Set the light in {area} to {color} | Assist-Freitext |
| 142 | accio {target} | de: "{target} einschalten / en: "Turn on {target} | Assist-Freitext |
| 148 | expelliarmus {target} | de: "{target} ausschalten / en: "Turn off {target} | Assist-Freitext |
| 154 | reparo {target} | de: "{target} neu starten / en: "Restart {target} | Assist-Freitext |
| 160 | engorgio {target} | de: "{target} auf volle Leistung stellen / en: "Set {target} to full power | Assist-Freitext |
| 166 | reducio {target} | de: "{target} auf minimale Leistung stellen / en: "Set {target} to minimum power | Assist-Freitext |
| 172 | aguamenti {target} | de: "{target} einschalten / en: "Turn on {target} | Assist-Freitext |
| 180 | alohomora {target} | de: "{target} öffnen / en: "Open {target} | Assist-Freitext |
| 186 | colloportus {target} | de: "{target} schließen / en: "Close {target} | Assist-Freitext |
| 192 | immobulus {target} | de: "{target} stoppen / en: "Stop {target} | Assist-Freitext |
| 200 | wingardium leviosa {target} | de: "{target} hochfahren / en: "Raise {target} | Assist-Freitext |
| 206 | descendo {target} | de: "{target} herunterfahren / en: "Lower {target} | Assist-Freitext |
| 225 | sonorus {area} | de: "Musik {area} lauter / en: "Increase the volume for {area} | Assist-Freitext |
| 231 | quietus {area} | de: "Musik {area} leiser / en: "Decrease the volume for {area} | Assist-Freitext |
| 237 | muffliato {area} | de: "Musik {area} pausieren / en: "Pause the music in {area} | Assist-Freitext |
| 243 | renervate {area} | de: "Musik {area} fortsetzen / en: "Resume the music in {area} | Assist-Freitext |
| 249 | diffindo {area} | de: "nächster Titel im {area} / en: "Next track in {area} | Assist-Freitext |
| 255 | reparifarge {area} | de: "vorheriger Titel im {area} / en: "Previous track in {area} | Assist-Freitext |
| 263 | scourgify {target} | de: "{target} starten / en: "Start {target} | Assist-Freitext |
| 269 | tergeo {target} | de: "{target} anhalten / en: "Stop {target} | Assist-Freitext |
| 277 | incendio {area} | de: "Temperatur in {area} erhöhen / en: "Increase the temperature in {area} | Assist-Freitext |
| 283 | glacius {area} | de: "Temperatur in {area} verringern / en: "Decrease the temperature in {area} | Assist-Freitext |
| 289 | anapneo {area} | de: "Lüftung in {area} einschalten / en: "Turn on the ventilation in {area} | Assist-Freitext |
| 301 | aparecium {area} | de: "Wie warm ist es in {area} / en: "What's the temperature in {area} | Assist-Freitext |
| 308 | finite incantatem {area} | de: "Alle Geräte im Bereich {area} ausschalten / en: "Turn off all devices in {area} | Assist-Freitext |
| 314 | priori incantatem {area} | de: "Vorherige Szene in {area} aktivieren / en: "Activate the previous scene in {area} | Assist-Freitext |
| 320 | expecto patronum {area} | de: "Gemütliche Szene in {area} aktivieren / en: "Activate the cozy scene in {area} | Assist-Freitext |
| 328 | protego {area} | de: "Alarm in {area} scharfschalten / en: "Arm the alarm in {area} | Assist-Freitext |
| 334 | protego totalum {area} | de: "Alle Schlösser in {area} verriegeln / en: "Lock all locks in {area} | Assist-Freitext |
| 340 | finite {target} | de: "Alarm bei {target} deaktivieren / en: "Deactivate the alarm at {target} | Assist-Freitext |
| 361 | homenum revelio {area} | de: "Ist jemand im Bereich {area} / en: "Is anyone in {area} | Assist-Freitext |
| 366 | point me {target} | de: "Wo befindet sich {target} / en: "Where is {target} | Assist-Freitext |
| 373 | specialis revelio {target} | de: "Wie ist der Status von {target} / en: "What is the status of {target} | Assist-Freitext |
| 378 | revelio {area} | de: "Was ist in {area} aktiv / en: "What is active in {area} | Assist-Freitext |
| 383 | legilimens {target} | de: "Was macht {target} gerade / en: "What is {target} currently doing | Assist-Freitext |
| 390 | meteolojinx recanto {area} | de: "Wie ist das Wetter in {area} / en: "What's the weather like in {area} | Assist-Freitext |
| 395 | tempus | de: "Wie spät ist es / en: "What time is it | Assist-Freitext |

### star_trek.yaml

| Zeile | Trigger (DE / EN) | Heutiger Output / Action | Pfad |
|---|---|---|---|
| 12 | computer licht {area} an | Licht {area} einschalten | Assist-Freitext |
| 16 | computer licht {area} aus | Licht {area} ausschalten | Assist-Freitext |
| 23 | computer alle lichter an | light_all_on | Direktaktion |
| 27 | computer alle lichter aus | light_all_off | Direktaktion |
| 31 | computer temperatur {area} auf {value} grad | Temperatur in {area} auf {value} Grad stellen | Assist-Freitext |

## Umfang

**Update 2026-09-10:** `star_trek.yaml` wurde als ungenutzt entfernt - die Zeile unten bleibt als historischer Datensatz des Ausgangsstands stehen, ist aber im aktuellen Working Tree nicht mehr vorhanden. Aktuelle Summe: 86 Einträge, 5 Direktaktionen, 81 Assist-Freitext (`test_real_production_spellbooks_match_the_etappe_1_inventory` prüft das gegen die real vorhandenen Dateien). Außerdem existieren seit demselben Datum `spellbooker_books/middle_earth.yaml` (8 Befehle) und `spellbooker_books/potter_verse.yaml` (6 Befehle) - die Teilmenge der jeweiligen Direktaktionen plus reines Licht-An/Aus und Titel-Vor/Zurück, alles was der heutige typisierte Katalog trägt. Der Rest (Rollläden, Schlösser, Alarm, Klima, Szenen, Helligkeit/Farbe, sämtliche Assist-Abfragen) bleibt ausschließlich im Legacy-Pfad, bis der Katalog erweitert wird.

| Datei | Einträge | Direktaktion | Assist-Freitext |
|---|---:|---:|---:|
| middle_earth.yaml | 41 | 3 | 38 |
| potter_verse.yaml | 45 | 2 | 43 |
| star_trek.yaml (entfernt 2026-09-10) | 5 | 2 | 3 |
