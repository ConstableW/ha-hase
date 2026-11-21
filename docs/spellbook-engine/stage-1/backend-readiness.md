# CODE27-Backend: belegte Fähigkeiten und offene Prüfung

Quellen: lokales api.py, coordinator.py, bridge.py und gespeicherte Vergleichsaufnahmen im Repository. Keine neuen Texte ans Gerät gesendet, keine Geräteeinstellungen verändert. Aussagen betreffen die implementierte API und vorhandene Aufnahmen, nicht alle denkbaren undokumentierten Endpunkte.

| Fähigkeit | Befund | Konsequenz |
|---|---|---|
| Text an Slot senden | api.py:281 POST /data/user/chat/text mit text/slot; Rückgabe optionale chat_id | Sendebestätigung ist keine Assistant-Antwort |
| Historie lesen | api.py:231ff und :253; datum-/slotbezogen, Rollen und Nachrichten-IDs | Grundlage für Polling vorhanden |
| Profile/Slots wählen | api.py:218–226; Einstellungen und aktiver Slot | Explizite stabile Profilzuordnung nötig; keine automatische Umschaltung des physischen Frontends pro Anfrage |
| Assistant-Ausgabe beobachten | coordinator.py erzeugt assistant-message Events | Noch kein Request→Reply-Vertrag |
| App vs. Gerät unterscheiden | code27-origin-comparison-20260910.json: gleicher group_chat_id und Slot; unterschiedliche chat_id | group_chat_id ist kein Beleg für getrennte Interaktionen oder Herkunft; gemeinsamer Kanal |
| Strukturiertes Tool-Protokoll | Kein implementierter Endpunkt/Parser; bridge.py:5 beschreibt fehlendes gefundenes Function Calling | Nicht als unterstützt zusagen; Text-JSON müsste streng validiert werden und seine Zuverlässigkeit erst zeigen |
| Parent-/Request-ID einer Antwort | In API-Wrapper und vorhandener User-Nachrichtenaufnahme nicht nachgewiesen | Reihenfolge allein genügt bei konkurrierendem physischem Chat nicht |
| Getrennte Historie pro interaction_session | Nur slotbezogene Historie implementiert | SessionStore trennt HA-Rückfragen, nicht automatisch Backend-Kontext |
| Anfrage an inaktiven Slot | **Live geprüft (2026-09-10/11, Test-Instanz):** `Code27ConversationBackend.converse` vergleicht den konfigurierten Slot gegen `coordinator.data.current_slot` und verweigert (`status="unavailable"`) statt zu senden, wenn sie nicht übereinstimmen - mehrfach reproduziert: aktiven Charakter weg von Lilyas Slot wechseln, dieselbe Nicht-Treffer-Äußerung ergibt zuverlässig "Ich kann das gerade nicht erreichen." statt einer Antwort der falschen Persona | Slot-Mismatch-Schutz nachgewiesen korrekt - siehe trotzdem die Restunsicherheit unten (Slot-Übereinstimmung beweist nicht Antwort-zu-Anfrage-Zuordnung bei echter Nebenläufigkeit) |
| Kontrollierte Ausgabe ohne physisches Sprechen | TTS-Audition ist separater Ausgang; Seiteneffekte von Chat-Einspeisung ungeprüft | Backend-Aufruf darf nicht ungeplant im Büro sprechen |
| App-Präsenz / Push-Hinweis | Vorhandene Presence-/Transition-Probes beobachten System und Musik/BT; kein belegtes Chat-Korrelationsprotokoll | Nicht als Antwortbereitschaft nutzen |

## Minimale nächste Fähigkeitsprüfung vor Etappe 6

An einem Testgerät mit dokumentiertem Ausgangszustand: eine harmlose Anfrage, Antwortaufnahme inklusive Metadaten, zwei aufeinanderfolgende Anfragen, Konkurrenz durch physischen Chat, inaktiver Slot, Timeout und verspätete Antwort. Prüfen: stabile Antwortkorrelation, tatsächliches Profil, Audio-/Chat-Seiteneffekte, Echo-Vermeidung, strukturierte Ausgabe inklusive ungültiger Ausgabe, Möglichkeit zur Übergabe von ExecutionResults. Keine HA-Aktion für diesen Test erforderlich.

