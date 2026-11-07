#!/usr/bin/env python3
"""Prevodník produktových feedov: stiahne feed klienta, opraví ho podľa konfigurácie a uloží opravenú kópiu.

    python3 prevod.py klienti/voono_zbozi.json          # jeden klient a platforma
    python3 prevod.py --vsetky                          # všetky klienti/*.json (GitHub Actions)
    python3 prevod.py klienti/voono_zbozi.json --vystup /tmp/test   # test mimo docs/

Len štandardná knižnica. Každý klient má vlastný konfiguračný súbor, vlastný výstup a vlastnú poistku
na domény; chyba jedného klienta nezastaví ostatných, ale beh skončí kódom 2 (GitHub pošle mail).

Poistky: keď sa feed nestiahne, má málo ponúk, URL mimo povolených domén, ponuku bez cieľovej kategórie
alebo kategóriu, ktorá na platforme neexistuje, opravený feed sa NEPREPÍŠE a platforma ďalej ťahá posledný dobrý.
"""
import argparse
import concurrent.futures
import csv
import datetime
import glob
import gzip
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

KOREN = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'feed-prevodnik/1.0'}

PLATFORMY = {
    'zbozi': {
        'ns': 'http://www.zbozi.cz/ns/offer/1.0',
        'polozka': 'SHOPITEM',
        'kategoria': 'CATEGORYTEXT',
        'popis': 'DESCRIPTION',
        'ean': 'EAN',
        'id': 'ITEM_ID',
        'skupina': 'ITEMGROUP_ID',
        'url_polia': ['URL', 'IMGURL'],
        'zoznam_kategorii': 'https://www.zbozi.cz/static/categories.csv',
    },
}


TAXONOMIA_GOOGLE = 'https://www.google.com/basepages/producttype/taxonomy-with-ids.en-US.txt'


class Poistka(Exception):
    """Opravený feed sa nesmie zverejniť."""


def stiahni(url, timeout=120):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as o:
        return o.read()


def bez_html(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def cisty_text(text):
    """HTML → čistý text s odsekmi (špecifikácia Zboží: bez HTML, odseky oddelené riadkom)."""
    text = re.sub(r'(?i)<br\s*/?>|</(p|div|li|h[1-6])>', '\n', text or '')
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    odseky = [re.sub(r'[ \t\xa0]+', ' ', o).strip() for o in text.split('\n')]
    return '\n'.join(o for o in odseky if o)


def ean_platny(kod):
    """GS1 kontrolná číslica pre EAN-8, UPC-A, EAN-13 a GTIN-14."""
    if not re.fullmatch(r'\d{8}|\d{12}|\d{13}|\d{14}', kod or ''):
        return False
    cisla = [int(c) for c in kod]
    suma = sum(c * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(cisla[:-1])))
    return (10 - suma % 10) % 10 == cisla[-1]


def kategorie_zbozi(url):
    """Celé cesty kategórií Zboží (CSV v cp1250, oddeľovač ;)."""
    text = stiahni(url).decode('cp1250')
    riadky = list(csv.reader(io.StringIO(text), delimiter=';'))
    return {r[2].strip() for r in riadky[1:] if len(r) >= 3}


def popisy_woo(url):
    """Krátke popisy z verejného WooCommerce Store API (bez kľúča), podľa ID produktu."""
    popisy, strana = {}, 1
    while True:
        oddelovac = '&' if '?' in url else '?'
        data = json.loads(stiahni('%s%sper_page=100&page=%d' % (url, oddelovac, strana)))
        for p in data:
            text = cisty_text(p.get('short_description') or p.get('description') or '')
            if text:
                popisy[str(p['id'])] = text
        if len(data) < 100:
            return popisy
        strana += 1


def http_stav(url):
    """HTTP kód cieľovej stránky (presmerovanie sa sleduje). Text chyby, keď server neodpovie."""
    for metoda in ('HEAD', 'GET'):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=metoda, headers=UA), timeout=30) as o:
                return o.status
        except urllib.error.HTTPError as e:
            if e.code == 405 and metoda == 'HEAD':
                continue
            return e.code
        except Exception as e:
            chyba = str(e)[:60]
    return chyba


def hodnota(polozka, tag, ns):
    return (polozka.findtext('{%s}%s' % (ns, tag)) or '').strip()


def nastav(polozka, tag, ns, text, pred=None):
    el = polozka.find('{%s}%s' % (ns, tag))
    if el is None:
        el = ET.Element('{%s}%s' % (ns, tag))
        deti = list(polozka)
        idx = next((i for i, d in enumerate(deti) if pred and d.tag == '{%s}%s' % (ns, pred)), len(deti))
        polozka.insert(idx, el)
    el.text = text


def parametre(polozka, ns):
    return {(p.findtext('{%s}PARAM_NAME' % ns) or '').strip(): (p.findtext('{%s}VAL' % ns) or '').strip()
            for p in polozka.findall('{%s}PARAM' % ns)}


