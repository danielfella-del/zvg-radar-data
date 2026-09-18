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
<tr><td>Objekt/Lage</td><td><b>Eigentumswohnung : Mainstraße 12, 60311 Frankfurt am Main, Innenstadt</b></td></tr>
<tr><td>Verkehrswert in EUR</td><td><b>Lfd. Nr. 1: 250.000,00 € Lfd. Nr. 2: 15.000,00 €</b></td></tr>
<tr><td>Termin</td><td>Donnerstag, 15. Oktober 2026, 10:00 Uhr</td></tr>
</table>
(letzte Aktualisierung 18-09-2026 09:15)
</body></html>
'''.encode("cp1252", errors="replace")

rows = parse_search_html(HTML, "he")
assert len(rows) == 1, rows
r = rows[0]
assert r["id"] == "he-12345", r
assert r["market_value"] == 265000, r
assert r["market_values"] == [250000, 15000], r
assert r["postcode"] == "60311", r
assert r["city"] == "Frankfurt am Main", r
assert r["address"] == "Mainstraße 12", r
assert r["district"] == "Innenstadt", r
assert r["property_type"] == "Eigentumswohnung", r
assert r["auction_date"] == "2026-10-15T10:00", r
assert r["court"] == "Frankfurt am Main", r

HTML2 = '''
<html><body>
<!-- Aktenzeichen -->
<table>
<tr><td>Amtsgericht</td><td><b>Siegen</b></td></tr>
<tr><td><nobr>0019 K 0051/2025 (Detailansicht)</nobr></td>
<td><a href="index.php?button=showZvg&zvg_id=168348&land_abk=nw">Detailansicht</a></td></tr>
<tr><td>Objekt/Lage</td><td><b>Einfamilienhaus, Gebäude- und Freifläche : Hickengrundstraße 44, 57299 Burbach-Holzhausen</b></td></tr>
<tr><td>Verkehrswert in €</td><td><b>430.000,00 €</b></td></tr>
<tr><td>Termin</td><td>Freitag, 18. September 2026, 09:00 Uhr</td></tr>
</table>
</body></html>
'''.encode("utf-8")

rows2 = parse_search_html(HTML2, "nw")
assert len(rows2) == 1, rows2
r2 = rows2[0]
assert r2["market_value"] == 430000, r2
assert r2["postcode"] == "57299", r2
assert r2["city"] == "Burbach-Holzhausen", r2
assert "Gebäude" in r2["property_type_raw"], r2
assert "Hickengrundstraße" in r2["address"], r2

print("parser smoke tests: OK")
