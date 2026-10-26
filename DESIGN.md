# DESIGN.md – ZVGPro

## 1. Designziel

ZVGPro soll datenstark wirken, aber nicht technisch überladen.

Grundsatz:

> **Klare Oberfläche, starke Datenbasis im Hintergrund.**

Bestehendes Logo und etablierte Gestaltung bleiben erhalten. Designänderungen erfolgen gezielt und nicht als vollständiger Neustart.

## 2. Visuelle Prioritäten

- klare Hierarchie
- konsistente Typografie
- konsistente Button-Größen
- ruhige Abstände
- kompakte Karten
- gute Lesbarkeit
- wenige dominante Farben
- keine unnötigen Kachelwüsten

## 3. Kartenansicht

Die Karte ist ein zentraler Such- und Orientierungspunkt.

- Leaflet/OpenStreetMap und bestehendes Marker-Clustering respektieren
- keine unnötigen visuellen Linien/Artefakte
- Kartenfläche nicht durch übergroße Panels verdrängen
- mobile Bedienbarkeit prüfen
- Marker/Objektbezug muss nachvollziehbar bleiben
- Koordinaten-Näherungen nicht als exakte Hausposition darstellen

## 4. Objektkarten

Standardmäßig nur relevante Werte:

- Ort
- Objektart
- Verkehrswert
- Versteigerungstermin
- wesentliche Statusinformation
- klare Aktion zum Objekt

Zusatzinformationen erst auf Detailseite oder bei Bedarf.

## 5. Objekt-/Detailseite

Oben:

- klare Objektidentifikation
- Termin
- Verkehrswert
- Ort
- wichtigste Dokumente / Fakten

Darunter strukturiert:

- Verfahrensdaten
- Objektinformationen
- Gutachten / Dokumente
- Richtwerte / Orientierung
- Risiko-/Umfeldinformationen
- PRO-Inhalte

Amtlich und geschätzt müssen visuell und sprachlich unterscheidbar sein.

## 6. Richtwerte

- amtlichen Bodenrichtwert als amtlich kennzeichnen
- wenn nicht vorhanden, bestehende PLZ-/Umfeldschätzung als **Orientierung**
- Schätzung niemals optisch oder sprachlich wie amtlichen Wert darstellen

## 7. Mobile First

Jede größere Änderung muss auf mindestens drei Größen geprüft werden:

- Mobil
- Tablet
- Desktop

Besonders prüfen:

- Suchpanel / Z-Ebenen
- Karte
- Filter
- Modals/Popups
- Footer
- Login/PRO
- Investorenreport-CTA
- Tabellen
- Karten/Kacheln

## 8. Sprache

Natürliches, verständliches Deutsch.

Nicht verwenden:

- KI-Marketingfloskeln
- unbelegte Superlative
- unnötige Fachsprache
- Formulierungen, die Schätzungen als Tatsachen darstellen

## 9. Visueller QA-Agent

Bei jeder größeren Frontendänderung ist zusätzlich zur Codeprüfung eine visuelle Prüfung erforderlich.

Prüffragen:

- Wirkt die Seite wie aus einem Guss?
- Sind Abstände und Schriften konsistent?
- Ist die Hauptaktion sofort erkennbar?
- Ist die Karte gut nutzbar?
- Gibt es Überlagerungen oder abgeschnittene Inhalte?
- Funktioniert Mobil/Tablet/Desktop?
- Sind PRO-/freie Inhalte klar getrennt?
- Ist amtlich vs. Orientierung klar erkennbar?
- Wurde Logo/CI unbeabsichtigt verändert?

## 10. Keine eigenmächtigen Redesigns

Codex soll bestehende Screens nicht „modernisieren“, wenn dies nicht konkret beauftragt wurde.

Fehlerbehebungen und Konsistenzverbesserungen sind erlaubt, sofern:
- der Scope klar ist,
- bestehende Funktion erhalten bleibt,
- relevante Screens geprüft werden.