def novy_nazov(ziskaj, par, pravidla, aktualny):
    """Názov podľa šablóny. Kľúč (napr. časť URL) určí šablónu; neznámy kľúč = Poistka, nič sa nehádže naslepo.
    `ziskaj(pole)` vráti hodnotu poľa ponuky, `par` sú parametre ponuky (Objem, Balení)."""
    m = re.search(pravidla['kluc']['regex'], ziskaj(pravidla['kluc']['pole']))
    if not m:
        predpona = pravidla.get('predpona_ostatnym', '')
        if predpona and predpona.strip().lower() not in aktualny.lower():
            return predpona + aktualny
        return aktualny
    sablona = pravidla['mapa'].get(m.group(1))
    if sablona is None:
        raise Poistka('„%s“ (%s) nemá v konfigurácii overenú šablónu názvu' % (aktualny, m.group(1)))
    od = pravidla['odtien']
    mo = re.search(od['regex'], ziskaj(od['pole']))
    odtien = mo.group(1) if mo else ''
    for vzor, nahrada in od.get('upravy', []):
        odtien = re.sub(vzor, nahrada, odtien)

    def dosad(x):
        meno, _, filtr = x.group(1).partition('|')
        val = odtien.strip() if meno == 'odtien' else par.get(meno.split(':', 1)[1], '') if meno.startswith('param:') else ''
        return val.lower() if filtr == 'lower' else val
    return re.sub(r'\s+', ' ', re.sub(r'\{([^}]+)\}', dosad, pravidla['sablony'][sablona])).strip()


def najdi_kategoriu(polozka, ns, konf):
    pid = hodnota(polozka, PLATFORMY[konf['platforma']]['id'], ns)
    if pid in konf.get('kategorie_podla_id', {}):
        return konf['kategorie_podla_id'][pid]
    for pravidlo in konf['kategorie']:
        if re.search(pravidlo['regex'], hodnota(polozka, pravidlo['pole'], ns), re.I):
            return pravidlo['kategoria']
    return None


