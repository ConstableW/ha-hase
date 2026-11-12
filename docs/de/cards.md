🇩🇪 Deutsch | 🇬🇧 [English](../en/cards.md)

# HASEAMP (Dashboard-Card)

HASEAMP ist HASEs Winamp-inspirierte Musik-Card: `custom_components/hase/cards/haseamp-card.js`, von der Integration unter `/hase-cards/haseamp-card.js` ausgeliefert und automatisch geladen. Lege dazu eine Card vom Typ `custom:haseamp-card` an. Der Name gehört ausschließlich dieser Benutzeroberfläche: kein Backend-Modul, kein Service und keine Musikplattform.

Für Gerätebedienung und Charakter-Chat gibt es separat `custom:code27-card`. Sie wird wie HASEAMP von der Integration mitgeliefert und automatisch geladen (HASE: `/hase-cards/code27-card.js`, Standalone-Integration: `/code27-cards/code27-card.js`); eine manuelle Lovelace-Ressource ist nicht nötig. Die Card erkennt den Owner des gewählten Geräts. HASEAMP bleibt die zusätzliche Karte für Musikbedienung.

Konfigurationsschlüssel: `target` (die `config_entry_id` eines echten CODE27-Geräte-Entries unter `hase` oder `code27`; ein HASE-External-Binding ist kein Target), `name`, `provider` (welcher Frontend-Provider angesprochen wird; Standard ist der registrierte) sowie die Sichtbarkeitsflags `show_browser`, `show_queue`, `show_playlists`, `show_import`, `show_animation`, `show_diagnostics` sowie `follow_light` (Standard an; die Farben der Card folgen dem Umgebungslicht des Geräts).

Die Card ist providerneutral. Sie hält selbst keinen Queue- und keinen Playlist-Zustand (nur die zuletzt angeklickte Zeile), ruft nie direkt eine Geräte-API auf und bietet nur, was das Backend als unterstützt meldet: Transport, Seek, Lautstärke, Quelle, Queue-Modi und Medienbrowser werden über die Capability-Flags des Snapshots (`features.*`) freigeschaltet. Ein Backend ohne eine Funktion bekommt dadurch ein ehrlich als nicht verfügbar beschriftetes Bedienelement statt eines vorgetäuschten – und geräteabhängiger Text, etwa dass ein Stop emuliert ist, weil das Gerät keine STOP-Aktion kennt, wird aus diesen Flags abgeleitet statt in der Card fest verdrahtet.

Die Card ist ein Fenster mit vier Tabs (Queue, Bibliothek, Playlists, Import). Sie zeigt „MusicMode ist off!“, solange die Musik-Ansicht am Gerät geschlossen ist, kann Audiodateien und M3U-Playlists auf die Karte des Geräts importieren, Dateien und Ordner löschen, gespeicherte Playlists bearbeiten und exportieren und übernimmt ihre Farben vom Umgebungslicht des Geräts. Diese Funktionen und ihre Grenzen stehen in [CODE27 Music und die HASEAMP-Card](providers/code27.md#aufbau-musicmode-und-farben); wie alles andere werden sie nur angeboten, wenn das Backend des Providers sie umsetzt.

## Die Frontend-Provider-Grenze

Alles Provider-spezifische steht in einer markierten Tabelle im Quelltext der Card (`PROVIDER_TRANSPORTS`) und nirgendwo sonst:

| Eintrag | Bedeutung |
|---|---|
| `command` | der WebSocket-Befehl, der die Operationen trägt |
| `subscribe` | das Abonnement, das Snapshots pusht |
| `noDeviceReason` | der Reason-Code für „kein Gerät dieses Providers geladen" |
| `deviceLabel` | wie das Gerät dieses Providers in Benutzerhinweisen heißt |

Außerhalb dieser Tabelle bleibt die Card frei von Gerätevokabular: keine Geräte-Einheiten, Statusnamen, Pfade, Track-ID-Präfixe, Fehlercodes oder Namespaces. Zwei Contract-Tests erzwingen das auf dem kommentarbereinigten Quelltext (`test_card_code_stays_provider_neutral_outside_the_boundary` und `test_card_declares_its_provider_boundary_exactly_once` in `tests/test_code27_music.py`, dazu die Namespace-Prüfung in `tests/test_haseamp_frontend.mjs`) – Gerätelogik kann also nicht in die UI zurückwandern.

Das ist eine Frontend-Grenze, kein HASE-Vertrag: dahinter steht kein Core-Modul, kein Python-Interface und kein gemeinsames Musikmodell. Jeder Frontend-Provider definiert und implementiert seine eigene Musik-Schnittstelle – mit eigenem Namespace, eigenen Operationen, eigener Payload-Form und eigenen Gerätebesonderheiten. Ein Provider ohne Musikfunktionen registriert nichts und versorgt damit auch keine HASEAMP-Card. HASE Core bekommt nie eine Musikplattform, und kein Provider muss einen anderen nachahmen.

Der erste implementierte Frontend-Provider ist CODE27 – siehe [CODE27-Provider](providers/code27.md) für dessen Vertrag, Modulaufbau und Gerätebesonderheiten.
