# Postup: nový prevodník feedu (iný klient alebo iná platforma)

Návod pre session, ktorá pridáva prevodník. Prvý ostrý prevodník (Voono → Zboží, 6. 11. 2026)
prešiel presne týmto postupom. Čítaj spolu s [PASCE.md](PASCE.md); pasce sa tu neopakujú, len odkazujú.

**Poradie je povinné.** Nič sa nezverejňuje, kým výstup neprejde validátorom platformy,
a platformu na nový feed prepína Marek, nie Claude.

---

## 0. Pred začatím

- Prečítaj `~/marketing/CLAUDE.md`, memory `feed-prevodnik` a `PASCE.md` celé.
- Zisti od Mareka, ak to nie je jasné z NOTES klienta: **ktorý feed (URL)**, **pre ktorú platformu** a čo sa má
  vyradiť (B2B balenia, poukazy, iné). Nehádaj; Voono pravidlá (1000 g, poukazy) neplatia automaticky inde.
- Žiadne nové platené služby (memory `marketing-bez-platenych-nakladov`). GitHub Actions a Pages vo verejnom repe sú zadarmo.

## 1. Špecifikácia a validátor platformy

| Platforma | Špecifikácia | Validátor | Stav v `prevod.py` |
|---|---|---|---|
| Zboží.cz / Sklik Nákupy | https://napoveda.sklik.cz/reklamy/xml-feed/specifikace/ | https://prodejce.seznam.cz/n/validace-feedu/ | hotová (`zbozi`) |
| Google Merchant Center | https://support.google.com/merchants/answer/7052112 | diagnostika v Merchant Center (Marek) | **treba doplniť** |
| Ďalšie (Meta katalóg, Heureka…) | nájdi oficiálnu špecifikáciu a zapíš ju do tabuľky v `PASCE.md` | | **treba doplniť** |

Špecifikáciu prečítaj celú (stačí cez web-fetch agenta) a vypíš si povinné polia, formáty, zakázaný obsah
a pravidlá, čo sa nesmie inzerovať. Ak platforma nemá verejný validátor, zapíš do `PASCE.md`, ako sa výstup overí.

## 2. Profil zdrojového feedu

Stiahni feed do vlastného priečinka v scratchpade, Python spúšťaj s `-I` (súbor je nedôveryhodný). Zisti a zapíš si:

1. počet ponúk, štruktúru (koreň, menný priestor, kde sú položky),
2. kategórie e-shopu a koľko ponúk má ktorú, prázdne kategórie,
3. HTML v názvoch a popisoch, interné kódy v názvoch, prázdne povinné polia,
4. EAN/GTIN: dĺžka a kontrolná číslica, **zdieľané EAN medzi rôznymi produktmi**,
5. duplicitné názvy a URL,
6. **HTTP stav každej URL** (Voono: 70 zo 171 vracalo 404); 404 over aspoň na jednej ponuke v Claude in Chrome,
7. URL a obrázky mimo domény klienta.

Výsledok porovnaj s pascami v `PASCE.md`. Čo je nové, dopíš tam hneď, nie až na konci.

## 3. Nová platforma v `prevod.py` (len ak ešte nie je v `PLATFORMY`)

Jadro je dnes napísané pre Zboží. Pri inej platforme treba:

- doplniť položku do `PLATFORMY`: menný priestor, tag položky, názvy polí (id, skupina, kategória, popis, EAN, URL polia),
  zdroj stromu kategórií (alebo `None`, ak platforma kategóriu nevyžaduje v pevnom strome);
- **zovšeobecniť natvrdo zapísané polia Zboží**: `PRODUCT` (názov pri kategóriách a popisoch), `URL`, `PRICE_VAT`
  a predpoklad, že položky sú priamo pod koreňom. Napr. Google RSS má položky v `rss/channel/item`, polia
  v mennom priestore `g:` aj bez neho a cenu v tvare `299.00 CZK`;
- `kategorie_zbozi()` číta CSV Zboží; iná platforma potrebuje vlastný načítač (Google: taxonómia s ID);
- pridať testy do `test_prevod.py` na novú platformu, existujúce testy Zboží musia ďalej prechádzať.

Zmena jadra nesmie zmeniť výstup existujúcich feedov: po úprave pusti `python3 prevod.py --vsetky --vystup /tmp/x`
a porovnaj `voono/zbozi.xml` so zverejneným (`diff`).

## 4. Konfigurácia klienta

`klienti/<klient>_<platforma>.json`, ako vzor skopíruj `klienti/voono_zbozi.json`. Povinne nastav:

