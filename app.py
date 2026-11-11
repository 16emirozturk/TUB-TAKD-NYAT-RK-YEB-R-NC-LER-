"""Güvenli Okul — bildirim ve takip platformu (Streamlit, sürüm 2).

Mimari: app.py (arayüz) · db.py (veri) · guvenlik.py (kimlik/maskeleme) ·
hakaret_tespiti.py (kural tabanlı sinyal) · degerlendirme.py (ölçüm) · stil.py (CSS).
"""
from __future__ import annotations

import html
import json
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import db
import degerlendirme
import guvenlik
import hakaret_tespiti as ht
import stil
from sabitler import (ACIL_HATLAR, BIRIMLER, DURUMLAR, ONCELIK_SIRASI, ONCELIKLER, OLAY_TURLERI,
                      ORTAMLAR, SINIFLAR, TEKRARLAR, ZAMANLAR)

st.set_page_config(page_title="Güvenli Okul", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")
stil.uygula()

# ------------------------------------------------------------------ ayarlar
OTURUM_ZAMAN_ASIMI_SN = 30 * 60      # hareketsizlikte yönetici oturumu kapanır
OTURUM_HATALI_ESIK = 5               # bir tarayıcı oturumunda ardışık hatalı giriş sınırı
OTURUM_KILIT_SN = 5 * 60
GLOBAL_HATALI_ESIK = 20              # tüm oturumlarda 15 dakikadaki hatalı giriş sınırı
FORM_PENCERE_SN, FORM_PENCERE_MAKS = 600, 5   # bildirim spamına karşı oturum başına sınır
SORGU_PENCERE_SN, SORGU_MAKS = 300, 10        # takip kodu tahminine karşı sınır
ISTATISTIKLER_HERKESE_ACIK = False
SLA_SAAT = 24                        # ilk incelemeye alınmayan kayıt bu süreden sonra "gecikmiş" sayılır

ss = st.session_state


def _secret(bolum: str, anahtar: str, varsayilan: str = "") -> str:
    try:
        return str(st.secrets[bolum][anahtar])
    except Exception:
        return varsayilan


ADMIN_KULLANICI = _secret("admin", "username")
ADMIN_PAROLA = _secret("admin", "password_hash") or _secret("admin", "password")
DEMO_MODU = _secret("uygulama", "demo_modu", "true").lower() != "false"


@st.cache_resource
def _veritabanini_hazirla() -> bool:
    db.hazirla()
    db.anonim_hazirla()
    return True


_veritabanini_hazirla()

# ------------------------------------------------------------ oturum durumu
for _k, _v in {"yonetici_giris": False, "yonetici_adi": "", "son_etkinlik": 0.0, "hatali_giris": 0,
               "kilit_bitis": 0.0, "form_surumu": 0, "son_bildirim": None, "ana_menu": "Genel bakış"}.items():
    ss.setdefault(_k, _v)


def flash(tur: str, mesaj: str) -> None:
    ss["_flash"] = (tur, mesaj)


def flash_goster() -> None:
    f = ss.pop("_flash", None)
    if f:
        getattr(st, f[0])(f[1])


def sayfaya_git(ad: str) -> None:
    ss["ana_menu"] = ad


def oturumu_kapat(neden: str = "Oturum kapatıldı") -> None:
    if ss.yonetici_giris:
        db.denetim_ekle(ss.yonetici_adi, neden)
    ss.yonetici_giris = False
    ss.yonetici_adi = ""


def oturum_denetle() -> None:
    if ss.yonetici_giris:
        if time.time() - ss.son_etkinlik > OTURUM_ZAMAN_ASIMI_SN:
            oturumu_kapat("Oturum zaman aşımı")
            flash("warning", "Hareketsizlik nedeniyle yönetici oturumu kapatıldı.")
        else:
            ss.son_etkinlik = time.time()


def hiz_siniri(anahtar: str, pencere_sn: int, maks: int) -> tuple[bool, int]:
    simdi = time.time()
    liste = [t for t in ss.get(anahtar, []) if simdi - t < pencere_sn]
    if len(liste) >= maks:
        ss[anahtar] = liste
        return False, int(pencere_sn - (simdi - liste[0])) + 1
    liste.append(simdi)
    ss[anahtar] = liste
    return True, 0


def acil_hat_html() -> str:
    parca = " · ".join(f"<b>{html.escape(n)}</b> {html.escape(a)}" for n, a in ACIL_HATLAR)
    return f"<div class='emergency'>Acil bir tehlike varsa beklemeyin: {parca}</div>"


def yonetici_gerekli() -> None:
    if ss.yonetici_giris:
        return
    st.markdown(
        "<div class='access-panel'><strong>Yönetici erişimi gerekli</strong><br>"
        "<span style='color:#5b6b7e'>Bu bölümü görüntülemek için yönetici oturumu açın.</span></div>",
        unsafe_allow_html=True,
    )
    st.button("Yönetici girişine git", type="primary", on_click=sayfaya_git, args=("Yönetici girişi",))
    altbilgi()
    st.stop()


def altbilgi() -> None:
    st.divider()
    st.caption("Güvenli Okul · Bildirim ve takip sistemi · Kural tabanlı sinyaller karar değildir.")


def pill(metin: str, sinif: str) -> str:
    return f"<span class='pill {sinif}'>{html.escape(metin)}</span>"


DURUM_SINIF = {"İnceleme bekliyor": "status-waiting", "İnceleniyor": "status-active",
               "Takip gerekiyor": "status-followup", "Sonuçlandırıldı": "status-done"}
ONCELIK_SINIF = {"Sinyal yok": "prio-none", "Düşük": "prio-low", "Orta": "prio-mid",
                 "Yüksek": "prio-high", "Acil": "prio-urgent"}


# ============================================================ 1. GENEL BAKIŞ
def sayfa_genel_bakis() -> None:
    st.markdown(
        """<div class="hero"><div class="eyebrow">BİLDİRİM VE TAKİP PLATFORMU</div>
        <h1>Her öğrenci güvenli bir okul ortamını hak eder.</h1>
        <p>Bildirim süreçlerini düzenlemek, inceleme adımlarını izlemek ve güvenli bir okul kültürünü desteklemek için tasarlandı.</p></div>""",
        unsafe_allow_html=True,
    )
    if ss.yonetici_giris:
        kay = db.kayitlari_getir()
        acik = [k for k in kay if k["durum"] != "Sonuçlandırıldı"]
        st.subheader("Yönetici özeti")
        o = st.columns(4)
        o[0].metric("Açık kayıt", len(acik))
        o[1].metric("Yüksek veya acil sinyal (açık)", sum(k["oncelik"] in ("Acil", "Yüksek") for k in acik))
        o[2].metric(f"{SLA_SAAT} saati aşan bekleyen",
                    sum(k["durum"] == "İnceleme bekliyor" and db.gecen_saat(k["olusturma_zamani"]) > SLA_SAAT for k in kay))
        o[3].metric("Toplam kayıt", len(kay))
        st.button("İnceleme kuyruğunu aç", type="primary", on_click=sayfaya_git, args=("Bildirim inceleme",), key="g_kuyruk")
    st.markdown(acil_hat_html(), unsafe_allow_html=True)
    st.subheader("Nasıl kullanılır?")
    a, b, c = st.columns(3, gap="medium")
    with a:
        st.markdown("<div class='soft-card'><h3>1 · Bildirim oluştur</h3><p>Formu doldur; kimliğin "
                    "istenmez. Sana gizli bir takip kodu verilir.</p></div>", unsafe_allow_html=True)
        st.button("Yeni bildirim", type="primary", on_click=sayfaya_git, args=("Yeni bildirim",), key="g_yeni")
    with b:
        st.markdown("<div class='soft-card'><h3>2 · Durumu sorgula</h3><p>Takip koduyla kaydın hangi "
                    "aşamada olduğunu görür, aynı gün ek bilgi ekleyebilirsin.</p></div>", unsafe_allow_html=True)
        st.button("Bildirim sorgula", on_click=sayfaya_git, args=("Bildirim sorgula",), key="g_sorgu")
    with c:
        st.markdown("<div class='soft-card'><h3>3 · İnsan incelemesi</h3><p>Yetkili kişiler kaydı inceler, "
                    "birim atar ve not ekler. İşlemler denetim kaydına eklenir.</p></div>", unsafe_allow_html=True)
        st.button("Yönetici girişi", on_click=sayfaya_git, args=("Yönetici girişi",), key="g_yon")

    st.subheader("Güvenlik ilkeleri")
    g1, g2, g3 = st.columns(3, gap="medium")
    g1.markdown("**Gizlilik**\n\nAnonim seçenekte kimlik alanları hiç kaydedilmez; açıklamadaki "
                "telefon, T.C. kimlik numarası, e-posta ve IBAN otomatik maskelenir.")
    g2.markdown("**İnsan değerlendirmesi**\n\nOtomatik ifade eşleşmeleri yalnızca öncelik sinyalidir; "
                "kesin karar veya kanıt değildir.")
    g3.markdown("**Hesap verebilirlik**\n\nYönetici işlemleri, kim tarafından yapıldığıyla birlikte "
                "denetim kaydında tutulur.")


# =========================================================== 2. YENİ BİLDİRİM
def baslik_blogu(ust: str, baslik: str, aciklama: str = "") -> None:
    st.markdown(f"<div class='page-heading'><div class='eyebrow-dark'>{html.escape(ust)}</div>"
                f"<h1>{html.escape(baslik)}</h1>" + (f"<p>{html.escape(aciklama)}</p>" if aciklama else "")
                + "</div>", unsafe_allow_html=True)


def sorguya_git(kod: str = "") -> None:
    if kod:
        ss["sorgu_aktif_kod"] = kod
    ss["ana_menu"] = "Bildirim sorgula"


def _bildirim_basari_karti(son: dict) -> None:
    with st.container(border=True):
        st.success("Bildirimin kaydedildi. Teşekkür ederiz.")
        st.write("**Gizli takip kodun.** Durumu ve ek bilgi eklemeyi bu kodla yaparsın. "
                 "Not al ya da ekran görüntüsü al; kod kaybolursa yeniden oluşturulamaz:")
        st.code(son["takip_no"])
        if son["maskelenen"]:
            st.info("Açıklamandaki şu bilgiler gizlilik için otomatik gizlendi: " + ", ".join(son["maskelenen"]) + ".")
        if son["oncelik"] in ("Acil", "Yüksek"):
            st.error("Yazdıkların için endişeleniyoruz. Şu an güvende değilsen ya da kendini veya başkasını "
                     "tehlikede hissediyorsan lütfen hemen güvendiğin bir yetişkine, okul rehber öğretmenine "
                     "ya da yukarıdaki acil hatlara ulaş.")
        st.caption("Bu takip kodu yalnızca uygulamadaki kaydı tanımlar. Rehberlikle mesajlaşma yoktur; "
                   "yalnızca bildirimin durumunu görürsün."
                   + (" Herhangi bir kuruma otomatik bildirim gönderilmez." if DEMO_MODU else ""))
        c1, c2 = st.columns(2)
        c1.button("Durumu sorgula", type="primary", on_click=sorguya_git, args=(son["takip_no"],), key="son_sorgu")
        c2.button("Kodumu not aldım", on_click=lambda: ss.update(son_bildirim=None), key="son_kapat")


def sayfa_yeni_bildirim() -> None:
    baslik_blogu("BİLDİRİM", "Yeni bildirim",
                 "Gördüğün ya da yaşadığın zorbalığı kimliğini vermeden bildirebilirsin. Ad, sınıf veya telefon istenmez.")
    st.markdown(acil_hat_html(), unsafe_allow_html=True)

    jeton = _cihaz_jetonu()
    son = ss.get("son_bildirim")
    if son:
        _bildirim_basari_karti(son)
        return
    if not jeton:
        st.caption("Hazırlanıyor…")
        return
    if db.anonim_kota_dolu_mu(jeton):
        st.warning("Günlük bildirim hakkınız dolmuştur. Yarın yeniden bildirim yapabilirsin. "
                   "Bugünkü bildirimine takip koduyla ek bilgi ekleyebilirsin.")
        st.button("Bildirimime ek bilgi ekle", on_click=sorguya_git, key="kota_sorgu")
        return
    st.caption("Günde bir bildirim gönderilebilir. Bu sınır, tarayıcında saklanan rastgele bir kodla uygulanır; "
               "kimliğinle ilişkili değildir, bildirime bağlanmaz ve gün bitince silinir."
               + ("" if DEMO_MODU else " Telefon, adres gibi bilgileri yazmana gerek yok; yalnızca olayı anlat."))

    no = ss.form_surumu
    with st.form(f"bildirim_formu_{no}", clear_on_submit=False):
        olay_turu = st.selectbox("Ne oldu?", OLAY_TURLERI, key=f"tur_{no}")
        ortam = st.selectbox("Nerede oldu?", ORTAMLAR, key=f"ortam_{no}")
        zaman_bilgisi = st.selectbox("Ne zaman oldu?", ZAMANLAR, key=f"zaman_{no}")
        tekrar = st.radio("Bu olay kaç kez yaşandı?", TEKRARLAR, key=f"tekrar_{no}")
        aciklama = st.text_area("Olayı kendi cümlelerinle anlat", max_chars=3000, height=150, key=f"aciklama_{no}",
                                placeholder="Ne oldu, nerede ve ne zaman oldu? Ad, telefon gibi bilgileri yazma.")
        onay = st.checkbox(
            "Gerçek kişisel bilgi içermeyen örnek bir açıklama girdiğimi onaylıyorum." if DEMO_MODU
            else "Bilgilendirme metnini okudum; verdiğim bilgilerin bildirim sürecinde işlenmesini kabul ediyorum.",
            key=f"onay_{no}")
        gonder = st.form_submit_button("Bildirimi gönder", type="primary")

    if not gonder:
        return
    if not aciklama.strip():
        st.error("Lütfen olayı anlat.")
    elif not any(ch.isalnum() for ch in aciklama):
        st.error("Açıklama yalnızca boşluk veya noktalama işaretlerinden oluşamaz. Kısaca ne olduğunu yaz.")
    elif not onay:
        st.error("Lütfen onay kutusunu işaretle.")
    else:
        izin, bekle = hiz_siniri("gonderim_zamanlari", FORM_PENCERE_SN, FORM_PENCERE_MAKS)
        if not izin:
            st.error(f"Kısa sürede çok fazla bildirim gönderildi. Lütfen yaklaşık {bekle} saniye sonra tekrar dene.")
            return
        try:
            sonuc = db.anonim_bildirim_ekle(jeton, olay_turu=olay_turu, ortam=ortam, zaman_bilgisi=zaman_bilgisi,
                                            tekrar_durumu=tekrar, aciklama=aciklama.strip(), demo=DEMO_MODU)
        except db.AnonimHata as e:
            st.warning(e.mesaj)
            return
        except (sqlite3.Error, RuntimeError):
            st.error("Kayıt oluşturulamadı. Lütfen daha sonra tekrar dene.")
            return
        ss.son_bildirim = {"takip_no": sonuc["takip_no"], "maskelenen": sonuc["maskelenen"],
                           "oncelik": sonuc["analiz"].oncelik}
        ss.form_surumu += 1
        st.rerun()


# ========================================================= 3. BİLDİRİM SORGULA
DURUM_ACIKLAMA = {
    "İnceleme bekliyor": "Bildirimin alındı. Yetkili kişi henüz incelemeye başlamadı.",
    "İnceleniyor": "Bildirimin yetkili kişiler tarafından inceleniyor.",
    "Takip gerekiyor": "İnceleme sürüyor; konu takip edilmeye devam ediyor.",
    "Sonuçlandırıldı": "Bildirimin değerlendirildi ve süreç tamamlandı.",
}


def sayfa_sorgula() -> None:
    baslik_blogu("TAKİP", "Bildirim sorgula",
                 "Takip kodunla bildiriminin durumunu görebilir, aynı gün içinde ek bilgi ekleyebilirsin. "
                 "Açıklama, notlar ve kişi bilgileri gösterilmez; mesajlaşma yoktur.")
    with st.form("sorgu_formu"):
        kod = st.text_input("Gizli takip kodu", placeholder="GO-XXXXXXXXXX", max_chars=20)
        ara = st.form_submit_button("Durumu göster", type="primary")
    if ara:
        izin, bekle = hiz_siniri("sorgu_zamanlari", SORGU_PENCERE_SN, SORGU_MAKS)
        if not izin:
            st.error(f"Çok fazla sorgu yapıldı. Lütfen yaklaşık {bekle} saniye sonra tekrar dene.")
            return
        ozet = db.anonim_takip_ozeti(kod)
        if not ozet:
            ss.pop("sorgu_aktif_kod", None)
            st.error("Bu kodla eşleşen bir kayıt bulunamadı. Kodu kontrol edip tekrar dene.")
            return
        ss["sorgu_aktif_kod"] = ozet["takip_no"]
    aktif = ss.get("sorgu_aktif_kod")
    if not aktif:
        return
    ozet = db.anonim_takip_ozeti(aktif)
    if not ozet:
        ss.pop("sorgu_aktif_kod", None)
        return
    st.markdown(
        f"<div class='record-banner'><div><div class='record-kicker'>TAKİP KODU</div>"
        f"<div class='record-id'>{html.escape(ozet['takip_no'])}</div></div>"
        f"{pill(ozet['durum'], DURUM_SINIF.get(ozet['durum'], 'status-waiting'))}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='durum-not'>{html.escape(DURUM_ACIKLAMA.get(ozet['durum'], ''))}</div>",
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.metric("Oluşturulma", ozet["olusturma_zamani"])
    c2.metric("Son güncelleme", ozet["son_guncelleme"] or "—")

    st.markdown("#### Ek bilgi ekle")
    if not ozet["ek_uygun"]:
        st.info("Ek bilgi yalnızca anonim bildirimlerde, bildirimin yapıldığı gün ve bildirim sonuçlanmadan önce "
                "eklenebilir.")
        return
    st.caption(f"Eklenen ek bilgi: {ozet['ek_adet']} / {db.MAKS_EK_SAYISI}. Ek bilgiler aynı bildirimin altına "
               f"eklenir; her ek arasında {db.EK_BEKLEME_SN} saniye beklenir.")
    if ozet["ek_kalan_hak"] == 0:
        st.info("Bugün için ek bilgi sınırına ulaştın.")
        return
    if ozet["bekle_sn"] > 0:
        st.warning(f"Yeni ek bilgi için yaklaşık {ozet['bekle_sn']} saniye beklemen gerekiyor.")
    ss.setdefault("anon_ek_surumu", 0)
    with st.form("anon_ek_formu"):
        ek = st.text_area("Eklemek istediğin bilgi", max_chars=db.MAKS_EK_UZUNLUGU, height=110,
                          key=f"anon_ek_{ss.anon_ek_surumu}",
                          placeholder="Ad, telefon gibi bilgileri yazma.")
        gonder = st.form_submit_button("Ek bilgiyi gönder", type="primary")
    if not gonder:
        return
    try:
        sonuc = db.ek_bilgi_ekle(aktif, ek)
    except db.AnonimHata as e:
        st.error(e.mesaj)
        return
    except (sqlite3.Error, RuntimeError):
        st.error("Ek bilgi kaydedilemedi. Lütfen daha sonra tekrar dene.")
        return
    ss.anon_ek_surumu += 1
    flash("success", f"Ek bilgin eklendi. Bugün {sonuc['kalan_hak']} ek bilgi hakkın kaldı.")
    st.rerun()


# ======================================================== 4. YÖNETİCİ GİRİŞİ
def sayfa_yonetici_girisi() -> None:
    st.markdown("<div class='hero'><h1>Yönetici girişi</h1><p>Bildirim inceleme alanına yalnızca "
                "yetkili kullanıcılar erişebilir.</p></div>", unsafe_allow_html=True)
    if ss.yonetici_giris:
        st.success("Yönetici oturumu açık.")
        st.button("Bildirim incelemeye git", type="primary", on_click=sayfaya_git, args=("Bildirim inceleme",))
        return
    if not ADMIN_KULLANICI or not ADMIN_PAROLA:
        st.error("Yönetici bilgileri ayarlanmamış. .streamlit/secrets.toml içindeki [admin] bölümünü yapılandırın.")
        return
    kalan = int(ss.kilit_bitis - time.time())
    if kalan > 0:
        st.error(f"Çok fazla hatalı deneme. Lütfen {kalan} saniye sonra tekrar deneyin.")
        return
    if db.basarisiz_giris_sayisi(15) >= GLOBAL_HATALI_ESIK:
        st.error("Sistem genelinde çok fazla hatalı giriş denemesi var. Lütfen bir süre sonra tekrar deneyin.")
        return

    with st.form("yonetici_giris_formu"):
        kullanici = st.text_input("Kullanıcı adı")
        parola = st.text_input("Parola", type="password")
        giris = st.form_submit_button("Giriş yap", type="primary")
    if not giris:
        return
    if guvenlik.kimlik_dogrula(kullanici, parola, ADMIN_KULLANICI, ADMIN_PAROLA):
        db.giris_denemesi_kaydet(True)
        ss.update(yonetici_giris=True, yonetici_adi=ADMIN_KULLANICI, son_etkinlik=time.time(), hatali_giris=0)
        db.denetim_ekle(ADMIN_KULLANICI, "Yönetici girişi")
        flash("success", "Yönetici girişi başarılı.")
        st.rerun()
    else:
        db.giris_denemesi_kaydet(False)
        ss.hatali_giris += 1
        db.denetim_ekle("(bilinmiyor)", "Başarısız giriş denemesi")
        if ss.hatali_giris >= OTURUM_HATALI_ESIK:
            ss.kilit_bitis = time.time() + OTURUM_KILIT_SN
            ss.hatali_giris = 0
        st.error("Kullanıcı adı veya parola yanlış.")


# ===================================================== 5. BİLDİRİM İNCELEME
def _kayit_detayi(k: dict) -> None:
    st.markdown("#### Bildirim ayrıntıları")
    a, b = st.columns(2)
    a.markdown(f"**Olay türü**  \n{k['olay_turu']}\n\n**Ortam**  \n{k['ortam']}\n\n**Yaklaşık zaman**  \n{k['zaman_bilgisi']}")
    b.markdown(f"**Tekrarlanma**  \n{k['tekrar_durumu']}\n\n**Oluşturulma**  \n{k['olusturma_zamani']}\n\n"
               f"**Bildirim türü**  \n{'Anonim' if k['anonim'] else 'Kimlik bilgisi belirtilmiş'}")
    if not k["anonim"]:
        with st.expander("Bildirimde belirtilen kişi bilgileri"):
            st.write(f"Ad soyad: {(k['ad'] or 'Belirtilmedi')} {k['soyad'] or ''}")
            st.write(f"Sınıf: {k['sinif'] or 'Belirtilmedi'}")
    st.markdown("**Açıklama**")
    with st.container(border=True):
        st.write(k["aciklama"] or "Açıklama bulunmuyor.")
    ekler = db.ek_bilgileri_getir(k["takip_no"])
    if ekler:
        st.markdown(f"**Anonim gönderenin ek bilgileri ({len(ekler)})**")
        for e in ekler:
            with st.container(border=True):
                st.caption(e["zaman"])
                st.write(e["metin"])
    with st.expander("Otomatik sinyal ve değerlendirme notu", expanded=k["oncelik"] in ("Acil", "Yüksek")):
        st.write(f"**Öncelik sinyali:** {k['oncelik']}")
        st.write(f"**Kategoriler:** {k['kategoriler'] or '—'}")
        st.write(f"**Eşleşen ifadeler:** {k['eslesmeler'] or 'Belirgin eşleşme yok'}")
        for u in filter(None, (k["baglam_uyarilari"] or "").split(" | ")):
            st.warning(u)
        if k["maskelenen"]:
            st.caption(f"Açıklamada otomatik maskelenen bilgi türleri: {k['maskelenen']}")
        aciklama_kontrol = (k.get("aciklama") or "").strip()
        kelimeler = [parca for parca in aciklama_kontrol.split() if any(ch.isalnum() for ch in parca)]
        yalniz_tekrar = len(aciklama_kontrol) >= 4 and len(set(aciklama_kontrol.lower().replace(" ", ""))) <= 2
        if len(aciklama_kontrol) < 8 or len(kelimeler) < 2 or yalniz_tekrar:
            st.warning("**Açıklama kalitesi kontrolü:** Metin çok kısa, yeterince açıklayıcı olmayabilir veya tekrar eden karakterler içeriyor. "
                       "Bu bir spam/asılsız bildirim kanıtı değildir; otomatik silinmez, yetkili kişi bağlamı kontrol etmelidir.")
        else:
            st.info("**Açıklama kalitesi kontrolü:** Metin temel uzunluk kontrolünü geçti. Bu, bildirimin doğru olduğu anlamına gelmez.")
        st.caption(f"Algılayıcı sürümü: {k['analiz_surumu'] or 'v1 (eski kayıt)'}. Bu sürüm gerçek bir yapay zekâ modeli değil, kural tabanlı sinyal ve basit metin kalite kontrolü kullanır.")


def _inceleme_formlari(k: dict) -> None:
    no = k["takip_no"]
    st.markdown("#### İnceleme ve yönlendirme")
    with st.form(f"inceleme_{no}"):
        c1, c2 = st.columns(2)
        durum = c1.selectbox("İnceleme durumu", DURUMLAR, key=f"durum_{no}",
                             index=DURUMLAR.index(k["durum"]) if k["durum"] in DURUMLAR else 0)
        birim = c2.selectbox("Sorumlu birim", BIRIMLER, key=f"birim_{no}",
                             index=BIRIMLER.index(k["birim"]) if k["birim"] in BIRIMLER else 0)
        kaydet = st.form_submit_button("Değişiklikleri kaydet", type="primary")
    if kaydet:
        try:
            degisen = db.durum_ve_birim_guncelle(no, durum, birim, ss.yonetici_adi)
        except (sqlite3.Error, ValueError, LookupError):
            st.error("Değişiklik kaydedilemedi. Lütfen yeniden deneyin.")
        else:
            flash("success" if degisen else "info",
                  "İnceleme bilgileri kaydedildi." if degisen else "Değişiklik yapılmadı; bilgiler zaten güncel.")
            st.rerun()

    notlar_k, gecmis_k = st.columns(2, gap="large")
    with notlar_k:
        st.markdown("#### İnceleme notları")
        with st.form(f"not_{no}"):
            metin = st.text_area("Yeni not", max_chars=db.MAKS_NOT_UZUNLUGU, key=f"not_metni_{no}",
                                 placeholder="İncelemeyle ilgili kısa not ekleyin.")
            gonder = st.form_submit_button("Notu kaydet")
        if gonder:
            try:
                db.not_ekle(no, metin, ss.yonetici_adi)
            except ValueError:
                st.error("Boş not eklenemez.")
            except (sqlite3.Error, LookupError):
                st.error("Not kaydedilemedi.")
            else:
                flash("success", "Not eklendi.")
                st.rerun()
        notlar = db.notlari_getir(no)
        if not notlar:
            st.caption("Henüz not eklenmemiş.")
        for n in notlar:
            with st.container(border=True):
                st.caption(f"{n['zaman']} · {n['yazar'] or '—'}")
                st.write(n["not_metni"])
    with gecmis_k:
        st.markdown("#### İşlem geçmişi")
        gecmis = db.gecmisi_getir(no)
        if not gecmis:
            st.caption("Henüz işlem geçmişi yok.")
        for g in gecmis:
            with st.container(border=True):
                st.markdown(f"**{g['islem']}**")
                st.caption(f"{g['zaman']} · {g['aktor'] or '—'}")
                if g["ayrinti"]:
                    st.write(g["ayrinti"])


def _kayitlar_sekmesi(kayitlar: list[dict]) -> None:
    sayac = {d: sum(k["durum"] == d for k in kayitlar) for d in DURUMLAR}
    acil = sum(k["oncelik"] in ("Acil", "Yüksek") and k["durum"] != "Sonuçlandırıldı" for k in kayitlar)
    gecikmis = sum(k["durum"] == "İnceleme bekliyor" and db.gecen_saat(k["olusturma_zamani"]) > SLA_SAAT for k in kayitlar)
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m6.metric(f"{SLA_SAAT} saati aşan bekleyen", gecikmis)
    m1.metric("Toplam kayıt", len(kayitlar))
    m2.metric("İlk inceleme", sayac["İnceleme bekliyor"])
    m3.metric("Aktif takip", sayac["İnceleniyor"] + sayac["Takip gerekiyor"])
    m4.metric("Sonuçlandırıldı", sayac["Sonuçlandırıldı"])
    m5.metric("Yüksek veya acil sinyal (açık)", acil)
    if not kayitlar:
        st.info("Henüz kayıt yok. Yeni bir bildirim oluşturulduğunda burada görünür.")
        return

    filtre_k, detay_k = st.columns([0.9, 1.7], gap="large")
    with filtre_k:
        st.markdown("#### Kayıt kuyruğu")
        arama = st.text_input("Kayıt ara", placeholder="Takip kodu, olay türü, ortam veya birim", key="inc_arama")
        durum_f = st.selectbox("İnceleme durumu", ["Tüm durumlar"] + DURUMLAR, key="inc_durum")
        oncelik_f = st.selectbox("Öncelik sinyali", ["Tümü"] + list(ONCELIKLER[::-1]), key="inc_oncelik")
        tur_f = st.selectbox("Bildirim türü", ["Tümü", "Anonim", "Kimlik bilgisi belirtilmiş"], key="inc_anonim")
        birim_f = st.selectbox("Sorumlu birim", ["Tüm birimler"] + BIRIMLER, key="inc_birim")
        sirala = st.selectbox("Sıralama", ["Öncelik (yüksekten)", "En yeni önce", "En eski önce"], key="inc_sira")

        gorunen = []
        a = arama.strip().casefold()
        for k in kayitlar:
            if durum_f != "Tüm durumlar" and k["durum"] != durum_f: continue
            if oncelik_f != "Tümü" and k["oncelik"] != oncelik_f: continue
            if tur_f == "Anonim" and not k["anonim"]: continue
            if tur_f == "Kimlik bilgisi belirtilmiş" and k["anonim"]: continue
            if birim_f != "Tüm birimler" and k["birim"] != birim_f: continue
            if a and a not in " ".join(str(k.get(x, "")) for x in ("takip_no", "olay_turu", "ortam", "birim")).casefold():
                continue
            gorunen.append(k)
        gorunen.sort(key=lambda x: x["olusturma_zamani"], reverse=(sirala != "En eski önce"))
        if sirala.startswith("Öncelik"):
            gorunen.sort(key=lambda x: ONCELIK_SIRASI.get(x["oncelik"], 0), reverse=True)

        st.caption(f"{len(gorunen)} kayıt")
        if not gorunen:
            st.info("Bu filtrelerle eşleşen kayıt bulunamadı.")
            return
        secenekler = [k["takip_no"] for k in gorunen]
        ek_say = db.ek_sayilari()
        etiket = {k["takip_no"]: f"{k['takip_no']} · {k['olay_turu']} · {k['oncelik']} · {k['durum']}"
                  + (f" · +{ek_say[k['takip_no']]} ek bilgi" if k["takip_no"] in ek_say else "")
                  + (" · GECİKMİŞ" if k["durum"] == "İnceleme bekliyor" and db.gecen_saat(k["olusturma_zamani"]) > SLA_SAAT else "")
                  for k in gorunen}
        if ss.get("inceleme_secili") not in secenekler:
            ss["inceleme_secili"] = secenekler[0]
        secili_no = st.selectbox("Kayıt seç", secenekler, key="inceleme_secili", format_func=etiket.get)
        k = next(x for x in gorunen if x["takip_no"] == secili_no)
        st.markdown(
            f"<div class='record-banner'><div><div class='record-kicker'>SEÇİLİ KAYIT</div>"
            f"<div class='record-id'>{html.escape(k['takip_no'])}</div></div><div class='pills'>"
            f"{pill(k['durum'], DURUM_SINIF.get(k['durum'], 'status-waiting'))}"
            f"{pill('Sinyal: ' + k['oncelik'], ONCELIK_SINIF.get(k['oncelik'], 'prio-none'))}</div></div>",
            unsafe_allow_html=True)
    with detay_k:
        _kayit_detayi(k)
        _inceleme_formlari(k)


def sayfa_inceleme() -> None:
    st.markdown("<div class='page-heading'><div class='eyebrow-dark'>KAYIT YÖNETİMİ</div><h1>Bildirim inceleme</h1>"
                "<p>Kayıtları tek bir çalışma alanında görüntüleyin, yönlendirin ve işlem geçmişini takip edin.</p></div>",
                unsafe_allow_html=True)
    yonetici_gerekli()
    kayitlar = db.kayitlari_getir()
    sekme1, sekme2, sekme3 = st.tabs(["Kayıtlar", "Denetim kaydı", "Dışa aktarım ve saklama"])
    with sekme1:
        _kayitlar_sekmesi(kayitlar)
    with sekme2:
        st.caption("Girişler, başarısız denemeler, dışa aktarımlar ve silme işlemleri burada izlenir.")
        satirlar = db.denetim_getir()
        if satirlar:
            st.dataframe([{"Zaman": r["zaman"], "Kullanıcı": r["aktor"], "İşlem": r["islem"],
                           "Ayrıntı": r["ayrinti"]} for r in satirlar], hide_index=True)
        else:
            st.info("Henüz denetim kaydı yok.")
    with sekme3:
        st.subheader("Dışa aktarım")
        kimlik = st.checkbox("Kimlik bilgilerini (ad, soyad, sınıf) dahil et", value=False,
                             help="Varsayılan olarak kapalıdır (veri minimizasyonu).")
        st.download_button("CSV indir", data=db.csv_aktar(kayitlar, kimlik),
                           file_name=f"guvenli_okul_{datetime.now(db.TZ):%Y%m%d_%H%M}.csv", mime="text/csv",
                           on_click=db.denetim_ekle,
                           args=(ss.yonetici_adi, "CSV dışa aktarım", "kimlik dahil" if kimlik else "kimlik hariç"))
        st.subheader("Saklama süresi")
        st.caption("Süresi dolan, sonuçlandırılmış kayıtlar notları ve geçmişiyle birlikte kalıcı olarak silinir.")
        gun = st.number_input("Saklama süresi (gün)", min_value=30, value=180, step=30)
        onay = st.checkbox("Silmenin geri alınamayacağını anlıyorum", key="silme_onay")
        if st.button("Süresi dolan kayıtları sil", disabled=not onay):
            n = db.sonuclanmis_kayitlari_sil(int(gun), ss.yonetici_adi)
            flash("success", f"{n} kayıt silindi.")
            st.rerun()
    altbilgi()


# ========================================================== 6. İSTATİSTİKLER (MODÜL D ENTEGRELİ)
def _saat_metni(o: dict) -> str:
    return "Veri yok" if not o["n"] else f"{o['medyan']:.1f} sa (n={o['n']})"


def sayfa_istatistikler() -> None:
    st.title("İstatistikler ve Trend Analitiği")
    if not ISTATISTIKLER_HERKESE_ACIK:
        yonetici_gerekli()
    s = db.istatistik_ozeti()
    dagilim = db.ortam_ve_tur_dagilimi()
    
    st.caption("Grafikler yalnızca bu uygulamada kayıtlı bilgileri özetler; okul geneline ilişkin sonuç göstermez.")
    if not s["toplam"]:
        st.info("Henüz kayıt yok. Yeni kayıtlar eklendikçe istatistikler burada görüntülenir.")
        return
    if s["toplam"] < 10:
        st.warning("Kayıt sayısı çok az; yüzdeler ve karşılaştırmalar yanıltıcı olabilir, yorum yapmayın.")
    
    m = st.columns(4)
    m[0].metric("Toplam bildirim", s["toplam"])
    m[1].metric("Anonim oranı", f"%{100 * s['anonim'] / s['toplam']:.0f}")
    m[2].metric("İlk işlem süresi (medyan)", _saat_metni(s["ilk_inceleme_saat"]))
    m[3].metric("Sonuçlanma süresi (medyan)", _saat_metni(s["sonuclanma_saat"]))
    
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Durum dağılımı")
        st.bar_chart(s["durum"], horizontal=True)
        st.subheader("Ortam bazlı dağılım (Harita / Trend)")
        if dagilim["ortamlar"]:
            st.bar_chart(dagilim["ortamlar"], horizontal=True)
        else:
            st.caption("Ortam verisi yok.")
        st.subheader("Öncelik sinyali dağılımı")
        st.bar_chart(s["oncelik"], horizontal=True)
    with c2:
        st.subheader("Olay türü dağılımı")
        st.bar_chart(s["tur"], horizontal=True)
        st.subheader("Son 30 gün: günlük bildirim")
        if s["gunluk"]:
            st.bar_chart(dict(s["gunluk"]))
        else:
            st.caption("Bu dönemde kayıt yok.")


# ================================================== B. REHBERLİK BİLGİ BANKASI MODÜLÜ
def sayfa_rehberlik_destek() -> None:
    baslik_blogu("ÖNLEYİCİ REHBERLİK", "Rehberlik ve Destek Bilgi Bankası",
                 "Akran zorbalığıyla baş etme yöntemleri, öğrenci rehberliği ve sık sorulan sorular.")
    st.markdown(acil_hat_html(), unsafe_allow_html=True)
    
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.markdown("### Öğrenciler İçin Rehber")
        with st.expander("1. Zorbalığa uğradığında ne yapmalısın?", expanded=True):
            st.write("• Sakin kal ve güvenli bir alana uzaklaş.\n"
                     "• Karşılık vererek fiziksel veya sözel olayı büyütme.\n"
                     "• Olayı kimliğini vermeden bu sistem üzerinden bildir veya okul rehberlik servisine anlat.\n"
                     "• Dijital zorbalıkta ekran görüntüsü al ve mesajları silme.")
        with st.expander("2. Bir arkadaşının zorbalığa uğradığını görürsen"):
            st.write("Sessiz izleyici olma. Onu yalnız bırakma, güvenli bir yere gitmesini sağla ve güvendiğin bir yetişkine ya da öğretmene haber ver.")
    with c2:
        st.markdown("### Veliler ve Okul İdaresi İçin")
        with st.expander("1. Zorbalığın uyarı işaretleri nelerdir?", expanded=True):
            st.write("• Okula gitmek istememe veya okul korkusu\n"
                     "• Ani not düşüşleri ve ders başarısında gerileme\n"
                     "• Açıklanamayan kıyafet/eşya zararları\n"
                     "• İçe kapanma, uyku bozuklukları ve yüksek kaygı")
        with st.expander("2. Okul rehberlik servisinin rolü"):
            st.write("Rehberlik birimi, gelen bildirimleri gizlilik ilkelerine uygun olarak inceler, taraflarla bireysel görüşmeler yapar ve gerekli destek süreçlerini başlatır.")


# ================================================== E. KVKK VE ETİK KURUL MODÜLÜ
def sayfa_kvkk_etik() -> None:
    baslik_blogu("YASAL VE ETİK ÇERÇEVE", "KVKK (6698) ve Etik Uyumluluk Paneli",
                 "Projenin veri gizliliği, veri minimizasyonu ve etik ilkeler doğrultusunda tasarım özeti.")
    
    st.markdown("### 1. Veri Minimizasyonu ve Anonimlik İlkesi")
    st.write("Projede tam anonim bildirim akışında öğrencinin adı, soyadı, sınıfı veya telefon numarası **kesinlikle toplanmaz ve veritabanına yazılmaz**. Sistem, cihaz güvenliğini sağlamak için yalnızca tarayıcıda saklanan rastgele bir jetonun SHA-256 özetini ve tarihi tutar; gün bitiminde bu günlük hak sayaçları temizlenir.")

    st.markdown("### 2. Otomatik Kişisel Veri Maskeleme (PII Scrubbing)")
    st.write("Kullanıcılar serbest metin alanlarına yanlışlıkla T.C. Kimlik Numarası, telefon numarası, e-posta veya IBAN yazsa bile, `guvenlik.py` modülü bu bilgileri veritabanına kaydedilmeden önce otomatik olarak maskeler (`[T.C. KİMLİK NO]`, `[TELEFON]` vb.).")

    st.markdown("### 3. Saklama ve İmha Politikası")
    st.write("Sonuçlandırılmış kayıtlar, belirlenen saklama süresi (varsayılan 180 gün) sonunda notları ve işlem geçmişiyle birlikte veritabanından kalıcı olarak silinir. Bu mekanizma `db.py` içerisindeki `sonuclanmis_kayitlari_sil` fonksiyonu ile yetkili yönetici denetiminde gerçekleştirilir.")

    st.markdown("### 4. Bilimsel ve Etik Sorumluluk Reddi")
    st.write("Bu yazılım otomatik bir karar mekanizması veya adli kanıt sistemi **değildir**. Kural tabanlı sinyaller yalnızca insan (rehberlik ve idare) incelemesi için öncelik sıralaması oluşturur.")


# ================================================== 7. ARAŞTIRMA VE TEST
KONTROL_LISTESI = (
    ("T01", "Anonim bildirim", "Formda ad/soyad/sınıf istenmemeli ve kaydedilmemeli."),
    ("T02", "Sınıf seçenekleri", "9/A … 12/B seçenekleri görünmeli."),
    ("T03", "Takip kodu", "Her kayıt benzersiz ve tahmin edilmesi zor bir kod üretmeli."),
    ("T04", "Yetkisiz erişim", "Girişsiz, inceleme/istatistik/denetim kayıtları görünmemeli."),
    ("T05", "Durum güncelleme", "Değişiklik kaydedilmeli, geçmişe işlemi yapanla birlikte eklenmeli."),
    ("T06", "Not geçmişi", "Not kaydedilmeli; yazar bilgisiyle ilgili kayıtta görünmeli."),
    ("T07", "Boş form", "Açıklama veya onay yoksa kayıt oluşturulmamalı; yazılan metin silinmemeli."),
    ("T08", "Temiz başlangıç", "Yeni kurulumda örnek kayıt olmamalı; yeniden başlatmada kayıt silinmemeli."),
    ("T09", "İstatistikler", "Grafik toplamları kayıt sayılarıyla uyumlu olmalı."),
    ("T10", "Güvenli metin", "Arayüz gerçek kişisel veri girilmemesi gerektiğini açıkça belirtmeli."),
    ("T11", "Hatalı giriş sınırı", "5 hatalı denemeden sonra giriş geçici olarak kilitlenmeli."),
    ("T12", "Oturum zaman aşımı", "30 dakika hareketsizlikte yönetici oturumu kapanmalı."),
    ("T13", "Kişisel veri maskeleme", "Açıklamaya yazılan telefon/T.C. no/e-posta/IBAN kayıtta maskeli olmalı."),
    ("T14", "Takip kodu sorgusu", "Sorgu yalnızca durum ve tarih göstermeli; açıklama/not göstermemeli."),
    ("T15", "CSV dışa aktarım", "Kimlik alanları varsayılan olarak dışarıda olmalı; işlem denetim kaydına yazılmalı."),
    ("T16", "Günlük kota", "Anonim bildirimden sonra aynı cihazdan ikinci anonim bildirim engellenmeli."),
    ("T17", "Ek bilgi", "Takip koduyla aynı gün ek bilgi eklenebilmeli; 60 sn bekleme ve günde 3 sınırı uygulanmalı."),
)


def _degerlendirme_sekmesi() -> None:
    st.write("Algılayıcının sentetik örnek kümesindeki performansını ölçer.")
    st.caption(f"Algılayıcı sürümü: {ht.SURUM} · Sözlük: {sum(ht.sozluk_ozeti().values())} ifade")
    if st.button("Değerlendirmeyi çalıştır", type="primary"):
        ss["degerlendirme_sonucu"] = degerlendirme.degerlendir()
    r = ss.get("degerlendirme_sonucu")
    if not r:
        return
    m = st.columns(5)
    m[0].metric("Örnek sayısı", r["toplam"])
    m[1].metric("Kesinlik (precision)", f"{r['precision']:.2f}")
    m[2].metric("Duyarlılık (recall)", f"{r['recall']:.2f}")
    m[3].metric("F1", f"{r['f1']:.2f}")
    m[4].metric("Doğruluk", f"{r['accuracy']:.2f}")
    st.markdown("**Karışıklık matrisi**")
    st.table({"": ["Gerçekte sinyal var", "Gerçekte sinyal yok"],
              "Sinyal verildi": [r["tp"], r["fp"]], "Sinyal verilmedi": [r["fn"], r["tn"]]})
    st.markdown("**Örnek türüne göre doğruluk**")
    st.dataframe([{"Tür": t, "Örnek": o["n"], "Doğru": o["dogru"], "Oran": round(o["dogru"] / o["n"], 2)}
                  for t, o in r["tur_ozeti"].items()], hide_index=True)
    st.markdown("**Kategoriye göre yakalama (duyarlılık)**")
    st.dataframe([{"Kategori": t, "Örnek": o["n"], "Yakalanan": o["bulunan"], "Oran": round(o["bulunan"] / o["n"], 2)}
                  for t, o in r["kategori_ozeti"].items()], hide_index=True)


def sayfa_arastirma() -> None:
    st.title("Araştırma ve kalite kontrol")
    s1, s2, s3 = st.tabs(["Otomatik değerlendirme", "Elle kontrol listesi", "Yöntem ve sınırlılıklar"])
    with s1:
        _degerlendirme_sekmesi()
    with s2:
        for kod, baslik, aciklama in KONTROL_LISTESI:
            with st.expander(f"{kod} · {baslik}"):
                st.write(aciklama)
                st.checkbox("Bu senaryoyu test ettim", key=f"test_{kod}")
        bitti = sum(bool(ss.get(f"test_{kod}")) for kod, _, _ in KONTROL_LISTESI)
        st.progress(bitti / len(KONTROL_LISTESI), text=f"İşaretlenen test: {bitti}/{len(KONTROL_LISTESI)}")
    with s3:
        st.markdown("""
1. **Amaç:** Bildirim sürecinin anlaşılırlığını ve kural tabanlı sinyalin ölçülebilir başarımını raporlamak.
2. **Algılayıcı:** Türkçe'ye duyarlı normalleştirme, kelime başı sınırı, harf tekrarı çözümü ve öncelik kuralı kullanır.
3. **Ölçüt:** Kesinlik, duyarlılık, F1 ve kategori bazlı yakalama.
4. **Sınırlılıklar:** Bağlam, ironi ve imâ yakalanmaz; deyimsel abartılar yanlış alarm üretebilir.
5. **Yol haritası:** Etik onaylı veri kümesi, Cohen kappa uyumu, eğitim/test ayrımı ve kullanılabilirlik testi (SUS).""")


# ======================================================= 8. GÜVENLİ KULLANIM
def sayfa_guvenli_kullanim() -> None:
    st.title("Güvenli kullanım")
    st.markdown(acil_hat_html(), unsafe_allow_html=True)
    st.subheader("Platformun işlevleri")
    st.markdown("""
- Tam anonim bildirim alır; günde bir bildirim hakkı vardır.
- Açıklamadaki telefon, T.C. kimlik numarası, e-posta ve IBAN'ı otomatik maskeler.
- Takip koduyla durum sorgulamaya ve ek bilgi eklemeye izin verir.
- Yönetici girişinden sonra kayıtları listeler; durum/birim günceller; not ve işlem geçmişi tutar.""")
    st.subheader("Sınırlar")
    st.markdown("""
- Gerçek bir okul veya rehberlik servisine bağlı değildir; acil yardım ya da resmi bildirim hizmeti değildir.
- Otomatik kesin karar vermez; sinyaller öncelik sıralaması içindir.""")
    altbilgi()


_CIHAZ_HTML = r"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"></head><body>
<script>
(function () {
  var HEX = /^[a-f0-9]{48}$/;
  function uret() {
    var a = new Uint8Array(24); crypto.getRandomValues(a);
    return Array.from(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  function cerezOku() {
    var m = document.cookie.match(/(?:^|; )gb_cihaz=([a-f0-9]{48})/); return m ? m[1] : null;
  }
  var t = null;
  try { t = localStorage.getItem("gb_cihaz"); } catch (e) {}
  if (!t || !HEX.test(t)) t = cerezOku();
  if (!t || !HEX.test(t)) t = uret();
  try { localStorage.setItem("gb_cihaz", t); } catch (e) {}
  try { document.cookie = "gb_cihaz=" + t + ";max-age=31536000;path=/;SameSite=Lax"; } catch (e) {}

  function gonder(tip, ek) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: tip }, ek), "*");
  }
  var gonderildi = false;
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "streamlit:render" && !gonderildi) {
      gonderildi = true;
      gonder("streamlit:setComponentValue", { value: t, dataType: "json" });
    }
  });
  gonder("streamlit:componentReady", { apiVersion: 1 });
  gonder("streamlit:setFrameHeight", { height: 0 });
})();
</script></body></html>
"""


def _cihaz_bileseni_hazirla():
    try:
        klasor = Path(tempfile.gettempdir()) / "guvenli_okul_cihaz_bileseni"
        klasor.mkdir(parents=True, exist_ok=True)
        dosya = klasor / "index.html"
        if not dosya.exists() or dosya.read_text(encoding="utf-8") != _CIHAZ_HTML:
            dosya.write_text(_CIHAZ_HTML, encoding="utf-8")
        return components.declare_component("cihaz_jetonu", path=str(klasor))
    except Exception:
        return None


_CIHAZ_BILESENI = _cihaz_bileseni_hazirla()


def _cihaz_jetonu() -> str | None:
    if _CIHAZ_BILESENI is None:
        ss.setdefault("cihaz_jetonu_deger", secrets.token_hex(24))
        return ss["cihaz_jetonu_deger"]
    deger = _CIHAZ_BILESENI(key="cihaz_jetonu", default=None)
    if isinstance(deger, str) and re.fullmatch(r"[a-f0-9]{48}", deger):
        ss["cihaz_jetonu_deger"] = deger
    return ss.get("cihaz_jetonu_deger")


# ============================================================== YÖNLENDİRME
SAYFALAR = {
    "Genel bakış": sayfa_genel_bakis,
    "Yeni bildirim": sayfa_yeni_bildirim,
    "Bildirim sorgula": sayfa_sorgula,
    "Rehberlik bilgi bankası": sayfa_rehberlik_destek,
    "Yönetici girişi": sayfa_yonetici_girisi,
    "Bildirim inceleme": sayfa_inceleme,
    "İstatistikler": sayfa_istatistikler,
    "KVKK ve Etik İlkeler": sayfa_kvkk_etik,
    "Araştırma ve test": sayfa_arastirma,
    "Güvenli kullanım": sayfa_guvenli_kullanim,
}

oturum_denetle()

NAV = (
    ("KULLANICI", (("Genel bakış", "dashboard"), ("Yeni bildirim", "edit_note"), ("Bildirim sorgula", "search"), ("Rehberlik bilgi bankası", "support"))),
    ("YÖNETİM", (("Yönetici girişi", "login"), ("Bildirim inceleme", "inbox"), ("İstatistikler", "bar_chart"))),
    ("KURUMSAL", (("KVKK ve Etik İlkeler", "gavel"), ("Araştırma ve test", "science"), ("Güvenli kullanım", "verified_user"))),
)
KORUMALI = {"Bildirim inceleme", "İstatistikler"}

if ss.get("ana_menu") not in SAYFALAR:
    ss["ana_menu"] = "Genel bakış"
secili_sayfa = ss["ana_menu"]

with st.sidebar:
    logo_path = Path(__file__).with_name("bozuyuk_fen_lisesi_logo.png")
    if logo_path.exists():
        logo_sol, logo_orta, logo_sag = st.columns([1, 2, 1])
        with logo_orta:
            st.image(str(logo_path), width=100)
    st.markdown("<div class='top-brand'><div><div class='top-brand-name'>Bozüyük Fen Lisesi"
                "</div><div class='top-brand-sub'>Güvenli Okul · Bildirim ve takip</div></div></div>", unsafe_allow_html=True)
    for baslik, ogeler in NAV:
        st.markdown(f"<div class='nav-title'>{baslik}</div>", unsafe_allow_html=True)
        for ad, ikon in ogeler:
            kilit = ad in KORUMALI and not ss.yonetici_giris
            st.button(ad, key=f"nav_{ad}", icon=":material/lock:" if kilit else f":material/{ikon}:",
                      type="primary" if ad == secili_sayfa else "secondary", on_click=sayfaya_git, args=(ad,))
    st.markdown("<div class='nav-title'>OTURUM</div>", unsafe_allow_html=True)
    if ss.yonetici_giris:
        st.caption(f"Yönetici: {ss.yonetici_adi}")
        st.button("Oturumu kapat", on_click=oturumu_kapat, key="oturum_kapat", icon=":material/logout:")
    else:
        st.caption("Ziyaretçi (giriş yapılmadı)")
    st.caption("Acil: 112 · 155 · 183")
    st.caption(f"Sürüm 2.0 · Algılayıcı {ht.SURUM}")

st.markdown(
    f"<div class='topbar'><div class='crumb'>Güvenli Okul <span>/</span> <b>{html.escape(secili_sayfa)}</b></div>"
    f"<div class='pills'>{pill('DEMO' if DEMO_MODU else 'PİLOT', 'prio-mid')}"
    f"{pill('Yönetici' if ss.yonetici_giris else 'Ziyaretçi', 'status-active' if ss.yonetici_giris else 'prio-none')}</div></div>",
    unsafe_allow_html=True)
if DEMO_MODU:
    st.markdown("<div class='notice-bar'><span class='notice-dot'></span><span>Bağımsız çalışma alanı · Gerçek okul "
                "sistemine bağlı değildir. Gerçek kişisel verileri girmeyin.</span></div>", unsafe_allow_html=True)
flash_goster()

SAYFALAR[secili_sayfa]()
if secili_sayfa in ("Genel bakış", "Yeni bildirim", "Bildirim sorgula", "Rehberlik bilgi bankası",
                    "Yönetici girişi", "İstatistikler", "KVKK ve Etik İlkeler", "Araştırma ve test"):
    altbilgi()
