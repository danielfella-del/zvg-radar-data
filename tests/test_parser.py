import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
from collect import parse_search_html

HTML = b'''<html><body><table>
<tr><td>15.10.2026 10:00</td><td>Amtsgericht Frankfurt am Main</td><td>12 K 34/26</td><td>Eigentumswohnung 60311 Frankfurt am Main</td><td>250.000 EUR</td><td><a href="index.php?button=showZvg&zvg_id=12345&land_abk=he">Details</a></td></tr>
</table></body></html>'''

rows = parse_search_html(HTML, "he")
assert len(rows) == 1
r = rows[0]
assert r["id"] == "he-12345"
assert r["market_value"] == 250000
assert r["postcode"] == "60311"
assert r["property_type"] == "Eigentumswohnung"
assert r["auction_date"].startswith("2026-10-15T10:00")
print("parser smoke test: OK")
