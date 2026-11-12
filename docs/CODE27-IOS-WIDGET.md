# CODE27 – iOS-Widget: Analyse und Implementierungsplan

Stand: 13.09.2026. Status: Analyse abgeschlossen; Zielwidget noch nicht implementiert.
Die bestehende Integration und Card bleiben unverändert.

## Machbarkeit

Das gewünschte große Widget lässt sich im aktuellen Home Assistant Companion
Custom Widget nicht vollständig konfigurieren. Es rendert eine gespeicherte Liste
von Entity-Kacheln. Bedingte Chat-/Musik-Bereiche, ein großes Branding-Bild,
Character-Portraits mit aktivem Ring und ein Lautstärke-Slider sind dort nicht
als konfigurierbare Elemente vorgesehen. Eine Lovelace-JavaScript-Card kann nicht
in dieses native Widget geladen werden.

Ein Template-Sensor würde diese Layoutgrenze nicht beheben. Er würde außerdem
zusätzliche Entities einführen, obwohl die relevanten Daten bereits existieren.
Ein HTML-/Screenshot-/Scriptable-Ersatz wird hier nicht als Companion-Widget ausgegeben.

Die offizielle Dokumentation und der aktuelle Entwicklungszweig unterscheiden
sich bei Aktionen: Die Dokumentation nennt unter anderem Navigation und Skripte;
der geprüfte Quellcode unterstützt zusätzlich `performAction` mit Payload sowie
getrennte Aktionen für Icon und restliche Kachel. Das ist kein Nachweis, dass die
auf dem iPhone installierte Version diese neueren Funktionen bereits enthält.

## Bestehende Daten und Aktionen

Die folgenden Schlüssel sind `translation_key`-Werte, **keine fest zugesicherten
Entity-IDs**. IDs können durch Sprache, Umbenennung und Geräteanzahl abweichen.
Die Card löst sie über Gerätezugehörigkeit und Übersetzungsschlüssel auf;
Portraits zusätzlich über `slot_number`. In der Companion-Konfiguration müssen
die tatsächlich vorhandenen Entities desselben CODE27-Geräts gewählt werden.

| Zweck | Vorhandene Quelle | Verwendung / Einschränkung |
| --- | --- | --- |
| Auto-Switch | `binary_sensor`, `music_mode_active` | Ausschließlich `state == on` bedeutet Musikansicht. |
| Online | `sensor`, `build_type` | Card: Entity vorhanden und State ungleich `unavailable`; auch `unknown` zählt in dieser bestehenden Anzeige als online. Kein neuer Ping. |
| Aktueller Character | `sensor`, `active_character`; `select`, `character_slot` | Sensorname bzw. ausgewählte Option aus vorhandenen Slots. |
| Alle Characters | `sensor`, `character_overview`, Attribut `characters` | `slot_number`, `name`, `active`, letzte Nachricht und Rolle. Keine fest erfundene Slot-Zuordnung für Lilya/Mara/Ren/Ella/Ginny. |
| Portraits | `image`, `character_portrait`, Attribut `slot_number` | Vorhandene HA-Bild-Entities verwenden; keine CODE27-Bild-API vom Widget aus. |
| Branding | `custom_components/hase/cards/portrait.png` | Visuell geprüft: Hase mit Buch, kein Character-Portrait. HA-Pfad `/hase-cards/portrait.png`. Das Motiv unterscheidet sich vom Mockup. |
| Chatvorschau | `sensor`, `last_assistant_message` | State bis 255 Zeichen; `full_text`, `created_at`, `chat_id` vorhanden. Kein neues Filtern der gesamten History. |
| Track / Wiedergabe | `media_player`, `music` | `media_title`, State, `media_duration`, `media_position`, `volume_level`, `source`. Kein Albumcover in dieser Implementierung. |
| Hinweise | `binary_sensor`, `notification_active` | Bei `on` zeigt die Card `message`. Bestehender Hinweis, kein neuer Alarmdetektor. |
| Quittieren | `button`, `acknowledge_notification` | `button.press`. |
| Wecker | `sensor`, `active_alarms` | Anzahl aktivierter Wecker, **kein** klingelnder oder kritischer Alarmzustand. Keine Alarmüberlagerung daraus ableiten. |
| IP | Card-Konfiguration `ip`, sonst Präfixauswertung | Kein dedizierter IP-Sensor nötig; im kompakten Widget weglassen. |