def preved(konf, zoznam_kategorii=None, popisy=None, woo=None):
    """Vráti (bajty XML, súhrn). Pri porušení poistky vyhodí Poistka."""
    pl = PLATFORMY[konf['platforma']]
    ns = pl['ns']
    try:
        koren = ET.fromstring(stiahni(konf['zdroj']))
    except Exception as e:
        raise Poistka('zdrojový feed sa nedá stiahnuť alebo prečítať: %s' % e)
    polozky = koren.findall('{%s}%s' % (ns, pl['polozka']))
    suhrn = {'klient': konf['klient'], 'platforma': konf['platforma'], 'zdroj_ponuk': len(polozky),
             'vyradene': {}, 'kategoria': {}, 'opravy': {'html_v_nazve': 0, 'kod_v_nazve': 0,
                                                       'popis_doplneny': 0, 'ean_odstraneny': 0,
                                                       'html_v_popise': 0}, 'bez_popisu': []}
    if len(polozky) < konf['min_ponuk']:
        raise Poistka('zdroj má %d ponúk, minimum je %d' % (len(polozky), konf['min_ponuk']))

    # Varianty typu „ZACHRAŇ PRODUKT“ často nemajú popis; prevezme sa z bežnej verzie s rovnakým názvom.
    vzory = konf.get('popis_zo_zakladneho', [])
    zakladny_nazov = lambda n: re.sub(r'\s+', ' ', re.sub('|'.join(vzory) or '$^', '', n, flags=re.I)).strip().lower()
    popisy_podla_nazvu = {}
    for p in polozky:
        text = cisty_text(hodnota(p, pl['popis'], ns)) or (popisy or {}).get(hodnota(p, pl['id'], ns), '')
        if text:
            popisy_podla_nazvu.setdefault(zakladny_nazov(hodnota(p, 'PRODUCT', ns)), text)

    # Ponuka, ktorej URL nevedie na živú stránku, sa na Zboží nesmie zobraziť (pravidlá 2.9 a 3.5).
    mrtve = set()
    if konf.get('over_url'):
        urls = sorted({hodnota(p, 'URL', ns) for p in polozky})
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            stavy = dict(zip(urls, ex.map(http_stav, urls)))
        mrtve = {u for u, st in stavy.items() if not (isinstance(st, int) and st < 400)}
        podiel = len(mrtve) / max(len(urls), 1)
        if podiel > konf.get('max_podiel_mrtvych', 0.5):
            raise Poistka('%d z %d URL nefunguje (%.0f %%), web je asi nedostupný' % (len(mrtve), len(urls), 100 * podiel))

    domeny = konf['povolene_domeny']
    bez_kategorie = []
    for p in polozky:
        dovod = next((v['dovod'] for v in konf.get('vyradit', [])
                      if re.search(v['regex'], hodnota(p, v['pole'], ns), re.I)), None)
        if not dovod and hodnota(p, 'URL', ns) in mrtve:
            dovod = 'URL nefunguje'
        # Variant, ktorý web nepozná (Voono: Předpigmentační bez obalu, Fire red 500 g), sa kúpiť nedá.
        if not dovod and woo is not None and hodnota(p, pl['id'], ns) not in woo:
            dovod = 'nie je na webe (Woo)'
        if not dovod:
            try:
                if float(hodnota(p, 'PRICE_VAT', ns).replace(',', '.')) <= 0:
                    dovod = 'nulová cena'
            except ValueError:
                dovod = 'chýba cena'
        if dovod:
            koren.remove(p)
            suhrn['vyradene'][dovod] = suhrn['vyradene'].get(dovod, 0) + 1
            continue

        for pole in pl['url_polia']:
            url = hodnota(p, pole, ns)
            host = urllib.parse.urlsplit(url).hostname or ''
            if url and not any(host == d or host.endswith('.' + d) for d in domeny):
                raise Poistka('%s %s mimo povolených domén: %s' % (pole, hodnota(p, pl['id'], ns), url))

        for pole in konf.get('cistit_nazvy', {}).get('polia', []):
            povodny = hodnota(p, pole, ns)
            novy = povodny
            if '<' in novy:
                novy = bez_html(novy)
                suhrn['opravy']['html_v_nazve'] += 1
            for vzor in konf['cistit_nazvy'].get('odstranit', []):
                novy, n = re.subn(vzor, '', novy)
                suhrn['opravy']['kod_v_nazve'] += n
            novy = re.sub(r'\s+', ' ', novy).strip()
            if novy != povodny:
                nastav(p, pole, ns, novy)

        for pole, text in konf.get('doplnit', {}).items():
            if not hodnota(p, pole, ns):
                nastav(p, pole, ns, text, pred=pl['kategoria'])

        povodny = hodnota(p, pl['popis'], ns)
        popis = cisty_text(povodny)
        if popis != povodny:
            nastav(p, pl['popis'], ns, popis)
            if '<' in povodny:
                suhrn['opravy']['html_v_popise'] += 1
        if not popis and popisy is not None:
            popis = popisy.get(hodnota(p, pl['id'], ns)) or popisy.get(hodnota(p, pl['skupina'], ns)) or ''
        # Len pre varianty z konfigurácie. EAN na párovanie popisov nepoužívať: Voono má rovnaký EAN pri
        # MAXI kondicionéri Ambra aj levanduľa a pri Oil to milk aj SPF 30 kréme.
        if not popis and vzory and re.search('|'.join(vzory), hodnota(p, 'PRODUCT', ns), re.I):
            zaklad = zakladny_nazov(hodnota(p, 'PRODUCT', ns))
            popis = popisy_podla_nazvu.get(zaklad, '')
            if not popis:
                # „AHA Peeling koncentrát“ → jediný produkt, ktorého názov tak začína. Viac zhôd = nič.
                zhody = {t for n, t in popisy_podla_nazvu.items() if n.startswith(zaklad + ' ')}
                popis = zhody.pop() if len(zhody) == 1 else ''
        if popis and popis != cisty_text(povodny):
            nastav(p, pl['popis'], ns, popis)
            suhrn['opravy']['popis_doplneny'] += 1
        if not popis:
            suhrn['bez_popisu'].append('%s %s' % (hodnota(p, pl['id'], ns), hodnota(p, 'PRODUCT', ns)))

        if konf.get('ean') == 'len_platne':
            el = p.find('{%s}%s' % (ns, pl['ean']))
            if el is not None and not ean_platny((el.text or '').strip()):
                p.remove(el)
                suhrn['opravy']['ean_odstraneny'] += 1

        kat = najdi_kategoriu(p, ns, konf)
        if kat is None:
            bez_kategorie.append('%s %s' % (hodnota(p, pl['id'], ns), hodnota(p, 'PRODUCT', ns)))
            continue
        if zoznam_kategorii is not None and kat not in zoznam_kategorii:
            raise Poistka('kategória „%s“ na platforme neexistuje' % kat)
        nastav(p, pl['kategoria'], ns, kat)
        suhrn['kategoria'][kat] = suhrn['kategoria'].get(kat, 0) + 1

        # Až po kategórii: pravidlá kategórií sa viažu na pôvodný názov.
        if konf.get('premenovat'):
            for pole in konf['premenovat']['polia']:
                povodny = hodnota(p, pole, ns)
                novy = novy_nazov(lambda f: hodnota(p, f, ns), parametre(p, ns), konf['premenovat'], povodny)
                if novy != povodny:
                    nastav(p, pole, ns, novy)
                    suhrn['opravy']['nazov_rozsireny'] = suhrn['opravy'].get('nazov_rozsireny', 0) + 1

    if bez_kategorie:
        raise Poistka('%d ponúk nemá v konfigurácii cieľovú kategóriu: %s'
                      % (len(bez_kategorie), '; '.join(bez_kategorie[:10])))
    # Validátor Zboží berie duplicitný PRODUCTNAME ako chybu a pravidlá chcú každú ponuku len raz.
    nazvy = {}
    for p in koren.findall('{%s}%s' % (ns, pl['polozka'])):
        nazvy.setdefault(hodnota(p, 'PRODUCTNAME', ns), []).append(hodnota(p, pl['id'], ns))
    duplicity = {n: ids for n, ids in nazvy.items() if len(ids) > 1}
    if duplicity:
        raise Poistka('duplicitné PRODUCTNAME: %s' % '; '.join('%s (%s)' % (n, ', '.join(i)) for n, i in list(duplicity.items())[:5]))
    vystup = len(koren.findall('{%s}%s' % (ns, pl['polozka'])))
    suhrn['vystup_ponuk'] = vystup
    if vystup < konf.get('min_vystup', 1):
        raise Poistka('po úprave ostalo %d ponúk, minimum je %d' % (vystup, konf['min_vystup']))
    ET.register_namespace('', ns)
    xml = ET.tostring(koren, encoding='utf-8', xml_declaration=True)
    return xml, suhrn


# ---------------------------------------------------------------------------------------------------
# Meta katalóg (Commerce Manager): zdroj je Google Merchant RSS klienta, výstup RSS s menným priestorom g:.
# Špecifikácia: https://developers.facebook.com/docs/marketing-api/catalog/reference/ (pasce v PASCE.md).

