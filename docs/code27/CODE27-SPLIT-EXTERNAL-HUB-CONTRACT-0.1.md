# CODE27 Split & External Hub Contract 0.1

**Status: FROZEN (2026-10-05).** Gilt für die aktuelle HASE-Codebasis bei `d0c9e9d`. Dieser Vertrag beschreibt das verbindliche Ziel; er behauptet nicht, dass die External-Hub-Anbindung bereits implementiert ist. Änderungen an den Ownership-Invarianten oder dem Umfang der 0.1-Garantie erfordern eine neue Vertragsrevision.

**Spätere verbindliche Scope-Klarstellung:** [CODE27-SPLIT-SCOPE-CLARIFICATION-2026-10-05.md](CODE27-SPLIT-SCOPE-CLARIFICATION-2026-10-05.md) hebt nur die Bestands-/Upgrade-Garantien für Installationen vor diesem Split auf. Die Ownership- und Binding-Invarianten dieses Contracts bleiben gültig.

## 1. Grundsatz

Es gibt **eine kanonische CODE27-Geräteimplementierung** und zwei legitime, dauerhafte HA-Owner. Kanonisch sind Protokoll/API, Transport, Datenmodell und soweit möglich der Geräte-Runtime-Core. Die Config-Flows, HA-Entry- und Entity-Wrapper dürfen sich zwischen den Domains unterscheiden. Der Installationsweg allein richtet kein Gerät ein und wechselt keinen bestehenden Owner.

| Einrichtung eines Geräts | ConfigEntry-Domain | Owner von Credentials, Client, Coordinator, Geräte-Device und CODE27-Entities |
| --- | --- | --- |
| Über den eingebetteten HASE-Provider | `hase` | HASE-Entry, wie heute |
| Über die Standalone-Integration | `code27` | CODE27-Entry |

Ein HASE-gebundenes Gerät bleibt unter `hase`, auch wenn `ha-code27` später installiert wird. Ein standalone eingerichtetes Gerät bleibt unter `code27`, auch wenn HASE später installiert wird. Für einen solchen Entry erstellt HASE keine CODE27-Geräte-Entities und keine zweite Geräte-Runtime.

Die zwei Owner sind **als Varianten und für verschiedene Geräte gleichzeitig** zulässig. Für dasselbe physische Gerät darf zur selben Zeit nur einer der beiden Owner eine aktive CODE27-Runtime besitzen.

## 2. Gemeinsamer Code und Distribution

Die bestehende Geräteimplementierung in `custom_components/hase/providers/code27/` ist die fachliche Ausgangsbasis. Pairing-Protokoll, signierter Client, Geräte-WebSocket, Polling, Reconnect, CODE27-Datenmodell und möglichst der Geräte-Runtime-Core werden aus **einer Quelle** in die beiden Distributionen übernommen. Die Wrapper entscheiden über Domain, ConfigEntry-Flow, Runtime-Ablage, Device-Identifier, Entity-Erzeugung und Übersetzungs-/Service-Namespace; sie müssen nicht byte-identisch sein. Änderungen an der gemeinsamen Geräteimplementierung werden nicht unabhängig in zwei Kopien entwickelt.

`ha-hase` verwaltet mit HACS nur `custom_components/hase`; `ha-code27` verwaltet nur `custom_components/code27`. Es gibt keinen von beiden HACS-Repositories gemeinsam verwalteten Zielpfad. HASE allein kann den eingebetteten, optionalen Provider anbieten. Die Standalone-Installation allein erzeugt keinen ConfigEntry und übernimmt kein HASE-Gerät.

Der konkrete Build-/Synchronisationsweg für die eine Codequelle ist eine Distributionsentscheidung, kein Teil des Runtime-Contracts. Er muss vor Veröffentlichung reproduzierbar feststehen; ein manueller, unabhängig gepflegter Fork erfüllt diesen Vertrag nicht.

## 3. Ownership-Invarianten

