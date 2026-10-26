# ZVG Radar – Datenpipeline für ZVGPro

Dieses Repository sammelt öffentlich veröffentlichte Zwangsversteigerungstermine, ergänzt Dokumente und Medien und veröffentlicht den Datenbestand für ZVGPro.

## Mit Codex weiterarbeiten

Die Projektregeln stehen in [AGENTS.md](AGENTS.md). Produktziel, Gestaltung und Datenregeln sind zusätzlich in [PROJECT.md](PROJECT.md), [DESIGN.md](DESIGN.md) und [DATA.md](DATA.md) festgehalten. Einrichtung, Betriebsablauf und der noch fehlende vollständige Website-Quellcode sind in [docs/CODEX-UEBERGABE.md](docs/CODEX-UEBERGABE.md) beschrieben.

```bash
bash scripts/setup-dev.sh
bash scripts/test-offline.sh
```

Die Entwicklungsprüfungen verwenden vorhandene Fixtures und benötigen keine Produktionszugänge. Python 3.12 entspricht den GitHub-Workflows.

## Datenfeed

https://raw.githubusercontent.com/danielfella-del/zvg-radar-data/main/data/auctions.json

Die vollständige produktive Website liegt bei Strato. `strato/` in diesem Repository ist lediglich eine einfache statische Feed-Ansicht.

## Automatische Aktualisierung

- `update-zvg.yml`: Collector stündlich zur Minute 17.
- `enrich-zvg.yml`: Anreicherung stündlich zur Minute 37.
- `sync-zvgpro-db.yml`: MariaDB-Sync zu den Minuten 7, 22, 37 und 52.

GitHub kann geplante Läufe verzögern. Die Workflows lassen sich zusätzlich manuell starten. Ein solcher Start kann Produktionsdaten oder Benachrichtigungen verändern und gehört nicht zum Entwicklungs-Setup.

## Quellen und Datenqualität

Alle 16 Bundesländer sind konfiguriert. Zusätzlich zur gemeinsamen Portalstrecke gibt es amtliche Adapter für Baden-Württemberg, Hamburg, Mecklenburg-Vorpommern und Schleswig-Holstein. Hamburg und Mecklenburg-Vorpommern verwenden eigene amtliche Bekanntmachungen. Die Quellenerreichbarkeit und der aktuelle Datenbestand müssen bei Bedarf separat geprüft werden.

Die amtliche Veröffentlichung bleibt maßgeblich. Ein Investment-Score wird nicht aus dem Verkehrswert allein erfunden.

Der Collector verwendet die [PLZ-Geokoordinaten des WZB](https://github.com/WZBSocialScienceCenter/plz_geocoord). Kartenpositionen können PLZ- oder Bundesland-Näherungen sein; sie sind kein Nachweis exakter Hauskoordinaten.