G = 'http://base.google.com/ns/1.0'


def woo_vsetko(url):
    """Produkty aj varianty z verejného WooCommerce Store API podľa ID. Čo tu nie je, sa na webe kúpiť nedá."""
    vsetko = {}
    for typ in ('', 'type=variation&'):
        strana = 1
        while True:
            oddelovac = '&' if '?' in url else '?'
            data = json.loads(stiahni('%s%s%sper_page=100&page=%d' % (url, oddelovac, typ, strana)))
            for p in data:
                vsetko[str(p['id'])] = p
            if len(data) < 100:
                break
            strana += 1
    return vsetko


def http_ciel(url):
    """(HTTP kód, cieľová URL po presmerovaní). Zrušený produkt Woo presmeruje na kategóriu s kódom 200."""
    for metoda in ('HEAD', 'GET'):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=metoda, headers=UA), timeout=30) as o:
                return o.status, o.geturl()
        except urllib.error.HTTPError as e:
            if e.code == 405 and metoda == 'HEAD':
                continue
            return e.code, url
        except Exception as e:
            chyba = str(e)[:60]
    return chyba, url


def url_ziva(stav):
    kod, povodna, ciel = stav
    if not (isinstance(kod, int) and kod < 400):
        return False
    return urllib.parse.urlsplit(ciel).path.rstrip('/') == urllib.parse.urlsplit(povodna).path.rstrip('/')


def taxonomia_google(url):
    """ID kategórií z taxonómie Google (riadky „ID - cesta“)."""
    return {r.split(' - ', 1)[0].strip() for r in stiahni(url).decode('utf-8').splitlines() if ' - ' in r}


def cena(woo, pole):
    pr = woo['prices']
    return int(pr[pole]) / 10 ** pr['currency_minor_unit'], pr['currency_code']


