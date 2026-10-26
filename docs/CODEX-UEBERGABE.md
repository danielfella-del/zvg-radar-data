# ZVGPro – Übergabe an Codex

Stand: 26. Oktober 2026. Geprüfte Codebasis vor dieser Übergabe: `1271446dbe6ee54f7a75662fa3e4246e925d6951`.

## Was bereits vorliegt

| Bestandteil | Zuständige Dateien / Ort |
| --- | --- |
| Portal-Collector und 16 konfigurierte Bundesländer | `collector/collect.py` |
| Zusätzliche amtliche Quellen BW, HH, MV, SH | `collector/collect_*_official.py` |
| Isolierte HH/MV-Erkennung und Abgleich | `collector/hh_mv_notices.py`, `collector/merge_supplemental.py` |
| Details, Dokumente, Fotos und Analyse | `collector/enrich_details.py`, `cache_media.py`, `extract_report_photos.py`, `analyze_documents.py` |
| Veröffentlichter Datenbestand | `data/auctions.json`, `media/` |
| MariaDB-Sync-Client | `.github/scripts/sync-zvgpro-db.py` |
| Automatisierung | `.github/workflows/` |
| Kleine statische Feed-Ansicht | `strato/`; nicht die vollständige produktive Website |

Die produktive Website liegt bei Strato unter **https://zvgpro.de**. Das öffentliche Feed-API ist **https://zvgpro.de/api/feed.php**. Der vollständige aktuelle PHP-/JavaScript-Stand, Login, Zahlungsintegration und serverseitige Konfiguration gehören nicht zu diesem Repository.

## Betriebsablauf

1. `update-zvg.yml` sammelt Termine, ergänzt amtliche Quellen, übernimmt Historie und schreibt den Datenbestand.
2. `enrich-zvg.yml` ergänzt Details, Medien, Gutachtenfotos und Analysen.
3. `sync-zvgpro-db.yml` überträgt einen unveränderlichen Repository-Snapshot an das PHP-Sync-API auf Strato.

Konfigurierte Zeitpläne: Collector stündlich zur Minute 17, Anreicherung zur Minute 37, DB-Sync zu den Minuten 7, 22, 37 und 52. Das sind GitHub-Zeitpläne, keine garantierten Startzeiten.

Collector und Anreicherung teilen `zvg-data-writer`; ein wartender Job liest `main` erst beim Checkout. Der DB-Sync lädt dagegen JSON und Client aus seinem festen `GITHUB_SHA`. Ein erneuter Lauf eines alten Sync-Jobs importiert deshalb nicht automatisch den neuesten Datenstand. Für einen ausdrücklich gewünschten aktuellen Sync einen neuen Lauf auf `main` starten.

Der Sync verwendet das GitHub-Secret `ZVGPRO_DB_SYNC_TOKEN`. Es ist für lokale Entwicklung und Offline-Tests nicht erforderlich und darf nicht in Codex-Projektdateien kopiert werden. Das serverseitige Gegenstück sind `api/github-sync.php`, `api/_remote-sync.php` und `api/_remote-sync-transfer.php` im Webspace.

## Entwicklung ohne Produktionszugriff

```bash
bash scripts/setup-dev.sh
bash scripts/test-offline.sh
```

Das Setup installiert die bestehenden drei Python-Abhängigkeiten in `.venv`. Der Testlauf prüft die Python-Syntax und führt diese bestehenden Tests aus:

- Portalparser und Detailparser
- Konfiguration der 16 Bundesländer
- Amtliche Bekanntmachungen einschließlich 11 HH/MV-Regressionstests
- 7 Tests zur Warteschlange für Gutachtenfotos

Es werden keine Produktionsimporte, E-Mails oder Collector-Abrufe gestartet. Der Test zur Bundesländerliste bestätigt die Konfiguration, nicht die heutige Vollständigkeit der Quelldaten. Ein grüner Offline-Test ersetzt keinen beauftragten Live-Test.

Für Arbeiten am Sync zusätzlich dessen Verhalten mit einer simulierten `post`-Funktion testen. Transportlimits, verlorene Antworten, Wiederaufnahme und vollständiger Abschluss sind dabei die wesentlichen Risiken. Der bisherige Test-Wrapper enthält keinen vollständigen End-to-End-Test zwischen PHP und MariaDB.

Das Repository enthält einen großen Medienbestand und eine umfangreiche Historie. Für reine Parserarbeit genügen Quellcode und Fixtures; keinen kompletten Medien-Download oder Umbau der Git-Historie als vermeintliche Optimierung durchführen.

## Was für die vollständige Website-Übernahme noch fehlt

Ein aktueller, vollständiger Export des zu **zvgpro.de** gehörenden Webspace-Verzeichnisses einschließlich PHP, JavaScript, CSS, Assets und `.htaccess` ist noch erforderlich. Vor dem Einchecken müssen echte Zugangsdaten, personenbezogene Laufzeitdaten, Logs, Caches und Datenbankexporte ausgeschlossen werden; Konfigurationen als Vorlagen mit Platzhaltern übernehmen. Der Webcode gehört in ein **privates** Repository, nicht ungeprüft in dieses öffentliche Daten-Repository.

Die gefundenen Archive sind Referenzen, keine verifizierte aktuelle Website-Basis:

| Archiv | Einordnung |
| --- | --- |
| `ZVGPro-1.4.8-RICHTWERTE-UMFELD-FLAT.zip` | 60 Dateien; auch dieses ältere Paket enthält nicht sämtliche Website-Dateien |
| `ZVGPro-1.4.18-SEO-KORREKTUR-FLAT.zip` | 7 Dateien; späterer SEO-Teilpatch |
| `ZVGPro-Sync-Fix-20261010-Flat.zip` | 5 Dateien; drei PHP-Sync-Dateien und Dokumentation |

Diese Pakete dürfen nicht zusammenkopiert und als aktueller Komplettstand ausgegeben werden. Öffentlich ausgelieferte HTML-Seiten ersetzen den PHP-Quellcode nicht.

Nach einem vollständigen Export: Herkunft und Stand dokumentieren, sensible Inhalte ausschließen, private Codebasis anlegen, an die Codex-Umgebung anbinden und zunächst lokal prüfen. Die Übernahme selbst erfordert keine Änderung oder erneute Bereitstellung der laufenden Website.

## Belegte jüngere Änderung

Am 16. Oktober 2026 wurden Hamburg und Mecklenburg-Vorpommern in Commit `c2046992be60a95ca70ef144ddc57026927c67c3` gezielt korrigiert. Die Parser sind mit echten Text-Fixtures abgesichert. Andere Bundesländer sollten dadurch nicht umgebaut werden. Historische Verfahrenszahlen sind keine Zusicherung für den heutigen Bestand.

## Produktvorgaben für die spätere Website-Arbeit

- Amtlicher Bodenrichtwert hat Vorrang. Fehlt er, die bestehende PLZ-Umfeldschätzung als **Orientierung** ausweisen.
- Keine fehlenden Objektangaben, Bewertungen oder amtlichen Nachweise erfinden.
- Logo und vorhandene Gestaltung erhalten; kleine Änderungen gezielt auf Mobilgerät, Tablet und Desktop prüfen.
- Verständliches Deutsch, konsistente Schriften und Buttons. Keine Werbung mit unvollständigen Beispielberichten.
- Änderungen nur am beauftragten Bereich; Erfolg und Grenzen anhand konkreter Prüfungen berichten.