Vorhandene HA-Serviceaufrufe:

| Aktion | Service | Daten |
| --- | --- | --- |
| Character wählen | `select.select_option` | `entity_id` des Character-Selects und exakte vorhandene `option`; gleiche Namens-/Slotauflösung wie Card respektieren. |
| Abspielen | `media_player.media_play` | `entity_id` des CODE27-Mediaplayers. |
| Pausieren | `media_player.media_pause` | Dieselbe Entity. |
| Zurück / Weiter | `media_player.media_previous_track` / `media_player.media_next_track` | Dieselbe Entity. Beide Features sind bereits implementiert. |
| Lautstärke | `media_player.volume_set` | Dieselbe Entity, `volume_level` von 0 bis 1. |
| Chat senden | `hase.send_chat_message` | Bestehende Card übernimmt Eingabe und Serviceaufruf; kein Texteingabefeld im Widget. |

Die Card entscheidet bei Play/Pause anhand des Mediaplayer-States zwischen den
zwei Services. Für einen älteren Companion-Client kann ein schlankes HA-Skript
diese Aktion verpacken. Es darf keine Activity-Erkennung oder Moduszustände speichern.

## Exakte Auto-Switch- und Idle-Semantik

Quelle: `custom_components/hase/cards/code27-card.js`, `_update()` und `_applyChatVisibility()`;
Backend: `providers/code27/binary_sensor.py`, `Code27MusicModeBinarySensor.is_on`.

Der vorhandene Binärsensor stellt bereits `music_page.is_open` bereit.
Die Card liest nur diesen Sensor, nicht Wiedergabestatus, Titel, Zeit seit der
letzten Nachricht oder eine eigene Activity-Heuristik.

| Musikmodus-Sensor | Mediaplayer | Bestehender dynamischer Bereich |
| --- | --- | --- |
| `on` | `playing` | Musik |
| `on` | `paused` | Musik |
| `on` | `idle` | Musik |
| `off` | beliebig | Chat |
| `unknown`, `unavailable`, fehlend | beliebig | Chat |

Die Card hat zusätzlich eine lokale Chat-Aufklapppräferenz (`localStorage`)
und `show_chat: false`. Musik blendet Chat unabhängig davon aus. Diese lokale
Präferenz ist kein HA-Sensor und steht einer nativen Widget-Erweiterung nicht
zur Verfügung. Sie darf nicht zu einer neuen serverseitigen Moduslogik werden.

Musikbefehle können bei geschlossener Musikseite mit `MODE_INACTIVE` scheitern.
Die Integration kann diese Geräteseite nach jetzigem Stand nicht remote öffnen.
Ein Tap „Musik öffnen“ öffnet eine HA-Oberfläche, nicht den Musikmodus am Gerät.

## Navigation

Die untersuchte Card enthält Chat und Musik als Bereiche derselben Card;
es gibt keine implementierten getrennten Chat-/Musik-Routen oder URL-Parameter,
die diese Bereiche erzwingen. Der bestehende Dashboard-/View-Pfad ist in den
untersuchten Quell- und Dokumentationsdateien nicht festgelegt.

Vor Einrichtung den tatsächlichen HA-Pfad der bestehenden CODE27-Card aus der
laufenden Oberfläche übernehmen und in Companion als Navigationsziel wählen.
Keine vermuteten `/code27/chat`- oder `/code27/music`-Links erzeugen.
Bei Wiederverwendung dieser einen View entscheidet weiterhin die bestehende Card
über ihren sichtbaren Bereich. Ein als „Chat öffnen“ beschrifteter Link könnte
bei aktivem Musikmodus deshalb Musik zeigen; für die Kachel-Alternative ist
„CODE27 öffnen“ die ehrliche Beschriftung.

## Geprüfte Companion-Implementierung

Öffentliche Primärquellen, am 13.09.2026 gelesen:

