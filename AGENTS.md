# ZVGPro: Vorgaben für Codex

## Umfang und Arbeitsweise

- Dieses Repository enthält die Datenpipeline von ZVGPro. Es ist öffentlich.
- Der vollständige PHP-/JavaScript-Quellcode der produktiven Website `zvgpro.de` ist hier nicht enthalten. `strato/` ist nur eine einfache Feed-Ansicht. Nicht als aktuellen Website-Stand ausgeben oder auf Produktion hochladen.
- Lies zuerst `docs/CODEX-UEBERGABE.md`. Prüfe danach den aktuellen Code und den Git-Stand; alte Chat-Aussagen oder ZIP-Dateien ersetzen diese Prüfung nicht.
- Ändere nur den konkret beauftragten Bereich. Keine beiläufigen Refactorings, Dependency-Upgrades oder Designänderungen. Die Seite läuft produktiv; bestehendes Verhalten hat Vorrang.
- Kommuniziere in verständlichem Deutsch. Trenne beobachtete Fakten, Annahmen, bestandene Tests und noch offene Prüfungen.
- Fehlende Daten nicht erfinden. Ein grüner Parser-Test beweist weder aktuelle Quellenerreichbarkeit noch die Vollständigkeit aller Verfahren.

## Einrichtung und Prüfungen

```bash
bash scripts/setup-dev.sh
bash scripts/test-offline.sh
```

Python 3.12 entspricht den bestehenden GitHub-Workflows. Das Setup installiert ausschließlich die vorhandenen Anforderungen aus `collector/requirements.txt` in `.venv`. Die Tests benötigen weder Produktionszugänge noch den Medienbestand.

Nicht `unittest discover` allein als vollständigen Testlauf verwenden: Mehrere bestehende Tests sind Skripte mit Assertions. Der Test-Wrapper führt die relevanten Skripte explizit aus. Die HH/MV-Tests laufen dabei bereits über `test_official_notice_parsers.py`.

## Grenzen zum Produktivbetrieb

- Setup und Tests dürfen keine Collector-Vollausführung, Datenbank-Synchronisation, E-Mail-Auslösung oder Bereitstellung auf Strato starten.
- Für Tests temporäre Verzeichnisse und vorhandene Fixtures verwenden. Bei Sync-Tests eine lokale, simulierte `post`-Funktion an `run(raw, post)` übergeben; den echten HTTP-Client nicht aufrufen.
- Produktionsläufe und Uploads nur ausführen, wenn sie zum ausdrücklich beauftragten aktuellen Arbeitsschritt gehören.
- Geheimnisse, Datenbankexporte, Nutzerdateien, Zahlungen und Zugangsdaten niemals in dieses öffentliche Repository aufnehmen. Bestehende Secrets nicht ausgeben.
- `data/` und `media/` sind von den laufenden Workflows verwaltete Bestände. Nicht zur Entwicklungsbereinigung löschen, umstrukturieren oder neu erzeugen.
- `main` wird automatisch fortgeschrieben. Vor dem Übernehmen von Änderungen den aktuellen Stand prüfen; keine Force-Pushes oder Überschreibung neuer Daten-Commits.

## Bestehende Verträge erhalten

- Alle 16 Bundesländer bleiben konfiguriert. HH/MV verwenden ihre eigenen amtlichen Bekanntmachungen und den Parser `hh_mv_notices.py`.
- Bei HH/MV nur erfolgreich gelesene Ausgaben abgleichen. Bei Quellfehlern frühere Datensätze erhalten; Gerichtssitz und Veröffentlichungsdatum nicht als Objektadresse und Versteigerungstermin übernehmen.
- Der MariaDB-Sync verwendet `field-chunks-v1`, vollständige Objekte, Hash-Bestätigungen und eine Vollständigkeitskontrolle. Limits und Abschlusslogik nicht ohne konkreten Auftrag ändern.
- Der Sync lädt Datensatz und Client aus demselben unveränderlichen `GITHUB_SHA`. Ein alter Workflow-Re-run verwendet einen alten Snapshot. Nicht stillschweigend auf wechselnde `main`-URLs umstellen.
- Workflow-Zeitpläne, Concurrency-Gruppen, Datenformat und öffentliche Medien-URLs bei reinen Entwicklungs- oder Dokumentationsaufgaben unverändert lassen.
- Produktvorgabe für spätere Website-Arbeit: Bodenrichtwert zuerst amtlich; falls nicht verfügbar, vorhandene PLZ-Umfeldschätzung deutlich als „Orientierung“ anzeigen. Eine Schätzung niemals als amtlich kennzeichnen. Logo und bestehendes Design erhalten.

## Abschluss einer Änderung

Beschreibe konkret, was geändert wurde, warum, welche passenden Prüfungen bestanden wurden und was nicht geprüft werden konnte. Keine Produktionsprüfung behaupten, wenn nur lokale Tests gelaufen sind.


## Projektsteuerung und Rollen

Zusätzlich sind `PROJECT.md`, `DESIGN.md` und `DATA.md` verbindliche Referenzen.

### Product / Architecture
- Scope klein und konkret halten.
- Bestehende Verträge und produktive Stabilität schützen.
- Schwer rückgängig zu machende Änderungen begründen.

### Data / Pipeline
- Quellen, Parser, Historienerhalt, Sync-Verträge und Datenqualität prüfen.
- Nie Quellfehler durch pauschales Löschen lösen.

### Code / QA
- passende Offline-Tests ausführen.
- Regressionen, Fehlerfälle und Security prüfen.
- Produktionsprüfung nicht behaupten, wenn nur lokal getestet wurde.

### Visual / UX
Sobald die private Website-Codebasis verfügbar ist, größere Frontendänderungen zusätzlich gegen `DESIGN.md` prüfen:
- Mobil
- Tablet
- Desktop
- Karte
- Overlays
- Typografie
- Buttons
- CI/Logo
- amtlich vs. Orientierung

## GitHub-/Codex-Workflow

- Anforderungen als GitHub-Issue konkretisieren.
- Für größere Arbeiten eigener Branch.
- Codex setzt um und führt passende Tests aus.
- Änderung per Pull Request gegen den Zielbranch.
- PR beschreibt: Änderung, Grund, Tests, nicht geprüfte Punkte.
- Vor Merge bei UI-Arbeiten Code- **und** visuelles Review.
- Deployment auf Strato ist ein eigener Schritt und erfolgt nicht automatisch durch reine Entwicklungsarbeit.

## Repository-Grenze

Dieses Repository bleibt die öffentliche Datenpipeline.

Der vollständige Website-Code von `zvgpro.de` gehört in ein separates **privates Website-Repository**. Niemals Zugangsdaten, private Laufzeitdaten, Datenbankexporte oder produktive Secrets in dieses öffentliche Repository übernehmen.