def preved_meta(konf, woo, taxonomia=None):
    """Vráti (bajty XML, súhrn). Cena, zľava a sklad sú z webu (Woo Store API), nie z feedu (Voono: staré ceny variantov)."""
    gv = lambda it, t: (it.findtext('{%s}%s' % (G, t)) or '').strip()
    try:
        koren = ET.fromstring(stiahni(konf['zdroj']))
    except Exception as e:
        raise Poistka('zdrojový feed sa nedá stiahnuť alebo prečítať: %s' % e)
    polozky = koren.findall('channel/item')
    suhrn = {'klient': konf['klient'], 'platforma': konf['platforma'], 'zdroj_ponuk': len(polozky), 'vyradene': {},
             'kategoria': {}, 'stitky': {}, 'opravy': {'cena_z_webu': 0, 'sklad_z_webu': 0, 'gtin_odstraneny': 0,
                                                     'nazov_rozsireny': 0, 'popis_z_webu': 0},
             'zmeny_cien': [], 'bez_popisu': []}
    if len(polozky) < konf['min_ponuk']:
        raise Poistka('zdroj má %d ponúk, minimum je %d' % (len(polozky), konf['min_ponuk']))
    if len(woo) < konf.get('min_woo', 1):
        raise Poistka('Woo Store API vrátilo %d produktov, web je asi nedostupný' % len(woo))

    mrtve = set()
    if konf.get('over_url'):
        urls = sorted({gv(p, 'link') for p in polozky})
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            stavy = dict(zip(urls, ex.map(http_ciel, urls)))
        mrtve = {u for u, (kod, ciel) in stavy.items() if not url_ziva((kod, u, ciel))}
        podiel = len(mrtve) / max(len(urls), 1)
        if podiel > konf.get('max_podiel_mrtvych', 0.5):
            raise Poistka('%d z %d URL nefunguje (%.0f %%), web je asi nedostupný' % (len(mrtve), len(urls), 100 * podiel))

    domeny = konf['povolene_domeny']
    vystup, bez_kategorie, gtiny = [], [], {}
    for p in polozky:
        pid = gv(p, 'id')
        w = woo.get(pid)
        dovod = next((v['dovod'] for v in konf.get('vyradit', []) if re.search(v['regex'], gv(p, v['pole']), re.I)), None)
        if not dovod and gv(p, 'link') in mrtve:
            dovod = 'URL nefunguje'
        if not dovod and w is None:
            dovod = 'nie je na webe (Woo)'
        if not dovod and cena(w, 'regular_price')[0] <= 0:
            dovod = 'nulová cena'
        if dovod:
            suhrn['vyradene'][dovod] = suhrn['vyradene'].get(dovod, 0) + 1
            continue

        polia = {'id': pid, 'item_group_id': gv(p, 'item_group_id'), 'title': bez_html(gv(p, 'title')),
                 'link': gv(p, 'link'), 'image_link': gv(p, 'image_link'),
                 'additional_image_link': [e.text.strip() for e in p.findall('{%s}additional_image_link' % G) if e.text]}
        for pole in ('link', 'image_link'):
            host = urllib.parse.urlsplit(polia[pole]).hostname or ''
            if not any(host == d or host.endswith('.' + d) for d in domeny):
                raise Poistka('%s %s mimo povolených domén: %s' % (pole, pid, polia[pole]))
        # Galéria z webu: obrázky variantu a potom rodiča (Google feed pri farbách posiela len jeden).
        if konf.get('obrazky_z_webu'):
            web = [x['src'] for x in w.get('images', [])]
            if polia['item_group_id'] in woo:
                web += [x['src'] for x in woo[polia['item_group_id']].get('images', [])]
            z_feedu = set(polia['additional_image_link'])
            polia['additional_image_link'] += web
        vynechat = konf.get('obrazky_vynechat')
        videne = {polia['image_link']}
        dalsie = []
        for u in polia['additional_image_link']:
            if u in videne or (vynechat and re.search(vynechat, u)):
                continue
            if not any((urllib.parse.urlsplit(u).hostname or '') in (d, 'www.' + d) for d in domeny):
                continue
            videne.add(u)
            dalsie.append(u)
        polia['additional_image_link'] = dalsie[:konf.get('max_dalsich_obrazkov', 20)]
        if konf.get('obrazky_z_webu') and set(polia['additional_image_link']) - z_feedu:
            suhrn['opravy']['obrazky_z_webu'] = suhrn['opravy'].get('obrazky_z_webu', 0) + 1

        # Cena a zľava z webu. Feed sa porovná, rozdiel ide do súhrnu.
        bezna, mena = cena(w, 'regular_price')
        akcna, _ = cena(w, 'sale_price')
        if mena != konf['mena']:
            raise Poistka('%s má menu %s, feed je v %s' % (pid, mena, konf['mena']))
        feed_cena = gv(p, 'price').split()[0] if gv(p, 'price') else ''
        feed_akcia = gv(p, 'sale_price').split()[0] if gv(p, 'sale_price') else feed_cena
        if feed_cena != '%.2f' % bezna or feed_akcia != '%.2f' % akcna:
            suhrn['opravy']['cena_z_webu'] += 1
            suhrn['zmeny_cien'].append('%s %s: feed %s/%s, web %.2f/%.2f' % (pid, polia['title'], feed_cena, feed_akcia, bezna, akcna))
        polia['price'] = '%.2f %s' % (bezna, mena)
        polia['sale_price'] = '%.2f %s' % (akcna, mena) if akcna < bezna else ''
        sklad = 'in stock' if w.get('is_in_stock') else 'out of stock'
        if sklad.replace(' ', '_') != gv(p, 'availability'):
            suhrn['opravy']['sklad_z_webu'] += 1
        polia['availability'] = sklad

        popis = cisty_text(gv(p, 'description'))
        if not popis:
            popis = cisty_text(w.get('short_description') or w.get('description') or '')
            if popis:
                suhrn['opravy']['popis_z_webu'] += 1
        if not popis:
            suhrn['bez_popisu'].append('%s %s' % (pid, polia['title']))
        polia['description'] = popis[:9999]

        gtin = gv(p, 'gtin')
        if gtin and ean_platny(gtin) and gtin.startswith(konf.get('gtin_predpona', '')):
            polia['gtin'] = gtin
            gtiny.setdefault(gtin, []).append(pid)
        elif gtin:
            suhrn['opravy']['gtin_odstraneny'] += 1

        polia.update(konf.get('doplnit', {}))
        ziskaj = lambda f: polia.get(f, '')
        kat = najdi_kategoriu_meta(ziskaj, konf)
        if kat is None:
            bez_kategorie.append('%s %s' % (pid, polia['title']))
            continue
        polia['product_type'], polia['google_product_category'] = kat['kategoria'], str(kat['gpc'])
        if taxonomia is not None and polia['google_product_category'] not in taxonomia:
            raise Poistka('google_product_category %s neexistuje' % polia['google_product_category'])
        suhrn['kategoria'][kat['kategoria']] = suhrn['kategoria'].get(kat['kategoria'], 0) + 1

        # Štítky pre sady produktov. Neznámy kľúč v mape = Poistka (nový odtieň treba zaradiť ručne).
        for meno, st in konf.get('stitky', {}).items():
            hodn = st_hodnota(st, polia)
            if hodn:
                polia[meno] = hodn
                suhrn['stitky'].setdefault(meno, {})
                suhrn['stitky'][meno][hodn] = suhrn['stitky'][meno].get(hodn, 0) + 1

        # Až po kategórii: pravidlá kategórií sa viažu na pôvodný názov. Parametre z variantu na webe.
        if konf.get('premenovat'):
            par = dict(re.findall(r'([^:,]+):\s*([^,]+)', w.get('variation') or ''))
            par = {k.strip(): v.strip() for k, v in par.items()}
            novy = novy_nazov(ziskaj, par, konf['premenovat'], polia['title'])
            if novy != polia['title']:
                polia['title'] = novy
                suhrn['opravy']['nazov_rozsireny'] += 1
        if len(polia['title']) > 200:
            raise Poistka('%s: názov má %d znakov, Meta povoľuje 200' % (pid, len(polia['title'])))
        vystup.append(polia)

    if bez_kategorie:
        raise Poistka('%d ponúk nemá v konfigurácii cieľovú kategóriu: %s' % (len(bez_kategorie), '; '.join(bez_kategorie[:10])))
    # Zdieľaný GTIN medzi rôznymi produktmi (Voono to má) by Meta mohla spojiť; zdieľaný sa vynechá.
    for gtin, ids in gtiny.items():
        if len(ids) > 1:
            for polia in vystup:
                if polia.get('gtin') == gtin:
                    del polia['gtin']
                    suhrn['opravy']['gtin_odstraneny'] += 1
    nazvy = {}
    for polia in vystup:
        nazvy.setdefault(polia['title'], []).append(polia['id'])
    duplicity = {n: i for n, i in nazvy.items() if len(i) > 1}
    if duplicity:
        raise Poistka('duplicitné názvy: %s' % '; '.join('%s (%s)' % (n, ', '.join(i)) for n, i in list(duplicity.items())[:5]))
    skupiny = {pol['item_group_id'] for pol in vystup if pol['item_group_id']}
    konflikt = [pol['id'] for pol in vystup if pol['id'] in skupiny]
    if konflikt:
        raise Poistka('ID sa zhoduje s item_group_id inej ponuky (Meta: retailer_id_used_by_group): %s' % ', '.join(konflikt))
    suhrn['vystup_ponuk'] = len(vystup)
    if len(vystup) < konf.get('min_vystup', 1):
        raise Poistka('po úprave ostalo %d ponúk, minimum je %d' % (len(vystup), konf['min_vystup']))
    return zapis_rss(konf, vystup), suhrn


