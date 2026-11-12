🇩🇪 Deutsch | 🇬🇧 [English](../../en/development/contributing.md)

# Dokumentation und Code beitragen

Deutsch ist die primäre und verbindliche öffentliche Dokumentationssprache von HASE. Erstelle und verifiziere zuerst die deutsche Seite aus Implementierung, ausführbaren Tests und aktuellen Referenzen; übersetze danach diese geprüfte Seite ins Englische. Beide Fassungen dürfen nicht unabhängig hergeleitet werden. Bei Abweichungen gilt Deutsch, anschließend sind beide Seiten zu korrigieren.

Technische Identifikatoren bleiben unverändert: Python-Namen, Modulpfade, Konfigurations-/YAML-Schlüssel, Methodennamen, Status-/Reason-Codes und Protokollbegriffe. Jede öffentliche Seite verlinkt ihr Gegenstück; Navigation bleibt in der gewählten Sprache.

Bei Codeänderungen gilt: `core/` besitzt Semantik; Frontends adaptieren Transport und Darstellung. Operationen werden nur in `core/operations.py` ergänzt, gemeinsamer Executor und Policies werden wiederverwendet.

Historische Dokumente sind Belege, keine aktuellen Anleitungen. Marker erhalten und den internen Index (`../../internal/INDEX.md`, ein Maintainer-Dokument, das nicht Teil des öffentlichen Repos ist) pflegen.
