🇩🇪 Deutsch | 🇬🇧 [English](README.en.md)

# HASE – Home Assistant Spellbook Engine

![HASE](docs/branding/hase-title.png)

HASE verbindet eine geräteunabhängige Spellbook Engine mit optionalen Companion-Providern.
Standalone-Agents brauchen kein CODE27-Gerät. CODE27 ist der erste Provider mit Gerätefunktionen,
Chat-Bridge und Satellite-Subentries.

**Rebrand mit Neueinrichtung:** Die HA-Domain lautet `hase`. Alte `code27`-Entries werden nicht
migriert. Vor dem Wechsel sichern, alte Entries mit installiertem Altcode entfernen und HASE
frisch einrichten. Es gibt keine alten Service-, Event- oder Pfad-Aliasse.
Siehe [Cutover und Live-Abnahme](docs/HASE-CUTOVER.md) und [Architektur](docs/HASE-ARCHITECTURE.md).
Ein viertes Frontend, MCP (Model Context Protocol – externe Agenten/LLM-Tools rufen Spells
direkt auf), ist in [MCP-Schnittstelle](docs/HASE-MCP.md) dokumentiert; eigene Bridge-/
Frontend-Adapter siehe [Neue Bridges anbinden](docs/HASE-NEW-BRIDGES.md).

## Inhalt

