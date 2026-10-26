# Datenpipeline: Offline-Health-Baseline

Stand: 26. Oktober 2026, UTC. Bezug: [Issue #2](https://github.com/danielfella-del/zvg-radar-data/issues/2).

**Ergebnis: Die lokale Entwicklungs- und Offline-Testumgebung ist nutzbar.**
Die vorhandene Setup-/Testkette wurde mit einer frischen `.venv` und anschließend
erneut mit derselben Umgebung erfolgreich ausgeführt. Keine Prüfung schlug fehl;
an Anwendungscode, Tests oder Abhängigkeitsspezifikation war keine Korrektur nötig.
Das ist eine lokale Code-/Fixture-Baseline, kein Nachweis des produktiven Betriebs.

## Geprüfter Stand und Voraussetzungen

- Repository: `danielfella-del/zvg-radar-data`, Branch `codex/data-health-baseline`.
- Ausgangscommit von `main`: `6261e53d3a877e022f5b3bb7a4d76d3bbfd92714`.
- Vorher gelesen: [AGENTS.md](../AGENTS.md), [PROJECT.md](../PROJECT.md),
  [DESIGN.md](../DESIGN.md), [DATA.md](../DATA.md),
  [CODEX-UEBERGABE.md](CODEX-UEBERGABE.md) und [README.md](../README.md).
- Tatsächliche Cloud-Umgebung: Linux x86_64, glibc 2.41, Bash 5.2.37,
  Python 3.12.14, pip 25.0.1. Python-Optimierungsstufe `0`, Assertions aktiv.
- Benötigt: Bash, Python 3.12 mit `venv`/pip und beim Setup Zugang zum Paketindex.
  Die Offline-Tests verwenden lokale Fixtures; Produktionszugänge und der
  Medienbestand werden dafür nicht benötigt.

## Reproduktion

Im Repository-Hauptverzeichnis ausführen; Produktions-Secrets nicht einrichten:

```bash
set -euo pipefail
git rev-parse HEAD
python3 --version
unset PYTHON_BIN PYTHONOPTIMIZE
bash scripts/setup-dev.sh
.venv/bin/python -c 'import sys; print(sys.version); print("Assertions aktiv:", __debug__)'
bash scripts/test-offline.sh
.venv/bin/python -m pip check
.venv/bin/python -m pip list --format=freeze
git status --short
```

`python3` muss für diese Baseline Python 3.12 sein. Ist es unter einem anderen
Namen installiert, nur das Setup beispielsweise mit
`PYTHON_BIN=python3.12 bash scripts/setup-dev.sh` starten. Den Test-Wrapper danach
ohne `PYTHON_BIN` aufrufen, damit er `.venv/bin/python` verwendet. Kein `-O` und
kein `PYTHONOPTIMIZE` verwenden: Mehrere Testskripte prüfen mit `assert`.

Das Setup erstellt bzw. verwendet ausschließlich `.venv` und installiert die
bestehenden Anforderungen aus `collector/requirements.txt`. Ein erneuter Aufruf
prüft auch die Wiederverwendbarkeit dieser Umgebung. Für eine zusätzliche frische
Installation eine separate Arbeitskopie ohne `.venv` verwenden; `data/` und
`media/` dafür nicht löschen oder neu erzeugen.

Der Wrapper beendet sich bei einem Fehler mit einem von null verschiedenen
Exitcode. Bei Protokollierung mit `tee` muss `set -o pipefail` aktiv sein, damit
ein fehlgeschlagener Test nicht durch den erfolgreichen Log-Schreibvorgang
verdeckt wird. `.venv`, `__pycache__` und lokale Testprotokolle nicht einchecken.

## Vollständig ausgeführter Prüfumfang

Alle folgenden Prüfungen bestanden in beiden Läufen:

| Prüfung | Was tatsächlich geprüft wird | Aussagegrenze |
| --- | --- | --- |
| `compileall` im [Wrapper](../scripts/test-offline.sh) | Python-Syntax von `collector/`, `tests/` und `.github/scripts/sync-zvgpro-db.py`; an diesem Stand 22 Python-Dateien | Führt Collector und Sync nicht aus; keine Laufzeit-, Netzwerk- oder Transportprüfung |
| [Portalparser](../tests/test_parser.py) | Zwei HTML-Beispiele mit CP1252-/UTF-8-Inhalten; ID, Werte/Summe, Objektart, Adresse, PLZ/Ort, Ortsteil, Termin und Gericht | Keine aktuellen Portalantworten und keine bundesweite Vollständigkeit |
| [Detailparser](../tests/test_detail_parser.py) | Ein gültiges HTML-Beispiel mit Verfahrensdaten und zwei Anhängen; Ablehnung bei fehlendem/fremdem Aktenzeichen, fehlender Objektzeile und falscher Detailtabelle | Keine echten Anhänge oder Downloads; geprüft sind die konkreten Beispiel- und Ablehnungsfälle |
| [Bundesländer](../tests/test_state_coverage.py) | Exakte Gleichheit der konfigurierten Länderkürzel mit allen 16 Bundesländern | Keine Erreichbarkeit, Aktualität oder Verfahrenszahlen pro Land |
| [Gemeinsame Bekanntmachungsparser](../tests/test_official_notice_parsers.py) | Neun Assertions zu HH-/MV-/SH-Beispielen, Zeichenbereinigung, Terminformat und Trennung zweier SH-Verfahren | Keine vollständigen amtlichen Adapterläufe und kein BW-Beispiel in diesem Test |
| [Isolierte HH/MV-Regressionen](../tests/test_hh_mv_notices.py) | Elf `unittest`-Fälle: sechs gespeicherte Text-Fixtures, Datums-/Adress-/Verfahrenstrennung, Aufhebungen, Mehrdeutigkeiten, PDF-Link-Erkennung, fehlgeschlagene Ausgabe und begrenzter Abgleich mit Bestandserhalt | Kein tatsächlicher PDF-Download und keine erfolgreiche PDF-Textauslese im Testpfad; Quellenstand der Fixtures siehe [Fixture-Dokumentation](../tests/fixtures/README_hh_mv.md) |
| [Gutachtenfoto-Warteschlange](../tests/test_report_photo_queue.py) | Sieben `unittest`-Fälle: Erledigt-Status, geänderte Quellen, Berücksichtigung aller Gutachten, Fehlerwartezeit/Priorisierung, erneute Prüfpflicht, Erhalt anderer Fotos und gespeicherter Fortschritt auch ohne Fotoergebnis | Keine echten PDF-Downloads, Fotoextraktion, Bildqualität oder produktiven Medienzuordnungen |

Der Wrapper startet **fünf Testskripte**. Die elf HH/MV-Fälle laufen bereits über
`test_official_notice_parsers.py` und werden nicht doppelt gezählt. Insgesamt sind
das **18 `unittest`-Fälle plus die Skript-Assertions**. `unittest discover` allein
ersetzt diesen Lauf nicht.

## Beobachtete Ergebnisse

| Tatsächlicher Lauf am 26.10.2026 | Ergebnis |
| --- | --- |
| Frisches Setup, Start 14:41:40 UTC; vorherige lokale `.venv` außerhalb des Repositorys aufbewahrt | Exitcode 0; bestehende Anforderungen erfolgreich installiert |
| Erster Offline-Lauf, Start 14:42:30 UTC | Exitcode 0; Syntaxprüfung, alle fünf Skripte, elf HH/MV- und sieben Queue-Fälle bestanden |
| Erneutes Setup und erneuter Offline-Lauf, 14:43:14–14:43:17 UTC | Beide Exitcode 0; Pakete bereits vorhanden; gleicher erfolgreicher Prüfumfang |
| `pip check` nach beiden Offline-Läufen | Jeweils Exitcode 0, `No broken requirements found.` |
| Git-Prüfung vor und nach den Läufen | Arbeitsbaum sauber; keine Änderung an `data/`, `media/`, Collector, Tests, Skripten oder Workflows |

Beobachtete Hinweise, keine fehlgeschlagenen Prüfungen:

- pip konnte seinen lokalen Cache nicht beschreiben und verwendete ihn deshalb
  nicht. Installation und Konsistenzprüfung liefen trotzdem erfolgreich.
- PyMuPDF meldete eine Deprecation-Warnung zur vorhandenen `fitz`-API. Sie wurde
  dokumentiert; ein Import-Umbau oder Dependency-Upgrade gehört nicht zu diesem Issue.
- `temporary source failure`, `ok: false` und `count: 0` stammen aus dem absichtlich
  fehlschlagenden HH/MV-Download-Callback gegen `example.invalid`. Sie sind erwartete
  Ausgaben des Fehlerfalltests, kein Live-Quellenbefund. Auch die Foto-Queue-Ausgaben
  zu `he-1`/`he-2` und `downloaded_bytes: 100` stammen aus Mocks und temporären Dateien.

## Installierte Versionen dieser Baseline

```text
beautifulsoup4==4.15.0
certifi==2026.10.23
charset-normalizer==3.5.2
idna==3.20
pip==25.0.1
pymupdf==1.28.2
requests==2.34.2
soupsieve==3.0.4
typing_extensions==4.16.0
urllib3==2.8.0
```

Das ist die beobachtete Installation, keine neue Anforderungs- oder Lockdatei.
Die vorhandenen Versionsbereiche blieben unverändert. Spätere frische Setups
können innerhalb dieser Bereiche andere Versionen auflösen; eine bitidentische
Abhängigkeitsauflösung wird damit nicht zugesichert. Versionen bei Folgeläufen
erneut festhalten und Testergebnisse getrennt vergleichen.

## Produktionsgrenze und offene Prüfungen

Die ausgeführten Testpfade arbeiten mit eingebetteten Beispielen, lokalen
Fixtures, temporären Daten-/Medienpfaden und Mocks. Die HH/MV-Download-Funktion
schlägt im Fehlerfalltest gezielt fehl; die Queue-Tests ersetzen Kontext,
PDF-Download und Fotospeicherung. Es wurden keine produktiven HTTP-/API-Aufrufe,
Collector-Vollausführungen, MariaDB-Synchronisationen, E-Mails, Benachrichtigungen
oder Strato-Bereitstellungen gestartet. Die folgenden Ebenen bleiben ungeprüft:

- heutige Erreichbarkeit und Vollständigkeit sämtlicher amtlicher Quellen;
- aktueller öffentlicher Datenbestand und vollständiger Enrichment-/Medienlauf;
- Laufzeitverhalten des Sync-Clients, `field-chunks-v1`, Wiederaufnahme,
  Hash-Bestätigungen und produktiver PHP-/MariaDB-Abschluss;
- produktive Website-Darstellung, Login, Zahlung, E-Mail und SEO.

Für spätere Sync-Arbeit sind zusätzlich isolierte Tests mit einer simulierten
`post`-Funktion erforderlich. Ein Live-Test ist ein gesonderter Auftrag. Diese
Baseline ändert weder Zeitpläne noch öffentliche Datenformate oder Datenbestände.
