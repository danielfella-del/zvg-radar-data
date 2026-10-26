# PROJECT.md – ZVGPro

## 1. Produktziel

ZVGPro ist ein Portal für Zwangsversteigerungen in Deutschland. Es soll veröffentlichte Versteigerungsverfahren übersichtlich auffindbar machen, relevante Objekt- und Verfahrensdaten verständlich aufbereiten und Nutzern eine belastbare Grundlage für die weitere Prüfung geben.

Die amtliche Veröffentlichung und Originalunterlagen bleiben maßgeblich. ZVGPro darf fehlende Fakten, Bewertungen oder amtliche Nachweise nicht erfinden.

## 2. Produktprinzip

**Viele Daten im Hintergrund, klare Entscheidungen im Vordergrund.**

Der Nutzer soll schnell verstehen:

- welche Verfahren relevant sind,
- wo sich das Objekt befindet,
- wann der Termin stattfindet,
- welcher Verkehrswert veröffentlicht wurde,
- welche Unterlagen verfügbar sind,
- welche Hinweise oder Risiken belegt sind,
- welche Informationen Schätzung bzw. Orientierung und welche amtlich sind.

## 3. Bestehende Kernfunktionen

Zum bestätigten Produktumfang gehören insbesondere:

- bundesweite Verfahrensdaten
- Karten-/Objektübersicht
- Suche und Filter
- Objekt-/Verfahrensdetailseiten
- Dokumente und Gutachtenbezug
- Investorenreport
- PRO-Abo / Stripe
- Kündigungszugang im Footer
- Backend/Statistik
- automatische Datenpipeline
- Datenbanksynchronisierung zu Strato

## 4. Kritische Produktregeln

- Amtliche Daten haben Vorrang.
- Schätzungen immer eindeutig als Schätzung oder Orientierung kennzeichnen.
- Keine erfundenen Investitionsurteile aus unvollständigen Daten.
- Ein Investment-Score muss auf nachvollziehbaren Daten beruhen und darf Unsicherheit nicht kaschieren.
- Keine unvollständigen Beispielberichte als vollwertige Analyse vermarkten.
- Logo und etablierte visuelle Identität nicht eigenmächtig verändern.
- Produktive Bereiche nur gezielt und regressionsarm ändern.

## 5. Nutzererlebnis

Die Oberfläche soll trotz großer Datenmenge klar bleiben:

- verständliche Navigation
- kompakte, konsistente Karten
- gute mobile Bedienung
- Karte als echter Such-/Orientierungspunkt
- wenige wichtige Kennzahlen zuerst
- Details und Quellen bei Bedarf
- klare Abgrenzung PRO / frei zugänglich

## 6. Technische Trennung

### Öffentliches Daten-Repository
`zvg-radar-data`

Enthält Datenpipeline, Collector, Enrichment, öffentliche Daten-/Medienbestände, Tests und Sync-Client.

### Produktive Website
Der vollständige PHP-/JavaScript-/CSS-Stand von `zvgpro.de` liegt derzeit noch auf Strato und ist **noch nicht vollständig als private Git-Codebasis übernommen**.

Der Website-Code gehört später in ein **privates Repository**. Er darf nicht ungeprüft in `zvg-radar-data` eingecheckt werden.

## 7. Entwicklungsworkflow

ChatGPT/Produktchat:
- Produktentscheidungen
- Prioritäten
- Funktionsumfang
- Designrichtung
- Fehlerbeschreibung

GitHub:
- Source of Truth für Code und verbindliche Projektdokumente
- Issues für klar abgegrenzte Arbeit
- Pull Requests für Änderungen

Codex:
- technische Umsetzung
- Tests
- Refactoring nur wenn beauftragt
- PR-Erstellung

Nach größeren Änderungen:
1. Code-/Funktionsprüfung
2. Daten-/Integritätsprüfung
3. visuelle Prüfung auf Mobil, Tablet und Desktop
4. erst danach Freigabe/Merge/Deployment

## 8. Vorsichtsprinzip

ZVGPro ist produktiv. Bestehendes funktionierendes Verhalten hat Vorrang vor „Aufräumen“.

Keine beiläufigen:
- Framework-Wechsel
- großen Refactorings
- Dependency-Upgrades
- Design-Neubauten
- Datenformatänderungen
- Workflow-Zeitplanänderungen

ohne konkreten Auftrag und Prüfung.
