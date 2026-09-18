import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'collector'))
from official_notice_common import *

mv='''Bekanntmachung des Amtsgerichts Rostock Vom 5. November 2026 68 K 28/25 Im Wege der Zwangsvollstreckung soll am Mittwoch, 28. Januar 2027, um 13:00 Uhr, im Amtsgericht Rostock, Zochstraße 13, 18057 Rostock öffentlich versteigert werden: Grundstück. Objektbeschreibung/Lage: Einfamilienhaus. Verkehrswert: 240.000,00 EUR'''
r=make_record('mv','test','https://example.invalid',normalize_case('68 K 28/25'),mv)
assert r and r['court']=='Rostock' and r['market_value']==240000 and r['postcode']=='18057' and r['auction_date'].startswith('2027-01-28T13:00')

hh='''Zwangsversteigerung 717 K 13/26. Im Wege der Zwangsvollstreckung soll das in 22041 Hamburg, Musterstraße 1 belegene Wohnungseigentum durch das Gericht versteigert werden. Verkehrswert gemäß § 74 a Absatz 5 ZVG: 320 000,00 Euro. Der Versteigerungstermin wird bestimmt auf Dienstag, den 12. Februar 2027, 10.00 Uhr, vor dem Amtsgericht Hamburg-Wandsbek.'''
r=make_record('hh','test','https://example.invalid',normalize_case('717 K 13/26'),hh)
assert r and r['market_value']==320000 and r['postcode']=='22041' and 'Wandsbek' in (r['court'] or '')

sh='''Amtsgericht Lübeck 52 K 30/26. Im Wege der Zwangsvollstreckung soll am Donnerstag, 18. März 2027, um 10:00 Uhr im Amtsgericht Lübeck ein Grundstück öffentlich versteigert werden. Verkehrswert: 410.000,00 EUR.'''
r=make_record('sh','test','https://example.invalid',normalize_case('52 K 30/26'),sh)
assert r and r['court']=='Lübeck' and r['market_value']==410000


assert clean('No' + chr(2) + 'vember')=='November'
assert clean('Hamburg' + chr(173) + '-Barmbek')=='Hamburg-Barmbek'
assert parse_german_date(clean('soll am 6. November 2027, 9 Uhr öffentlich versteigert werden'))=='2027-11-06T09:00'

sh_trailing='''Amtsgericht Ahrensburg soll am Donnerstag, 5. November 2027, um 10.00 Uhr ein Grundstück öffentlich versteigert werden. Beschreibung ''' + ('x '*600) + ''' 70 K 25/25 Amtsgericht Ahrensburg. Nächste Sache soll am 3. Dezember 2027, 9 Uhr öffentlich versteigert werden. ''' + ('y '*500) + ''' 71 K 26/25 Amtsgericht Ahrensburg'''
parts=split_case_chunks(sh_trailing,case_at_end=True)
assert len(parts)==2
assert parse_german_date(parts[0][1])=='2027-11-05T10:00'
assert parse_german_date(parts[1][1])=='2027-12-03T09:00'

print('official notice parser tests: OK')
