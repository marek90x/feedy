# Feed prevodník: pasce a povinné kontroly

**Platí pre každý prevodník feedu, pre každého klienta a platformu.** Pred novou konfiguráciou
alebo zmenou pravidiel si tento súbor prečítaj celý a prejdi kontrolný zoznam na konci.
Postup krok za krokom (vrátane zverejnenia) je v [POSTUP.md](POSTUP.md).
Nové pasce dopisuj sem, nie do poznámok klienta.

## Zdroje pravdy

| Platforma | Špecifikácia | Validátor |
|---|---|---|
| Zboží.cz / Sklik Nákupy | https://napoveda.sklik.cz/reklamy/xml-feed/specifikace/ | https://prodejce.seznam.cz/n/validace-feedu/ (URL alebo súbor, max. 150 MB) |
| Zboží.cz, pravidlá obsahu | https://napoveda.sklik.cz/pravidla/pravidla-a-podminky-zbozi-cz/pravidla-pro-internetove-obchody/ | |
| Zboží.cz, strom kategórií | https://www.zbozi.cz/static/categories.csv (cp1250, `;`) | prevodník ho sťahuje pri každom behu |

**Výstup sa nasadzuje až vtedy, keď validátor napíše „Feed je v pořádku“.** Validátor kontroluje
dĺžku EAN, ale nie kontrolnú číslicu; prevodník je prísnejší.

## Pasce zistené na reálnych feedoch

### Feed klienta nie je zdroj pravdy (Voono, 6. 11. 2026)

E-shopové feedy bývajú zanedbané. Klientovi nič neopravujeme vo feede, ale **všetko kontrolujeme na začiatku každého behu** a čo neprejde, úplne vyradíme:

| Pasca | Voono 6. 11. 2026 | Čo robí prevodník |
|---|---|---|
| **Neexistujúce produkty:** URL vracia 404 (koncepty, skryté produkty, `?post_type=product&p=ID`) | 70 zo 171 ponúk (sady, eko balíčky, vianočné kazety, ZACHRAŇ PRODUKT, „- test“) | `over_url`: každá URL sa overí, nefunkčná ponuka sa vyradí |
| Spadnutý web by vyradil všetko | | `max_podiel_mrtvych`: keď nefunguje viac než nastavený podiel URL, feed sa nezverejní |
| B2B balenia v B2C feede | 22× 1000 g | `vyradit` regexom |
| Dárkové poukazy | 2 | `vyradit` (vlastné poukazy shopu Zboží povoľuje, Marek ich nechce) |
| Duplikáty „(Kopírovat)“ | 6 | `vyradit` |
| Duplicitné PRODUCTNAME | 3 dvojice; všetky boli 404 | rieši `over_url`; validátor duplicity hlási ako chybu |
| HTML v názve (`<span class="description">`) | 85 | `cistit_nazvy` |
| Interný kód v názve „(100019)“, „(#12379)“, „(600009-1)“ | 157 | `cistit_nazvy.odstranit`; po úprave over, že nevznikli duplicitné názvy |
| HTML v popise (pravidlá 3.4: žiadne HTML v ponuke) | 108 | popis sa vždy prevedie na čistý text, odseky ostávajú |
| Chýbajúci popis (povinný) | 75 | doplní sa z WooCommerce Store API; ZACHRAŇ varianty z bežnej verzie s rovnakým začiatkom názvu |
| **Interné kódy v EAN** (800020, 100118-1, 812000013) | 85 | `ean: len_platne`: dĺžka 8/12/13/14 a GS1 kontrolná číslica, inak sa EAN vynechá |
| **EAN nie je jednoznačný:** MAXI kondicionér Ambra aj levanduľa majú rovnaký EAN, Oil to milk má EAN SPF krému | | EAN **nikdy** nepoužívať na párovanie popisov ani iných údajov |
| Kategórie e-shopu namiesto stromu platformy („Ostatní \| Dárkové balíčky“); Zboží potom háda (Romány, Vrtáky, Lahve) | 201 | mapovanie podľa názvu alebo URL na celú cestu zo stromu Zboží; neexistujúca cesta = poistka |
| Kategória e-shopu nesedí na produkt (psí šampón vo Vlasovej péči, cassia v Maskách) | | mapuj podľa **názvu produktu a URL**, nie podľa kategórie e-shopu |
| Chýba výrobca | 201 | `doplnit.MANUFACTURER` |
| Poradie elementov: validátor hlási „Extra element EAN in interleave“ | zdroj | prevodník zapisuje XML nanovo, výstup prešiel |

### Ďalej platí

- Ponuka bez cieľovej kategórie **zastaví celý beh**: nový produkt na webe = doplniť pravidlo, nie hádať.
- Po každej zmene pravidiel skontroluj súhrn `docs/<klient>/<platforma>_suhrn.json`: počty vyradených, `bez_popisu`, rozdelenie kategórií.
- Woo Store API vracia len zverejnené produkty; čo v ňom nie je, býva aj 404 na webe.
- Zmena `PRODUCTNAME` môže zmeniť párovanie s kartou produktu na Zboží; meň ho len o odstránenie balastu, výrobcu nepridávaj do názvu bez overenia.
- `IMGURL`: zmena obrázka bez zmeny URL sa na Zboží neprejaví (špecifikácia).
- GitHub vypne plánované behy vo verejnom repe po 60 dňoch bez commitu; súhrn preto nesie dátum (`den`), aby denne vznikol commit.
- Klienti sú oddelení: vlastný konfiguračný súbor, výstup aj `povolene_domeny`. URL mimo domény klienta zastaví beh.

## Kontrolný zoznam pre nový prevodník alebo zmenu

1. Prečítaj špecifikáciu platformy (tabuľka hore) a tento súbor.
2. Stiahni zdrojový feed a profiluj ho: počty, kategórie, prázdne polia, HTML, EAN, duplicitné názvy a URL.
3. Over HTTP stav všetkých URL; 404 over aspoň na jednej ponuke v prehliadači.
4. Napíš konfiguráciu (`klienti/<klient>_<platforma>.json`) a mapovanie kategórií na strom platformy.
5. `python3 test_prevod.py`, potom `python3 prevod.py klienti/... --vystup /tmp/test` a prečítaj súhrn.
6. Výstup nahraj do validátora platformy. Nasadzuje sa až pri „Feed je v pořádku“.
7. Porovnaj s validáciou pôvodného feedu a výsledok zapíš do `NOTES.md` klienta.
