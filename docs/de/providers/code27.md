🇩🇪 Deutsch | 🇬🇧 [English](../../en/providers/code27.md)

# CODE27-Provider

CODE27 ist ein optionaler Companion-Provider, nicht die Identität von HASE. Eigenständige HASE-Agenten, Spellbooks, Studio und MCP brauchen keine CODE27-Hardware.

Der eingebettete Provider paired Home Assistant mit einem lokalen CODE27-Gerät und nutzt dessen REST-/WebSocket-API. Er stellt Entitäten für Musik, Umgebungslicht, Charakterwahl, Alarme, Notizen, Chat/Status und Diagnosen sowie `hase.*`-Services, Chat-Events und die optionalen Cards `custom:code27-card` und `custom:haseamp-card` bereit. Exakte Service-Schemas stehen in `custom_components/hase/services.yaml`.

## Gerät einrichten und Spellbook nutzen

Es gibt zwei Wege für ein physisches Gerät:

1. **Eingebettet in HASE (`domain=hase`):** Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → HASE → CODE27-Gerät verbinden** die lokale IP und den Port (gewöhnlich `8081`) eingeben. Den sechsstelligen Code auf dem Gerät bestätigen. Der HASE-Entry besitzt Runtime, Gerät und Entities.
2. **Standalone CODE27 mit HASE (`domain=code27`):** Das Gerät zuerst über die eigenständige [CODE27-Integration](https://github.com/ConstableW/ha-code27) pairen. Danach unter **Integration hinzufügen → HASE → Vorhandenen eigenständigen CODE27 anbinden** den geladenen CODE27-Entry auswählen. HASE legt nur einen Binding-Entry an. Runtime, Gerät, Entities und Credentials bleiben beim `code27`-Entry; ein zweites Pairing findet nicht statt.

In **beiden** Fällen beim jeweiligen **HASE-Hub → Konfigurieren** den *Konversations-Agenten für Codies Sätze* wählen (normalerweise ein HASE-Spellbooker mit deinem Spellbook; eigene Bücher liegen unter `<config>/hase_spellbooks/`). Codies eigenes Modell reicht dann weiter, was du sagst - ohne Codewort. Einem standalone CODE27-Entry selbst wird das nicht zugewiesen; diese Einstellung gehört zum HASE-Binding.

Wird ein standalone CODE27-Entry gelöscht und neu angelegt, zeigt HASE für das alte Binding eine Reparaturmeldung. Richte das Gerät unter CODE27 ein und wähle beim HASE-Binding **Neu konfigurieren** für die neue Entry-ID, oder lösche das nicht mehr benötigte HASE-Binding. Das Anbinden verändert weder die Daten noch die Registry-Ownership des CODE27-Entry. Derselbe erkennbare `machine_id` kann nicht zusätzlich als eingebettetes HASE-Gerät eingerichtet werden.

Im lokalen Home-Assistant-Realtest wurden die verfügbaren HASE-Funktionen mit einem physischen CODE27 geprüft, darunter eingebettete und externe Anbindung, Spellbooker und MCP über hasechan. Die unten beschriebenen technischen Grenzen betreffen den Funktionsumfang oder besondere Randfälle.

## CODE27 Music und die HASEAMP-Card

Zwei Namen, zwei Dinge: **HASEAMP** ist die Winamp-inspirierte Lovelace-Card (die Benutzeroberfläche, `custom_components/hase/cards/haseamp-card.js`). **CODE27 Music** ist die Musikimplementierung, die CODE27 in diesem Provider besitzt: Queue, Playlists, Persistenz und Autoadvance plus den WebSocket-Vertrag, über den die Card sie erreicht. Es gibt keine HASE-Musikplattform und keinen generischen Provider-Musikvertrag – HASE bleibt eine Spellbook-Engine, jeder Frontend-Provider bringt seine eigene Musik-Schnittstelle mit, falls er eine hat, und die Card selbst kennt kein Gerät: sie wird von den Capability-Flags des Backends (`features.*`) gesteuert und erreicht das Backend über eine kleine Provider-Tabelle in ihrem eigenen Quelltext. Queue, Playlists, Persistenz und Autoadvance liegen vollständig im CODE27-Provider (`custom_components/hase/providers/code27/`); Router, Matcher und Executor bleiben unangetastet.

```
CODE27-Provider (Coordinator + Code27Client + media_player-Entity)  = Geräteautorität
      └─ CODE27 Music Runtime (providers/code27/music_runtime.py)   = Queue + Playlists + Autoadvance
            └─ WebSocket hase/code27/music  (music_api.py)
                  └─ custom_components/hase/cards/haseamp-card.js  ->  /hase-cards/haseamp-card.js
```

### Modulaufbau

| Modul | Aufgabe |
|---|---|
| `music.py` | CODE27-Musikzugriff: der Zeilen-/Browserbaum, zu dem ein TF-Kartenpfad wird, die Geräte-Fehlertexte und die Wiedergabe-Projektion. Geteilt mit der MediaPlayerEntity. |
| `music_model.py` | CODE27s eigenes Domänenmodell: `Track`, `ActiveQueue`, `Playlist`, `PlaylistLibrary`, `MusicError`. Reines Python – kein Home Assistant, kein Gerätezugriff. |
| `music_runtime.py` | Die Runtime: Queue-Orchestrierung, Persistenz, Autoadvance, Gerätekorrektur und die Transport-Operationen (inklusive `seek`), die TF-Karten-Verwaltung (Inventar, Import, Löschen) und das Bearbeiten gespeicherter Playlists. |
| `music_api.py` | CODE27s WebSocket-Vertrag `hase/code27/music` (+ `/subscribe`) und dessen Registrierung für einen Provider-Eintrag. |
| `media_player.py` | Die native Home-Assistant-`MediaPlayerEntity` – ein zweiter Konsument desselben Musikcodes, unabhängig von der Card. |

```
                 ┌── media_player.py ──► nativer HA-MediaPlayer
CODE27 Music ────┤
                 └── music_api.py ─────► HASEAMP-Card
```

Die Modulnamen sind bewusst architekturbeschreibend: HASEAMP ist die Card, deshalb trägt kein CODE27-Backendmodul diesen Namen – und keines dieser Module ist ein HASE-weiter Vertrag. Cards allgemein – und die Frontend-Grenze, die ein Provider dort registriert – stehen in [HASEAMP (Dashboard-Card)](../cards.md).

Installation: HASE liefert und lädt `haseamp-card.js` automatisch; lege nur eine Card vom Typ `custom:haseamp-card` an. Optional sind `target` (config_entry_id eines echten CODE27-Geräte-Entries), `name`, `show_browser`, `show_queue`, `show_playlists`, `show_import`, `show_animation`, `show_diagnostics` und `follow_light` (Standard an: die Farben der Card folgen dem Umgebungslicht des Geräts). Der Editor zeigt echte Geräte-Entries beider Owner, keine HASE-External-Bindings; das gewählte Target bestimmt den Musik-Namespace. Bei mehreren Targets ist die Auswahl erforderlich.

Die Card ist reines Frontend: sie spricht **nie** direkt mit der CODE27-Geräte-REST-/WS-API, sondern über den WebSocket-Vertrag des jeweiligen Owners (`hase/code27/music` oder `code27/music`). Die aktive Queue und die gespeicherten Playlists liegen in dessen Home-Assistant-Runtime, nicht im Browser: ein Dashboard-Reload kann sie nicht verlieren, und eine Queue-Änderung verändert niemals implizit eine gespeicherte Playlist.

Was CODE27 nicht kann und was die Music Runtime deshalb nicht behauptet: `/data/user/music/control` kennt **keine** STOP-Aktion (bestätigte Aktionsmenge: `PLAY | PAUSE | PREVIOUS | NEXT | SET_PLAY_MODE | SEEK | SET_VOLUME | SET_SOUND_EFFECT`). Ein Stop ist daher ein Queue-Stop plus Geräte-Pause – die Card beschriftet das aus dem Capability-Flag, nicht aus fest verdrahtetem Gerätewissen. Shuffle und Repeat sind Queue-Modi der jeweiligen Owner-Runtime, keine Gerätewiedergabemodi. `SEEK` **ist** dagegen eine echte Geräteaktion (`progress_ms`) und wird sowohl von der MediaPlayerEntity als auch von der Music Runtime genutzt.

### Queue steuert die Wiedergabe – trotz geräteseitiger Ordner-Fortsetzung

CODE27 spielt nach dem Ende eines Titels **die nächste Datei im Ordner** weiter, nicht den nächsten Queue-Eintrag, und sein Status bleibt dabei auf `PLAYING`. Der `PLAYING`→`IDLE`-Vertrag sieht also gar kein Track-Ende. Die Music Runtime erkennt stattdessen den **Wechsel der vom Gerät gemeldeten `track_id`** und korrigiert:

* spielt das Gerät bereits genau die Zeile, die als Nächstes dran wäre, wird sie übernommen (kein `PLAY`, das den Titel von vorn starten würde);
* spielt es irgendetwas anderes, holt sich die Runtime die Wiedergabe zurück und startet die nächste Queue-Zeile;
* korrigiert wird **nur**, wenn das Gerät die von HASE gestartete Zeile vorher bestätigt hat (also dieselbe `track_id` zurückmeldet). Meldet das Gerät nie eine passende `track_id`, kann die Runtime nicht erkennen, was es tut, und kämpft nicht dagegen an – dann bleibt nur die Steuerung über die Card.

Solange die Queue läuft (`queue.playing`), gehört die Wiedergabe HASE; Stop/Pause in der Card beendet das, danach wird nur noch beobachtet. Der Grund der letzten Entscheidung steht in der Diagnosezeile der Card (`last: …`), und `diagnostics.device_track_id` / `position_reported` / `duration_reported` zeigen, was das Gerät tatsächlich meldet.

### Bedienung im Detail

* **Fortschrittsbalken**: ein Klick auf den Balken **spult an die angeklickte Stelle**. Die Card schickt die Klickposition in Sekunden als `transport` mit `action: "seek"`, das Backend reicht sie als die geräteeigene `SEEK`-Aktion weiter (`progress_ms`, plus die vom Gerät gemeldete `track_id`) – derselbe `music_control`-Aufruf, den `async_media_seek` der MediaPlayerEntity macht, es gibt also keinen zweiten Seek-Pfad. Der separate **↺-Knopf** **startet den aktuellen Titel weiterhin neu** (`transport: restart`, `PLAY` für die aktuelle Queue-Zeile) und ist nicht die Aktion des Balkens. Der Balken hat zwei Anzeigen: die Füllung **und** einen Positionszeiger, die gemeinsam aus `position`/`duration` gesetzt werden. Meldet das Gerät keine Titellänge, wird der Balken gestreift und animiert dargestellt, sagt es im Klartext und nimmt gar keinen Klick mehr an – es gäbe nichts zu treffen; das ist bewusst von „Fortschritt bleibt bei null hängen“ unterscheidbar. Die Länge wird, wenn möglich, aus der Queue-Zeile oder dem TF-Inventar ergänzt (`duration_source`: `device` / `queue_row` / `music_inventory`).
* **Aufteilung der Bedienung**: Transport-Tasten (⏮ ▶/⏸ ⏹ ⏭ ↺) und Optionen (Lautstärke, Quelle, SHF, RPT, „Gespielte entfernen“) stehen nebeneinander unter dem Display und brechen auf schmalen Bildschirmen um, sodass Zustandswechsel den Lautstärkeregler nie verschieben.
* **„Gespielte entfernen“** (Standard: aus) ist der Schalter zwischen den beiden Queue-Konzepten:
  * **aus** – die Queue behält alles; gespielte Zeilen werden nur gedämpft markiert und die Kopfzeile zeigt die Position (`3/12`). `previous` funktioniert weiter.
  * **an** – gespielte Zeilen **verlassen** die Queue, sobald die Wiedergabe weiterzieht; sie liest sich dann wie eine „als Nächstes“-Liste, und Zurückspringen auf Gespieltes ist naturgemäß nicht mehr möglich.
  Angewendet wird der Modus nur in den **automatischen** Pfaden (Track-Ende, Gerätekorrektur) – ein manueller Zeilensprung löscht nie etwas. Der Modus ist Backend-Zustand, wird also pro CODE27-Ziel persistiert und übersteht Reload und Neustart.
* **Überschreiben wird gefragt**: „Speichern“/„Speichern als“ auf einen bereits existierenden Namen stellt eine Rückfrage; ein „Ja“ sendet `playlist_save` (überschreiben), ein „Nein“ ändert nichts. `playlist_save_as` selbst überschreibt nie – ein „Ja“ wechselt bewusst die Operation, damit aus einem Anlegen niemals still ein Ersetzen wird.
* **Texteingabe**: Subscription-Pushes kommen etwa im Sekundentakt. Die Card zeichnet Abschnitte nur bei inhaltlicher Änderung neu und lässt den Abschnitt in Ruhe, in dem gerade getippt oder ein Regler gezogen wird.

### Aufbau, MusicMode und Farben

* **Ein Fenster, vier Tabs**: Oben sitzt der Player (Uhr, Titel, Fortschrittsbalken, Transport, Lautstärke, SHF/RPT); **Queue**, **Bibliothek**, **Playlists** und **Import** teilen sich das Feld darunter. Doppelklick auf eine Queue-Zeile spielt sie, Doppelklick auf eine Playlist lädt sie in die Queue. Tab-Wahl und Markierungen sind reiner Card-Zustand und erreichen nie das Backend.
* **„MusicMode ist off!“**: Solange die Musik-Ansicht am Gerät geschlossen ist (`binary_sensor` *Musikmodus aktiv* / `snapshot.music_mode.active == false`), lehnt das Gerät Wiedergabe-Befehle ab (`MODE_INACTIVE`). Die Card schreibt das ins Display, statt einen toten Player zu zeigen, und sperrt die Transport-Tasten. Queue, Playlists und Import bleiben nutzbar. `music_mode.known` ist false, bis das Gerät seinen Seitenzustand gemeldet hat - dann zeigt die Card nichts Besonderes, statt zu raten.
* **Farben folgen dem Gerätelicht**: Die Card liest die Farbe der Licht-Entität *Umgebungsfarbe* des Geräts (nur lesend) und färbt das ganze Fenster - Titelleiste, Rahmen, Buttons, Tabs, Schrift - in deren Farbton, wie ein klassischer Player-Skin. Die Helligkeit ist je Teil festgelegt, damit der Text lesbar bleibt; Blau- und Violetttöne bekommen eine hellere Schrift. Ein ausgeschaltetes oder unbekanntes Licht lässt das Standard-Grün. Die Farbe ist nur das, was Home Assistant zuletzt ins Licht geschrieben hat (das Gerät hat keinen Live-Readback), und rein kosmetisch. Abschalten mit `follow_light: false`.

### Musikverwaltung

Alles läuft über den bestehenden WebSocket-Vertrag; die Card baut nichts, was das Backend nicht selbst besitzt.

| Operation | Wirkung |
|---|---|
| `inventory` | Alle Dateien auf der TF-Karte als `{name, size, track}`, dazu Dateien, die HASE hochgeladen hat und das Gerät noch nicht listet. |
| `import_chunk` | Nimmt ein base64-Stück einer Datei entgegen; das letzte Stück lädt die zusammengesetzte Datei auf die Karte (`POST /data/user/music/upload`, nur Audio-Typen, höchstens 64 MB je Datei). |
| `delete_file` / `delete_folder` | Entfernen eine Datei bzw. alles unter einem Ordner von der Karte (`DELETE /data/user/music/file`). Die laufende Datei wird nie gelöscht; ein Ordner-Löschen entfernt höchstens 500 Dateien. Queue-Zeilen gelöschter Dateien entfallen; gespeicherte Playlists behalten ihre Einträge absichtlich. |
| `playlist_load` + `resume: true` | Lädt eine gespeicherte Playlist und macht dort weiter, wo zuletzt gespielt wurde: der gemerkte Titel wird gewählt, gestartet und an die gemerkte Stelle gesetzt (3 Sekunden früher; ein Titel, der höchstens 5 Sekunden vor dem Ende war, geht mit dem nächsten weiter). Ohne Fortsetzungspunkt oder ohne `resume` wird nur geladen. |
| `playlist_get` / `playlist_update` | Titel einer gespeicherten Playlist lesen; Zeilen per Position umsortieren oder entfernen (`order` = die aktuellen Positionen, die bleiben, in neuer Reihenfolge). Die aktive Queue bleibt unberührt. |
| `playlists_from_folders` | Legt für jeden Kartenordner, der direkt Musik enthält, eine gespeicherte Playlist an (Name = Ordnerpfad, verschachtelt `Eltern / Kind`; Dateien in natürlicher Reihenfolge). Ordner, die wie eine CD heißen (`CD 1`, `Disc 02`, `Teil 3`, `Part 4`), gehören zur Playlist des Ordners darüber, in CD-Reihenfolge - ein Hörbuch mit mehreren CDs wird **eine** Playlist. Namen werden auf die Grenze von 64 Zeichen gekürzt (der eigene Ordnername geht vor einem gekürzten Pfad). Bestehende Playlists werden übersprungen, gelöschte neu gebaut, die Queue bleibt unberührt. |

* **Import-Tab**: Audiodateien, eine M3U **oder einen ganzen Ordner** wählen oder hineinziehen, optional eine Playlist benennen. Ein hineingezogener (oder gewählter) Ordner benennt die Playlist nach sich (außer es wurde ein Name getippt), behält die Dateien in natürlicher Reihenfolge und lädt wie die M3U nur hoch, was noch nicht auf der Karte liegt. *Nur importieren* lädt sie hoch; *Importieren & Playlist bauen* lädt hoch, ersetzt die Queue durch die importierten Titel und speichert sie als Playlist (`queue_clear` + `queue_add` + `playlist_save_as`). Vor dem Hochladen prüft die Card den freien Platz (`library.capacity_*`).
* **M3U**: Eine `.m3u`/`.m3u8` **zusammen mit den Audiodateien** hineinziehen. Ein Browser kann nur Dateien lesen, die der Nutzer ihm gibt - verwendet wird daher nur der *Dateiname* jeder Zeile (Ordner und Laufwerksbuchstaben spielen keine Rolle). Jede Zeile wird mit der Karte nach Name und Größe verglichen: schon auf der Karte - kommt ohne Upload in die Playlist; fehlt - wird aus den gewählten Dateien hochgeladen; gleicher Name, andere Größe - die Version auf der Karte gilt, die Zeile trägt ein `*`; weder noch - wird als fehlend gemeldet; `http(s)`-Zeilen - werden aufgelistet, aber nicht geladen. Erst erscheint eine Zusammenfassung, hochgeladen wird erst nach Bestätigung. Die Playlist behält die Reihenfolge der M3U und heißt wie die Datei.
* **Ordner scannen & Playlists anlegen** (Tab Playlists) baut für jeden Kartenordner mit Musik eine Playlist - gedacht für Karteninhalt, der nicht über die Card kam (Kartenleser, die Geräteseite). Bestehende Playlists bleiben unberührt; eine gelöschte entsteht beim nächsten Scan neu.
* **Klangvoreinstellungen** (Optionszeile des Players): *Klassik / Surround / Stimme* sind die eigenen Voreinstellungen des Geräts (`SET_SOUND_EFFECT`), dargestellt wie ein Equalizer-Schalter; die aktive folgt dem, was das Gerät meldet (`sound_effect`). Sie ersetzen einen Equalizer - die Card hat keinen Zugriff auf das Audio selbst. Den Abspielmodus des Geräts (`sequence`, `list_loop`, `single_loop`, `shuffle`) bieten wir bewusst **nicht** an: HASE führt die Queue, ein Loop-Modus am Gerät würde dagegen arbeiten. Die Tanz-Einstellung des Geräts ist nicht Teil der API (siehe Feedback-Dokument).
* **Fortsetzen** (Tab Playlists): Solange eine gespeicherte Playlist geladen ist, merkt sich HASE je Playlist, welcher Titel an welcher Stelle lief - gespeichert beim Titelwechsel, alle 10 Sekunden Fortschritt und exakt bei einer Pause, im Store der Runtime, also auch über einen Neustart. Eine Playlist-Zeile zeigt `▶ Titel 5 · 12:34`; der Button **Fortsetzen** (und ein Doppelklick auf so eine Zeile) lädt die Playlist und macht dort weiter. *Laden* lädt weiterhin ohne abzuspielen. Beim Löschen einer Playlist wird ihr Fortsetzungspunkt vergessen. Das Spulen ist best effort: Ist das Gerät nicht bereit, beginnt der Titel von vorn.
* **Per Button oder Tag starten bzw. fortsetzen**: Der Service `hase.play_playlist` (`config_entry_id`, `playlist`, optional `resume`, Standard an) lädt eine gespeicherte Playlist und spielt sie ab - an der gemerkten Stelle oder, wenn sie noch nie gespielt wurde (oder mit `resume: false`), ab dem ersten Titel. Die Musik-Ansicht muss am Gerät offen sein; sonst zeigt die Automations-Ablaufverfolgung, warum abgelehnt wurde, und ein unbekannter Playlist-Name ist ein klarer Fehler. Ein Dashboard-Button:

  ```yaml
  type: button
  name: Hörbuch
  tap_action:
    action: perform-action
    perform_action: hase.play_playlist
    data:
      config_entry_id: <Config-Entry-ID des Geräts>
      playlist: "Walter Moers - Die 13½ Leben des Käpt'n Blaubär"
  ```

  Derselbe Aufruf als Automations-Aktion mit einem Tag-Trigger (RFID/NFC) ergibt „Figur scannen, Geschichte weiterhören“:

  ```yaml
  triggers:
    - trigger: tag
      tag_id: <deine Tag-ID>
  actions:
    - action: hase.play_playlist
      data:
        config_entry_id: <Config-Entry-ID des Geräts>
        playlist: "Walter Moers - Die 13½ Leben des Käpt'n Blaubär"
  ```
* **Lichtszene und Umgebungsfarbe**: Die Szenen-Auswahl (*Lichtszene*) zeigt die Szene, die das Gerät angewendet hat. Eine von hier geschriebene Umgebungsfarbe liegt darüber, deshalb ist die Auswahl **leer**, solange sie gezeigt wird, bis wieder eine Szene gewählt wird (nach einem Neustart zeigt sie die angewendete Szene, weil das Gerät die Umgebungsfarbe nicht zurückmelden kann).
* **Playlist-Editor** (Tab Playlists): Eine gewählte Playlist zeigt ihre Titel mit Nach oben/unten und Entfernen; *Als M3U speichern* lädt die Playlist in einer Form herunter, die der Import wieder lesen kann. Ein Ordner im Tab Bibliothek hat einen Button *Ordner als Playlist speichern* für Dateien, die auf anderem Weg auf die Karte kamen.
* **TF-Karte wechseln**: Track-URIs tragen die Karten-ID (`/storage/<id>/...`). Wird eine gespeicherte Playlist angezeigt oder geladen, werden Titel, deren URI nicht auf der aktuellen Karte liegt, stattdessen über ihren Pfad unterhalb von `code27/music` zugeordnet; Titel ohne Treffer bleiben unverändert und sind einfach nicht abspielbar.
* **Gerätegrenzen**: Das Gerät legt jeden Upload im **Root** seines Musikordners ab - ein Ordnername im Dateinamen wird ignoriert, ein hineingezogener Ordner wird also zur *Playlist*, nicht zu einem Ordner auf der Karte. Dateinamen gehen unverändert hinaus (eine frühere Version ließ den HTTP-Client sie prozentkodieren, was auf der Karte als `%20` auftauchte; die Card zeigt solche Namen jetzt dekodiert). Das Gerät kennt weder Datei-Download noch Playlists, daher kann HASE keine `.m3u` lesen, die anderweitig auf die Karte kam, und das Gerät führt seine Dateiliste träge nach (beobachtet: nicht bei geschlossener Musik-Ansicht). HASE merkt sich deshalb eigene Uploads, bis das Gerät sie listet, und vergisst sie beim Kartenwechsel.

Die vollständige Fassung des Track-Ende-Vertrags steht als `TRACK_END_CONTRACT` in `providers/code27/music.py` und wird der Card mitgeliefert.

Das TF-Karten-Inventar hat zwei Quellen: den `music_storage`-WebSocket-Push und den REST-Aufruf `GET /data/user/music/storage`, der bei jedem Poll mitgelesen wird. Beide werden nach demselben Vorrang zusammengeführt (neuestes gültiges Inventar gewinnt), sodass eine Update-Runde ohne Inventar das zuletzt bekannte **niemals** löscht und der Browser schon nach dem ersten Poll – auch direkt nach einem Home-Assistant-Neustart – gefüllt ist. Solange noch gar kein Inventar vorliegt, meldet der Browser "warte auf das Musik-Inventar", statt eine leere Bibliothek vorzutäuschen.

Die Card hält selbst keine Bibliotheksdaten vor: Ein Subscription-Push wiederholt die Ordnerliste bewusst nicht, sondern meldet über `library.revision`, ob sich das Inventar geändert hat. Fehlt die Liste in einer Nachricht, bedeutet das "unverändert" – niemals "leer"; das Backend bleibt die Autorität.

Ein **Satellite** ist eine experimentelle Conversation-Subentry an einem Gerät. Er versucht zuerst sein Spellbook und nutzt bei No-Match den Charakter-Backend-Fallback. Reply-Korrelation bei gleichzeitigem physischem Chat ist nicht verifiziert.

Grenzen: kein Capability-basiertes Feature-Gating, kein belegter Endpoint für dynamische Stimmenlisten, Bildchat nur im Backend, Diary nur lesend und teils nur beispielhafte Vacuum-/Geräteprofile. Details im internen Index (`../../internal/INDEX.md`, ein Maintainer-Dokument, das nicht Teil des öffentlichen Repos ist).

## Firmware 1.6.0: Codie-Tools, Gerätesteuerung, Events

Unterstützt wird nur Firmware 1.6.0 und neuer; die Fallbacks für ältere Firmware sind entfernt.

**Codie-Tool-Gateway.** HASE registriert Tools am Gerät, und Codies eigenes Modell ruft sie über den Tool-WebSocket des Geräts auf - HASE liest den Chat nicht mehr mit, braucht kein Codewort und bereinigt keine Historie. Zwei Geräteoptionen steuern es: *Konversations-Agent für Codies Sätze* ergänzt `home_command`, *Codie-Tool-Bridge* (eine MCP-Bridge) ergänzt `spell_cast` und `spell_list`. Sind beide leer, werden keine Tools registriert:

| Tool | Zweck |
| --- | --- |
| `home_command` | Der Satz des Nutzers geht unverändert an den gewählten Konversations-Agenten: exakter und unscharfer Trigger-Abgleich, Area-Policy und Rückfragen eines HASE-Spellbookers. Eine Rückfrage kommt als `QUESTION: ...` zurück, Codie fragt den Nutzer und ruft mit der Antwort erneut auf. Sätze ohne Spellbook-Trigger gehen an das normale Assist von Home Assistant. |
| `spell_cast` | Spell ausführen: `spell` (ID) und `args` (JSON-Objekt als String). Läuft über dieselbe Projektion, Area-Policy, Assist-Freigabe und Generation-Lease wie MCP. |
| `spell_list` | Der vollständige Spell-Katalog der Bridge mit Argumentnamen. |

Zwei Tools halten das ganze Spellbook erreichbar (das Gerät erlaubt 16 Tools, 8 KiB je Schema und etwa 512 Zeichen je Beschreibung - `spell_cast` enthält so viele Katalogzeilen, wie hineinpassen, den Rest liefert `spell_list`). `home_command` hat keine Katalog-Grenze, weil der Host den Abgleich macht; es ist das Tool für alltägliche Sätze.

**Gerätesteuerung.** *Display-Helligkeit* (Number, 0-100), *Display* (Switch) und *Gerätemodus* (LIVE / MUSIC) sind Entitäten des Geräts; Chat-Turns können `interrupt`, `retention` und `tools` mitgeben (`create_chat_turn`).

**Events.** Die Integration hört außerdem den Statuskanal `/v1/ws/events`; ein Chat-Historien-Event löst sofort einen Chat-Poll aus, statt auf den 3-s-Timer zu warten, der als Rückfall bleibt.

**Standalone-CODE27.** Alles Obige außer dem HASE-gestützten Dispatcher ist geteilter Gerätecode und läuft auch in der Standalone-CODE27-Integration (`scripts/sync_code27.py` erzeugt sie neu). Standalone allein registriert keine Tools; HASE liefert den Dispatcher, auch für ein Gerät, das einem Standalone-Entry gehört (siehe `docs/code27/CODE27-SPLIT-EXTERNAL-HUB-CONTRACT-0.1.md`).