def najdi_kategoriu_meta(ziskaj, konf):
    for pravidlo in konf['kategorie']:
        if re.search(pravidlo['regex'], ziskaj(pravidlo['pole']), re.I):
            return pravidlo
    return None


def st_hodnota(st, polia):
    """Hodnota štítku: 'akcia' (je zľava), alebo mapa podľa kľúča z poľa."""
    if st['typ'] == 'akcia':
        return st['ano'] if polia.get('sale_price') else st['nie']
    m = re.search(st['regex'], polia.get(st['pole'], ''))
    if not m:
        return ''
    if m.group(1) not in st['mapa']:
        raise Poistka('„%s“ (%s) nemá v konfigurácii štítok %s' % (polia['title'], m.group(1), st.get('popis', '')))
    return st['mapa'][m.group(1)]


PORADIE_META = ['id', 'item_group_id', 'title', 'description', 'link', 'image_link', 'additional_image_link',
                'availability', 'condition', 'price', 'sale_price', 'brand', 'gtin', 'google_product_category',
                'product_type', 'custom_label_0', 'custom_label_1', 'custom_label_2', 'custom_label_3', 'custom_label_4']


def zapis_rss(konf, vystup):
    ET.register_namespace('g', G)
    rss = ET.Element('rss', {'version': '2.0'})
    kanal = ET.SubElement(rss, 'channel')
    for tag, text in (('title', konf.get('nazov', konf['klient'])), ('link', 'https://' + konf['povolene_domeny'][0]),
                      ('description', 'Opravený feed pre Meta katalóg')):
        ET.SubElement(kanal, tag).text = text
    for polia in vystup:
        it = ET.SubElement(kanal, 'item')
        for pole in PORADIE_META:
            hodn = polia.get(pole)
            for h in (hodn if isinstance(hodn, list) else [hodn]):
                if h:
                    ET.SubElement(it, '{%s}%s' % (G, pole)).text = h
    ET.indent(rss)
    return ET.tostring(rss, encoding='utf-8', xml_declaration=True)


# ---------------------------------------------------------------------------------------------------
# OpenAI Ads (kanál „Hostovaná URL adresa“): zdroj je Google Merchant RSS Shoptetu, výstup JSONL.gz.
# Špecifikácia: https://developers.openai.com/commerce/specs/file-upload/products (pasce v PASCE.md).

def povoleny_obrazok(url, konf):
    """Obrázok na doméne klienta alebo na CDN Shoptetu v priečinku klienta (cdn.myshoptet.com/usr/www.<doména>/)."""
    cast = urllib.parse.urlsplit(url)
    host = cast.hostname or ''
    if any(host == d or host.endswith('.' + d) for d in konf['povolene_domeny']):
        return True
    return any((host + cast.path).startswith(p) for p in konf.get('povolene_obrazky', []))


def popis_z_parametrov(it):
    """Čistý text z parametrov produktu (Stanoviště: Slunce, Polostín. …), keď Shoptet popis nemá."""
    par = {}
    for d in it.findall('{%s}product_detail' % G):
        meno, hodn = (d.findtext('{%s}attribute_name' % G) or '').strip(), (d.findtext('{%s}attribute_value' % G) or '').strip()
        if meno and hodn and hodn not in par.setdefault(meno, []):
            par[meno].append(hodn)
    return ' '.join('%s: %s.' % (m, ', '.join(v)) for m, v in par.items())


