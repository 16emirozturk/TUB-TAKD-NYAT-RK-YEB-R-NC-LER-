"""Veri katmanı (SQLite). Streamlit'e bağımlı değildir; bağımsız test edilebilir.

v1'e göre düzeltmeler
- `with sqlite3.connect()` bağlantıyı KAPATMAZ (yalnızca commit/rollback yapar);
  burada bağlantı her zaman kapatılır.
- Saat dilimi Europe/Istanbul (bulut sunucuları UTC çalışır; v1 saatleri 3 saat kayıktı).
- Şema sürümü (PRAGMA user_version) ve eski veritabanını koruyan göç.
- v1'deki "ilk açılışta tüm kayıtları sil" mantığı kaldırıldı (veri kaybı riski).
- Takip kodu 32 bit yerine ~50 bit entropi, çakışmada yeniden deneme.
- Dizinler, WAL, işlem (aktör) ve denetim kaydı, giriş denemesi sayacı.

v2.1 (EK): tam anonim akış — cihaz jetonu özetiyle günlük kota, takip koduyla ek bilgi,
sunucu tarafı bekleme süresi. Mevcut işlevler değiştirilmedi; yalnızca dosya sonuna eklendi.
"""
from __future__ import annotations

import csv
import io
import os
import re
import hashlib
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import guvenlik
import hakaret_tespiti as ht
from sabitler import BIRIMLER, DURUMLAR, OLAY_TURLERI, ONCELIKLER

DB_YOLU = Path(os.environ.get("GUVENLI_OKUL_DB", Path(__file__).with_name("guvenli_okul_demo.db")))
TZ = ZoneInfo("Europe/Istanbul")
SEMA_SURUMU = 2
ANONIM_ZAMAN_YUVARLA = True  # anonim kayıtlarda zaman saate yuvarlanır (kimlik ifşasını zorlaştırır)
MAKS_NOT_UZUNLUGU = 1000
_KOD_ALFABESI = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 0/O, 1/I/L karışmaz
_ZAMAN_BICIMI = "%Y-%m-%d %H:%M:%S"


# ------------------------------------------------------------------ yardımcılar
def simdi(saate_yuvarla: bool = False) -> str:
    t = datetime.now(TZ)
    if saate_yuvarla:
        t = t.replace(minute=0, second=0, microsecond=0)
    return t.strftime(_ZAMAN_BICIMI)


def _onceki(dakika: int) -> str:
    return (datetime.now(TZ) - timedelta(minutes=dakika)).strftime(_ZAMAN_BICIMI)


def _saat_farki(baslangic: str, bitis: str) -> float:
    return (datetime.strptime(bitis, _ZAMAN_BICIMI) - datetime.strptime(baslangic, _ZAMAN_BICIMI)).total_seconds() / 3600


def gecen_saat(zaman: str) -> float:
    """Verilen zaman damgasından bu yana geçen saat."""
    return _saat_farki(zaman, simdi())


