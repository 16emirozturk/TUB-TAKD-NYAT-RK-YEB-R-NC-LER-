"""Uygulama genelinde kullanılan sabit listeler (tek kaynak)."""

DURUMLAR = ["İnceleme bekliyor", "İnceleniyor", "Takip gerekiyor", "Sonuçlandırıldı"]

BIRIMLER = ["Henüz atanmadı", "Rehberlik birimi", "Okul yönetimi", "Öğrenci destek ekibi"]

OLAY_TURLERI = [
    "Hakaret veya alay", "Tehdit", "Dışlama", "Dijital zorbalık",
    "Fiziksel zorbalık şüphesi", "Diğer",
]

ORTAMLAR = ["Sınıfta", "Okul bahçesinde", "Koridorda", "Sosyal medyada", "Mesajlaşmada", "Diğer"]
ZAMANLAR = ["Bugün", "Dün", "Bu hafta", "Daha önce", "Emin değilim"]
TEKRARLAR = ["Bir kez oldu", "Birkaç kez oldu", "Sık sık / sürekli oluyor", "Emin değilim"]
SINIFLAR = ["9/A", "9/B", "10/A", "10/B", "11/A", "11/B", "12/A", "12/B"]

ONCELIKLER = ("Sinyal yok", "Düşük", "Orta", "Yüksek", "Acil")
ONCELIK_SIRASI = {ad: i for i, ad in enumerate(ONCELIKLER)}

ACIL_HATLAR = (
    ("112", "Acil çağrı merkezi"),
    ("155", "Polis imdat"),
    ("183", "ALO Sosyal Destek Hattı (7/24)"),
)