def preved_openai(konf, predchadzajuce_id=None):
    """Vráti (bajty JSONL.gz, súhrn). Vyberá len kategórie z konfigurácie, značku dopĺňa len tam, kde chýba."""
    gv = lambda it, t: (it.findtext('{%s}%s' % (G, t)) or it.findtext(t) or '').strip()
    try:
        koren = ET.fromstring(stiahni(konf['zdroj']))
    except Exception as e:
        raise Poistka('zdrojový feed sa nedá stiahnuť alebo prečítať: %s' % e)
    polozky = koren.findall('channel/item')
    suhrn = {'klient': konf['klient'], 'platforma': konf['platforma'], 'zdroj_ponuk': len(polozky), 'vyradene': {},
             'kategoria': {}, 'opravy': {'znacka_doplnena': 0, 'popis_z_parametrov': 0, 'gtin_odstraneny': 0},
             'znacky': {}, 'vyradene_id': {}}
    if len(polozky) < konf['min_ponuk']:
        raise Poistka('zdroj má %d ponúk, minimum je %d' % (len(polozky), konf['min_ponuk']))

    def vyrad(p, dovod):
        suhrn['vyradene'][dovod] = suhrn['vyradene'].get(dovod, 0) + 1
        suhrn['vyradene_id'].setdefault(dovod, []).append(gv(p, 'id'))

    # Výber: len kategórie z konfigurácie (product_type), potom vylúčenia (príslušenstvo, letničky) a sklad.
    vybrane = []
    for p in polozky:
        typ = gv(p, 'product_type')
        if not any(re.search(r, typ) for r in konf['zaradit']):
            suhrn['vyradene']['mimo vybraných kategórií'] = suhrn['vyradene'].get('mimo vybraných kategórií', 0) + 1
            continue
        dovod = next((v['dovod'] for v in konf.get('vyradit', []) if re.search(v['regex'], gv(p, v['pole']), re.I)), None)
        if not dovod and konf.get('len_skladom') and gv(p, 'availability') != 'in stock':
            dovod = 'nie je skladom'
        if dovod:
            vyrad(p, dovod)
            continue
        vybrane.append(p)

    mrtve = set()
    if konf.get('over_url'):
        urls = sorted({gv(p, 'link') for p in vybrane})
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            stavy = dict(zip(urls, ex.map(http_ciel, urls)))
        mrtve = {u for u, (kod, ciel) in stavy.items() if not url_ziva((kod, u, ciel))}
        podiel = len(mrtve) / max(len(urls), 1)
        if podiel > konf.get('max_podiel_mrtvych', 0.5):
            raise Poistka('%d z %d URL nefunguje (%.0f %%), web je asi nedostupný' % (len(mrtve), len(urls), 100 * podiel))

    stav_sklad = {'in stock': 'in_stock', 'out of stock': 'out_of_stock', 'preorder': 'pre_order', 'backorder': 'backorder'}
    vystup, gtiny = [], {}
    for p in vybrane:
        pid = gv(p, 'id')
        link, obrazok = gv(p, 'link'), gv(p, 'image_link')
        if link in mrtve:
            vyrad(p, 'URL nefunguje')
            continue
        if not obrazok:
            vyrad(p, 'chýba obrázok')
            continue
        host = urllib.parse.urlsplit(link).hostname or ''
        if not any(host == d or host.endswith('.' + d) for d in konf['povolene_domeny']):
            raise Poistka('link %s mimo povolených domén: %s' % (pid, link))
        if not povoleny_obrazok(obrazok, konf):
            raise Poistka('image_link %s mimo povolených domén: %s' % (pid, obrazok))
        try:
            suma, mena = gv(p, 'price').split()
            bezna = float(suma)
        except ValueError:
            vyrad(p, 'chýba cena')
            continue
        if mena != konf['mena']:
            raise Poistka('%s má menu %s, feed je v %s' % (pid, mena, konf['mena']))
        if bezna <= 0:
            vyrad(p, 'nulová cena')
            continue
        sklad = stav_sklad.get(gv(p, 'availability'))
        if sklad is None:
            raise Poistka('%s: neznámy stav skladu „%s“' % (pid, gv(p, 'availability')))

        popis = cisty_text(gv(p, 'description'))
        if not popis:
            popis = popis_z_parametrov(p)
            if popis:
                suhrn['opravy']['popis_z_parametrov'] += 1
        if not popis:
            vyrad(p, 'chýba popis')
            continue

        polia = {'item_id': pid, 'title': bez_html(gv(p, 'title')), 'description': popis[:5000], 'url': link,
                 'image_url': obrazok, 'price': '%.2f %s' % (bezna, mena), 'availability': sklad,
                 'condition': 'new', 'product_category': ' > '.join(x.strip() for x in gv(p, 'product_type').split('>'))}
        dalsie = [e.text.strip() for e in p.findall('{%s}additional_image_link' % G) if (e.text or '').strip()]
        dalsie = [u for u in dalsie if u != obrazok and povoleny_obrazok(u, konf)]
        if dalsie:
            polia['additional_image_urls'] = dalsie[:10]
        if gv(p, 'sale_price'):
            akcna = float(gv(p, 'sale_price').split()[0])
            if 0 < akcna < bezna:
                polia['sale_price'] = '%.2f %s' % (akcna, mena)

        # Značka: skutočná zo Shoptetu (Kordes Roses, SEMO…), inak potvrdená hodnota z konfigurácie (Marek 7. 11. 2026).
        polia['brand'] = gv(p, 'brand') or konf['znacka_ak_chyba']
        if not gv(p, 'brand'):
            suhrn['opravy']['znacka_doplnena'] += 1
        suhrn['znacky'][polia['brand']] = suhrn['znacky'].get(polia['brand'], 0) + 1

        gtin = gv(p, 'gtin')
        if gtin and ean_platny(gtin):
            polia['gtin'] = gtin
            gtiny.setdefault(gtin, []).append(pid)
        elif gtin:
            suhrn['opravy']['gtin_odstraneny'] += 1

        polia.update(konf['predajca'])
        polia.update({'is_eligible_search': True, 'is_eligible_checkout': False, 'is_ads_eligible': True})
        hlavna = polia['product_category'].split(' > ')[0]
        suhrn['kategoria'][hlavna] = suhrn['kategoria'].get(hlavna, 0) + 1
        vystup.append(polia)

    # GTIN identifikuje jeden tovar; zdieľaný medzi rôznymi položkami sa vynechá.
    for gtin, ids in gtiny.items():
        if len(ids) > 1:
            for polia in vystup:
                if polia.get('gtin') == gtin:
                    del polia['gtin']
                    suhrn['opravy']['gtin_odstraneny'] += 1
    ids = [pol['item_id'] for pol in vystup]
    if len(ids) != len(set(ids)):
        raise Poistka('duplicitné item_id: %s' % ', '.join(sorted({i for i in ids if ids.count(i) > 1})[:10]))
    suhrn['sklad'] = {s: sum(1 for pol in vystup if pol['availability'] == s) for s in {pol['availability'] for pol in vystup}}
    suhrn['vystup_ponuk'] = len(vystup)
    if len(vystup) < konf.get('min_vystup', 1):
        raise Poistka('po úprave ostalo %d ponúk, minimum je %d' % (len(vystup), konf['min_vystup']))
    if predchadzajuce_id is not None:
        suhrn['pribudlo'] = len(set(ids) - predchadzajuce_id)
        suhrn['ubudlo'] = len(predchadzajuce_id - set(ids))
        # Shoptet 29. 10.–4. 11. 2026 posielal OpenAI 0 produktov a kanál to bral ako úspešné nahratie.
        if predchadzajuce_id and len(vystup) < len(predchadzajuce_id) * (1 - konf.get('max_pokles', 0.5)):
            raise Poistka('výstup klesol z %d na %d ponúk (viac než %.0f %%)'
                          % (len(predchadzajuce_id), len(vystup), 100 * konf.get('max_pokles', 0.5)))
    telo = ''.join(json.dumps(pol, ensure_ascii=False, sort_keys=True) + '\n' for pol in vystup).encode('utf-8')
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:   # mtime 0: rovnaký obsah = rovnaké bajty, žiadny prázdny commit
        gz.write(telo)
    return buf.getvalue(), suhrn