- [Offizielle iOS-Widget-Dokumentation](https://companion.home-assistant.io/docs/integrations/ios-widgets/).
- Entwicklungsstand `bd46befc8de44226044bd03285df7d98a8828e1b` von `home-assistant/iOS`:
  [MagicItem](https://github.com/home-assistant/iOS/blob/bd46befc8de44226044bd03285df7d98a8828e1b/Sources/Shared/MagicItem/MagicItem.swift)
  enthält feste Texte, Icon-/Text-/Hintergrundfarben und Aktionen; keinen
  Bild- oder bedingten Layoutparameter.
- [WidgetBasicViewModel](https://github.com/home-assistant/iOS/blob/bd46befc8de44226044bd03285df7d98a8828e1b/Sources/Extensions/Widgets/Common/WidgetBasicViewModel.swift):
  Titel, Untertitel, Material-Icon und Kachelfarben, kein frei konfigurierbares Bildlayout.
- [Customization-UI](https://github.com/home-assistant/iOS/blob/bd46befc8de44226044bd03285df7d98a8828e1b/Sources/App/Settings/MagicItem/Edit/MagicItemCustomizationView.swift):
  Icon, Text, Farben und Aktionen; kein Template- oder Conditional-Editor.
- [TimelineProvider](https://github.com/home-assistant/iOS/blob/main/Sources/Extensions/Widgets/Custom/WidgetCustomTimelineProvider.swift):
  gelesener Blob `355d1c89554089965e0dc1e2e28b69e55cb0abb7`; lädt die konfigurierte
  Item-Liste, begrenzt sie nach Widgetgröße und holt Entity-States. Vorgesehenes
  Ablaufintervall 15 Minuten. Attribute werden nicht als frei konfigurierbare
  Titel-/Lautstärke-/Chat-Layouts angeboten.
- [CustomWidget](https://github.com/home-assistant/iOS/blob/main/Sources/Shared/Widget/CustomWidget.swift):
  gelesener Blob `b61dbd5eae78d09d11d290913c0ed53b6c8584e6`; gespeicherte Item-Liste
  und Bestätigungszustände. Dessen `idle` bezeichnet die Tap-Bestätigung und hat
  nichts mit CODE27-Musikwiedergabe zu tun.

Aktualisierungen unterliegen iOS. Auch `update_widgets` per stillem Push garantiert
keine sofortige Aktualisierung. Keine Echtzeitanzeige, interpolierte Track-Uhr
oder sofortige Umschaltung versprechen. Aktualisierungszeit sichtbar lassen.

## Konkreter Implementierungsplan

### A – Native Kachel-Alternative (wesentliche Abweichung vom Ziel)

1. Installierte Companion-Version und echten Dashboard-Pfad feststellen; vorhandene
   Geräte-Entities wählen. Kein Entity-Präfix raten und keine Integration ändern.
2. Großes Custom Widget in den Companion-Einstellungen erstellen. Dunkler
   Hintergrund und violette Icon-Farbe, weiße Schrift. Farben sind Widget-
   Konfiguration; kein Lovelace-Theme wird automatisch übertragen.
3. Primär vier Kacheln verwenden: „CODE27 öffnen“ mit `build_type` als Quelle und
   expliziter Navigation; „Character“ mit `character_slot`; „Letzte Antwort“ mit
   `last_assistant_message`; „Musik“ mit dem vorhandenen Mediaplayer. Den
   `build_type`-State nicht als vermeintliche Online-Anzeige umbenennen: nativ
   wird der Build-Wert bzw. `unavailable` angezeigt, kein selbst berechnetes Label.
4. Optional Hinweis- und Musikmodus-Entity hinzufügen, soweit die tatsächliche
   Widgetgröße lesbar bleibt. Kacheln bleiben fest sichtbar; das ist kein Auto-Switch.
5. Character-Kachel öffnet den vorhandenen Select-Dialog, Musik-Kachel den
   Mediaplayer-Dialog mit vorhandenen Controls und Lautstärke. Alternativ
   separate Previous-/Next-/Play-/Pause-Aktionen über `performAction`, falls die
   installierte App dies unterstützt; sonst kleine HA-Skripte als Service-Wrapper.
   Explizite Tap-Aktionen konfigurieren, da Default-Verhalten versionsabhängig ist.
6. Keine inaktiven Musikcontrols mit einem automatischen Öffnen der Geräteseite
   kaschieren. Für die primäre Alternative den HA-Dialog nutzen. Dort stehen
   aktuelle Werte und die bestehenden Fehlerhinweise zur Verfügung.
7. Auf dem iPhone Navigation, Serverauswahl, Characterwechsel, Musikaktionen,
   Offline-Verhalten und Aktualisierungszeit prüfen.

Diese Variante liefert weder Branding-Bild noch Portrait-Ringe noch wechselnde
Widget-Bereiche. Sie ist erst nach Auswahl dieser Abweichung umzusetzen und darf
nicht als vollständige Umsetzung des Mockups bezeichnet werden.

### B – Zielwidget mit nativer iOS-Erweiterung

1. Zielprojekt klären: Erweiterung der Companion-App erfordert deren Swift-/Xcode-
   Projekt und Build-/Installationsweg. Dieses Repository enthält eine HA-
   Integration und Lovelace-Card, keine iOS-App. HA kann keinen zusätzlichen
   WidgetKit-Typ in die installierte Companion-App laden.
2. Reine SwiftUI-Darstellung für `systemLarge` ergänzen. Authentifizierung und
   HA-Verbindung der Companion-App wiederverwenden; keine CODE27-Endpunkte.
3. Konfiguration speichert nur bestehende Entity-Referenzen, Branding-Ressource
   und verifizierte HA-Navigationsziele. Keine neue Modus-Entity und kein Timer
   zur Ableitung von Idle.
4. Beim Rendern ausschließlich den vorhandenen `music_mode_active`-State
   auswerten (`on` = Musik, sonst Chat), entsprechend obiger Wahrheitstabelle.
   Das ist eine Darstellung des vorhandenen Status, keine neue State-Machine.
5. Hase als MainPicture, bestehende Image-Entities nach Gerät und Slot für
   Portraits; aktiven Ring aus `characters[].active`. Namen aus HA übernehmen.
   Letzte Assistant-Nachricht und Zeit aus dem bestehenden Nachrichtensensor;
   fehlende Daten neutral anzeigen und keinen Chat-Verlauf nachbauen.
6. Native Taps rufen nur bestehende HA-Services auf. Lautstärke über den HA-
   Mediaplayer-Dialog öffnen, wenn kein geeigneter nativer Regler verfügbar ist.
   Keine simulierten Slider und keine optimistische Wiedergabe-State-Machine.
7. Hinweis aus `notification_active` übernehmen. Aktivierte Wecker nicht als
   kritischen Alarm ausgeben. Online-Semantik der Card nicht still korrigieren.
8. Navigation bleibt an vorhandene Views gebunden. Getrennte, erzwungene
   Chat-/Musikziele würden eine gesonderte Erweiterung der bestehenden Navigation
   voraussetzen; bis dahin „CODE27 öffnen“ verwenden.
9. Widget-Timeline, Offline-/Cache-Anzeige und iPhone-Screenshots testen. Ein
   gleicher ausgewerteter Status führt zur gleichen Ansicht; zeitgleiche
   Aktualisierung mit der Live-Card kann iOS trotzdem nicht garantieren.

## Abnahme und Regressionsschutz

- Bestehende Card, Coordinator, Sensoren, Images und Services unverändert lassen.
- Vorhandene Entity-IDs auch bei Umbenennung, anderer Sprache und zwei Geräten prüfen.
- Alle Zeilen der Auto-Switch-Tabelle abdecken, besonders `on + idle` und
  `off + playing`; niemals den Mediaplayer als Modusquelle verwenden.
- Characterwechsel darf keine neue Slot-Zuordnung oder Portrait-API einführen.
- Chatvorschau bei leerem Text, langen Nachrichten und fehlendem Zeitstempel prüfen.
- Jeden Tap gegen tatsächliche HA-Serviceaufrufe bzw. echte Navigation prüfen.
- Bestehende Musikfehler müssen erkennbar bleiben; kein vorgetäuschter Erfolg.
- Sichtkontrolle auf einem echten großen iOS-Widget erforderlich. Hier wurde
  noch kein Widget auf einem iPhone eingerichtet oder getestet.

Die Analyse hat ausschließlich dieses Dokument hinzugefügt. Die funktionale
Umsetzung wartet auf die Wahl zwischen der reduzierten nativen Oberfläche und
dem zusätzlichen iOS-Projekt, weil die geforderte Hauptfunktion im aktuellen
Companion-Custom-Widget nicht verfügbar ist.
