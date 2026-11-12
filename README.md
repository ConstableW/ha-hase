<p align="center"><img width="600" alt="HASE – Home Assistant Spellbook Engine" src="docs/branding/hase-title.png" /></p>


🇩🇪 Deutsch | 🇬🇧 [English](README.en.md)

# HASE – Home Assistant Spellbook Engine

HASE ist die semantische Schicht zwischen dem, was ein Mensch oder Agent meint, und dem, was Home Assistant ausführt.

## Eine semantische Schicht. Mehrere Schnittstellen.

```text
                         Spellbooks
                mehrsprachige Absicht + Bedeutung
                              |
              Matching, Planung, Policy, Ausführung
                              |
          +-------------------+-------------------+
          |                   |                   |
   Assist / Conversation     MCP       CODE27 / weitere Frontends
                              |
                     Spellbook Studio
                    Authoring und Verwaltung
```

HASE definiert Befehle einmal als mehrsprachige **Spells**. Assist, MCP und CODE27 passen Ein- und Ausgabe an dieses Modell an; Spellbook Studio verwaltet es. Ein neuer Befehl wird als Spellbook-Eintrag verfasst, nicht als separate Frontend-Logik implementiert.

MCP ist daher nicht das Produkt, sondern ein Kanal. Ein herkömmlicher MCP-Server definiert Tools für ein Backend; HASE projiziert einen kontrollierten Teil seines vorhandenen semantischen Modells als Tools und führt Aufrufe über denselben Sicherheitspfad aus.

## Enthalten

- Eigenständiger Home-Assistant-Conversation-Agent ohne CODE27-Hardware.
- Aktionen, Status- und Mehrquellenabfragen, Combined Spells und reine Textantworten.
- Getrennte Bereichs-Policies für Aktionen und Abfragen, Assist Exposure und Expert-Gates.
- MCP-Frontend über Streamable HTTP mit Bridges pro Client.
- Spellbook Studio für Authoring, Validierung, Import/Export und Dry Runs.
- Optionaler CODE27-Companion-Provider für Geräteintegration, Charakter-Chat und Satelliten.

## CODE27-Ownership

HASE kann CODE27 selbst einrichten: Der darüber angelegte ConfigEntry und alle CODE27-Geräte/Entities gehören dann zu `domain=hase`. Alternativ kann [ha-code27](https://github.com/ConstableW/ha-code27) ein Gerät standalone unter `domain=code27` einrichten. HASE bindet einen solchen bereits geladenen Entry optional über **Vorhandenen eigenständigen CODE27 anbinden** an und ergänzt Bridge-/Satellite-Funktionen, ohne CODE27-Client, Device oder Entities zu duplizieren. Mehrere Geräte mit unterschiedlichen Ownern sind zulässig.

**CODE27 mit HASE nutzen:** Für ein eingebettetes Gerät unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → HASE → CODE27-Gerät verbinden** Host/IP eingeben und den Code auf dem Gerät bestätigen. Für einen bereits unter **CODE27** eingerichteten Hub stattdessen unter **HASE → Vorhandenen eigenständigen CODE27 anbinden** dessen Entry auswählen. Anschließend beim HASE-Hub **Konfigurieren** öffnen, **Assist-Bridge aktivieren** und ein Spellbook auswählen. Bei Spellbook-Triggern ist kein Codewort nötig. Die ausführlichen Schritte stehen im [CODE27-Provider-Guide](docs/de/providers/code27.md); die Pairing-Anleitung für standalone CODE27 in [ha-code27](https://github.com/ConstableW/ha-code27).

## Installation

**Über HACS (empfohlen):** In HACS nach „HASE“ suchen, herunterladen und Home Assistant neu starten. Danach unter *Einstellungen → Geräte & Dienste → Integration hinzufügen* „HASE“ wählen. Solange die Integration noch nicht im HACS-Katalog gelistet ist, geht es als benutzerdefiniertes Repository.

**Fallback – benutzerdefiniertes Repository:** HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/ConstableW/ha-hase` mit Kategorie *Integration* hinzufügen, dann wie oben herunterladen.

**Manuell:** Den Ordner `custom_components/hase` in das `custom_components`-Verzeichnis der Home-Assistant-Konfiguration kopieren und Home Assistant komplett neu starten.

## Einstieg

Mit [Erste Schritte](docs/de/getting-started.md) installierst du HASE und führst einen Spell über Assist aus. Danach:

- [Spellbook-Grundlagen](docs/de/spellbooks/concepts.md)
- [Architektur](docs/de/architecture.md)
- [Sicherheitsmodell](docs/de/security.md)
- [MCP-Überblick](docs/de/mcp/overview.md)
- [Spellbook Studio](docs/de/studio.md)
- [HASEAMP-Card](docs/de/cards.md)
- [CODE27-Provider](docs/de/providers/code27.md)
- [Fehlerbehebung](docs/de/troubleshooting.md)

## Projektstatus

HASE ist automatisiert getestet und wurde mit einem physischen CODE27 auf einer echten Home-Assistant-Installation durch seine verfügbaren Funktionen getestet. Dazu gehören der Spellbooker, die MCP-Schnittstelle mit hasechan sowie die CODE27-Anbindung unter HASE und als externer Hub. Der Realtest bestätigt die geprüften Funktionen; die jeweils dokumentierten technischen Grenzen bleiben bestehen.

Siehe [Tests und Verifikation](docs/de/development/testing.md), [Beitragen](docs/de/development/contributing.md) und [Architekturregeln](docs/de/development/architecture-rules.md).

## Lizenz und Urheberschaft

Open Source unter der [Apache-Lizenz 2.0](LICENSE). **Originalautor: ConstableW.** Die [NOTICE-Datei](NOTICE) enthält den Urheberhinweis. Wer eine abgeleitete Fassung weitergibt, muss die Lizenz beilegen, Änderungen kennzeichnen und die zutreffenden Hinweise aus `NOTICE` in lesbarer Form erhalten.

**KI-Unterstützung:** Bei Analyse, Entwurf, Implementierung und Dokumentation wurden ChatGPT/Codex, Claude, DeepSeek, Gemini sowie Ollama mit dem Modell `qwen3-coder:30b` als Werkzeuge eingesetzt. Teile des Projekts entstanden mit KI-Unterstützung; Projektverantwortung und Entscheidungen liegen bei ConstableW.

## Support & Issues

Für Support, Feature-Wünsche und Fehlermeldungen nutze bitte die [GitHub-Issues von HASE](https://github.com/ConstableW/ha-hase/issues). Beiträge und Feedback aus der Community sind willkommen!

Buy Me a Coffee – Maintainer:

[![Buy Me a Coffee – Maintainer](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/constablew)
