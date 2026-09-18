# ZVG Radar – Datenfeed

Dieses Repository sammelt öffentlich veröffentlichte Zwangsversteigerungstermine aus dem gemeinsamen ZVG-Portal und schreibt sie nach `data/auctions.json`.

## Datenfeed

https://raw.githubusercontent.com/danielfella-del/zvg-radar-data/main/data/auctions.json

## Automatische Aktualisierung

Der GitHub-Actions-Workflow läuft zweimal täglich sowie manuell über **Actions → ZVG Daten aktualisieren → Run workflow**.

## Hinweise

- Die amtliche Veröffentlichung bleibt maßgeblich.
- Hamburg und Mecklenburg-Vorpommern laufen nicht über dieselbe gemeinsame Portalstrecke und sind in diesem Collector derzeit nicht enthalten.
- Ohne Geocoder sind Kartenpositionen nur auf Bundesland-Ebene angenähert.
- Ein Investment-Score wird absichtlich nicht aus dem Verkehrswert allein erfunden.

## Geodaten

Für die Kartenpositionierung auf PLZ-Ebene wird der öffentliche GeoNames-Postleitzahlendatensatz verwendet. Quelle: https://www.geonames.org/ — Lizenz: CC BY 3.0. Die Koordinaten sind PLZ-Zentren bzw. Näherungen und keine exakten Hauskoordinaten.
