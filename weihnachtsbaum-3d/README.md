# Klemmbaustein-Weihnachtsbaum (3D-Druck)

Abgeleitet aus dem Mock. Alles im echten 8-mm-Raster mit Noppen, Anti-Noppen und Röhren,
also kompatibel zu normalen Klemmbausteinen. Erzeugt von `build.py` (parametrisch).

**Maße montiert:** 112 × 112 mm Grundfläche, ca. 180 mm hoch. **11 Teile + 4 Geschenke (Schmuck optional).**

## Aufbau (von unten)

| Schritt | Teil | Farbe | Hinweis |
|---|---|---|---|
| 1 | `20_braun/sockel_braun` | braun | rund, 2 Platten hoch, Noppen oben |
| 2 | `30_weiss/schneeplatte_weiss` | weiß | klemmt auf den Sockel, 112 mm rund, Noppen für Geschenke |
| 3 | `20_braun/stamm_braun` | braun | rund, 2×2 Noppen |
| 4 | `10_gruen_ebenen/ebene_1` … `ebene_7` | grün | jede Ebene klemmt auf den Noppen der darunter; die Größe nimmt nach oben ab |
| 5 | `40_gelb/stern_gelb` | gelb | facettierter 3D-Stern auf Rundstab über rundem Noppenfuß, klemmt auf Ebene 7 |
| – | `50_geschenke/geschenk_A…D` | je 2 Farben | stehen frei auf der weißen Platte |
| – | `60_schmuck/schmuck_12x…` | rot/weiß/gelb | 1×1-Rundplatten auf die Ringnoppen der Ebenen (1–5) |

Die Ebenen haben 8 Lappen und sind abwechselnd um 22,5° gedreht. Sie sind also nicht
drehsymmetrisch zum Raster, aber die Noppen sitzen immer im Raster.

## Zuerst: Passprobe drucken (ca. 20 min)

`00_passprobe/passprobe_stud_roehre_rund.stl` enthält Varianten der kritischen Maße.
Auf **echte** Steine stecken und das Maß wählen, das satt klemmt, ohne Gewalt:

- **Platte 4×2 mit Noppen**, von links nach rechts Ø 4,70 / 4,78 / 4,86 / 4,94 mm.
  Sie gehört in einen echten Stein. Aktuell verwendet: 5,06 im Modell (gedruckt ca. 4,85, Schrumpfung 0,21 gemessen).
- **Drei 2×2-Platten mit Röhre** Ø 6,40 / 6,48 / 6,56 mm. Sie gehören auf echte Noppen.
  Aktuell verwendet: Außen 6,65, innen 5,05 im Modell (Original 6,5 / 4,8).
- **Drei 1×1-Rundplatten**, Innenloch 4,75 / 4,85 / 4,95 mm. Aktuell verwendet: 4,85.

Dann in `build.py` oben `STUD_D`, `TUBE_OD` und `ORN_ID` anpassen und `python build.py` ausführen.
Die Passung hängt von Drucker, Filament und Kalibrierung ab. Die Werte sind ein
begründeter Startpunkt, **nicht** am Drucker getestet.

## Druckplatten (Projekt "Klemmbausteine Weihnachtsbaum", 9 Platten)

| Platte | Inhalt | Farbe |
|---|---|---|
| 01 | Sockel + Stamm | braun |
| 02 | Ebene 1–3 | grün |
| 03 | Ebene 4–7 | grün |
| 04 | Schneeplatte | weiß |
| 05 | Sternspitze | gelb |
| 06 | Schmuck (12 Rundplatten, optional) | gelb, rot oder weiß |
| 07 | Geschenke, 2-farbig mit AMS | 4 Farben |
| 08 | Geschenke mit Clips, 0,2er Düse, ohne Kleber (`clip_0.2mm_Duese`) | rot/gelb/weiß |
| 09 | Geschenke mit Clips, 0,4er Düse, mit Kleber (`clip`) | rot/gelb/weiß |

Die Ebenen passen auch als `DRUCKPLATTE_P1S_A/B` (Ebene 1–3 und 4–7) auf einen P1S. Die STLs liegen einzeln in `stl/`.

### Geschenke: fertige 2-Farb-Teile (3MF)

In `50_geschenke/` liegen zwei fertige Projekte mit allen 4 Geschenken. Jedes Geschenk ist **ein Objekt aus
zwei Teilen** (Körper + Schleife) mit bereits zugewiesenem Filament:

| Slot | Farbe |
|---|---|
| 1 | weiß |
| 2 | rot |
| 3 | grün |
| 4 | gelb |

- **`GESCHENKE_2farbig_H2C.3mf`**: H2C-Profil, Filament-Zuordnung zu den Düsen manuell gesetzt
  (rot + grün links, weiß + gelb rechts). Jedes Geschenk nutzt dadurch beide Düsen und braucht beim
  Farbwechsel kein Spülen. Die Slots 1–4 musst du in der Studio-Filamentliste auf deine echten Rollen legen.
