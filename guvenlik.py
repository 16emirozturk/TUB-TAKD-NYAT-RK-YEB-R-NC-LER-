"""Kimlik doğrulama yardımcıları ve kişisel veri maskeleme."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_PBKDF2_ONEK = "pbkdf2_sha256"
_PBKDF2_ITERASYON = 600_000


def _b(metin: str) -> bytes:
    return metin.encode("utf-8")


def sabit_zamanli_esit(a: str, b: str) -> bool:
    return hmac.compare_digest(_b(a), _b(b))


def parola_hashle(parola: str, iterasyon: int = _PBKDF2_ITERASYON) -> str:
    tuz = secrets.token_bytes(16)
    ozet = hashlib.pbkdf2_hmac("sha256", _b(parola), tuz, iterasyon)
    return f"{_PBKDF2_ONEK}${iterasyon}${tuz.hex()}${ozet.hex()}"


def parola_dogru_mu(parola: str, kayit: str) -> bool:
    if not kayit:
        return False
    if kayit.startswith(_PBKDF2_ONEK + "$"):
        try:
            _, it, tuz, ozet = kayit.split("$")
            hesap = hashlib.pbkdf2_hmac("sha256", _b(parola), bytes.fromhex(tuz), int(it))
            return hmac.compare_digest(hesap, bytes.fromhex(ozet))
        except (ValueError, TypeError):
            return False
    return sabit_zamanli_esit(parola, kayit)


def kimlik_dogrula(kullanici: str, parola: str, beklenen_kullanici: str, beklenen_parola: str) -> bool:
    k = sabit_zamanli_esit(kullanici, beklenen_kullanici) if beklenen_kullanici else False
    p = parola_dogru_mu(parola, beklenen_parola)
    return k and p


def tc_kimlik_gecerli_mi(deger: str) -> bool:
    if not re.fullmatch(r"[1-9]\d{10}", deger):
        return False
    d = [int(c) for c in deger]
    if (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


_IBAN = re.compile(r"(?<![A-Za-z0-9])TR\d{2}(?:\s?\d{4}){5}\s?\d{2}(?!\d)", re.I)
_EPOSTA = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_TC = re.compile(r"(?<!\d)\d{11}(?!\d)")
_TELEFON = re.compile(
    r"(?<!\d)(?:\+?90[\s\-]*|0[\s\-]*)?\(?5\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)


def hassas_veri_maskele(metin: str) -> tuple[str, list[str]]:
    bulunan: set[str] = set()

    def degistir(etiket, tur, kosul=None):
        def _f(m):
            if kosul and not kosul(m.group(0)):
                return m.group(0)
            bulunan.add(tur)
            return etiket
        return _f

    metin = _IBAN.sub(degistir("[IBAN]", "IBAN"), metin)
    metin = _EPOSTA.sub(degistir("[E-POSTA]", "e-posta"), metin)
    metin = _TC.sub(degistir("[T.C. KİMLİK NO]", "T.C. kimlik no", tc_kimlik_gecerli_mi), metin)
    metin = _TELEFON.sub(degistir("[TELEFON]", "telefon"), metin)
    return metin, sorted(bulunan)


def csv_hucre_temizle(deger) -> str:
    s = "" if deger is None else str(deger)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s
