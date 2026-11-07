"""Nahrá zverejnené OpenAI feedy do kanálov OpenAI Ads cez SFTP.

Hostovaná URL z GitHub Pages aj raw.githubusercontent.com dávala 7. 11. 2026 v kanáli 0 produktov (aj presná kópia
Shoptet súboru), preto feed tlačíme cez SFTP, oficiálnu cestu OpenAI.

Prístup na kanál generuje Ads Manager (Kanály → ⋯ → Upraviť pripojenie SFTP): adresa pripojenia a heslo.
Ukladá ich Marek do GitHub secrets, názvy v CIELE. Chýbajúci secret = kanál sa preskočí (nie chyba),
aby beh nepadal pred nastavením.

Pravidlá OpenAI: súbor priamo v koreni (žiadne priečinky), stály názov, každý upload je celý katalóg.
"""
import os
import sys
from urllib.parse import urlparse, unquote

# kanál: (súbor v docs, secret s adresou, secret s heslom)
CIELE = {
    'jahodarna CZ': ('docs/jahodarna/openai_cz.jsonl.gz', 'OA_SFTP_URI_JAHODARNA_CZ', 'OA_SFTP_HESLO_JAHODARNA_CZ'),
    'jahodarna SK': ('docs/jahodarna/openai_sk.jsonl.gz', 'OA_SFTP_URI_JAHODARNA_SK', 'JAHODARNA_SK_OPENAI'),
}
VZDIALENY_NAZOV = 'products.jsonl.gz'


def rozober_uri(uri):
    """sftp://pouzivatel@host:port (port voliteľný) alebo pouzivatel@host."""
    if '://' not in uri:
        uri = 'sftp://' + uri
    u = urlparse(uri.strip())
    if not u.hostname or not u.username:
        raise ValueError('adresa SFTP nemá host alebo používateľa')
    return u.hostname, u.port or 22, unquote(u.username)


def nahraj(nazov, subor, uri, heslo):
    import paramiko
    host, port, pouzivatel = rozober_uri(uri)
    transport = paramiko.Transport((host, port))
    try:
        transport.connect(username=pouzivatel, password=heslo)
        sftp = paramiko.SFTPClient.from_transport(transport)
        docasny = VZDIALENY_NAZOV + '.part'
        sftp.put(subor, docasny)
        try:
            sftp.posix_rename(docasny, VZDIALENY_NAZOV)
        except IOError:
            # Server bez posix-rename: prepíš priamo.
            sftp.remove(docasny)
            sftp.put(subor, VZDIALENY_NAZOV)
        velkost = sftp.stat(VZDIALENY_NAZOV).st_size
        if velkost != os.path.getsize(subor):
            raise RuntimeError('na serveri je %d B, lokálne %d B' % (velkost, os.path.getsize(subor)))
        print('OK %s: %s → %s (%d B)' % (nazov, subor, VZDIALENY_NAZOV, velkost))
    finally:
        transport.close()


def main():
    chyba = False
    for nazov, (subor, s_uri, s_heslo) in CIELE.items():
        uri, heslo = os.environ.get(s_uri, ''), os.environ.get(s_heslo, '')
        if not uri or not heslo:
            print('preskočené %s: chýba secret %s alebo %s' % (nazov, s_uri, s_heslo))
            continue
        if not os.path.exists(subor):
            print('CHYBA %s: súbor %s neexistuje' % (nazov, subor))
            chyba = True
            continue
        try:
            nahraj(nazov, subor, uri, heslo)
        except Exception as e:   # jeden kanál nezastaví druhý
            print('CHYBA %s: %s: %s' % (nazov, type(e).__name__, e))
            chyba = True
    return 2 if chyba else 0


if __name__ == '__main__':
    sys.exit(main())
