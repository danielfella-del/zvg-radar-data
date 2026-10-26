# DATA.md – ZVGPro

## 1. Grundsatz

ZVGPro verarbeitet veröffentlichte Verfahrens- und Objektdaten aus unterschiedlichen Quellen.

**Datenherkunft und Unsicherheit müssen nachvollziehbar bleiben.**

## 2. Source of Truth

Die jeweilige amtliche Veröffentlichung ist maßgeblich.

Interne Anreicherung, Geocodierung, Richtwerte und Analysen dürfen amtliche Fakten nicht überschreiben oder still verändern.

## 3. Bundesländer

Alle 16 Bundesländer bleiben konfiguriert.

Besondere bestehende amtliche Adapter unter anderem für:

- Baden-Württemberg
- Hamburg
- Mecklenburg-Vorpommern
- Schleswig-Holstein

Hamburg und Mecklenburg-Vorpommern verwenden eigene amtliche Bekanntmachungen und die dafür bestehende Parser-/Merge-Logik.

## 4. HH/MV-Schutzregeln

Bei Quellfehlern:

- frühere Datensätze erhalten
- keine fehlerhaften leeren Daten als neue Wahrheit übernehmen
- Gerichtssitz nicht als Objektadresse verwenden
- Veröffentlichungsdatum nicht als Versteigerungstermin verwenden

Andere Bundesländer nicht beiläufig umbauen, wenn eine Änderung nur HH/MV betrifft.

## 5. Synchronisierung

Der MariaDB-Sync verwendet bestehend:

- `field-chunks-v1`
- vollständige Objekte
- Hash-Bestätigungen
- Vollständigkeitskontrolle

Der Sync lädt Datensatz und Client aus demselben unveränderlichen `GITHUB_SHA`.

Diese Verträge nicht ohne konkreten Auftrag ändern.

## 6. Lösch-/Fehlerschutz

- Quellfehler dürfen vorhandene gültige Daten nicht pauschal löschen.
- Leere oder unvollständige Abrufe sind kein Nachweis, dass Verfahren verschwunden sind.
- Import-/Sync-Fehler müssen sichtbar protokolliert werden.
- Entwicklungs-Tests dürfen keine produktive Datenbank verändern.
- Keine Produktionsläufe als Nebeneffekt eines Tests.

## 7. Geodaten

Kartenkoordinaten können aus PLZ-/Bundesland-Näherungen stammen.

Sie sind dann:
- Orientierung
- kein Nachweis einer exakten Hausposition

Die UI darf daraus keine höhere Genauigkeit ableiten.

## 8. Bodenrichtwerte

Priorität:

1. amtliche Quelle
2. vorhandene, transparent bezeichnete Umfeld-/PLZ-Orientierung

Nie eine Schätzung als amtlichen Bodenrichtwert darstellen.

## 9. Dokumente / Medien

- Dokumente und Fotos nur dem korrekten Verfahren/Objekt zuordnen.
- Keine Symbol-/KI-Ersatzbilder als vermeintliche echte Objektfotos.
- Bei fehlenden Veröffentlichungsrechten keine echte Objektaufnahme veröffentlichen.
- Medienzuordnung muss nachvollziehbar bleiben.

## 10. Investment-/Analysewerte

Keine Bewertung aus einem Einzelwert erfinden.

Analysewerte sollen:
- nachvollziehbare Eingaben haben
- fehlende Daten berücksichtigen
- Unsicherheit kenntlich machen
- nicht als amtliche Einschätzung erscheinen

## 11. Tests

Lokale Standardprüfung:

```bash
bash scripts/setup-dev.sh
bash scripts/test-offline.sh
```

Ein grüner Offline-Test beweist:
- Code-/Fixture-Verhalten

Er beweist nicht automatisch:
- heutige Erreichbarkeit externer Quellen
- Vollständigkeit aktueller Verfahren
- erfolgreiche produktive MariaDB-Synchronisierung
- korrekte produktive Website-Darstellung

Diese Ebenen getrennt berichten.
