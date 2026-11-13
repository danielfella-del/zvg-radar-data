"""Synthetic regression cases; no source requests or production data needed."""
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))


def no_network(*args, **kwargs):
    raise AssertionError("Network is forbidden in this parser test")


socket.create_connection = no_network
socket.socket.connect = no_network
from collect import parse_money, parse_money_values, parse_search_html


class DeclaredTotalTest(unittest.TestCase):
    def test_verified_explicit_total(self):
        text = "Lfd. Nr. 1: 120.000,00 € Lfd. Nr. 2: 800,00 € Gesamtwert: 120.800,00 €"
        self.assertEqual(parse_money(text), 120800)
        self.assertEqual(parse_money_values(text), [120000, 800, 120800])

    def test_no_label_does_not_infer_total_from_numbers(self):
        self.assertEqual(parse_money("120.000,00 € 800,00 € 120.800,00 €"), 241600)

    def test_normal_single_and_separate_lots_unchanged(self):
        self.assertEqual(parse_money("25.000,00 €"), 25000)
        self.assertEqual(parse_money("Los 1: 25.000,00 € Los 2: 12.500,00 €"), 37500)
        self.assertIsNone(parse_money("Noch nicht festgesetzt"))

    def test_conflicting_total_not_selected(self):
        self.assertEqual(parse_money("1.000,00 € 500,00 € Gesamtwert: 1.600,00 €"), 3100)

    def test_negative_component_not_reinterpreted_as_positive(self):
        for sign in ["-", "−", "– ", "minus "]:
            self.assertEqual(parse_money(f"{sign}1.000,00 € 500,00 € Gesamtwert: 1.500,00 €"), 3000)

    def test_partial_value_labels_do_not_qualify_for_correction(self):
        for label in ["Teilwert", "Teilwerte", "Zwischensumme", "Teilsumme"]:
            self.assertEqual(parse_money(f"{label}: 1.000,00 € 500,00 € Gesamtwert: 1.500,00 €"), 3000)

    def test_multiple_totals_not_collapsed(self):
        self.assertEqual(parse_money(
            "1.000,00 € 500,00 € Gesamtwert: 1.500,00 € "
            "200,00 € 100,00 € Gesamtwert: 300,00 €"
        ), 3600)

    def test_partial_total_and_later_amount_not_collapsed(self):
        self.assertEqual(parse_money(
            "1.000,00 € 500,00 € Gesamtwert: 1.500,00 € weiter: 300,00 €"
        ), 3300)

    def test_scope_qualifier_not_treated_as_grand_total(self):
        self.assertEqual(parse_money(
            "1.000,00 € 500,00 € Gesamtwert der Gruppe A: 1.500,00 €"
        ), 3000)

    def test_cent_arithmetic_before_integer_rounding(self):
        self.assertEqual(parse_money("1.000,49 € 500,49 € Gesamtwert: 1.500,98 €"), 1501)
        self.assertEqual(parse_money("1.000,49 € 500,49 € Gesamtwert: 1.501,00 €"), 3001)

    def test_variants_and_no_currency_safety(self):
        for label in ["Gesamtwert", "Gesamtverkehrswert", "Verkehrswert insgesamt"]:
            self.assertEqual(parse_money(f"1.000,00 EUR 500,00 EUR {label}: 1.500,00 EUR"), 1500)
        self.assertEqual(parse_money("1.000,00 500,00 Gesamtwert: 1.500,00"), 3000)

    def test_collector_integration(self):
        html = '''<html><body><!-- Aktenzeichen --><table>
          <tr><td>Amtsgericht</td><td>Teststadt</td></tr>
          <tr><td>0001 K 0001/2026</td><td><a href="index.php?button=showZvg&amp;zvg_id=fixture1&amp;land_abk=he">Detail</a></td></tr>
          <tr><td>Objekt/Lage</td><td>Einfamilienhaus : Testweg 1, 12345 Teststadt</td></tr>
          <tr><td>Verkehrswert in EUR</td><td><b>Lfd. Nr. 1: 120.000,00 €<br>Lfd. Nr. 2: 800,00 €<br>Gesamtwert: 120.800,00 €</b></td></tr>
          <tr><td>Termin</td><td>15.12.2026 10:00</td></tr>
        </table></body></html>'''
        row = parse_search_html(html, "he")[0]
        self.assertEqual(row["market_value"], 120800)
        self.assertEqual(row["market_values"], [120000, 800, 120800])
        self.assertIn("Gesamtwert: 120.800,00 €", row["description"])
        self.assertEqual(row["id"], "he-fixture1")


if __name__ == "__main__":
    unittest.main()
