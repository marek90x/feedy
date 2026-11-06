#!/usr/bin/env python3
"""Testy poistiek bez siete: python3 test_prevod.py"""
import unittest
from unittest import mock

import prevod

NS = 'http://www.zbozi.cz/ns/offer/1.0'


def feed(*polozky):
    telo = ''.join('<SHOPITEM><ITEM_ID>%s</ITEM_ID><PRODUCT>%s</PRODUCT><PRODUCTNAME><![CDATA[%s (100019)<span class="description"></span>]]>'
                   '</PRODUCTNAME><URL>%s</URL><EAN>%s</EAN><DESCRIPTION></DESCRIPTION>'
                   '<PRICE_VAT>299</PRICE_VAT><CATEGORYTEXT>Ostatní</CATEGORYTEXT></SHOPITEM>' % p for p in polozky)
    return ('<?xml version="1.0"?><SHOP xmlns="%s">%s</SHOP>' % (NS, telo)).encode()


def konf(**zmena):
    k = {'klient': 'test', 'platforma': 'zbozi', 'zdroj': 'x', 'vystup': 'test/zbozi.xml',
         'povolene_domeny': ['voono.cz'], 'min_ponuk': 1,
         'vyradit': [{'pole': 'PRODUCT', 'regex': '1000\\s?g', 'dovod': 'b2b'}],
         'cistit_nazvy': {'polia': ['PRODUCTNAME'], 'odstranit': ['\\s*\\(#?\\d{4,8}(-\\d+)?\\)']},
         'doplnit': {'MANUFACTURER': 'VOONO'}, 'ean': 'len_platne',
         'kategorie': [{'pole': 'PRODUCT', 'regex': 'henna', 'kategoria': 'A | Barvy'}]}
    k.update(zmena)
    return k


DOBRA = ('1', 'Henna Copper', 'Henna Copper', 'https://www.voono.cz/a', '8595654000015')


class Test(unittest.TestCase):
    def preved(self, xml, k, kategorie=None):
        with mock.patch.object(prevod, 'stiahni', return_value=xml):
            return prevod.preved(k, kategorie)

    def test_oprava(self):
        xml, s = self.preved(feed(DOBRA, ('2', 'Henna 1000g', 'Henna 1000g', 'https://voono.cz/b', '100118')),
                             konf(), {'A | Barvy'})
        text = xml.decode()
        self.assertIn('<PRODUCTNAME>Henna Copper</PRODUCTNAME>', text)
        self.assertIn('<MANUFACTURER>VOONO</MANUFACTURER>', text)
        self.assertIn('<CATEGORYTEXT>A | Barvy</CATEGORYTEXT>', text)
        self.assertNotIn('1000g', text)
        self.assertEqual(s['vyradene'], {'b2b': 1})

    def test_neplatny_ean_odstrani(self):
        xml, _ = self.preved(feed(('1', 'Henna', 'Henna', 'https://voono.cz/a', '800020')), konf())
        self.assertNotIn('<EAN>', xml.decode())

    def test_cudzia_domena_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(('1', 'Henna', 'Henna', 'https://jahodarna.cz/a', '')), konf())

    def test_podobna_domena_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(('1', 'Henna', 'Henna', 'https://voono.cz.evil.com/a', '')), konf())

    def test_bez_kategorie_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(('1', 'Šampon', 'Šampon', 'https://voono.cz/a', '')), konf())

    def test_neexistujuca_kategoria_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(DOBRA), konf(), {'Iná | Kategória'})

    def test_malo_ponuk_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(DOBRA), konf(min_ponuk=150))

    def test_nestiahnutie_zastavi(self):
        with mock.patch.object(prevod, 'stiahni', side_effect=OSError('timeout')):
            with self.assertRaises(prevod.Poistka):
                prevod.preved(konf())

    def test_mrtva_url_vyradi(self):
        druha = ('2', 'Henna Red', 'Henna Red', 'https://www.voono.cz/b', '')
        with mock.patch.object(prevod, 'http_stav', side_effect=lambda u: 404 if u.endswith('/b') else 200):
            _, s = self.preved(feed(DOBRA, DOBRA[:0] + ('3',) + DOBRA[1:], druha), konf(over_url=True))
        self.assertEqual(s['vyradene'], {'URL nefunguje': 1})

    def test_spadnuty_web_zastavi(self):
        with mock.patch.object(prevod, 'http_stav', return_value=503):
            with self.assertRaises(prevod.Poistka):
                self.preved(feed(DOBRA), konf(over_url=True))

    def test_ean(self):
        self.assertTrue(prevod.ean_platny('8595654000015'))
        self.assertFalse(prevod.ean_platny('8595654000016'))
        self.assertFalse(prevod.ean_platny('100118'))


if __name__ == '__main__':
    unittest.main()