@contextmanager
def baglanti():
    conn = sqlite3.connect(DB_YOLU, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def takip_kodu_uret() -> str:
    return "GO-" + "".join(secrets.choice(_KOD_ALFABESI) for _ in range(10))


def takip_kodu_normalle(girdi: str) -> str | None:
    kod = re.sub(r"\s+", "", (girdi or "")).upper()
    return kod if re.fullmatch(r"GO-[A-Z0-9]{8,10}", kod) else None


# ------------------------------------------------------------------------ şema
_EK_SUTUNLAR = {
    "bildirimler": (
        ("anonim", "INTEGER NOT NULL DEFAULT 1"),
        ("ad", "TEXT NOT NULL DEFAULT ''"),
        ("soyad", "TEXT NOT NULL DEFAULT ''"),
        ("sinif", "TEXT NOT NULL DEFAULT ''"),
        ("birim", "TEXT NOT NULL DEFAULT 'Henüz atanmadı'"),
        ("son_guncelleme", "TEXT NOT NULL DEFAULT ''"),
        ("oncelik", "TEXT NOT NULL DEFAULT 'Sinyal yok'"),
        ("kategoriler", "TEXT NOT NULL DEFAULT ''"),
        ("baglam_uyarilari", "TEXT NOT NULL DEFAULT ''"),
        ("maskelenen", "TEXT NOT NULL DEFAULT ''"),
        ("analiz_surumu", "TEXT NOT NULL DEFAULT ''"),
    ),
    "inceleme_notlari": (("yazar", "TEXT NOT NULL DEFAULT ''"),),
    "islem_gecmisi": (("aktor", "TEXT NOT NULL DEFAULT ''"),),
}


def hazirla() -> None:
    """Tabloları oluşturur, eksik sütunları ekler; mevcut veriyi silmez."""
    with baglanti() as conn:
        mevcut_surum = conn.execute("PRAGMA user_version").fetchone()[0]
        if mevcut_surum > SEMA_SURUMU:
            raise RuntimeError("Veritabanı bu uygulamadan daha yeni bir şema sürümüne sahip.")
        conn.execute("PRAGMA journal_mode = WAL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bildirimler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                takip_no TEXT UNIQUE NOT NULL,
                olusturma_zamani TEXT NOT NULL,
                olay_turu TEXT NOT NULL,
                ortam TEXT NOT NULL,
                zaman_bilgisi TEXT NOT NULL,
                tekrar_durumu TEXT NOT NULL,
                aciklama TEXT NOT NULL,
                eslesmeler TEXT NOT NULL,
                durum TEXT NOT NULL DEFAULT 'İnceleme bekliyor',
                demo INTEGER NOT NULL DEFAULT 1
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inceleme_notlari (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                takip_no TEXT NOT NULL REFERENCES bildirimler(takip_no),
                zaman TEXT NOT NULL,
                not_metni TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS islem_gecmisi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                takip_no TEXT NOT NULL REFERENCES bildirimler(takip_no),
                zaman TEXT NOT NULL,
                islem TEXT NOT NULL,
                ayrinti TEXT NOT NULL DEFAULT ''
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS giris_denemeleri (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zaman TEXT NOT NULL,
                basarili INTEGER NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS denetim_kaydi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zaman TEXT NOT NULL,
                aktor TEXT NOT NULL,
                islem TEXT NOT NULL,
                ayrinti TEXT NOT NULL DEFAULT ''
            )""")

        for tablo, sutunlar in _EK_SUTUNLAR.items():
            var = {r["name"] for r in conn.execute(f"PRAGMA table_info({tablo})")}
            for ad, tanim in sutunlar:
                if ad not in var:
                    conn.execute(f"ALTER TABLE {tablo} ADD COLUMN {ad} {tanim}")

        for ad, ifade in (
            ("ix_notlar_takip", "inceleme_notlari(takip_no)"),
            ("ix_gecmis_takip", "islem_gecmisi(takip_no)"),
            ("ix_bildirim_durum", "bildirimler(durum)"),
            ("ix_bildirim_zaman", "bildirimler(olusturma_zamani)"),
            ("ix_giris_zaman", "giris_denemeleri(zaman)"),
        ):
            conn.execute(f"CREATE INDEX IF NOT EXISTS {ad} ON {ifade}")

        conn.execute(f"PRAGMA user_version = {SEMA_SURUMU}")


# ----------------------------------------------------------- yazma işlemleri
def _islem(conn, takip_no, islem, ayrinti="", aktor="", zaman=None):
    conn.execute(
        "INSERT INTO islem_gecmisi (takip_no, zaman, islem, ayrinti, aktor) VALUES (?, ?, ?, ?, ?)",
        (takip_no, zaman or simdi(), islem, ayrinti, aktor),
    )


def bildirim_ekle(*, olay_turu, ortam, zaman_bilgisi, tekrar_durumu, aciklama,
                  anonim, ad="", soyad="", sinif="", demo=True) -> dict:
    """Bildirimi kaydeder. Dönüş: {takip_no, analiz, maskelenen}."""
    analiz = ht.analiz_et(aciklama)
    maskeli, maskelenen = guvenlik.hassas_veri_maskele(aciklama)
    if anonim:
        ad = soyad = sinif = ""
    zaman = simdi(saate_yuvarla=bool(anonim) and ANONIM_ZAMAN_YUVARLA)
    eslesme_metni = ", ".join(analiz.ifadeler) if analiz.ifadeler else "Belirgin eşleşme yok"

    for _ in range(5):
        takip_no = takip_kodu_uret()
        try:
            with baglanti() as conn:
                conn.execute(
                    """INSERT INTO bildirimler (
                        takip_no, olusturma_zamani, olay_turu, ortam, zaman_bilgisi, tekrar_durumu,
                        aciklama, eslesmeler, durum, demo, anonim, ad, soyad, sinif, birim,
                        son_guncelleme, oncelik, kategoriler, baglam_uyarilari, maskelenen, analiz_surumu)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (takip_no, zaman, olay_turu, ortam, zaman_bilgisi, tekrar_durumu,
                     maskeli, eslesme_metni, "İnceleme bekliyor", int(demo), int(bool(anonim)),
                     ad, soyad, sinif, "Henüz atanmadı", zaman, analiz.oncelik,
                     ", ".join(analiz.kategoriler), " | ".join(analiz.baglam_uyarilari),
                     ", ".join(maskelenen), analiz.surum),
                )
                _islem(conn, takip_no, "Yeni bildirim oluşturuldu", aktor="Bildirim formu", zaman=zaman)
            return {"takip_no": takip_no, "analiz": analiz, "maskelenen": maskelenen}
        except sqlite3.IntegrityError as e:
            if "takip_no" not in str(e):
                raise
    raise RuntimeError("Benzersiz takip kodu üretilemedi.")


def durum_ve_birim_guncelle(takip_no: str, yeni_durum: str, yeni_birim: str, aktor: str) -> list[str]:
    if yeni_durum not in DURUMLAR or yeni_birim not in BIRIMLER:
        raise ValueError("Geçersiz durum veya birim.")
    degisiklikler: list[str] = []
    with baglanti() as conn:
        eski = conn.execute("SELECT durum, birim FROM bildirimler WHERE takip_no = ?", (takip_no,)).fetchone()
        if eski is None:
            raise LookupError("Kayıt bulunamadı.")
        if eski["durum"] != yeni_durum:
            _islem(conn, takip_no, "Durum değiştirildi", f"{eski['durum']} → {yeni_durum}", aktor)
            degisiklikler.append("durum")
        if eski["birim"] != yeni_birim:
            _islem(conn, takip_no, "Birim ataması değiştirildi", f"{eski['birim']} → {yeni_birim}", aktor)
            degisiklikler.append("birim")
        if degisiklikler:
            conn.execute(
                "UPDATE bildirimler SET durum = ?, birim = ?, son_guncelleme = ? WHERE takip_no = ?",
                (yeni_durum, yeni_birim, simdi(), takip_no),
            )
    return degisiklikler


def not_ekle(takip_no: str, metin: str, yazar: str) -> None:
    metin = (metin or "").strip()
    if not metin or len(metin) > MAKS_NOT_UZUNLUGU:
        raise ValueError("Not boş olamaz ve en fazla 1000 karakter olabilir.")
    with baglanti() as conn:
        if conn.execute("SELECT 1 FROM bildirimler WHERE takip_no = ?", (takip_no,)).fetchone() is None:
            raise LookupError("Kayıt bulunamadı.")
        conn.execute(
            "INSERT INTO inceleme_notlari (takip_no, zaman, not_metni, yazar) VALUES (?, ?, ?, ?)",
            (takip_no, simdi(), metin, yazar),
        )
        _islem(conn, takip_no, "İnceleme notu eklendi", aktor=yazar)


# ------------------------------------------------------------ okuma işlemleri
def kayitlari_getir(limit: int = 5000) -> list[dict]:
    with baglanti() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM bildirimler ORDER BY id DESC LIMIT ?", (limit,))]


def notlari_getir(takip_no: str) -> list[dict]:
    with baglanti() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT zaman, not_metni, yazar FROM inceleme_notlari WHERE takip_no = ? ORDER BY id DESC", (takip_no,))]


def gecmisi_getir(takip_no: str) -> list[dict]:
    with baglanti() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT zaman, islem, ayrinti, aktor FROM islem_gecmisi WHERE takip_no = ? ORDER BY id DESC", (takip_no,))]


def takip_durumu_getir(girdi: str) -> dict | None:
    kod = takip_kodu_normalle(girdi)
    if not kod:
        return None
    with baglanti() as conn:
        r = conn.execute(
            "SELECT takip_no, durum, olusturma_zamani, son_guncelleme FROM bildirimler WHERE takip_no = ?", (kod,)
        ).fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------- istatistik
def _sure_ozeti(saatler: list[float]) -> dict:
    if not saatler:
        return {"n": 0, "ortalama": None, "medyan": None}
    s = sorted(saatler)
    orta = len(s) // 2
    medyan = s[orta] if len(s) % 2 else (s[orta - 1] + s[orta]) / 2
    return {"n": len(s), "ortalama": sum(s) / len(s), "medyan": medyan}


def istatistik_ozeti(gun: int = 30) -> dict:
    with baglanti() as conn:
        toplam = conn.execute("SELECT COUNT(*) FROM bildirimler").fetchone()[0]
        durum = dict(conn.execute("SELECT durum, COUNT(*) FROM bildirimler GROUP BY durum").fetchall())
        tur = dict(conn.execute("SELECT olay_turu, COUNT(*) FROM bildirimler GROUP BY olay_turu").fetchall())
        oncelik = dict(conn.execute("SELECT oncelik, COUNT(*) FROM bildirimler GROUP BY oncelik").fetchall())
        anonim = conn.execute("SELECT COALESCE(SUM(anonim),0) FROM bildirimler").fetchone()[0]
        esik = (datetime.now(TZ) - timedelta(days=gun)).strftime("%Y-%m-%d")
        gunluk = [(r[0], r[1]) for r in conn.execute(
            "SELECT substr(olusturma_zamani,1,10) g, COUNT(*) FROM bildirimler "
            "WHERE substr(olusturma_zamani,1,10) >= ? GROUP BY g ORDER BY g", (esik,))]
        ilk = [_saat_farki(r[0], r[1]) for r in conn.execute(
            "SELECT b.olusturma_zamani, MIN(g.zaman) FROM bildirimler b "
            "JOIN islem_gecmisi g ON g.takip_no = b.takip_no AND g.islem = 'Durum değiştirildi' "
            "GROUP BY b.takip_no")]
        bitis = [_saat_farki(r[0], r[1]) for r in conn.execute(
            "SELECT b.olusturma_zamani, MIN(g.zaman) FROM bildirimler b "
            "JOIN islem_gecmisi g ON g.takip_no = b.takip_no AND g.ayrinti LIKE '% → Sonuçlandırıldı' "
            "GROUP BY b.takip_no")]
    return {
        "toplam": toplam,
        "durum": {d: durum.get(d, 0) for d in DURUMLAR},
        "tur": {t: tur.get(t, 0) for t in OLAY_TURLERI},
        "oncelik": {o: oncelik.get(o, 0) for o in ONCELIKLER},
        "anonim": anonim,
        "gunluk": gunluk,
        "ilk_inceleme_saat": _sure_ozeti(ilk),
        "sonuclanma_saat": _sure_ozeti(bitis),
    }


def ortam_ve_tur_dagilimi() -> dict:
    """Yönetici istatistikleri ve trend analizi için ortam ve tür kırılımı."""
    with baglanti() as conn:
        ortamlar = dict(conn.execute("SELECT ortam, COUNT(*) FROM bildirimler GROUP BY ortam").fetchall())
        turler = dict(conn.execute("SELECT olay_turu, COUNT(*) FROM bildirimler GROUP BY olay_turu").fetchall())
    return {"ortamlar": ortamlar, "turler": turler}


# ---------------------------------------------- güvenlik: giriş ve denetim kaydı
def giris_denemesi_kaydet(basarili: bool) -> None:
    with baglanti() as conn:
        conn.execute("INSERT INTO giris_denemeleri (zaman, basarili) VALUES (?, ?)", (simdi(), int(basarili)))
        conn.execute("DELETE FROM giris_denemeleri WHERE zaman < ?", (_onceki(60 * 24 * 7),))


def basarisiz_giris_sayisi(dakika: int = 15) -> int:
    with baglanti() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM giris_denemeleri WHERE basarili = 0 AND zaman >= ?", (_onceki(dakika),)
        ).fetchone()[0]


def denetim_ekle(aktor: str, islem: str, ayrinti: str = "") -> None:
    with baglanti() as conn:
        conn.execute(
            "INSERT INTO denetim_kaydi (zaman, aktor, islem, ayrinti) VALUES (?, ?, ?, ?)",
            (simdi(), aktor, islem, ayrinti),
        )


def denetim_getir(limit: int = 300) -> list[dict]:
    with baglanti() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT zaman, aktor, islem, ayrinti FROM denetim_kaydi ORDER BY id DESC LIMIT ?", (limit,))]


# ------------------------------------------------- dışa aktarım ve veri saklama
_CSV_ALANLARI = ("takip_no", "olusturma_zamani", "son_guncelleme", "olay_turu", "ortam", "zaman_bilgisi",
                 "tekrar_durumu", "durum", "birim", "oncelik", "kategoriler", "anonim", "maskelenen", "aciklama")
_CSV_KIMLIK = ("ad", "soyad", "sinif")


def csv_aktar(kayitlar: list[dict], kimlik_dahil: bool = False) -> bytes:
    alanlar = _CSV_ALANLARI + (_CSV_KIMLIK if kimlik_dahil else ())
    cikti = io.StringIO()
    yazici = csv.writer(cikti, delimiter=";")
    yazici.writerow(alanlar)
    for k in kayitlar:
        yazici.writerow([guvenlik.csv_hucre_temizle(k.get(a, "")) for a in alanlar])
    return cikti.getvalue().encode("utf-8-sig")


def sonuclanmis_kayitlari_sil(gun: int, aktor: str) -> int:
    if gun < 1:
        raise ValueError("Gün sayısı en az 1 olmalı.")
    esik = (datetime.now(TZ) - timedelta(days=gun)).strftime(_ZAMAN_BICIMI)
    with baglanti() as conn:
        kodlar = [r[0] for r in conn.execute(
            "SELECT takip_no FROM bildirimler WHERE durum = 'Sonuçlandırıldı' AND son_guncelleme < ?", (esik,))]
        for kod in kodlar:
            conn.execute("DELETE FROM inceleme_notlari WHERE takip_no = ?", (kod,))
            conn.execute("DELETE FROM islem_gecmisi WHERE takip_no = ?", (kod,))
            conn.execute("DELETE FROM bildirimler WHERE takip_no = ?", (kod,))
        conn.execute(
            "INSERT INTO denetim_kaydi (zaman, aktor, islem, ayrinti) VALUES (?, ?, ?, ?)",
            (simdi(), aktor, "Saklama süresi temizliği", f"{len(kodlar)} kayıt silindi (>{gun} gün)"),
        )
    return len(kodlar)


# =====================================================================================
# v2.1 EK: TAM ANONİM AKIŞ
# =====================================================================================
MAKS_EK_SAYISI = 3
EK_BEKLEME_SN = 60
MAKS_EK_UZUNLUGU = 1000


class AnonimHata(Exception):
    def __init__(self, kod: str, mesaj: str, bekle_sn: int = 0):
        super().__init__(mesaj)
        self.kod, self.mesaj, self.bekle_sn = kod, mesaj, bekle_sn


def _bugun() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _jeton_ozeti(jeton: str) -> str:
    if not jeton or len(jeton) < 32:
        raise AnonimHata("jeton", "Cihaz jetonu geçersiz.")
    return hashlib.sha256(("gb1:" + jeton).encode("utf-8")).hexdigest()


def anonim_hazirla() -> None:
    with baglanti() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS anonim_cihaz ("
                     "token_ozeti TEXT PRIMARY KEY, gun TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS ek_bilgiler ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "takip_no TEXT NOT NULL REFERENCES bildirimler(takip_no), "
                     "zaman TEXT NOT NULL, metin TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS ek_kilit ("
                     "takip_no TEXT PRIMARY KEY REFERENCES bildirimler(takip_no), son_epoch REAL NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_ek_takip ON ek_bilgiler(takip_no)")


def anonim_kota_dolu_mu(jeton: str) -> bool:
    ozet, bugun = _jeton_ozeti(jeton), _bugun()
    with baglanti() as conn:
        conn.execute("DELETE FROM anonim_cihaz WHERE gun < ?", (bugun,))
        return conn.execute("SELECT 1 FROM anonim_cihaz WHERE token_ozeti = ? AND gun = ?",
                            (ozet, bugun)).fetchone() is not None


def anonim_bildirim_ekle(jeton: str, *, olay_turu, ortam, zaman_bilgisi, tekrar_durumu, aciklama, demo=True) -> dict:
    ozet, bugun = _jeton_ozeti(jeton), _bugun()
    with baglanti() as conn:
        conn.execute("DELETE FROM anonim_cihaz WHERE gun < ?", (bugun,))
        try:
            conn.execute("INSERT INTO anonim_cihaz (token_ozeti, gun) VALUES (?, ?)", (ozet, bugun))
        except sqlite3.IntegrityError:
            raise AnonimHata("kota", "Günlük bildirim hakkınız dolmuştur.") from None
    try:
        return bildirim_ekle(olay_turu=olay_turu, ortam=ortam, zaman_bilgisi=zaman_bilgisi,
                             tekrar_durumu=tekrar_durumu, aciklama=aciklama, anonim=True, demo=demo)
    except BaseException:
        with baglanti() as conn:
            conn.execute("DELETE FROM anonim_cihaz WHERE token_ozeti = ? AND gun = ?", (ozet, bugun))
        raise


def anonim_takip_ozeti(girdi: str) -> dict | None:
    d = takip_durumu_getir(girdi)
    if not d:
        return None
    with baglanti() as conn:
        r = conn.execute("SELECT anonim, durum, olusturma_zamani FROM bildirimler WHERE takip_no = ?",
                         (d["takip_no"],)).fetchone()
        adet = conn.execute("SELECT COUNT(*) FROM ek_bilgiler WHERE takip_no = ?", (d["takip_no"],)).fetchone()[0]
        k = conn.execute("SELECT son_epoch FROM ek_kilit WHERE takip_no = ?", (d["takip_no"],)).fetchone()
    bekle = max(0, math.ceil(EK_BEKLEME_SN - (time.time() - k["son_epoch"]))) if k else 0
    d.update(
        ek_adet=adet,
        ek_kalan_hak=max(0, MAKS_EK_SAYISI - adet),
        bekle_sn=bekle,
        ek_uygun=bool(r["anonim"]) and r["olusturma_zamani"][:10] == _bugun() and r["durum"] != "Sonuçlandırıldı",
    )
    return d


def ek_bilgi_ekle(girdi: str, metin: str) -> dict:
    metin = (metin or "").strip()
    if not metin or not any(c.isalnum() for c in metin):
        raise AnonimHata("bos", "Ek bilgi boş olamaz.")
    if len(metin) > MAKS_EK_UZUNLUGU:
        raise AnonimHata("bos", f"Ek bilgi en fazla {MAKS_EK_UZUNLUGU} karakter olabilir.")
    kod = takip_kodu_normalle(girdi)
    analiz = ht.analiz_et(metin)
    maskeli, maskelenen = guvenlik.hassas_veri_maskele(metin)
    with baglanti() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT * FROM bildirimler WHERE takip_no = ?", (kod,)).fetchone() if kod else None
        if r is None or not r["anonim"]:
            raise AnonimHata("gecersiz_kod", "Bu kodla ek bilgi eklenebilecek bir bildirim bulunamadı.")
        if r["olusturma_zamani"][:10] != _bugun():
            raise AnonimHata("gun_gecti", "Ek bilgi yalnızca bildirimin yapıldığı gün eklenebilir.")
        if r["durum"] == "Sonuçlandırıldı":
            raise AnonimHata("kapali", "Bu bildirim sonuçlandırıldığı için ek bilgi eklenemez.")
        adet = conn.execute("SELECT COUNT(*) FROM ek_bilgiler WHERE takip_no = ?", (kod,)).fetchone()[0]
        if adet >= MAKS_EK_SAYISI:
            raise AnonimHata("limit", f"Bugün en fazla {MAKS_EK_SAYISI} ek bilgi eklenebilir.")
        k = conn.execute("SELECT son_epoch FROM ek_kilit WHERE takip_no = ?", (kod,)).fetchone()
        if k:
            kalan = math.ceil(EK_BEKLEME_SN - (time.time() - k["son_epoch"]))
            if kalan > 0:
                raise AnonimHata("bekle", f"Yeni ek bilgi için yaklaşık {kalan} saniye beklemelisin.", kalan)

        zaman = simdi(saate_yuvarla=True)
        conn.execute("INSERT INTO ek_bilgiler (takip_no, zaman, metin) VALUES (?, ?, ?)", (kod, zaman, maskeli))
        conn.execute("INSERT INTO ek_kilit (takip_no, son_epoch) VALUES (?, ?) "
                     "ON CONFLICT(takip_no) DO UPDATE SET son_epoch = excluded.son_epoch", (kod, time.time()))

        oncelik = max(r["oncelik"], analiz.oncelik, key=lambda o: ONCELIKLER.index(o) if o in ONCELIKLER else 0)
        kat = sorted({x for x in (r["kategoriler"] or "").split(", ") if x} | set(analiz.kategoriler))
        esk = [x for x in (r["eslesmeler"] or "").split(", ") if x and x != "Belirgin eşleşme yok"]
        esk += [x for x in analiz.ifadeler if x not in esk]
        uyari = [x for x in (r["baglam_uyarilari"] or "").split(" | ") if x]
        uyari += [x for x in analiz.baglam_uyarilari if x not in uyari]
        mask = sorted({x for x in (r["maskelenen"] or "").split(", ") if x} | set(maskelenen))
        conn.execute(
            "UPDATE bildirimler SET oncelik = ?, kategoriler = ?, eslesmeler = ?, baglam_uyarilari = ?, "
            "maskelenen = ?, son_guncelleme = ? WHERE takip_no = ?",
            (oncelik, ", ".join(kat), ", ".join(esk) or "Belirgin eşleşme yok", " | ".join(uyari),
             ", ".join(mask), zaman, kod))
        _islem(conn, kod, "Anonim ek bilgi eklendi", f"{adet + 1}. ek bilgi", "Anonim gönderen", zaman)
    return {"adet": adet + 1, "kalan_hak": MAKS_EK_SAYISI - adet - 1, "oncelik": analiz.oncelik,
            "maskelenen": maskelenen}


def ek_bilgileri_getir(takip_no: str) -> list[dict]:
    with baglanti() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT zaman, metin FROM ek_bilgiler WHERE takip_no = ? ORDER BY id", (takip_no,))]


def ek_sayilari() -> dict[str, int]:
    with baglanti() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT takip_no, COUNT(*) FROM ek_bilgiler GROUP BY takip_no")}
