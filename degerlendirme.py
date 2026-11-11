"""Algılayıcı için küçük, SENTETİK değerlendirme seti ve metrikler."""
from __future__ import annotations

import hakaret_tespiti as ht

VERI: tuple[tuple[str, bool, str | None, str], ...] = (
    ("Sınıfta bana aptal dediler.", True, "hakaret", "pozitif"),
    ("Herkes onu SALAK diye çağırıyor.", True, "hakaret", "pozitif"),
    ("Gerizekalı diyerek alay ettiler.", True, "hakaret", "pozitif"),
    ("ONA GERİZEKALI DEDİLER", True, "hakaret", "pozitif"),
    ("Bana ahmak ve beyinsiz dedi.", True, "hakaret", "pozitif"),
    ("Aptalsın sen, defol buradan!", True, "hakaret", "pozitif"),
    ("Bana şerefsiz dedi ve herkesin içinde güldüler.", True, "hakaret", "pozitif"),
    ("Okul çıkışında seni döverim dedi.", True, "tehdit", "pozitif"),
    ("Beni öldüreceğini söyledi.", True, "tehdit", "pozitif"),
    ("Okul çıkışında görürüz seni, hesap soracağım.", True, "tehdit", "pozitif"),
    ("Kimse seninle oynamayacak, grubumuza alma onu.", True, "dışlama", "pozitif"),
    ("Onu gruba almayın, kimse onunla konuşmasın.", True, "dışlama", "pozitif"),
    ("Kimse seni sevmiyor, sınıfta herkes söylüyor.", True, "dışlama", "pozitif"),
    ("FotoğrafınıHerkese göndereceğim, seni rezil ederim.", True, "dijital", "pozitif"),
    ("Mesajlarını ifşa ederim, bekle.", True, "dijital", "pozitif"),
    ("Artık yaşamak istemiyorum.", True, "acil", "pozitif"),
    ("Kendime zarar vermeyi düşünüyorum.", True, "acil", "pozitif"),
    ("Kendimi öldüreceğim, kimse umursamıyor.", True, "acil", "pozitif"),
    ("Çantasında bıçak getirmiş, kullanacağını söyledi.", True, "acil", "pozitif"),
    ("Yarın okula silah getireceğini söyledi.", True, "acil", "pozitif"),
    ("Bana \"aptal\" dedi ve grup sohbetinde herkes güldü.", True, "hakaret", "pozitif"),
    ("a p t a l", True, "hakaret", "bulaniklastirilmis"),
    ("4ptal bir çocuksun", True, "hakaret", "bulaniklastirilmis"),
    ("aptaaaal", True, "hakaret", "bulaniklastirilmis"),
    ("s.a.l.a.k", True, "hakaret", "bulaniklastirilmis"),
    ("GER1ZEKAL1 diye bağırdı", True, "hakaret", "bulaniklastirilmis"),
    ("ap7al dedi bana", True, "hakaret", "bulaniklastirilmis"),
    ("gerizekali diyorlar", True, "hakaret", "bulaniklastirilmis"),
    ("Bugün matematik sınavı vardı, çok çalıştım.", False, None, "negatif"),
    ("Teneffüste arkadaşlarımla futbol oynadık.", False, None, "negatif"),
    ("Öğretmen bize güzel bir proje ödevi verdi.", False, None, "negatif"),
    ("Kantinde sandviç aldık, sonra sınıfa döndük.", False, None, "negatif"),
    ("Yarın okul gezisi var, otobüs sabah 8'de kalkacak.", False, None, "negatif"),
    ("Salata yapmak için domates ve salatalık aldık.", False, None, "negatif"),
    ("Kaptan gemiyi limana yanaştırdı.", False, None, "negatif"),
    ("Dövme yaptırmak istiyor ama ailesi izin vermiyor.", False, None, "negatif"),
    ("Bomba gibi bir haber aldım, sınavı erteleyecekler.", False, None, "negatif"),
    ("9/A sınıfı fen laboratuvarına gitti.", False, None, "negatif"),
    ("Kütüphanede sessizce ders çalıştık.", False, None, "negatif"),
    ("Arkadaşım bana yardım etti, çok teşekkür ederim.", False, None, "negatif"),
    ("Beden eğitimi dersinde basketbol turnuvası yapıldı.", False, None, "negatif"),
    ("", False, None, "negatif"),
    ("Ezik domatesleri ayırıp salata yaptık.", False, None, "zor_negatif"),
    ("Notlarımı görürse babam beni öldürecek.", False, None, "zor_negatif"),
    ("Bu oyunda silah getir komutunu kullanman gerekiyor.", False, None, "zor_negatif"),
    ("Herkes ona bakıp gülüyor ve kimse yanına oturmuyor.", True, "dışlama", "zor_pozitif"),
    ("Apdal diye çağırıyorlar onu.", True, "hakaret", "zor_pozitif"),
    ("Onun gibi birinin bu okulda işi yok, gitsin artık.", True, "dışlama", "zor_pozitif"),
)


def _bolme(pay: int, payda: int) -> float:
    return pay / payda if payda else 0.0


def degerlendir(analiz_fn=ht.analiz_et) -> dict:
    tp = fp = fn = tn = 0
    tur_ozeti: dict[str, dict[str, int]] = {}
    kat_ozeti: dict[str, dict[str, int]] = {}
    yanlis_pozitifler: list[str] = []
    kacirilanlar: list[str] = []

    for metin, beklenen, kategori, tur in VERI:
        a = analiz_fn(metin)
        tahmin = a.oncelik != "Sinyal yok"
        dogru = tahmin == beklenen
        ozet = tur_ozeti.setdefault(tur, {"n": 0, "dogru": 0})
        ozet["n"] += 1
        ozet["dogru"] += int(dogru)

        if beklenen and tahmin:
            tp += 1
        elif beklenen and not tahmin:
            fn += 1
            kacirilanlar.append(metin)
        elif not beklenen and tahmin:
            fp += 1
            yanlis_pozitifler.append(metin)
        else:
            tn += 1

        if beklenen and kategori:
            k = kat_ozeti.setdefault(kategori, {"n": 0, "bulunan": 0})
            k["n"] += 1
            k["bulunan"] += int(kategori in a.kategoriler)

    precision = _bolme(tp, tp + fp)
    recall = _bolme(tp, tp + fn)
    return {
        "toplam": len(VERI),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": _bolme(2 * precision * recall, precision + recall),
        "accuracy": _bolme(tp + tn, len(VERI)),
        "tur_ozeti": tur_ozeti,
        "kategori_ozeti": kat_ozeti,
        "yanlis_pozitifler": yanlis_pozitifler,
        "kacirilanlar": kacirilanlar,
        "surum": ht.SURUM,
    }
