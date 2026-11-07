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
| Meta katalóg | https://developers.facebook.com/docs/marketing-api/catalog/reference/ (Help Center https://www.facebook.com/business/help/120325381656392 sa automaticky nedá prečítať) | bez verejného validátora; po nahratí diagnostika katalógu cez Meta MCP |
| Google taxonómia (pre Meta `google_product_category`) | https://www.google.com/basepages/producttype/taxonomy-with-ids.en-US.txt | prevodník ju sťahuje pri každom behu, neexistujúce ID = poistka |
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
| **Voono: predáva sa len kozmetika** (Marek 6. 11. 2026): miska, dóza, zástěra, čepice, rukavice, teploměr, kartáč | 7 | `vyradit` vo všetkých Voono feedoch; kategória „Pomůcky“ zámerne neexistuje, nová pomôcka zastaví beh |
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

### Google Merchant feed Voono ako zdroj pre Meta (6. 11. 2026)

| Pasca | Voono 6. 11. 2026 | Čo s tým |
|---|---|---|
| **Zrušený produkt presmeruje na kategóriu s HTTP 200** (varianty `?attribute_…` skrytých sád: Eko balíček, Předpigmentační set, Set pro začátečníky, Set pro cestovatele) | 38 položiek, `http_stav` ich berie ako živé | živá URL = cieľ po presmerovaní ostal na tej istej ceste produktu; presmerovanie na `/kategorie-produktu/` = mŕtva. Druhá kontrola: ID musí byť vo Woo Store API |
| **Ceny variantov sú v Google feede staré**, hoci feed má rovnaký čas generovania ako Zboží | 21 z 123 živých: „Bez obalu 100 g“ 249 namiesto 279 Kč, 500 g 629 namiesto 649, Cassia 229 namiesto 249 | cenu a `sale_price` brať z Woo Store API (`prices.regular_price`, `prices.sale_price`, v halieroch), feed nie je zdroj ceny |
| Masážny kartáč: 259,70 v oboch feedoch, web 371 Kč | 1 | to isté: cena z webu |
| `product_type` je zoznam všetkých kategórií e-shopu cez „\|“ | 179 z 194, 15 prázdnych | vlastná jedna cesta podľa názvu/URL, ako pri Zboží |
| `google_product_category` chýba | 108 z 194 | doplniť ID z taxonómie Google |
| Chýba `brand` a `condition` (Meta: povinné) | 194 | `doplnit` |
| Pixel Voono CZ posiela Woo ID (ViewContent zhoda 97 %) | | `g:id` sa **nesmie** meniť, inak katalógové reklamy prestanú párovať |
| Interné 8-miestne kódy prejdú kontrolnou číslicou (10000113, 10000106) | 2 | `gtin_predpona: "859"`; zdieľaný GTIN medzi produktmi sa vynechá |
| „Hair of Gaurí 100 g“ (44294) je na webe za 0 Kč bez kategórie: darček k objednávke, nie predajný produkt; pixel ho posiela | | do feedu nepatrí, nulová cena = vyradenie |
| Meta katalóg „VOONO katalog \| New“ plní WooCommerce plugin a mieša CZ (CZK) aj SK (EUR) | 339 položiek, 199× nesúlad meny, 500 g varianty za 0 Kč | do katalógu s pluginom sa feed s rovnakými ID nepridáva (prepisovali by sa) |
| **SK (7. 11. 2026): obrázky sú na voono.cz**, odkazy na voono.sk (WordPress multisite, `/uploads/sites/2/`) | 182 | `povolene_domeny` obe, voono.sk prvá (kanál feedu) |
| SK: iné ID, slugy a parametre než CZ (`/farby-na-vlasy/`, `wine-red` namiesto `henna-wine-red`, `henna-light-brown-honey`, `cassia-obovata`; parameter `objem` malým) | | mapy tónov a šablón sa nekopírujú bez prevodu slugov; `{param:objem}`; ID = Woo ID voono.sk |
| SK: „ZACHRÁŇ PRODUKT“ (zľavnená kópia) má živú URL, v CZ bola 404 | 3 | `vyradit` |
| SK: staré ceny aj tu (Dark Brown 100 g 10,50 vs. web 12,30 €, 500 g 24,50 vs. 25,30 €) | 3 | cena z Woo voono.sk |

### OpenAI Ads zo Shoptet Google feedu (Jahodárna, 7. 11. 2026)