| Kľúč | Čo | Pozor |
|---|---|---|
| `zdroj` | URL feedu klienta | |
| `vystup` | `<klient>/<platforma>.xml` | určuje verejnú URL |
| `povolene_domeny` | len domény klienta | poistka proti cudzím dátam, CLAUDE.md |
| `min_ponuk`, `min_vystup` | dolné hranice | nastav podľa profilu (Voono 150 / 80) |
| `over_url`, `max_podiel_mrtvych` | kontrola 404 a poistka na spadnutý web | `over_url: true` vždy |
| `vyradit` | čo sa úplne vyhodí, s dôvodom | len to, čo Marek potvrdil |
| `cistit_nazvy` | HTML a interné kódy z názvov | po úprave over, že nevznikli duplicitné názvy |
| `doplnit` | konštanty (výrobca) | |
| `ean` | `len_platne` | |
| `popisy`, `popis_zo_zakladneho` | dopĺňanie popisov | **nikdy podľa EAN** |
| `kategorie`, `kategorie_podla_id` | mapovanie na strom platformy | podľa názvu a URL, nie podľa kategórie e-shopu; poradie pravidiel rozhoduje |

## 5. Test lokálne

```bash
cd ~/marketing/vseobecne/feed_prevodnik
python3 test_prevod.py
python3 prevod.py klienti/<klient>_<platforma>.json --vystup <scratchpad>/feed_test
```

Prečítaj `<platforma>_suhrn.json`: vyradené podľa dôvodu, opravy, `bez_popisu`, rozdelenie kategórií.
Vypíš si každú ponuku s jej cieľovou kategóriou a skontroluj ich očami; regex vie zaradiť zle aj bez chyby.
Poistka „ponuka nemá cieľovú kategóriu“ je správanie, nie chyba: doplň pravidlo.

## 6. Validácia

Výstup nahraj do validátora platformy (súbor alebo URL), ideálne aj pôvodný feed na porovnanie.
Pokračuje sa až pri výsledku bez chýb. Pri Zboží: „Feed je v pořádku“.

## 7. Zverejnenie v repe `feedy`

Zdroj pravdy je `~/marketing/vseobecne/feed_prevodnik/`, repo `https://github.com/marek90x/feedy` je jeho kópia.

1. Naklonuj repo do scratchpadu: `git clone https://github.com/marek90x/feedy.git`.
2. Skopíruj súbory: `rsync -a --exclude __pycache__ ~/marketing/vseobecne/feed_prevodnik/ <klon>/`.
3. Skontroluj, že v nich nie sú tokeny ani maily, **bez výnimiek vo filtri** (repo je verejné a história sa
   nedá bez force pushu vyčistiť; 6. 11. 2026 takto do histórie unikol Marekov mail):
   `grep -rnE "@[a-z0-9.-]+\.[a-z]{2,}|token|password|secret" <klon> --exclude-dir=.git` a každý nález vysvetli.
4. `git add` **s vymenovanými súbormi** (hook blokuje `git add -A` aj tu), autor
   `marek90x@users.noreply.github.com`, správa končí riadkom `Co-Authored-By`.
5. `git push`. Push ide tokenom **mac-mini-trading** (Contents + Workflows); nové repo by sa doňho muselo pridať.
6. V záložke Actions počkaj na beh `feedy` (zelený), potom over verejnú URL:
   `https://marek90x.github.io/feedy/<klient>/<platforma>.xml` (HTTP 200, `application/xml`, správny počet ponúk).
7. Verejnú URL pošli ešte raz do validátora.

## 8. Prepnutie platformy (robí Marek)

Marek zmení URL feedu v administrácii platformy. Claude to v Chrome nemení, len overí:
Zboží cez Fénix `GET /nakupy/feeds/?premiseId=<id>` (`feedUrl`, `lastSuccessfulImport`), iné platformy v ich rozhraní.

## 9. Zápis

- `NOTES.md` klienta: od kedy je ostrý, URL, čo sa vyraďuje a koľko, výsledok validátora, čo poslať klientovi/webárovi.
- Tabuľka feedov v `README.md`.
- Nové pasce do `PASCE.md`.
- `vseobecne/napojenia/POSTUP_NAPOJENI.md`, ak pribudla platforma alebo nový prístup.
- Memory `feed-prevodnik`, ak sa zmenil postup.

## Prevádzka

- Beh 4× denne (`.github/workflows/feedy.yml`, cron `17 */6 * * *` UTC), ručne cez Actions → feedy → Run workflow.
- Pri poistke alebo chybe GitHub pošle mail na Marekov mail z nastavení GitHubu (Notifications → Actions: Failed workflows only)
  a zverejnený feed ostane posledný dobrý.
- Chyba jedného klienta nezastaví ostatných.
- Plánované behy sa vypnú po 60 dňoch bez commitu; `den` v súhrne zaručí denný commit.
