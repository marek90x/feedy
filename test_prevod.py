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
            _, s = self.preved(feed(DOBRA, ('3', 'Henna Black', 'Henna Black', 'https://www.voono.cz/c', ''), druha), konf(over_url=True))
        self.assertEqual(s['vyradene'], {'URL nefunguje': 1})

    def test_mimo_webu_vyradi(self):
        druha = ('2', 'Henna Red', 'Henna Red', 'https://www.voono.cz/b', '')
        with mock.patch.object(prevod, 'stiahni', return_value=feed(DOBRA, druha)):
            _, s = prevod.preved(konf(), None, None, {'1': {}})
        self.assertEqual(s['vyradene'], {'nie je na webe (Woo)': 1})

    def test_spadnuty_web_zastavi(self):
        with mock.patch.object(prevod, 'http_stav', return_value=503):
            with self.assertRaises(prevod.Poistka):
                self.preved(feed(DOBRA), konf(over_url=True))

    def test_duplicitny_nazov_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(DOBRA, ('2',) + DOBRA[1:]), konf())

    def test_premenovanie(self):
        k = konf(premenovat={'polia': ['PRODUCTNAME'], 'kluc': {'pole': 'URL', 'regex': '/barvy/([^/?]+)/'},
                             'odtien': {'pole': 'PRODUCT', 'regex': '^(?:Henna )?(.+)$'},
                             'sablony': {'h': 'VOONO Henna barva na vlasy {odtien}'}, 'mapa': {'copper': 'h'},
                             'predpona_ostatnym': 'VOONO '})
        xml, _ = self.preved(feed(('1', 'Henna Copper', 'Henna Copper', 'https://voono.cz/barvy/copper/', ''),
                                  ('2', 'Henna sampon', 'Henna sampon', 'https://voono.cz/sampon/', '')), k)
        self.assertIn('<PRODUCTNAME>VOONO Henna barva na vlasy Copper</PRODUCTNAME>', xml.decode())
        self.assertIn('<PRODUCTNAME>VOONO Henna sampon</PRODUCTNAME>', xml.decode())

    def test_neznamy_odtien_zastavi(self):
        k = konf(premenovat={'polia': ['PRODUCTNAME'], 'kluc': {'pole': 'URL', 'regex': '/barvy/([^/?]+)/'},
                             'odtien': {'pole': 'PRODUCT', 'regex': '(.+)'}, 'sablony': {}, 'mapa': {}})
        with self.assertRaises(prevod.Poistka):
            self.preved(feed(('1', 'Henna Nova', 'Henna Nova', 'https://voono.cz/barvy/nova/', '')), k)

    # --- Meta ---------------------------------------------------------------------------------
    def meta(self, polozky, woo, **zmena):
        k = {'klient': 'test', 'platforma': 'meta', 'zdroj': 'x', 'povolene_domeny': ['voono.cz'], 'mena': 'CZK',
             'min_ponuk': 1, 'gtin_predpona': '859', 'doplnit': {'brand': 'VOONO', 'condition': 'new'},
             'vyradit': [{'pole': 'title', 'regex': '1000\\s?g', 'dovod': 'b2b'}],
             'kategorie': [{'pole': 'link', 'regex': '/barvy/', 'kategoria': 'Barvy', 'gpc': 2814}],
             'stitky': {'custom_label_0': {'typ': 'akcia', 'ano': 'akce', 'nie': 'bez akce'}}}
        k.update(zmena)
        telo = ''.join('<item><g:id>%s</g:id><g:title>%s</g:title><g:description>&lt;p&gt;Popis&lt;/p&gt;</g:description>'
                       '<g:link>%s</g:link><g:image_link>https://www.voono.cz/i.jpg</g:image_link>'
                       '<g:price>249.00 CZK</g:price><g:availability>in_stock</g:availability><g:gtin>%s</g:gtin></item>' % p
                       for p in polozky)
        xml = ('<rss xmlns:g="%s" version="2.0"><channel>%s</channel></rss>' % (prevod.G, telo)).encode()
        with mock.patch.object(prevod, 'stiahni', return_value=xml):
            return prevod.preved_meta(k, woo)

    @staticmethod
    def woo(bezna, akcna, sklad=True):
        return {'prices': {'regular_price': str(bezna * 100), 'sale_price': str(akcna * 100), 'currency_minor_unit': 2,
                           'currency_code': 'CZK'}, 'is_in_stock': sklad, 'variation': 'Balení: Bez obalu, Objem: 100 g'}

    def test_meta_cena_a_akcia_z_webu(self):
        xml, s = self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', '8595654000015')],
                           {'1': self.woo(299, 259)})
        t = xml.decode()
        self.assertIn('<g:price>299.00 CZK</g:price>', t)
        self.assertIn('<g:sale_price>259.00 CZK</g:sale_price>', t)
        self.assertIn('<g:custom_label_0>akce</g:custom_label_0>', t)
        self.assertIn('<g:description>Popis</g:description>', t)
        self.assertIn('<g:brand>VOONO</g:brand>', t)
        self.assertEqual(s['opravy']['cena_z_webu'], 1)

    def test_meta_obrazky_z_webu(self):
        w = self.woo(279, 279)
        w['images'] = [{'src': 'https://www.voono.cz/i.jpg'}, {'src': 'https://www.voono.cz/vlasy.jpg'},
                       {'src': 'https://www.voono.cz/cover-barva-maska-miska.png'}, {'src': 'https://cdn.cizi.com/x.jpg'}]
        xml, s = self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', '')], {'1': w},
                           obrazky_z_webu=True, obrazky_vynechat='/cover-barva-maska-miska\\.png$')
        t = xml.decode()
        self.assertEqual(t.count('<g:additional_image_link>'), 1)
        self.assertIn('vlasy.jpg', t)
        self.assertNotIn('cover-barva', t)
        self.assertEqual(s['opravy']['obrazky_z_webu'], 1)

    def test_meta_bez_akcie_bez_sale_price(self):
        xml, _ = self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', '')], {'1': self.woo(279, 279)})
        self.assertNotIn('sale_price', xml.decode())

    def test_meta_mimo_webu_a_1000g_vyradi(self):
        _, s = self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', ''),
                          ('2', 'Copper 1000g', 'https://www.voono.cz/barvy/copper-1000/', ''),
                          ('3', 'Copper bez obalu', 'https://www.voono.cz/barvy/copper/?x=1', '')],
                         {'1': self.woo(279, 279), '2': self.woo(700, 700)})
        self.assertEqual(s['vyradene'], {'b2b': 1, 'nie je na webe (Woo)': 1})
        self.assertEqual(s['vystup_ponuk'], 1)

    def test_meta_interny_a_zdielany_gtin_vynecha(self):
        xml, _ = self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', '10000113'),
                            ('2', 'Orange', 'https://www.voono.cz/barvy/orange/', '8595654000015'),
                            ('3', 'Wine', 'https://www.voono.cz/barvy/wine/', '8595654000015')],
                           {i: self.woo(279, 279) for i in '123'})
        self.assertNotIn('<g:gtin>', xml.decode())

    def test_meta_cudzia_domena_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.meta([('1', 'Copper', 'https://jahodarna.cz/barvy/copper/', '')], {'1': self.woo(279, 279)})

    def test_meta_bez_kategorie_zastavi(self):
        with self.assertRaises(prevod.Poistka):
            self.meta([('1', 'Šampon', 'https://www.voono.cz/sampon/', '')], {'1': self.woo(279, 279)})

    def test_meta_ina_mena_zastavi(self):
        w = self.woo(12, 12)
        w['prices']['currency_code'] = 'EUR'
        with self.assertRaises(prevod.Poistka):
            self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', '')], {'1': w})

    def test_meta_neznamy_odtien_v_stitku_zastavi(self):
        st = {'custom_label_1': {'typ': 'mapa', 'pole': 'link', 'regex': '/barvy/([^/?]+)/', 'mapa': {}}}
        with self.assertRaises(prevod.Poistka):
            self.meta([('1', 'Copper', 'https://www.voono.cz/barvy/copper/', '')], {'1': self.woo(279, 279)}, stitky=st)

    def test_meta_presmerovanie_na_kategoriu_je_mrtve(self):
        self.assertFalse(prevod.url_ziva((200, 'https://www.voono.cz/e-shop/a/eko/?x=1',
                                          'https://www.voono.cz/kategorie-produktu/sety/?x=1')))
        self.assertTrue(prevod.url_ziva((200, 'https://www.voono.cz/e-shop/a/eko/?x=1', 'https://www.voono.cz/e-shop/a/eko/?x=1')))
        self.assertFalse(prevod.url_ziva((404, 'https://www.voono.cz/a/', 'https://www.voono.cz/a/')))

    def test_ean(self):
        self.assertTrue(prevod.ean_platny('8595654000015'))
        self.assertFalse(prevod.ean_platny('8595654000016'))
        self.assertFalse(prevod.ean_platny('100118'))


if __name__ == '__main__':
    unittest.main()
