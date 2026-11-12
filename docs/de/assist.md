🇩🇪 Deutsch | 🇬🇧 [English](../en/assist.md)

# Assist und Conversation

Ein eigenständiger HASE-Agent ist eine Home-Assistant-`ConversationEntity`. Er matched das ausgewählte Spellbook und führt den Plan durch denselben Sicherheitspfad wie andere Frontends aus.

Action- und Query-AreaPolicy sind getrennt. Alte Einträge ohne Query-Schlüssel spiegeln die Action-Policy; neue Konfigurationen verlangen einen expliziten Query-Bereich oder uneingeschränkten Zugriff. Ein konfiguriertes, defektes Buch scheitert geschlossen.

Der gemeinsame Router erreicht Aktionen, einzelne und mehrquellige Abfragen, Combined Spells sowie Text-Spells. Bei Text-Spells liefert `assist_redirect: false` das gerenderte `output`; `true` sendet diesen Text zur Interpretation und möglichen Ausführung an Home Assistants Conversation-Agent. Das Verhalten wird nie aus dem Wortlaut geraten.

Spezifischere Trigger haben Vorrang; Aliase werden unterstützt. Bei nahezu gleichwertigen gegensätzlichen Ergebnissen fragt HASE nach, statt zu raten. Conversation- und Klärungszustand liegt nur im Speicher.

Die CODE27 Assist-Bridge ist eine eigene Provider-Funktion: [CODE27](providers/code27.md).
