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


def novy_nazov(polozka, ns, pravidla, aktualny):
    """Názov podľa šablóny. Kľúč (napr. časť URL) určí šablónu; neznámy kľúč = Poistka, nič sa nehádže naslepo."""
    m = re.search(pravidla['kluc']['regex'], hodnota(polozka, pravidla['kluc']['pole'], ns))
    if not m:
        predpona = pravidla.get('predpona_ostatnym', '')
        if predpona and predpona.strip().lower() not in aktualny.lower():
            return predpona + aktualny
        return aktualny
    sablona = pravidla['mapa'].get(m.group(1))
    if sablona is None:
        raise Poistka('„%s“ (%s) nemá v konfigurácii overenú šablónu názvu' % (aktualny, m.group(1)))
    od = pravidla['odtien']
    mo = re.search(od['regex'], hodnota(polozka, od['pole'], ns))
    odtien = mo.group(1) if mo else ''
    for vzor, nahrada in od.get('upravy', []):
        odtien = re.sub(vzor, nahrada, odtien)
    par = parametre(polozka, ns)

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


def preved(konf, zoznam_kategorii=None, popisy=None):
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
                novy = novy_nazov(p, ns, konf['premenovat'], povodny)
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


def spracuj(cesta, vystup_koren):
    with open(cesta, encoding='utf-8') as f:
        konf = json.load(f)
    pl = PLATFORMY[konf['platforma']]
    zoznam = kategorie_zbozi(pl['zoznam_kategorii']) if pl.get('zoznam_kategorii') else None
    popisy = popisy_woo(konf['popisy']['url']) if konf.get('popisy', {}).get('zdroj') == 'woo_store' else None
    xml, suhrn = preved(konf, zoznam, popisy)
    ciel = os.path.join(vystup_koren, konf['vystup'])
    os.makedirs(os.path.dirname(ciel), exist_ok=True)
    docasny = ciel + '.tmp'
    with open(docasny, 'wb') as f:
        f.write(xml)
    os.replace(docasny, ciel)
    # Deň (nie čas): aspoň jeden commit denne, inak GitHub po 60 dňoch bez aktivity vypne plánované behy.
    suhrn['den'] = datetime.date.today().isoformat()
    with open(re.sub(r'\.xml$', '_suhrn.json', ciel), 'w', encoding='utf-8') as f:
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