- **`GESCHENKE_2farbig_P1S_AMS.3mf`**: dasselbe für P1S mit AMS (eine Düse).
  Das ist teuer: ca. 170 Farbwechsel und rund 78 min Spülzeit (laut Slicer), bei ca. 62 min reiner Druckzeit.
  Auf dem P1S lieber die einfarbigen `_komplett`-Dateien drucken, oder Körper und Schleife getrennt.
- Alternativ bleiben `_komplett` (einfarbig) und `_koerper` + `_schleife` als STL.

**Verifiziert:** Die P1S-3MF lässt sich in Bambu Studio laden und fehlerfrei slicen, alle vier Slots sind belegt.
**Nicht verifiziert:** die H2C-3MF. Bambu Studio lässt sich für den H2C nicht per Kommandozeile slicen
(auch eine einfache STL scheitert dort an der Düsenzuordnung), das geht nur in der GUI. Struktur und Dateiformat
sind identisch zur P1S-Datei. Falls Studio beim Öffnen die Düsenzuordnung neu verlangt, übernimm "Auto".

### Geschenke: Clip-Variante (ohne AMS, alles einfarbig)

Einfarbiger Körper mit Nut (`..._clip_koerper_<farbe>`) plus Clip-Bänder, die über Kreuz in die Nuten kommen.
Es gibt zwei Band-Varianten, der Körper ist derselbe:

| Ordner | Düse | Bänder | Kleber |
|---|---|---|---|
| `50_geschenke/clip_0.2mm_Duese/` | 0,2 mm | A und B, beide durchgehend. An der Kreuzung hat jedes nur halbe Dicke (0,35 mm), das drückt die 0,4er nicht sauber. | nicht nötig, hält per Reibung und Rastnase (praktisch erprobt) |
| `50_geschenke/clip/` | 0,4 mm | A durchgehend, B in zwei Hälften (B1, B2), die an A anstoßen. Nirgends dünner als 0,8 mm. | ein Tropfen Kleber an B1/B2 empfohlen |

- Die Bänder liegen beim Druck auf der Seite, so verlaufen die Schichten entlang des Bandes und die Arme brechen nicht.
- Körper wie bisher mit der Unterseite auf dem Bett. Keine Stützen.
- Nut 0,8 mm tief, Spiel 0,1 mm je Seite; Rastnase 0,35 mm bei den hohen Teilen (A, B, D). Beim flachen Geschenk C
  (nur eine Platte hoch) gibt es keine Rastnase, dort hält Reibung oder ein Tropfen Kleber.
- Die Wände sind an den Nuten innen verstärkt, damit sie nicht zu dünn werden.
- Getestet: die 0,2er-Variante (sitzt gut, ohne Kleber). Die 0,4er-Variante ist gedruckt, aber noch nicht abschließend bewertet.

## Druckeinstellungen

- Material PLA (oder PETG, aber PLA klemmt sauberer).
- Schichthöhe 0,16–0,20 mm, 3 Wände. Bei den Ebenen reichen 10–15 % Infill, sie sind
  innen massiv modelliert.
- **Keine Stützen nötig.** Alle Teile sind in Druckorientierung gebaut: Unterseite auf
  dem Bett, Noppen oben. Auch der Stern steht aufrecht und ist so geformt, dass er ohne
  Stützen geht.
- Brim ist nur bei den großen runden Ebenen sinnvoll (Warping).
- Noppen oben: Ironing aus, Elefantenfuß-Kompensation ca. 0,1–0,15 mm, falls die Unterseite aufquillt.

## Mengen (Schätzung)

Massives Volumen ca. 330 cm³ (siehe `stl/teileliste.csv`). Mit Wänden und 10–15 % Infill
sind das grob 120–180 g Filament. Die Druckzeit liegt vermutlich im Bereich einiger
Stunden pro Platte (grobe Schätzung). Der Slicer sagt es genauer.
Die 4–6 h aus dem Mock halte ich für zu knapp, weil die Ebenen deutlich größer sind.

## Geprüft (per Skript)

- Montage kollisionsfrei, alle Teile gegeneinander getestet.
- Ein Norm-Stein (Noppe 4,80 / Röhre 6,51) passt rechnerisch auf die Platte und den Stamm,
  und meine Geschenke passen auf Norm-Noppen.
- Alle STLs sind wasserdicht.

**Nicht geprüft:** echte Passung und Klemmkraft (braucht den Druck, daher die Passprobe),
Slicer-Verhalten, Druckzeit.

## Dateien

- `build.py` erzeugt alle STLs. Maße und Ebenen stehen oben im Skript (`TIERS`, `PRESENTS`).
- `vorlagen/` enthält die Projekteinstellungen (4 Filamente), die in die 3MFs eingebettet werden.
- `render.py` erzeugt die Vorschaubilder in `vorschau/`. Sie sind grob und haben
  Sortierartefakte; die STLs sind in Ordnung.
- `stl/99_vorschau/baum_komplett_montiert.stl` ist das Gesamtmodell zum Ansehen, nicht zum Drucken.