Abnahmekriterium: Der Adapter kann jede auszuführende Antwort eindeutig einem noch gültigen Request zuordnen und ungültige oder verspätete Vorschläge verwerfen. Ein Slot-Lock allein löst Konkurrenz mit direkter Benutzereingabe nicht. Ohne Nachweis bleibt SATELLITE mit CODE27 deaktiviert/nicht verfügbar; der backendlose Core ist davon unabhängig.

Historienteilung ist bewusst zu entscheiden: dieselbe Persona kann eine gemeinsame Historie haben, aber eingeschränkte Agents dürfen dadurch keine fremden Query-Daten erhalten. Ein Backend ohne isolierbaren/filtrierbaren Kontext darf nicht als sichere gemeinsame Historie für unterschiedlich berechtigte Agents gelten.

## Live-Härtungsnachweis 2026-09-10/11 (Fund #19, ehrlich bilanziert)

**Was tatsächlich geprüft und bestätigt wurde:**
- **SATELLITE-vs-SATELLITE-Serialisierung:** zwei echte, gleichzeitig via `/api/conversation/process` gefeuerte Backend-Anfragen (`asyncio.gather`, kein künstlicher Delay) an denselben CODE27-Slot ergaben eine Gesamtlaufzeit von ~12s bei einer Einzelanfrage-Baseline von ~7s - exakt die erwartete Signatur ernsthafter Serialisierung (die zweite Anfrage wartet auf die erste, statt parallel zu laufen), nicht ~7s (was ein Race verraten hätte). Beide Antworten waren zudem inhaltlich korrekt der jeweils eigenen Frage zugeordnet. Das bestätigt: `backends/code27.py`s `_device_locks` (modul-globale, geräteweite `asyncio.Lock`s) verhindert zuverlässig, dass zwei SATELLITE-Anfragen sich gegenseitig die Antwort stehlen.
- **Slot-Mismatch-Verweigerung:** siehe Tabelle oben - mehrfach reproduziert, funktioniert wie dokumentiert.

**Was NICHT geprüft werden konnte und weiterhin offen bleibt:**
- Echte Nebenläufigkeit zwischen einer SATELLITE-Anfrage und einem GLEICHZEITIGEN physischen/App-Chat auf demselben Gerät konnte in dieser Session nicht hergestellt werden - dafür wäre ein zweiter, von diesem HA-Adapter unabhängiger Kanal nötig (das physische CODE27-Gerät selbst oder die Companion-App), auf den von hier aus kein Zugriff besteht, ohne die im Gerät gespeicherten Zugangsdaten (`local_secret`) aus dem Storage zu extrahieren - das wurde bewusst nicht getan (unverhältnismäßiger Eingriff für einen POC-Test).
- Die vorhandene Text-basierte Eigenerkennung in `coordinator.py` (`_pending_chat_replies`/`_own_chat_ids`) markiert nur die selbst GESENDETE User-Nachricht als "eigen" - sie markiert NICHT die zugehörige Assistant-Antwort. `backends/code27.py`s Listener nimmt schlicht die erste neue Assistant-Nachricht auf dem passenden Slot nach dem eigenen Senden entgegen. Träfe im selben Zeitfenster eine echte, unabhängige Nachricht auf denselben Slot ein (App, physisches Mikrofon), gäbe es keinen Mechanismus, der die beiden Antworten unterscheiden könnte - dies ist eine architektonische Grenze des CODE27-Protokolls (kein Message-/Request-Correlation-Feld), keine dieser Session zurechenbare Programmierlücke.

**Ehrliches Fazit:** Der Teil von #19, der vollständig unter Kontrolle dieser Integration liegt (SATELLITE-eigene Nebenläufigkeit), ist nachweislich sicher. Der Teil, der außerhalb dieser Kontrolle liegt (echter physischer/App-Chat parallel zu einer SATELLITE-Anfrage), bleibt so unbewiesen wie zuvor - SATELLITE bleibt aus genau diesem Grund als experimentell markiert, nicht aus Vorsicht ohne konkreten Grund.
