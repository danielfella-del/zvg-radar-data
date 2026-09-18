import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
from collect import parse_search_html

HTML = r'''
<html><body>
<!-- Aktenzeichen -->
<table>
<tr><td>Amtsgericht</td><td><b>Frankfurt am Main</b></td></tr>
<tr><td><nobr>0030 K 0042/2025 (Detailansicht)</nobr></td>
<td><a href="index.php?button=showZvg&zvg_id=12345&land_abk=he">Detailansicht</a></td></tr>
<tr><td>Objekt/Lage</td><td><b>Eigentumswohnung:</b> Mainstraße 12, 60311 Frankfurt am Main, Innenstadt</td></tr>
<tr><td>Verkehrswert in EUR</td><td><b>250.000,00 €</b></td></tr>
<tr><td>Termin</td><td>Donnerstag, 15. Oktober 2026, 10:00 Uhr</td></tr>
<tr><td>Anhänge</td><td>
<a href="index.php?button=showAnhang&land_abk=he&file_id=9988&zvg_id=12345">Gutachten.pdf</a>
</td></tr>
</table>
(letzte Aktualisierung 18-09-2026 09:15)
</body></html>
'''.encode("utf-8")

rows = parse_search_html(HTML, "he")
assert len(rows) == 1, rows
r = rows[0]
assert r["id"] == "he-12345", r
assert r["market_value"] == 250000, r
assert r["postcode"] == "60311", r
assert r["city"] == "Frankfurt am Main", r
assert r["address"] == "Mainstraße 12", r
assert r["property_type"] == "Eigentumswohnung", r
assert r["auction_date"] == "2026-10-15T10:00", r
assert r["court"] == "Frankfurt am Main", r
assert r["has_report"] is True, r
assert "showAnhang" in r["gutachten_url"], r
print("parser smoke test: OK")
