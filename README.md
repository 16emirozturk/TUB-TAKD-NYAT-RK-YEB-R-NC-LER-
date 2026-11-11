# Güvenli Okul — Bildirim ve Takip Platformu (v2.2)

Okullarda akran zorbalığı bildirimlerini toplamak, **insan incelemesi** için önceliklendirmek ve süreci
izlenebilir kılmak için hazırlanmış bir Streamlit prototipidir. Karar veren bir sistem **değildir**.

## Yeni Eklenen Modüller (v2.2)
- **Rehberlik & Destek Bilgi Bankası:** Öğrenciler, veliler ve okul idaresi için akran zorbalığıyla baş etme kılavuzu.
- **İstatistiksel Trend ve Ortam Analitiği:** Olayların nerede ve hangi türlerde yoğunlaştığını gösteren analitik araçlar.
- **KVKK (6698) ve Etik Uyumluluk Paneli:** Projenin kişisel verileri koruma ilkeleri, maskeleme ve imha süreçlerini açıklayan detaylı panel.

## Çalıştırma
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
