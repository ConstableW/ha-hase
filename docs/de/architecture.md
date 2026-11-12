🇩🇪 Deutsch | 🇬🇧 [English](../en/architecture.md)

# Architektur

HASE besitzt einen semantischen Kern und mehrere Adapter.

```text
Assist       MCP       CODE27
   \          |          /
     Frontend-Adapter für Ein-/Ausgabe
                |
 spellbook_engine → Pläne → ActionExecutor
                |              |
       Matching/Sprachen     Zielauflösung + HA-Aufrufe
                |
             Spellbooks
```

`custom_components/hase/core/` besitzt die Bedeutung: Sprachen, Matching, Aktions-/Abfrage-/Combined-Planung, Policies, Status und Antworten. Der Ordner importiert absichtlich weder Home Assistant noch Provider. `spellbooker_ha.py` implementiert Zielauflösung, Assist Exposure, Serviceaufrufe und Assist-Weiterleitung. `conversation.py`, `providers/mcp/` und `providers/code27/` adaptieren ihre Kanäle.

Ein Frontend darf einen Spell anders auswählen—natürliche Sprache bei Assist/CODE27, exakte `spell_id` bei MCP—aber `operation`, `sources`, `steps`, `output`, `assist_redirect`, Ziele und Policies nicht neu definieren.

Mitgelieferte Bücher liegen in `custom_components/hase/spellbooks/`, Benutzerbücher in `<config>/hase_spellbooks/`; dieselbe stabile ID wird vollständig durch die Benutzerdatei ersetzt.

Auch Dashboard-Cards sind Frontend-Oberflächen. HASEAMP ist die providerneutrale Musik-Card: sie zeigt, was ein Frontend-Provider meldet, bietet nur, was dessen Capability-Flags erlauben, und kennt selbst kein Gerät. Alles Gerätezugewandte – Namespace, Operationen, Payloads, Einheiten, Besonderheiten – gehört dem Frontend-Provider, der es besitzt, in seinem eigenen Provider-Ordner; der erste ist die CODE27-Music-Runtime. HASE Core bekommt keine Musiklogik, und es gibt keinen zentralen Musikvertrag, den Provider implementieren. Siehe [HASEAMP (Dashboard-Card)](cards.md).

Siehe [Architekturregeln](development/architecture-rules.md) und die internen Referenzen (`../internal/INDEX.md`, Maintainer-Dokumente, die nicht Teil des öffentlichen Repos sind).
