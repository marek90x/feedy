# Feed prevodník

Stiahne produktový feed klienta, opraví ho podľa konfigurácie a zverejní opravenú kópiu na GitHub Pages.
Platforma (Zboží, Meta katalóg, neskôr Google Merchant) ťahá opravený feed namiesto pôvodného.

**Pred akoukoľvek zmenou si prečítaj [POSTUP.md](POSTUP.md)** (krok za krokom: nový klient, nová platforma,
zverejnenie) **a [PASCE.md](PASCE.md)** (špecifikácie, validátory a pasce pre všetkých klientov).

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
| Voono | Zboží | https://www.voono.cz/app/uploads/xml/feed_zbozi_1.xml | https://marek90x.github.io/feedy/voono/zbozi.xml (ostrý od 6. 11. 2026) |
| Voono CZ | Meta katalóg (nový CZ katalóg) | https://www.voono.cz/app/uploads/xml/feed_google_merchants_1.xml + Woo Store API (ceny, sklad) | https://marek90x.github.io/feedy/voono/meta.xml (ostrý od 6. 11. 2026, katalóg „VOONO CZ \| feed 2027 m“ 28998039579846275, feed 1421935526282230, hodinovo, pixel CZ 405460924254766) |

## Nový klient alebo platforma

Postup krok za krokom je v [POSTUP.md](POSTUP.md).

## Zverejnenie

Zdroj pravdy je tento priečinok v `~/marketing`; verejné repo `marek90x/feedy` je jeho kópia (postup v `POSTUP.md`, krok 7).
GitHub Pages: `main`, priečinok `/docs`.
