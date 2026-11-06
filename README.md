# Feed prevodník

Stiahne produktový feed klienta, opraví ho podľa konfigurácie a zverejní opravenú kópiu na GitHub Pages.
Platforma (Zboží, neskôr Google Merchant či Meta katalóg) ťahá opravený feed namiesto pôvodného.

**Pred akoukoľvek zmenou si prečítaj [PASCE.md](PASCE.md).** Je tam špecifikácia, validátor a pasce,
ktoré platia pre všetkých klientov.

## Súbory

| Súbor | Čo robí |
|---|---|
| `prevod.py` | jadro, len štandardná knižnica Pythonu |
| `klienti/<klient>_<platforma>.json` | konfigurácia jedného feedu: zdroj, výstup, domény, vyradenie, čistenie, kategórie |
| `test_prevod.py` | testy poistiek bez siete |
| `docs/<klient>/<platforma>.xml` | opravený feed (GitHub Pages) |
| `docs/<klient>/<platforma>_suhrn.json` | čo sa v poslednom behu vyradilo a opravilo |
| `.github/workflows/feedy.yml` | beh 4× denne, commit výstupu, pri poistke mail od GitHubu |

## Spustenie

```bash
python3 test_prevod.py
python3 prevod.py klienti/voono_zbozi.json --vystup /tmp/test
python3 prevod.py --vsetky
```

Kód 2 = poistka alebo chyba aspoň u jedného klienta; jeho opravený feed ostal nezmenený.

## Feedy

| Klient | Platforma | Zdroj | Opravený feed |
|---|---|---|---|
| Voono | Zboží | https://www.voono.cz/app/uploads/xml/feed_zbozi_1.xml | `voono/zbozi.xml` na GitHub Pages |

## Nový klient alebo platforma

Skopíruj `klienti/voono_zbozi.json`, uprav ho a prejdi kontrolný zoznam v `PASCE.md`. Novú platformu
treba doplniť do `PLATFORMY` v `prevod.py` (namespace, názvy polí, zoznam kategórií).

## Zverejnenie

Zdroj pravdy je tento priečinok v `~/marketing`. Verejné repo `feedy` na GitHube obsahuje jeho kópiu:
po zmene skopíruj súbory do klonu repa a pushni. GitHub Pages: Settings → Pages → Deploy from branch,
`main`, priečinok `/docs`.
