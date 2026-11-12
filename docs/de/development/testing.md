🇩🇪 Deutsch | 🇬🇧 [English](../../en/development/testing.md)

# Tests und Verifikation

Python-Suite:

```powershell
python -B -m pytest -q -p no:cacheprovider
```

Bei Studio-/Frontend-Änderungen:

```powershell
node --experimental-default-type=module --test tests/test_studio_frontend.mjs
```

Die automatisierten MCP-Tests umfassen Projektions-/Execution-/Context-Tests und einen lokalen Streamable-HTTP-Roundtrip mit offiziellem SDK auf simulierter Home-Assistant-Oberfläche. Zusätzlich wurde die MCP-Schnittstelle im Home-Assistant-Realtest mit hasechan und einem physischen CODE27 genutzt. Der Realtest umfasste auch die verfügbaren HASE-Funktionen einschließlich Spellbooker und CODE27-Anbindung.

Berichte trennen vier Belegstufen: automatisierte Tests, echte Home-Assistant-Verifikation, physische Geräteverifikation und unverifizierte/historische Beobachtung. Server-Erfolg ist ohne unabhängige Beobachtung kein bestätigter physischer Effekt.

Bei reinen Dokumentationsänderungen zusätzlich relative Links, Sprachgegenstücke, Terminologie und finalen Diff auf unbeabsichtigte Runtime-Änderungen prüfen.