- [Agents: Spellbooker & Satelliten](#agents-spellbooker--satelliten)
  - [Eigenständiger Spellbooker (kein CODE27-Gerät nötig)](#eigenständiger-spellbooker-kein-code27-gerät-nötig)
  - [Satellit (Subentry an einem CODE27-Gerät)](#satellit-subentry-an-einem-code27-gerät)
- [Installation](#installation)
- [CODE27-Provider](#code27-provider)
  - [Voraussetzungen](#voraussetzungen)
  - [Einrichtung (Schritt für Schritt)](#einrichtung-schritt-für-schritt)
  - [Was wird angelegt?](#was-wird-angelegt)
  - [Optionen konfigurieren](#optionen-konfigurieren)
  - [Services](#services)
  - [Events](#events)
  - [Assist-Bridge – Sprachbefehle aus dem Chat heraus](#assist-bridge--sprachbefehle-aus-dem-chat-heraus)
    - [Zweistufige Befehls-Bereinigung (optional)](#zweistufige-befehls-bereinigung-optional)
    - [Bewährte Sprachmuster (Assist-Grammatik)](#bewährte-sprachmuster-assist-grammatik)
    - [Spellbooks – feste Zauberformeln](#spellbooks--feste-zauberformeln)
    - [Vacuum-Area-Resolver](#vacuum-area-resolver)
  - [Dashboard-Karte (code27-card)](#dashboard-karte-code27-card)
  - [Bewusste Design-Entscheidungen](#bewusste-design-entscheidungen)
  - [Bekannte Grenzen](#bekannte-grenzen)
  - [Technischer Hintergrund](#technischer-hintergrund)
- [Support & Issues](#support--issues)

## Agents: Spellbooker & Satelliten

Hinter "Einstellungen → Geräte & Dienste → Integration hinzufügen → HASE" stecken zwei
unabhängige Wege, einen Agent anzulegen: ein CODE27-Gerät koppeln (siehe
[CODE27-Provider](#code27-provider) unten), oder einen eigenständigen **Spellbooker**-
Sprachassistenten erstellen, der kein Gerät braucht. Ein dritter Weg – ein **Satellit** –
wird nachträglich an einem bereits eingerichteten CODE27-Gerät hinzugefügt. Beide brauchen
kein CODE27-Gerät bzw. hängen an einem bereits vorhandenen – die im Rest des Dokuments
beschriebene Pairing-Prozedur, Geräte-Entitäten, Services, Events, Assist-Bridge und
Dashboard-Karte gehören ausschließlich zum CODE27-Provider.

### Eigenständiger Spellbooker (kein CODE27-Gerät nötig)

"Integration hinzufügen → HASE → Spellbooker-Sprachassistent anlegen" legt einen reinen
Conversation-Agent an, der ausschließlich Kommandos aus einem Spellbook versteht – ohne
CODE27-Hardware, Chat-Bridge-Umweg oder Backend-Fallback.

![Spellbooker anlegen auswählen](docs/screenshots/08-spellbooker-menu.png)

*(Platzhalter – Screenshot des Auswahlmenüs "CODE27-Gerät verbinden" / "Spellbooker-Sprachassistent anlegen" folgt.)*

| Feld | Bedeutung |
|---|---|
| Name | Titel des Eintrags und Name der resultierenden `conversation.*`-Entität |
| Spellbook | Eines der Bücher aus `custom_components/hase/spellbooks/` oder `<config>/hase_spellbooks/` |
| Erlaubte Bereiche (Aktionen) / Aktionen uneingeschränkt | In welchen Bereichen dieser Assistent Aktionen ausführen darf; "uneingeschränkt" erlaubt alle Bereiche und ignoriert die Auswahl |
| Erlaubte Bereiche (Abfragen) / Abfragen uneingeschränkt | Unabhängig von den Aktions-Bereichen: in welchen Bereichen Statusabfragen (z. B. "wie warm ist es in X") beantwortet werden dürfen |
| Antwort bei Nicht-Treffer | Wird gesagt, wenn ein Satz zu keinem Spellbook-Kommando passt. Leer = Standardantwort |

![Spellbooker-Formular ausfüllen](docs/screenshots/09-spellbooker-form.png)

*(Platzhalter – Screenshot des Spellbooker-Einrichtungsformulars folgt.)*

Für Aktionen und für Abfragen ist jeweils entweder mindestens ein Bereich oder
"uneingeschränkt" Pflicht. Der Spellbooker erscheint als eigener Eintrag unter Geräte &
Dienste – kein Gerät, keine weiteren Entitäten außer der Conversation-Entität selbst – und
lässt sich wie jeder andere Sprachassistent unter **Einstellungen → Sprachassistenten** in
eine Assist-Pipeline einhängen. Optionen (Spellbook, Bereiche, Nicht-Treffer-Antwort) lassen
sich jederzeit über "Konfigurieren" anpassen.

### Satellit (Subentry an einem CODE27-Gerät)

⚠️ **Experimentell**: Ein Satellit probiert zuerst sein eigenes Spellbook und reicht bei
Nicht-Treffer an ein echtes Charakter-Gespräch auf dem zugehörigen CODE27-Gerät weiter. Die
Antwortzuordnung zum Gerät (welche Antwort zu welcher Anfrage gehört) wurde nicht gegen
gleichzeitige physische Chat-Nutzung am Gerät selbst getestet – nicht dort einsetzen, wo eine
falsche oder verspätete Antwort ein echtes Problem wäre.

Auf der Seite eines bereits eingerichteten CODE27-Eintrags über dessen Subentry-Menü
**"Satellit hinzufügen"** wählen:

![Satellit-Subentry hinzufügen](docs/screenshots/10-satellite-subentry-menu.png)

*(Platzhalter – Screenshot des Subentry-Menüs am CODE27-Geräte-Eintrag folgt.)*

| Feld | Bedeutung |
|---|---|
| Name | Name des Satelliten; erscheint als eigenes "Gerät" unterhalb des CODE27-Geräts (z. B. "CODE27 Papa" → "Lilya Satellit") |
| Charakter-Slot | Nummer des Charakter-Slots auf dem Gerät, mit dem der Satellit spricht (dieselbe Nummer wie in dessen eigener Charakter-Slot-Auswahl) |
| Spellbook | Optional; "— Kein lokales Spellbook —" schickt jede Äußerung direkt an das CODE27-Backend (Charakter-Chat) |
| Erlaubte Bereiche (Aktionen) / Aktionen uneingeschränkt | Gilt für das lokale Spellbook **und** für alles, was das CODE27-Backend vorschlägt |
| Erlaubte Bereiche (Abfragen) / Abfragen uneingeschränkt | Unabhängig von den Aktions-Bereichen |
| Antwort bei Nicht-Treffer | Nur relevant ohne ausgewähltes Spellbook – ist eines gewählt, geht ein Nicht-Treffer stattdessen immer an den Charakter des Geräts |

![Satellit-Formular ausfüllen](docs/screenshots/11-satellite-form.png)

*(Platzhalter – Screenshot des Satelliten-Einrichtungsformulars folgt.)*

Ein Satellit teilt sich Coordinator und Backend-Lock mit seinem CODE27-Gerät und erzeugt
keinen eigenen Eintrag auf oberster Ebene – er hängt fest an genau dem Gerät, an dem er
angelegt wurde. Bestehende Satelliten lassen sich über ihr eigenes Subentry-Menü ("Satellit
neu konfigurieren") anpassen; jede Änderung lädt den gesamten CODE27-Eintrag neu.

## Installation

1. Kopiere den Ordner `custom_components/hase` in das `custom_components`-
   Verzeichnis deiner Home-Assistant-Konfiguration
   (`<config>/custom_components/hase`).
2. Home Assistant **komplett neu starten** (kein Reload – neue Integrationen
   und, wichtig für spätere Updates, neue Entitäten werden bei diesem
   Integrationstyp zuverlässig nur bei einem echten Neustart eingelesen).

Dieser Schritt gilt unabhängig davon, ob danach ein eigenständiger Spellbooker,
ein Satellit oder ein CODE27-Gerät eingerichtet wird – alle drei leben im selben
`custom_components/hase`.

## CODE27-Provider

Alles ab hier – Pairing, Geräte-Entitäten, Optionen, Services, Events, Assist-Bridge
und Dashboard-Karte – gehört ausschließlich zum **CODE27-Provider** und setzt ein
CODE27-Gerät voraus. Für einen eigenständigen Spellbooker oder einen Satelliten ist
nichts davon nötig (siehe [Agents: Spellbooker & Satelliten](#agents-spellbooker--satelliten)
oben). Die Screenshots zeigen den früheren CODE27-Auftritt; im neuen Integrationsdialog
heißt die Integration HASE.

Der CODE27-Provider verbindet dein CODE27-Gerät (Web-UI unter
`http://<ip>:8081`, intern **CODIE** genannt) über dessen **eigene lokale
REST- und WebSocket-API** einbindet – exakt das Protokoll, das auch die
Web-Oberfläche des Geräts selbst nutzt. Es wurde nichts erfunden, was das
Gerät nicht ohnehin schon anbietet.

Dazu gibt es eine passende **Dashboard-Karte** (`code27-card`) mit Chat,
Licht-/Musiksteuerung, Alarmen, Notizen und einem Live-Feed der Sprachbrücke.

### Voraussetzungen

- Ein CODE27-Gerät im selben lokalen Netzwerk wie Home Assistant, erreichbar
  unter `http://<ip>:8081`.
- Eine halbwegs aktuelle Home-Assistant-Version (die Assist-Bridge nutzt
  `conversation.async_converse` und den `ConversationAgentSelector`).
- Für die optionale Assist-Bridge: ein eingerichteter Assist-Sprachassistent
  in Home Assistant.

### Einrichtung (Schritt für Schritt)

#### 1. Integration hinzufügen

**Einstellungen → Geräte & Dienste → Integration hinzufügen → "HASE"** suchen, dann
**"CODE27-Gerät verbinden"** wählen.

![Integration hinzufügen](docs/screenshots/01-add-integration.png)

Gib die lokale IP-Adresse deines CODE27-Geräts (z. B. `192.168.xxx.xxx`) und
den Port (`8081`) ein.

![Host und Port eingeben](docs/screenshots/02-host-port.png)

#### 2. Pairing

Auf dem **Bildschirm des CODE27-Geräts selbst** erscheint jetzt ein
6-stelliger Pairing-Code (Device-Pairing-Screen, kein Login nötig – das ist
derselbe Vorgang wie beim Verbinden eines neuen Browsers mit dem Gerät):

![Pairing-Code auf dem Gerät](docs/screenshots/03-device-pairing-code.png)

Diesen Code in Home Assistant eingeben:

![Pairing-Code in HA eingeben](docs/screenshots/04-ha-pairing-code.png)

Home Assistant erhält dabei ein **eigenes, dauerhaftes lokales Secret** –
unabhängig von jeder Browser-Session, die du sonst mit dem Gerät benutzt
(so, als hätte HA sein eigenes "Gerät" in der CODE27-App).

#### 3. Fertig

Die Integration legt jetzt ein Gerät mit allen Entitäten an (siehe Tabelle
unten) und beginnt sofort mit dem Abfragen.

![Fertig eingerichtetes Gerät](docs/screenshots/05-device-overview.png)

### Was wird angelegt?

Alles hängt an einem Gerät ("CODE27 (\<host\>)") in Home Assistant:

| Entität | Typ | Beschreibung |
|---|---|---|
| Musik | `media_player` | Play/Pause/Weiter/Zurück, Lautstärke, Quelle (TF-Karte/Bluetooth), Titel, Position/Dauer |
| Umgebungsfarbe | `light` | Freie RGB-Farbe + Helligkeit + Effekt (Statisch/Atmen/Pulsieren/Blinken/Lauflicht) über den Preview-Kanal des Light Editors – nicht persistent, überschreibt keine gespeicherten Projekte. An/Aus spiegelt den geräteweiten Licht-Master-Switch |
| Lichtszene | `select` | Wählt eine im Light Editor gespeicherte Lichtszene und wendet sie an (`apply`, dauerhaft) |
| Aktiver Charakter | `select` | Wechselt den aktiven Charakter-Slot |
| Alarm *(je Alarm)* | `switch` | Ein/Aus je auf dem Gerät angelegtem Alarm, inkl. Uhrzeit/Wiederholung als Attribute |
| Notizen & Erinnerungen | `todo` | Liste mit Anlegen/Bearbeiten/Löschen, gespiegelt aus "Lab → Notes & Reminders". **Kein** "Erledigt"-Status – das Gerät kennt selbst nur Löschen, kein Abhaken |
| Musikmodus aktiv | `binary_sensor` | Ob die Musik-Seite gerade auf dem Gerätebildschirm offen ist |
| Hinweis aktiv | `binary_sensor` | Ob gerade ein per `hase.notify` ausgelöster Blink-Alarm läuft (siehe unten) |
| Hinweis quittieren | `button` | Beendet den aktuellen Hinweis-Alarm |
| Zuletzt Gesagtes (du) / (Charakter) | `sensor` | Letzte Chat-Nachricht, getrennt nach Rolle. Der Charakter-Sensor trägt zusätzlich die letzten ~30 Nachrichten als `history`-Attribut (Basis für die Karte) |
| Aktiver Charakter / Aktive Alarme / Notizen / Sprache / Zeitzone / Build-Typ | `sensor` | Diagnose- und Statuswerte |
| Testton abspielen / Ton stoppen | `button` | Gerätetest ohne Umweg über die Geräte-UI |

Ein eigenständiger Spellbooker oder ein Satellit legt **keine** dieser Entitäten an –
nur die eine `conversation.*`-Entität selbst (siehe
[Agents: Spellbooker & Satelliten](#agents-spellbooker--satelliten)).

### Optionen konfigurieren

**Einstellungen → Geräte & Dienste → HASE → Konfigurieren** öffnet den
Options-Dialog mit dem Abfrageintervall und der Assist-Bridge:

![Options-Dialog](docs/screenshots/06-options-dialog.png)

| Feld | Bedeutung |
|---|---|
| Abfrageintervall | Sicherheitsnetz-Polling für Alarme/Notizen/Charakter/System (Standard 15s). Musik- und Licht-Status kommen unabhängig davon per WebSocket-Push praktisch in Echtzeit, Chat wird alle 3s separat abgefragt (siehe [Technischer Hintergrund](#technischer-hintergrund)) |
| Assist-Bridge aktivieren | Schaltet die Sprachbrücke zu Home Assistant Assist ein/aus |
| Codewort(e) | Ein oder mehrere kommagetrennte Wörter, z. B. `Homeassist, Computer` |
| Spellbook | Optional; feste Zauberformeln statt Codewort + Freitext – siehe [Spellbooks](#spellbooks--feste-zauberformeln) |
| Spellbook-Modus | Nur relevant mit gewähltem Spellbook: "Fallback auf Codewort" prüft bei keinem Trigger-Treffer zusätzlich das Codewort, "Nur Spellbook" ignoriert alles andere |
| "Meintest du...?" bei unbekanntem Namen | Fragt bei einem nicht auflösbaren Bereichs-/Gerätenamen aktiv nach statt nur den Assist-Fehler zu melden |
| Quittier-Phrase(n) | Ein oder mehrere kommagetrennte Phrasen, die einen aktiven Hinweis-Alarm quittieren (wie der Quittieren-Button), z. B. `Alarm quittieren` |
| Sprachassistent | Optional; leer = der eingebaute `conversation.home_assistant`-Agent, **nicht** zwangsläufig dein unter Einstellungen → Sprachassistenten bevorzugter |
| Assist-Antwort zurückschicken | Standardmäßig **aus** – siehe [Assist-Bridge](#assist-bridge--sprachbefehle-aus-dem-chat-heraus) |
| Antwort laut aussprechen | Spricht Assists Antwort (bzw. eine Spellbook-Catchphrase) über die TTS-Vorschau des Geräts in der Stimme des aktiven Charakters |
| KI-Bereinigung aktivieren | Optional: lässt eine `ai_task`-Entität (z. B. ein lokales Ollama-Modell) den Befehl vor dem Senden an Assist umformulieren – siehe [Zweistufige Befehls-Bereinigung](#zweistufige-befehls-bereinigung-optional) |
| KI-Task-Entität | Welche `ai_task`-Entität für die Bereinigung genutzt wird |
| Vacuum-Aktion aktivieren | Standardmäßig **aus**; erlaubt Spellbook-Befehlen mit `operation: vacuum.clean_area` echtes, raumgenaues Saugen auszulösen – siehe [Vacuum-Area-Resolver](#vacuum-area-resolver) |

Das Abfrageintervall ist **pro Gerät** (Config Entry) gespeichert – hast du
mehrere CODE27-Geräte, hat jedes seine eigenen Optionen.

### Services

Alle Services zielen über `config_entry_id` auf ein bestehendes CODE27-Gerät und
stehen für einen eigenständigen Spellbooker oder einen Satelliten nicht zur Verfügung.

| Service | Beschreibung |
|---|---|
| `hase.create_alarm` | Neuen Alarm anlegen (Uhrzeit, Wochentage, Label, aktiviert) |
| `hase.update_alarm` | Vorhandenen Alarm anhand seiner `alarm_id` ändern |
| `hase.delete_alarm` | Vorhandenen Alarm anhand seiner `alarm_id` löschen |
| `hase.speak` | Text über die TTS-Vorschau des aktiven Charakters ausgeben |
| `hase.send_chat_message` | Schickt einen Text in den Chat des Charakters, als hättest du ihn selbst getippt |
| `hase.notify` | Lässt die LEDs in einer Farbe blinken, bis quittiert – für Alarme aus z. B. Node-RED. Felder: `message` (nur geloggt, nicht gesprochen), `rgb_color` (Standard Rot) |
| `hase.notify_clear` | Beendet das Blinken programmatisch (statt über den Button) |

### Events

Auch diese Events feuern nur für ein CODE27-Gerät (Chat mit dessen Charakter), nicht
für einen eigenständigen Spellbooker oder einen Satelliten.

| Event | Payload | Wann |
|---|---|---|
| `hase_user_message` | `text`, `chat_id`, `slot`, `created_at`, `config_entry_id` | Bei jeder neuen Nachricht, die du dem Charakter schreibst |
| `hase_assistant_message` | wie oben | Bei jeder neuen Antwort des Charakters |

Damit lassen sich eigene Automationen bauen, unabhängig von der eingebauten
Bridge:

```yaml
alias: "CODE27: eigene Automation auf Zuruf"
trigger:
  - platform: event
    event_type: hase_user_message
condition:
  - condition: template
    value_template: "{{ 'gute nacht' in trigger.event.data.text | lower }}"
action:
  - service: script.gute_nacht_routine
```

### Assist-Bridge – Sprachbefehle aus dem Chat heraus

CODE27 selbst hat (Stand heute) **kein** Function-Calling/Tool-System, mit dem
der Charakter direkt Geräte schalten könnte – im Web-Bundle der Geräte-UI
findet sich dafür keine Schnittstelle. Was das Gerät aber sehr wohl liefert,
ist eine saubere Chat-History, in der jede Nachricht klar nach
`role: "user"` bzw. `"assistant"` getrennt ist – darauf baut die Bridge auf.

Statt Formulierungen selbst per Automation abzufangen, reicht die Bridge
alles nach dem Codewort **1:1 an Home Assistant Assist** weiter
(`conversation.async_converse`) – also praktisch alles, was dein Assist
sonst auch versteht, nicht nur Lichter.

Jeder Schritt landet **live nachvollziehbar** im System-Log und als
Logbuch-Eintrag im Aktivität-Tab des Geräts:

```
Codewort erkannt, Befehl: „wie spät ist es?"
An Assist gesendet: „wie spät ist es?"
Assist-Antwort: „Es ist 12:51"
Antwort in Chat gesendet   (nur falls "Antwort zurückschicken" aktiv ist)
```

Aktiviere "Assist-Antwort zurückschicken" nur, wenn du willst, dass der
Charakter zusätzlich **noch einmal selbst reagiert** – Assists Antwort geht
dann als neue Chat-Nachricht rein und der Charakter antwortet eigenständig
darauf (kein 1:1-Vorlesen, es gibt keine API, um dem Charakter Worte "in den
Mund zu legen"). Standardmäßig aus, weil das sonst wie eine ungefragte
Doppel-Antwort wirkt.

#### Zweistufige Befehls-Bereinigung (optional)

Ein Charakter wird nicht wie ein Lautsprecher angesprochen, sondern im
Rollenspiel-Ton – z. B. „Du, sag mal, würdest du Home Assistant vielleicht
fragen, ob er eventuell das Licht am Schreibtisch auf 80 Prozent dimmen
würde, wenn er so nett ist?" statt einem knappen Befehl. Assists eigene NLU
erwartet aber eher direkte Formulierungen und scheitert an so viel
Drumherum. Die Bridge räumt deshalb in zwei Stufen auf, bevor der Text an
Assist geht:

1. **Stufe 1 – Regex-Filter (immer aktiv, kostenlos):** Entfernt gängige
   Floskeln wie „kannst du bitte…", „würdest du… fragen, ob…", „…wenn er so
   nett ist" per regulärem Ausdruck. Läuft lokal in Python, keine
   Konfiguration nötig, kein Modell erforderlich.
2. **Stufe 2 – KI-Bereinigung (optional, per `ai_task`):** Für Fälle, die
   Stufe 1 nicht vollständig fängt, kann zusätzlich eine `ai_task`-Entität
   (z. B. ein lokales Ollama-Modell) den verbleibenden Text auf einen kurzen
   Befehl im Stil "Licht Schreibtisch 80 Prozent" reduzieren. Fällt die
   KI-Anfrage aus irgendeinem Grund aus (Modell nicht erreichbar, Fehler),
   wird einfach der Text aus Stufe 1 weiterverwendet – die Bridge bricht nie
   deswegen ab.

Beide Stufen werden mit im Aktivität-Log protokolliert:

```
Codewort erkannt, Befehl: „du, sag mal, würdest du vielleicht fragen, ob er
eventuell die Umgebungsfarbe auf 80 Prozent dimmen würde, wenn er so nett ist?"
Floskeln entfernt: „die Umgebungsfarbe auf 80 Prozent dimmen würde"
KI-Bereinigung: „Umgebungsfarbe 80 Prozent"
An Assist gesendet: „Umgebungsfarbe 80 Prozent"
Assist-Antwort: „Helligkeit eingestellt"
```

**Hardware-Anforderungen für Stufe 2:** Ein sehr kleines Modell reicht völlig
– getestet mit `qwen3:0.6b` über den lokalen Ollama-Add-on, **CPU-only**
(kein GPU-Passthrough), auf einem gebrauchten ThinkPad E14 Gen 2 (i7-1165G7,
32 GB RAM) als eigene HA-Instanz. Die CPU-Auslastung geht dabei kurz auf
knapp 60 % während der Inferenz, sonst Leerlauf – für den seltenen Aufruf
eines einzelnen kurzen Befehls also problemlos auf schwacher Hardware
machbar, kein dediziertes KI-Rechenzentrum nötig.

✅ End-to-end gegen ein echtes Gerät getestet und funktionsfähig.

#### Bewährte Sprachmuster (Assist-Grammatik)

Assist versteht nicht jede naheliegende Formulierung gleich gut – diese
Muster wurden gegen eine echte Produktivinstanz durchgetestet und sind
bestätigt zuverlässig. Nicht die einzig möglichen Formulierungen, aber die,
auf die man sich verlassen kann (auch fürs Spellbook, siehe
`spellbooks/potter_verse.yaml`).

**Licht (pro Bereich):**

| Aktion | Formulierung |
|---|---|
| An/Aus | „Licht {Bereich} einschalten" / „Licht {Bereich} ausschalten" |
| Helligkeit | „Licht {Bereich} auf {Zahl} Prozent stellen" |
| Farbe/Farbtemperatur | „Licht {Bereich} auf {Farbe} stellen" / „… auf warmweiß stellen" |

⚠️ Das bloße „{Bereich} an" (ohne das Wort „Licht") funktioniert **nicht
zuverlässig** – manchmal klappt es, oft kommt „existiert nicht" oder „das
habe ich nicht verstanden", ohne erkennbares Muster wann welches. Immer das
Domain-Wort „Licht" voranstellen.

**Musik (pro Bereich, Media-Player):**

| Aktion | Formulierung |
|---|---|
| Pause | „Musik {Bereich} pausieren" |
| Fortsetzen | „Musik {Bereich} fortsetzen" |
| Lauter/Leiser | „Musik {Bereich} lauter" / „Musik {Bereich} leiser" |
| Nächster Titel | „nächster Titel im {Bereich}" (oder „nächstes Lied im {Bereich}") |
| Vorheriger Titel | „vorheriger Titel im {Bereich}" |

Auffällig: Play/Pause/Lautstärke folgen dem Muster „Musik {Bereich} Verb"
(Bereich vor dem Verb), Titel-Wechsel dagegen „Verb-Phrase im {Bereich}"
(Bereich danach, mit „im") – zwei unterschiedliche Satzmuster für
unterschiedliche Assist-Intents, nicht austauschbar.

❌ Stummschalten („Musik {Bereich} stummschalten", „stummschalten im
{Bereich}", „Musik {Bereich} stumm") hat in mehreren Versuchen **nicht**
funktioniert – noch keine passende Formulierung gefunden.

**Temperatur (pro Bereich):**

| Aktion | Formulierung |
|---|---|
| Abfrage | „Temperatur {Bereich}" oder „wie warm ist {Bereich}" |

Voraussetzung: Der Bereich braucht eine gesetzte `temperature_entity_id`
(Home Assistant → Bereich bearbeiten → "Zugehörige Sensoren") – ein reines
"Sensor liegt im Bereich" reicht nicht aus, Assist braucht die explizite
Bereichs-Verknüpfung.

❌ Luftfeuchtigkeit hat **kein** funktionierendes eingebautes Sprachmuster
in Home Assistant gefunden – mehrere Formulierungen getestet, alle
gescheitert. Vermutlich eine echte Lücke in Assists Standard-Intents, nicht
an dieser Bridge reparierbar.

**Generell unzuverlässig:** Einzelne Geräte beim Eigennamen ansprechen (statt
über den Bereich) funktioniert praktisch nie zuverlässig, selbst mit exakt
passendem Entitätsnamen. Bereichsbasierte Befehle sind der verlässliche Weg.

#### Spellbooks – feste Zauberformeln

Statt jedes Mal "Codewort, Licht Wohnzimmer einschalten" zu sagen, kann ein
**Spellbook** feste Trigger-Sätze ("Zauberformeln") direkt auf einen
Assist-Befehl abbilden – kein Codewort nötig, dafür mit eigener
Catchphrase-Antwort im Charakter-Ton:

```
Lumos Wohnzimmer            -> "Licht Wohnzimmer einschalten"   "Lumos."
Nox Wohnzimmer               -> "Licht Wohnzimmer ausschalten"   "Nox."
Computer Licht Küche an      -> "Licht Küche einschalten"        "Bestätigt."
```

Drei gemeinsame HASE-Spellbooks liegen bei (`custom_components/hase/spellbooks/`):
Potterverse, Middle-earth und Demo. Eigene Spellbooks als YAML-Datei in
`<config>/hase_spellbooks/` ablegen – werden automatisch erkannt, kein
Neustart nötig (YAML wird bei jeder Nachricht neu eingelesen, nur an Python-
Dateien geänderte Fixes brauchen einen echten Neustart). Dieselben Bücher
werden auch von einem eigenständigen Spellbooker und von Satelliten benutzt
(siehe [Agents: Spellbooker & Satelliten](#agents-spellbooker--satelliten)),
nicht nur von der CODE27-Bridge.

**Auswahl bei mehreren Kandidaten:** Passt eine gesprochene/getippte
Formulierung nicht exakt, sondern nur ungefähr auf mehrere Trigger
(Tippfehler, Verhören), gewinnt nicht der zuerst in der Datei stehende
Trigger, sondern der mit der tatsächlich höchsten Übereinstimmung.
Bewirken zwei fast gleich gut passende Kandidaten das **Gegenteil** (an/aus,
auf/zu, lauter/leiser, ver-/entriegeln), rät die Bridge nicht, sondern fragt
aktiv nach, statt versehentlich das Gegenteil des Gemeinten zu tun:

```
"Meinst du 'löscht die leuchtfeuer von Gondor' oder
'entzündet die leuchtfeuer von Gondor'?"
```

Die nächste Antwort wird gegen beide Formulierungen abgeglichen – ein klar
erkennbares Wort daraus reicht (z. B. nur "lösche" oder nur "entzünde").

**Gemeinsame semantische Befehle:** Bücher verwenden `operation`, `target` und `arguments`, optional `output`. Typisierte Befehle laufen durch den gemeinsamen Executor. Reine Output-Befehle gehen bei CODE27 an Assist und sind für Satellite-Ausführung unsichtbar. Sind beide vorhanden, gilt ausschließlich die Operation; Fehler führen niemals zum Output-Fallback. Frontend-Marker gehören nicht ins YAML.

Globale Beleuchtung verwendet `light.set_power`, `target: {kind: all_allowed}` und `arguments: {"on": true}` (oder `false`). Registry-/Exposure-Prüfung und Bereichspolicy bestimmen die erlaubten Ziele. Sauger verwenden `vacuum.clean_area` mit einem Bereichsziel. Details: [Etappe 8](docs/spellbook-engine/etappe-8-unified-spellbooks.md).

#### Vacuum-Area-Resolver

Ein Spellbook-Eintrag mit `operation: vacuum.clean_area` (z. B. "Reinigt die Hallen
von {area}") löst den gesprochenen Bereich auf einen Saugroboter-Raum auf und
startet die Reinigung nur dieses einen Raums. Über die CODE27 Assist-Bridge
standardmäßig **aus** (Option "Vacuum-Aktion aktivieren" – ein echter physischer
Seiteneffekt, kein reiner Textbefehl); ein Spellbooker oder Satellit mit einem
entsprechenden Buch-Eintrag hat dagegen keinen eigenen Umschalter dafür.

Zweistufig, in dieser Reihenfolge:

1. **Nativer Home-Assistant-Weg** (`vacuum.clean_area`, ab HA 2026.3):
   markenunabhängig, funktioniert mit jedem Saugroboter, dessen Integration
   dieses Feature unterstützt (bestätigt: Roborock). Voraussetzung: einmalig
   pro Sauger in dessen Entity-Einstellungen ("Zuordnung von
   Staubsauger-Abschnitten zu Bereichen") jeden Raum einem
   Home-Assistant-Bereich zuordnen. Danach braucht die Bridge weder eine
   herstellerspezifische Karten-Abfrage noch eigene Raum-Logik.
2. **Marken-Fallback** (`vacuum_providers/*.yaml`): nur für Sauger ohne
   native Unterstützung oder ohne gesetztes Mapping. Deklarativ, genau wie
   die Spellbooks selbst – eigene Provider-Dateien in
   `<config>/hase_vacuum_providers/`. `roborock.yaml` liegt bei und ist
   live gegen ein echtes Gerät verifiziert; `dreame_vacuum.yaml` und
   `xiaomi_miio.yaml` sind als Ausgangspunkt vorbereitet, aber **nicht**
   gegen ein echtes Gerät getestet (bei Xiaomi Miio fehlt Stand heute sogar
   ein API-Weg, die Raumnamen überhaupt automatisch zu ermitteln).

Kein Treffer, ein mehrdeutiger Treffer (z. B. mehrere Sauger mit
gleichnamigem Raum) oder ein Fehler beim Ansprechen des Geräts wird **nie
geraten** – die Bridge loggt den Grund und tut nichts, statt versehentlich
den falschen Raum zu reinigen.

### Dashboard-Karte (code27-card)

Eine Lovelace-Custom-Card mit CODE27-Branding, reaktivem Glow in der
aktuellen Umgebungsfarbe, Chat und den wichtigsten Bedienelementen:

![Dashboard-Karte](docs/screenshots/07-dashboard-card.png)

#### Installation der Karte

1. Kopiere `www/hase/code27-card.js` (und optional ein eigenes Portrait-Bild) in
   den `www`-Ordner deiner Home-Assistant-Konfiguration, z. B.
   `<config>/www/hase/code27-card.js`.
2. **Home Assistant neu starten** (der `www`-Ordner wird nur beim Start als
   `/local/...` verfügbar gemacht – wurde er gerade erst angelegt, greift
   das erst nach einem Neustart).
3. **Einstellungen → Dashboards → Ressourcen (⋮-Menü) → Ressource
   hinzufügen**: URL `/local/hase/code27-card.js`, Typ "JavaScript-Modul".
4. Karte auf einem Dashboard hinzufügen, Typ `custom:code27-card`.

#### Konfiguration

```yaml
type: custom:code27-card
device_prefix: code27_192_168_32_197   # gemeinsames Präfix aller Entity-IDs dieses Geräts
name: CODE27                            # optional, Standard "CODE27"
config_entry_id: 01M20777KPCKCNGQ3F45SWX1CG   # nötig für den Chat-Senden-Button
portrait_image: /local/hase/portrait.png    # optional, eigenes Bild
history_count: 8                        # wie viele Chat-Nachrichten angezeigt werden
show_chips: true                        # Diagnose-Chips (Sprache/Zeitzone/Alarme/Notizen)
show_feed: true                         # Live-Feed der Assist-Bridge-Aktivität
```

`device_prefix` ist der gemeinsame Teil aller Entity-IDs dieses Geräts (bei
`sensor.code27_192_168_32_197_notizen` also `code27_192_168_32_197`) – damit
findet die Karte alle zugehörigen Entitäten automatisch, ohne jede einzeln
eintragen zu müssen. `config_entry_id` findest du in der URL, wenn du in
**Einstellungen → Geräte & Dienste** auf die Integration klickst.

Die Karte zeigt/steuert: Umgebungslicht (Farbe, Effekt, Helligkeit),
Musik (Play/Pause/Skip, Lautstärke, Quelle, Fortschrittsbalken,
Musikmodus-Badge), Lichtszene- und Charakter-Auswahl, Alarme (Liste mit
Umschaltern), offene Notizen (mit Löschen-Button), Chat-Verlauf mit
Eingabefeld, den Hinweis-Alarm-Banner samt Quittieren, Diagnose-Chips und
den Live-Feed der Assist-Bridge. Die Karte folgt dabei automatisch deiner
Home-Assistant-Oberflächensprache (Deutsch bei Deutsch, sonst Englisch).

**Wichtig:** Nach jeder Änderung an `code27-card.js` merkt sich der Browser
die alte Version. Entweder die Ressourcen-URL mit einem neuen
`?v=`-Parameter versehen, oder Strg+Shift+R (harter Reload).

### Bewusste Design-Entscheidungen

- **Kein Reset/Character-Editor**: Das Zurücksetzen eines Charakters oder das
  Bearbeiten von Persönlichkeits-Prompts wurde bewusst *nicht* als Entität
  angelegt – das sind folgenreiche, gerätespezifische Aktionen, die weiterhin
  über die Geräte-UI laufen sollten.
- **Kein Persönlichkeits-Text in HA**: Der `character_prompt` (die
  ausführliche Charakterbeschreibung) wird nirgends in Home Assistant
  angezeigt oder gespeichert – nur der Name des aktiven Charakters.
- **Pairing statt Cookie-Klau**: Home Assistant bekommt beim Einrichten ein
  eigenes Pairing (wie ein zusätzliches "Gerät" in der CODE27-App), anstatt
  sich die Browser-Session zu leihen.
- **Kein separater LED-Master-Schalter mehr**: Anfangs gab es zusätzlich zum
  Umgebungsfarbe-Licht einen eigenen An/Aus-Schalter für dieselben LEDs –
  zwei Bedienelemente für ein Ding ist verwirrend, wurde entfernt.
- **Kein "Lichtszene wiederherstellen"-Button**: Existierte kurzzeitig, wurde
  wieder entfernt – das Verhalten (zurück zum "applied" Projekt) ist nicht
  vom Master-Switch-Status unterscheidbar, wenn das gespeicherte Projekt im
  Normal-Modus leer ist. Für "zu einer Szene zurück" die Lichtszene-Auswahl
  benutzen, die per `apply` sauber funktioniert.

### Bekannte Grenzen

Alles unten ist bewusst *nicht* gebaut, weil CODE27 es nicht anbietet – nicht
weil wir es übersehen hätten:

- **Kein geräteweites Lautstärke-Feature** außerhalb der Musikwiedergabe
  (Stimme/Sound-Effekte) – existiert nirgends in der API, auch nicht hinter
  Admin-Rechten.
- **"Spaces"** sind nur direkt am Gerätebildschirm wählbar, keine API dafür.
- **Kein Function-Calling** im Chat des Charakters (deshalb die
  Assist-Bridge als Workaround, siehe oben).
- **Portrait in der Karte** ist ein generisches Stock-Bild aus dem
  App-Bundle, nicht dein tatsächlich konfigurierter Charakter – dafür gibt's
  keine abrufbare Live-Renderansicht über die API.
- Hinter Admin-Rechten (die das lokale Pairing nicht bekommt) steckt u. a.
  ein **"Proactive"-System** für automatisches, präsenzbasiertes Ansprechen
  (Zeitfenster, Idle-Recovery, Kamera-Trigger für Lächeln/Näherung/Geste,
  Ruhezeiten) – spannend für später, falls CODE27 das mal für lokale
  Pairings öffnet.

### Technischer Hintergrund

Das Gerät verlangt für alle Endpunkte außer den drei Auth-Routen eine
HMAC-SHA256-Signatur (`X-Local-Sig` u. a. Header), abgeleitet aus einem beim
Pairing vergebenen lokalen Secret – dasselbe Verfahren, das die Web-UI des
Geräts selbst verwendet (aus deren JS-Bundle nachvollzogen, siehe
`custom_components/hase/providers/code27/api.py`). Der WebSocket-Kanal
(`/v1/ws/channel`, siehe `websocket_client.py`) nutzt dasselbe Verfahren,
nur als Query-Parameter statt Header (ein WebSocket-Handshake kann keine
individuellen Header setzen) – darüber kommen Musik- und Licht-Seiten-Status
praktisch in Echtzeit rein, ganz ohne Polling. Für Chat-Nachrichten gibt es
auf diesem Kanal **kein** Push-Event (nicht mal die eigene Web-UI des Geräts
bekommt da eins) – die werden separat alle 3 Sekunden abgefragt, deutlich
schneller als der Rest (15s Standard-Intervall).

Alles davon wurde live gegen ein echtes CODE27-Gerät verifiziert, nicht nur
aus dem JS-Bundle abgeleitet.

## Support & Issues

Für Support, Feature-Wünsche oder Bug-Reports bitte im GitHub-Repository
vorbeischauen. Beiträge und Feedback aus der Community sind willkommen!

Wenn dir das Projekt geholfen hat und du dem Maintainer einen Kaffee
spendieren möchtest:

**[☕ Buy Me a Coffee](https://buymeacoffee.com/constablew)**