| Pasca | Jahodárna 7. 11. 2026 | Čo s tým |
|---|---|---|
| **Shoptet OpenAI export vynecháva produkty bez značky** (OpenAI vyžaduje `brand`) | CZ 731 z 3 959, SK 594 z 3 260; chýbali takmer všetky cibuľoviny, jahody a trvalky | zdroj je Google feed Shoptetu (`/google/export/products.xml`), značka sa dopĺňa len tam, kde chýba (`znacka_ak_chyba`, hodnotu potvrdzuje Marek) |
| ID v Google feede (`g:id` = kód variantu `468-FRI`) ≠ `item_id` v Shoptet OpenAI exporte (interné ID) | zhoda 27 z 731 | po prepnutí kanála sa produkty v kampani vyberajú nanovo |
| **Kanál „Hostovaná URL“ načítal náš prvý `.jsonl.gz` ako 0 produktov, 0 chýb, 0 nevhodných** (7. 11. 2026, CZ aj SK); náhľad vzorky pre `.jsonl.gz` OpenAI nezobrazí („Tento formát nie je možné zobraziť v náhľade“) | rozdiel oproti Shoptet exportu, ktorý načíta: medzery v JSON, prázdny riadok na konci, `additional_image_urls` ako pole, gzip OS bajt 255 | výstup kopíruje tvar Shoptet exportu (kompaktný JSON, bez koncového `\n`, obrázky ako reťazec s čiarkami, OS=Unix); či to bola príčina, over v „História nahrávania“ po ďalšom načítaní |
| **Kanál ukazuje „Nahrané“ aj pri 0 produktoch** | CZ kanál 29. 10.–4. 11. 2026 sedem dní 0 | `max_pokles` (0,5): pri prepade oproti minulému behu sa feed neprepíše |
| Obrázky sú na `cdn.myshoptet.com`, nie na doméne obchodu | všetky | `povolene_obrazky`: len priečinok klienta `cdn.myshoptet.com/usr/www.<doména>/`; iný priečinok na CDN zastaví beh |
| `product_type` e-shopu mieša rastliny s príslušenstvom | „Vše pro květiny/trvalky/růže“, „Všetko na pestovanie jahôd“ (iné slovo než CZ), Fiskars kanvička priamo v „Cibuľoviny a hľuzy“, podložka pod jahody | `vyradit` podľa podkategórie aj názvu; po každom behu prejdi názvy, ktoré nie sú rastlina |
| Letničky sú v e-shope pod Trvalkami | CZ 12, SK 6 | `vyradit` (Marek chce trvalky) |
| Ruže Kordes bez popisu (povinný) | CZ 52 | popis z parametrov `g:product_detail` (Stanoviště, Termín výsadby, Doba květu); bez parametrov = vyradiť |
| Link v Google feede presmeruje na 404 (`/nerine/…` → `/trvalky/…` 404) | CZ 2 | `over_url` s `url_ziva` |
| Formát | | hostovaná URL berie `jsonl.gz` (aj `csv.gz`, `parquet`); gzip s `mtime=0`, aby rovnaký obsah nevyrobil commit |
| Shoptet `shipping` (SK „Doobjednanie tovaru…: 0.04 EUR“) | | neposiela sa; tvar `shipping` vyžaduje nastavenie kanála |

### Ďalej platí

- Ponuka bez cieľovej kategórie **zastaví celý beh**: nový produkt na webe = doplniť pravidlo, nie hádať.
- Po každej zmene pravidiel skontroluj súhrn `docs/<klient>/<platforma>_suhrn.json`: počty vyradených, `bez_popisu`, rozdelenie kategórií.
- Woo Store API vracia len zverejnené produkty; čo v ňom nie je, býva aj 404 na webe.
- **Rozšírené názvy (`premenovat`)**: tvrdenie v názve musí sedieť so zložením na webe. Voono 6. 11. 2026: 7 z 20 „farieb“ hennu neobsahuje (Champagne Pink, Light Ash Brown, Dark Ash Blonde, Indigo, Cassia, Mix), preto majú vlastnú šablónu. Šablóna sa vyberá podľa overenej mapy (kľúč z URL); **neznámy produkt v mape zastaví beh**, nový odtieň sa najprv overí na webe („Složení“).
- Premenovanie beží až **po** priradení kategórie, pravidlá kategórií sa viažu na pôvodný názov („^tricholog“ by po pridaní „VOONO“ nesedel).
- Duplicitný `PRODUCTNAME` zastaví beh (validátor ho berie ako chybu).
- **Párovanie s kartami Zboží sa nesmie premiešať.** Pred každou zmenou názvov, EAN alebo vyradením ulož stav a po importe ho porovnaj:
  `python3 skills/sklik/zbozi_parovanie.py <klient> <premiseId> --uloz logs/zbozi_parovanie_<klient>_<datum>.json` a potom `--porovnaj`.
  Kód 1 = ponuka zmenila kartu, stratila párovanie alebo pribudla; pozri ju ešte v ten deň. Voono pred zmenou: 113 ponúk, všetky spárované, každá s inou kartou (`logs/zbozi_parovanie_voono_2026-11-06_pred.json`).
- Zboží importuje feed podľa svojho rozvrhu (Voono max. 4× denne, `maxFeedDownloadsPerDay`), výsledok zmeny vidno až po importe.
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