1. Der **einrichtende ConfigEntry** besitzt Credentials, genau einen Geräte-Client, genau einen Geräte-Coordinator, Geräte-WebSocket/Poller, CODE27-Device und CODE27-Entities. Sein `entry_id` ist der Runtime-Schlüssel.
2. HASE darf einen geladenen fremden `code27`-Entry als Hub/Frontend **referenzieren**. Das Binding erzeugt keinen zweiten CODE27-Client, Coordinator, WebSocket, CODE27-Device und keine CODE27-Entity; es kopiert und erneuert keine Credentials. Hinsichtlich Ownership ist es read-only: HASE darf über den Contract Geräteaktionen auslösen, aber weder Daten/Optionen des `code27`-ConfigEntry noch dessen Registry-Ownership oder Lifecycle-Konfiguration verändern.
3. HASE-eigene Bridge-, Spellbook-, Studio- und Satellite-Zustände gehören HASE. Das fremde Binding darf eigene HASE-Listener/Tasks besitzen und muss sie beim Entbinden entfernen. Ein CODE27-Unload macht das Binding unverfügbar; ein späterer CODE27-Reload kann es wieder anbinden.
4. Die Config-/Device-/Entity-Registry bleibt die Quelle für vorhandene Entries, Geräte und Entities. Es wird keine eigene Ownership-Datenbank eingeführt.
5. Installieren oder Aktualisieren eines der Repositories verändert keinen bestehenden ConfigEntry-Owner. Es gibt **keine automatische Domain-Migration** von `hase` nach `code27`.

## 4. Kleinster External-Hub-Contract

Die Standalone-Integration veröffentlicht für **geladene** `code27`-Entries eine Entry-gebundene Runtime, vorzugsweise über `ConfigEntry.runtime_data`, und eine kleine dokumentierte Zugriffsschicht. HASE findet Kandidaten über `hass.config_entries.async_entries("code27")` und bindet anhand der `entry_id`. Der Zugriff ist nur gültig, solange der Entry geladen ist; State-Änderungen, Unload und Reload müssen berücksichtigt werden.

Die öffentliche Oberfläche muss die heute tatsächlich benötigten Operationen abdecken. Geräteaktionen sind erlaubt; Entry-, Registry- und Lifecycle-Verwaltung des fremden Owners sind es nicht:

| Fähigkeit | Bedarf im aktuellen HASE-Code |
| --- | --- |
| Entry-ID, geladen/verfügbar, Zustands-Snapshot und Update-Subscription | `bridge.py`, `backend.py`, `conversation.py`; aktueller Slot, Sprache, Charakter-/Voice-Daten, Notification-Status |
| User- und Assistant-Chat-Nachrichten mit Entry-ID, Slot, Text, Chat-ID und Zeit; abmeldbare Subscription | Bridge in `bridge.py`; Antwortkorrelation in `backend.py` |
| Chat-Antwort senden, mit bestehender Echo-Unterdrückung; optional Bild senden und erkannte Assist-Exchange bereinigen | `bridge.py`, `backend.py`, bestehende HASE-Services |
| TTS für aktiven Charakter mit optionaler Prüfung des erwarteten Slots; Notification auslösen/bestätigen | `bridge.py`, `hase.speak`, `hase.notify`, `hase.notify_clear` |

Das ist eine **fähigkeitsbezogene** Schnittstelle, keine Freigabe des gesamten veränderbaren Coordinators oder der Credentials als allgemeine HASE-API. Namen und Typen der Python-Methoden werden bei der Implementierung festgelegt; die obigen Semantiken sind der stabile Contract. Insbesondere darf ein fehlender/unloadender CODE27-Entry nicht durch heimliche Neuerzeugung einer HASE-Geräte-Runtime ersetzt werden.

HASEs `PendingCorrection`, `bridge_lock`, Spellbook- und Assist-Logik dürfen nicht als Voraussetzung in der standalone CODE27-Runtime verbleiben. Sie gehören an das HASE-Binding. Die aktuellen direkten Zugriffe in `bridge.py` auf diese Coordinator-Felder sind deshalb konkrete Anpassungsstellen.

## 5. Binding-Abläufe

**HASE-Owner (`domain=hase`):** Der vorhandene HASE-Provider-Flow und Entry bleiben gültig. HASE richtet seinen CODE27-Client und Coordinator wie bisher ein und bindet seine Bridge daran. Vorhandene Entry-ID, Credentials, Device-Identifier und Entity-Registry-Einträge werden durch den Split nicht umgeschrieben.

**Standalone-Owner (`domain=code27`):** `code27` richtet Client, Coordinator und CODE27-Entities ein. HASE erkennt den Entry, liest ausschließlich dessen veröffentlichte Runtime und hängt seine eigenen Fähigkeiten daran. HASE-Setup darf diesen CODE27-Entry weder pairen noch seine CODE27-Plattformen forwarden. HASE- und CODE27-Unload/Reload lösen Listener sauber und binden bei erneut geladener Runtime wieder an.