def ids_z_jsonl_gz(cesta):
    if not os.path.exists(cesta):
        return None
    with gzip.open(cesta, 'rt', encoding='utf-8') as f:
        return {json.loads(r)['item_id'] for r in f if r.strip()}


def spracuj(cesta, vystup_koren):
    with open(cesta, encoding='utf-8') as f:
        konf = json.load(f)
    if konf['platforma'] == 'openai':
        xml, suhrn = preved_openai(konf, ids_z_jsonl_gz(os.path.join(vystup_koren, konf['vystup'])))
    elif konf['platforma'] == 'meta':
        xml, suhrn = preved_meta(konf, woo_vsetko(konf['woo']), taxonomia_google(TAXONOMIA_GOOGLE))
    else:
        pl = PLATFORMY[konf['platforma']]
        zoznam = kategorie_zbozi(pl['zoznam_kategorii']) if pl.get('zoznam_kategorii') else None
        popisy = popisy_woo(konf['popisy']['url']) if konf.get('popisy', {}).get('zdroj') == 'woo_store' else None
        woo = woo_vsetko(konf['over_woo']) if konf.get('over_woo') else None
        if woo is not None and len(woo) < konf.get('min_woo', 100):
            raise Poistka('Woo Store API vrátilo %d produktov, web je asi nedostupný' % len(woo))
        xml, suhrn = preved(konf, zoznam, popisy, woo)
    ciel = os.path.join(vystup_koren, konf['vystup'])
    os.makedirs(os.path.dirname(ciel), exist_ok=True)
    docasny = ciel + '.tmp'
    with open(docasny, 'wb') as f:
        f.write(xml)
    os.replace(docasny, ciel)
    # Deň (nie čas): aspoň jeden commit denne, inak GitHub po 60 dňoch bez aktivity vypne plánované behy.
    suhrn['den'] = datetime.date.today().isoformat()
    with open(re.sub(r'\.(xml|jsonl\.gz)$', '_suhrn.json', ciel), 'w', encoding='utf-8') as f:
        json.dump(suhrn, f, ensure_ascii=False, indent=1, sort_keys=True)
    return suhrn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('konfig', nargs='*')
    ap.add_argument('--vsetky', action='store_true')
    ap.add_argument('--vystup', default=os.path.join(KOREN, 'docs'))
    a = ap.parse_args()
    cesty = sorted(glob.glob(os.path.join(KOREN, 'klienti', '*.json'))) if a.vsetky else a.konfig
    if not cesty:
        ap.error('zadaj konfiguráciu alebo --vsetky')
    chyby = 0
    for cesta in cesty:
        try:
            s = spracuj(cesta, a.vystup)
            print('OK %s: %d → %d ponúk, vyradené %s, opravy %s'
                  % (os.path.basename(cesta), s['zdroj_ponuk'], s['vystup_ponuk'], s['vyradene'], s['opravy']))
        except Poistka as e:
            chyby += 1
            print('POISTKA %s: %s (opravený feed nezmenený)' % (os.path.basename(cesta), e), file=sys.stderr)
        except Exception as e:
            chyby += 1
            print('CHYBA %s: %r (opravený feed nezmenený)' % (os.path.basename(cesta), e), file=sys.stderr)
    sys.exit(2 if chyby else 0)


if __name__ == '__main__':
    main()
