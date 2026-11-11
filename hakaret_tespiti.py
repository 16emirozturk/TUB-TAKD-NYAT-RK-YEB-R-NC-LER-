"""Kural tabanlı ifade işaretleme yardımcısı (sürüm 2)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SURUM = "2.0"
MAKS_METIN_UZUNLUGU = 10_000

ONCELIKLER = ("Sinyal yok", "Düşük", "Orta", "Yüksek", "Acil")

SOZLUK: tuple[tuple[str, str, int], ...] = (
    ("hakaret", "aptal", 1), ("hakaret", "salak", 1), ("hakaret", "gerizekalı", 1),
    ("hakaret", "geri zekalı", 1), ("hakaret", "ahmak", 1), ("hakaret", "beyinsiz", 1),
    ("hakaret", "dangalak", 1), ("hakaret", "budala", 1), ("hakaret", "mankafa", 1),
    ("hakaret", "ezik", 1), ("hakaret", "pislik", 1), ("hakaret", "defol", 1),
    ("hakaret", "şişko", 1), ("hakaret", "çirkin", 1),
    ("hakaret", "şerefsiz", 2), ("hakaret", "aşağılık", 2), ("hakaret", "iğrenç", 2),
    ("hakaret", "değersizsin", 2), ("hakaret", "hiçbir işe yaramazsın", 2),
    ("dışlama", "kimse seninle", 2), ("dışlama", "kimse onunla", 2),
    ("dışlama", "seninle oynamayız", 2), ("dışlama", "seninle oynamayacağız", 2),
    ("dışlama", "onunla konuşma", 2), ("dışlama", "grubumuza alma", 2),
    ("dışlama", "gruba almay", 2), ("dışlama", "aramıza alma", 2),
    ("dışlama", "kimse seni sevmiyor", 2), ("dışlama", "kimse seni istemiyor", 2),
    ("tehdit", "seni döv", 3), ("tehdit", "döverim", 3), ("tehdit", "döveceğ", 3), ("tehdit", "dövecek", 3),
    ("tehdit", "öldürürüm", 3), ("tehdit", "öldüreceğ", 3), ("tehdit", "öldürecek", 3),
    ("tehdit", "seni öldür", 3), ("tehdit", "seni bitir", 3), ("tehdit", "seni mahvet", 3),
    ("tehdit", "seni pişman ed", 3), ("tehdit", "hesap soracağım", 3),
    ("tehdit", "okul çıkışında gör", 3), ("tehdit", "çıkışta gör", 3),
    ("tehdit", "çıkışta bekle", 3), ("tehdit", "tehdit ediyorum", 3),
    ("dijital", "ifşa ed", 3), ("dijital", "rezil ed", 3), ("dijital", "herkese gönder", 3),
    ("dijital", "fotoğrafını payla", 3), ("dijital", "fotoğrafını", 2),
    ("dijital", "gizlice çek", 3), ("dijital", "ekran görüntüsünü payla", 3),
    ("acil", "intihar", 4), ("acil", "kendimi öldür", 4), ("acil", "kendime zarar", 4),
    ("acil", "ölmek istiyorum", 4), ("acil", "yaşamak istemiyorum", 4),
    ("acil", "kendini öldür", 4), ("acil", "kendine zarar", 4), ("acil", "bileğini kes", 4),
    ("acil", "bıçak getir", 4), ("acil", "silah getir", 4), ("acil", "silahım var", 4),
    ("acil", "bıçağım var", 4), ("acil", "bomba koy", 4), ("acil", "bomba at", 4),
)

_TR_HARITA = str.maketrans(
    {"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u"}
)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_AYIRICILI_HARFLER = re.compile(r"(?<![a-z0-9])(?:[a-z][\s.\-_*]+){2,}[a-z](?![a-z0-9])")
_AYIRICI = re.compile(r"[\s.\-_*]+")
_TEKRAR = re.compile(r"([a-z])\1+")


def _katla(metin: str) -> str:
    metin = unicodedata.normalize("NFKC", metin)
    metin = metin.replace("İ", "i").replace("I", "ı").lower()
    metin = metin.translate(_TR_HARITA)
    metin = "".join(c for c in unicodedata.normalize("NFD", metin) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", metin).strip()


def _tekrar_sadele(metin: str) -> str:
    return _TEKRAR.sub(r"\1", metin)


def _gizlemeyi_coz(katlanmis: str) -> str:
    belirtecler = []
    for b in katlanmis.split(" "):
        if re.search(r"[a-z]", b) and re.search(r"[0-9@$]", b):
            b = b.translate(_LEET)
        belirtecler.append(b)
    metin = " ".join(belirtecler)
    metin = _AYIRICILI_HARFLER.sub(lambda m: _AYIRICI.sub("", m.group(0)), metin)
    return _tekrar_sadele(metin)


def _desen(ifade: str, sadele: bool) -> re.Pattern[str]:
    f = _katla(ifade)
    if sadele:
        f = _tekrar_sadele(f)
    govde = r"\s+".join(re.escape(p) for p in f.split(" "))
    return re.compile(r"(?<![a-z0-9])" + govde + r"[a-z]*")


_DESENLER = tuple(
    (kategori, ifade, agirlik, _desen(ifade, False), _desen(ifade, True))
    for kategori, ifade, agirlik in SOZLUK
)

_OLUMSUZ = re.compile(r"(?<![a-z])(degil|demedim|demiyorum|diyemem|diyemez)(?![a-z])")
_AKTARIM = re.compile(
    r"(?<![a-z])(dedi|dediler|diyor|diyorlar|diye|soyledi|soylediler|yazdi|yazmis|bagirdi)(?![a-z])"
)
_SAKA = re.compile(r"(?<![a-z])(saka|espri|dalga)(?![a-z])")
_TIRNAK = re.compile(r"[\"“”‘’«»]")


@dataclass(frozen=True)
class Eslesme:
    kategori: str
    ifade: str
    metin_parcasi: str
    agirlik: int


@dataclass(frozen=True)
class Analiz:
    eslesmeler: tuple[Eslesme, ...]
    kategoriler: tuple[str, ...]
    oncelik: str
    baglam_uyarilari: tuple[str, ...]
    surum: str = SURUM

    @property
    def ifadeler(self) -> list[str]:
        return [e.ifade for e in self.eslesmeler]

    @property
    def sinyal_var(self) -> bool:
        return self.oncelik != "Sinyal yok"


_BOS = Analiz((), (), "Sinyal yok", ())


def _oncelik_hesapla(eslesmeler: list[Eslesme]) -> str:
    if not eslesmeler:
        return "Sinyal yok"
    kategoriler = {e.kategori for e in eslesmeler}
    if "acil" in kategoriler:
        return "Acil"
    if "tehdit" in kategoriler:
        return "Yüksek"
    toplam = sum(e.agirlik for e in eslesmeler)
    return "Orta" if (toplam >= 3 or len(kategoriler) >= 2) else "Düşük"


def analiz_et(metin: str) -> Analiz:
    if not metin or not metin.strip():
        return _BOS
    orijinal = metin[:MAKS_METIN_UZUNLUGU]
    a = _katla(orijinal)
    b = _gizlemeyi_coz(a)

    bulunan: dict[tuple[str, str], Eslesme] = {}
    for kategori, ifade, agirlik, d1, d2 in _DESENLER:
        m = d1.search(a) or d2.search(b)
        if m:
            bulunan[(kategori, ifade)] = Eslesme(kategori, ifade, m.group(0), agirlik)
    if not bulunan:
        return _BOS

    eslesmeler = sorted(bulunan.values(), key=lambda e: (-e.agirlik, e.kategori, e.ifade))
    uyarilar = []
    if _AKTARIM.search(a) or _TIRNAK.search(orijinal):
        uyarilar.append("Metinde aktarma/alıntı ifadeleri var; eşleşen söz bildirimi yapanın kendi sözü olmayabilir.")
    if _OLUMSUZ.search(a):
        uyarilar.append("Olumsuzlama ifadeleri var; eşleşme olumsuz bağlamda kullanılmış olabilir.")
    if _SAKA.search(a):
        uyarilar.append("Şaka/mizah ifadeleri var; bağlam insan tarafından değerlendirilmelidir.")

    return Analiz(
        eslesmeler=tuple(eslesmeler),
        kategoriler=tuple(sorted({e.kategori for e in eslesmeler})),
        oncelik=_oncelik_hesapla(eslesmeler),
        baglam_uyarilari=tuple(uyarilar),
    )


def supheli_ifadeleri_bul(metin: str) -> list[str]:
    return analiz_et(metin).ifadeler


def sozluk_ozeti() -> dict[str, int]:
    ozet: dict[str, int] = {}
    for kategori, _, _ in SOZLUK:
        ozet[kategori] = ozet.get(kategori, 0) + 1
    return ozet