**Beide Repositories installiert:** Es kann je nach Einrichtung Geräte mit `hase`-Owner und andere mit `code27`-Owner geben. Mehrere Entries derselben Domain bleiben ebenfalls möglich. Die Auswahl eines External Hubs geschieht anhand einer konkreten Entry-ID, nicht anhand eines globalen „ersten CODE27-Geräts“.

## 6. Schutz vor doppelter Einrichtung desselben Geräts

Beide Config-Flows prüfen vor dem Erstellen eines neuen Geräte-Entry **domainübergreifend** die bereits eingerichteten `hase`-CODE27- und `code27`-Entries. Ein Treffer führt zu einer verständlichen Abbruch-/Hinweismeldung mit dem bestehenden Owner. Das bloße Installieren des zweiten Repositories ist kein Treffer und erzeugt keine Runtime.

Die heutige HASE-Identität wird in `Code27FlowMixin._async_finish_pairing` aus `system.machine_id` (mit `user_id`-Fallback) abgeleitet. Ein gleicher bekannter `machine_id` ist ein klarer Duplicate-Treffer. **Ein nicht passender Wert beweist jedoch nicht, dass es ein anderes physisches Gerät ist:** Der bestehende API-Audit weist darauf hin, dass `machine_id` bei erneutem Pairing möglicherweise wechselt. Host/Port kann vor dem Pairing einen Verdacht liefern, ist wegen DHCP/Adresswechsel aber keine dauerhafte Identität. Die endgültige, belastbare physische Identitätsregel muss vor einer Garantie „keine Doppel-Runtime für dasselbe Gerät“ gegen das Geräteprotokoll verifiziert werden.

Die Prüfung muss für **beide Einrichtungsrichtungen** gelten. Falls die verlässliche Identität erst nach einer temporären Pairing-Prüfung feststeht und ein bereits verwaltetes Gerät erkannt wird, darf danach kein zweiter ConfigEntry und keine zweite laufende Runtime angelegt werden. Die Auswirkungen einer dabei bereits ausgestellten Pairing-Credential sind gesondert zu prüfen; der aktuelle Code liefert keinen belegten Weg, dies vorab für jeden IP-Wechsel auszuschließen. Die Flow-Prüfung verhindert normale Doppelkonfiguration, ersetzt aber **keinen gemeinsamen Runtime-Lock**. Gleichzeitige Setups und Fälle ohne belegbare physische Identität liegen außerhalb der 0.1-Garantie. Eine bewusste Übernahme ist ein eigener, späterer Vorgang und **nicht** Teil von Contract 0.1.

## 7. Kompatibilität und Abnahme

- Ein bestehender HASE-CODE27-Entry startet nach dem Split weiter unter `hase` mit derselben Entry-ID, denselben Credentials und denselben HA-Device-/Entity-Identitäten.
- Ein standalone CODE27-Entry startet unter `code27`; HASE-Binding erhöht für dieses Gerät die Zahl der CODE27-Clients, Coordinators, Geräte und Entities jeweils um **null**.
- Sind HASE und Standalone installiert, dürfen ihre HACS-Updates nur ihre je eigene Integrationsdirectory verwalten.
- Bestehende HASE-Automationen/Dashboards behalten `hase.*`-Services, Chat-/Logbook-Ereignisse und `hase/code27/music` beziehungsweise erhalten eine nachweislich kompatible Weiterleitung. Der persistierte Musik-Store-Schlüssel `hase.haseamp` darf nicht stillschweigend geändert werden.
- Unload/Reload eines standalone CODE27-Entries macht HASEs Binding sauber unavailable/available; es entstehen keine verwaisten Listener oder zweite Runtime.
- Ein Einrichtungsversuch eines nachweislich bereits anderswo verwalteten Geräts wird ohne neuen Entry beendet. Für Fälle ohne belastbare physische Identität wird keine Vollständigkeitsgarantie behauptet.

## 8. Nicht Teil von 0.1

Automatische Übernahme eines vorhandenen `hase`-Entries nach `code27`, Änderung bestehender Registry-Identitäten, eine eigene Ownership-Datenbank, ein allgemeines Provider-Framework sowie neue CODE27-Gerätefunktionen gehören nicht zu diesem Split.
