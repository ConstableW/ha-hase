# Klemmbaustein-Weihnachtsbaum, modular und kompatibel zu gängigen Klemmbausteinen

Ein 18 cm hoher Weihnachtsbaum aus 11 gedruckten Teilen. Alles sitzt im echten 8-mm-Raster mit Noppen, Anti-Noppen und Röhren,
also klemmt er auf Original-Klemmbausteinen und umgekehrt. Er steht auf einer weißen Schneeplatte, auf der Original-Steine
und Figuren Platz finden. Die Teile sind in Druckrichtung modelliert und brauchen **keine Stützen**.

## Was ist dabei

- Brauner Sockel (rund, 2 Platten hoch) und weiße Schneeplatte (Ø 112 mm, Noppen im Raster)
- Brauner Stamm mit 2×2 Noppen
- 7 grüne Ebenen mit Lappenrand, jede klemmt auf der darunter und wird nach oben kleiner. Die Ebenen sind abwechselnd
  um 22,5° gedreht, die Noppen sitzen trotzdem immer im Raster.
- Gelber, facettierter 3D-Stern auf einem Rundstab, mit rundem Noppenfuß für die Spitze
- Vier Geschenke: als ein Teil, als 2-Farb-3MF (Körper + Schleife) oder als **Clip-Variante** (einfarbiger Körper mit Nut,
  zwei Clip-Bänder, gut ohne AMS)
- Optional: Ringnoppen auf den Ebenen 1–5 für 1×1-Rundplatten als Schmuck. Du kannst dafür Original-Platten nehmen.

**Maße:** 112 × 112 mm Grundfläche, ca. 180 mm hoch.

## Drucken

- PLA, Schichthöhe 0,16–0,20 mm, 3 Wände, 10–15 % Infill. Brim nur bei den großen Ebenen.
- Alle Teile mit der Unterseite auf das Bett, Noppen nach oben. Der Stern steht aufrecht und braucht keine Stützen.
- Die Ebenen 1–3 und 4–7 passen je auf eine P1S-Platte (`DRUCKPLATTE_P1S_A/B`).
- **Maßkorrektur:** Die Noppen sind im Modell absichtlich dicker (Ø 5,06 mm statt Norm 4,80 mm), die Röhren auch (außen
  6,65 mm, innen 5,05 mm), weil gedruckte Teile bei mir um etwa 0,2 mm schrumpfen. Das ist bei dir eventuell anders.
  Miss eine gedruckte Noppe nach (Soll ca. 4,85 mm, Original ca. 4,8–4,87 mm) und passe `STUD_D`, `TUBE_OD`, `TUBE_ID`
  in `build.py` an. Dann `python build.py` ausführen, alle Dateien werden neu erzeugt.

## Geschenke

- `_komplett`: ein Teil, einfarbig.
- 2-Farb-3MF für Bambu Studio (H2C und P1S mit AMS), vier Filament-Slots vorbelegt. Auf dem P1S braucht das viele Farbwechsel.
- Clip-Variante: einfarbiger Körper plus zwei Bänder, die über Kreuz in die Nuten klemmen.
  `clip_0.2mm_Duese/` für die 0,2er Düse (hält ohne Kleber, praktisch erprobt), `clip/` für die 0,4er Düse (Band B in zwei
  Hälften, mit einem Tropfen Kleber).

## Passung und Status

Getestet mit Original-Steinen: Noppen oben (Minifigur steht), Stamm klemmt, Röhren sitzen fest auf Original-Noppen.
Gedruckt getestet: die Clip-Geschenke mit 0,2er Düse (sitzen ohne Kleber gut). Die 0,4er-Variante ist gedruckt, aber noch nicht abschließend bewertet.

## Dateien

- `stl/`: alle fertigen STLs, nach Farbe sortiert
- `build.py`: parametrischer Generator (Python, manifold3d, shapely, trimesh). Alle Maße stehen oben im Skript.
- `README.md`: Bau- und Druckanleitung auf Deutsch
