import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
from enrich_details import parse_detail_html

HTML = r'''
<html><body>
<table id="anzeige">
<tr><td>Termin:</td><td><b>Freitag, 18. September 2026, 09:30 Uhr</b></td></tr>
<tr><td>Ort der Versteigerung:</td><td>Amtsgericht, Homburger Str. 18, Saal 28</td></tr>
<tr><td>Informationen zum Gläubiger:</td><td>keine</td></tr>
<tr><td>Gericht:</td><td><a href="https://example.invalid/gericht">Internetseite des Gerichtes</a></td></tr>
<tr><td>GeoServer:</td><td><a href="https://example.invalid/karten">Karten, Luftbilder</a></td></tr>
<tr><td>Grundbuch:</td><td>Bad Homburg Blatt 1234</td></tr>
<tr><td>Art der Versteigerung:</td><td>Zwangsversteigerung</td></tr>
<tr><td>Beschreibung:</td><td>Einfamilienhaus mit Garage</td></tr>
<tr><td>Gutachten:</td><td><a href="?button=showAnhang&land_abk=he&file_id=111&zvg_id=999">Gutachten1.pdf</a> 1218,36 kB</td></tr>
<tr><td>amtliche Bekanntmachung:</td><td><a href="?button=showAnhang&land_abk=he&file_id=112&zvg_id=999">amtliche_Bekanntmachung1.pdf</a> 73,53 kB</td></tr>
</table>
</body></html>
'''.encode("cp1252", errors="replace")

d = parse_detail_html(HTML, "he", "999")
assert d["auction_venue"] == "Amtsgericht, Homburger Str. 18, Saal 28", d
assert d["creditor_info"] == "keine", d
assert d["court_url"] == "https://example.invalid/gericht", d
assert d["geoserver_urls"] == ["https://example.invalid/karten"], d
assert d["land_registry"] == "Bad Homburg Blatt 1234", d
assert d["auction_type"] == "Zwangsversteigerung", d
assert d["detail_description"] == "Einfamilienhaus mit Garage", d
assert len(d["detail_attachments"]) == 2, d
assert d["detail_attachments"][0]["type"] == "gutachten", d
assert round(d["detail_attachments"][0]["size_kb"], 2) == 1218.36, d
assert d["detail_attachments"][1]["type"] == "bekanntmachung", d
print("detail parser smoke test: OK")
