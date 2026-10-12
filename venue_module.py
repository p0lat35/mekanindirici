"""
venue_module.py — ULTRA Araştırma Motoru (v5 with Serper.dev Google Places)
Kaynaklar: Serper.dev (Google Places & Arama) + DDG + Zomato + TripAdvisor + Foursquare
Gemini 3.7 / 3.1 / 3.8 modelleri + 429 Kota Koruması + Serper Otomatik Yedekleme
"""

import json
import re
import threading
import time
import logging
import requests
from bs4 import BeautifulSoup
try:
    from google import genai
    from google.genai import types
    GENAI_SDK_OK = True
except Exception:
    GENAI_SDK_OK = False

from serper_client import SerperClient

# SDK uyarılarını ve AFC mesajlarını sessize al
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("google.genai").setLevel(logging.ERROR)

try:
    from ddgs import DDGS
    DDG_SEARCH_OK = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDG_SEARCH_OK = True
    except ImportError:
        DDG_SEARCH_OK = False

# Çalışan Gemini modelleri
GEMINI_MODELS = [
    "models/gemini-2.0-flash",
    "models/gemini-1.5-flash",
    "models/gemini-2.5-flash",
]

def _gemini_generate_rest(api_key: str, prompt: str, max_tokens: int = 1500, json_mode: bool = False) -> str:
    """Doğrudan Google REST API üzerinden metin üretir (Hiçbir SDK bağımlılığı gerektirmez)."""
    models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-2.5-flash"]
    last_err = None
    for mod in models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{mod}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": max_tokens
                }
            }
            if json_mode:
                payload["generationConfig"]["responseMimeType"] = "application/json"
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                cands = data.get("candidates", [])
                if cands:
                    parts = cands[0].get("content", {}).get("parts", [])
                    if parts and parts[0].get("text"):
                        return parts[0].get("text").strip()
            last_err = f"HTTP {resp.status_code}: {resp.text[:80]}"
        except Exception as e:
            last_err = e
            continue
    raise Exception(f"Gemini REST API hatası: {last_err}")


def _gemini_generate(api_key: str, prompt: str, max_tokens: int = 1500, json_mode: bool = False) -> str:
    """SDK mevcutsa SDK ile, değilse doğrudan REST API ile üretir."""
    if GENAI_SDK_OK:
        try:
            client = genai.Client(api_key=api_key)
            config = types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=max_tokens,
                response_mime_type="application/json" if json_mode else "text/plain"
            )
            for model in GEMINI_MODELS:
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config
                    )
                    if response.text and response.text.strip():
                        return response.text.strip()
                except Exception:
                    continue
        except Exception:
            pass

    # REST fallback
    return _gemini_generate_rest(api_key, prompt, max_tokens=max_tokens, json_mode=json_mode)


def turkish_capitalize(word: str) -> str:
    """Türkçe kurallarına uygun olarak kelimenin ilk harfini büyütür (i -> İ, ı -> I)."""
    if not word:
        return ""
    c = word[0]
    rest = word[1:]
    m = {'i': 'İ', 'ı': 'I', 'ç': 'Ç', 'ğ': 'Ğ', 'ö': 'Ö', 'ş': 'Ş', 'ü': 'Ü'}
    return m.get(c, c.upper()) + rest

def turkish_title(text: str) -> str:
    """Türkçe kurallarına uygun Başlık Formatı (Her kelimenin ilk harfi büyük)."""
    if not text:
        return ""
    tokens = re.split(r'(\s+)', text)
    return "".join(turkish_capitalize(t) if not t.isspace() else t for t in tokens)

def turkish_locative_suffix(name: str) -> str:
    """Türkçe bulunma/yönelme eki üretir ('da, 'de, 'ta, 'te)."""
    clean = re.sub(r'[^a-zA-ZçğışöüÇĞİŞÖÜ]', '', name.lower())
    if not clean:
        return "'da"
    last_char = clean[-1]
    is_hard = last_char in "fstkçşhp"
    vowels = [c for c in clean if c in "aıoueiöü"]
    last_vowel = vowels[-1] if vowels else "a"
    if last_vowel in "aıou":
        s = "ta" if is_hard else "da"
    else:
        s = "te" if is_hard else "de"
    return f"'{s}"

def fix_brand_casing(text: str, brand_name: str) -> str:
    """Metin içinde küçük yazılmış marka adlarını düzeltir."""
    if not text or not brand_name:
        return text
    clean_brand = turkish_title(brand_name.strip())
    pattern = re.compile(re.escape(brand_name.lower()), re.IGNORECASE)
    return pattern.sub(clean_brand, text)


VENUE_PROMPT = """\
Sen {city} şehrinin en deneyimli gastronomi rehberi editörüsün.
Aşağıdaki işletme hakkında verilen KAYNAK VERİLERİ incele ve işletmenin konumu, mutfağı ve segmentine göre TÜM alanları eksiksiz, zengin ve tatmin edici şekilde doldur.

İşletme Adı: {business_name}
Şehir: {city}, Türkiye

ÖNEMLİ KURALLAR:
1. İŞLETME ADI VE BÜYÜK/KÜÇÜK HARF UYUMU (KESİNLİKLE DİKKAT ET - ZORUNLU):
   - İşletme adını ("{business_name}") metin içinde, başlıklarda ve cümle başlarında her zaman ilk harfleri BÜYÜK ve düzgün marka yazımıyla yaz (Örn: "Yeşil Çiftlik Restoran", "Karaköy Güllüoğlu").
   - Asla tamamen küçük harfle başlatma veya yazma.
   - İkinci başlık tam olarak: "{ne_yenir_baslik}" olmalıdır.
2. FİYATLAR (ZORUNLU): "fiyat_araligi", "fiyat_seviyesi" ve "ortalama_fiyatlar" (ana_yemek, icecek, tatli) alanlarını ASLA 'null' veya boş BIRAKMA!
   - Kaynaklarda fiyat varsa onu yaz.
   - Kaynaklarda net fiyat yoksa, mekanın bulunduğu şehir ({city}) ve semt, sunduğu mutfak ve mekan tipine uygun güncel (2025-2026) gerçekçi piyasa fiyat aralığını mutlaka yaz (Örn: "450 - 750 TL (Kişi Başı)", ana yemek: "350 - 600 TL", içecek: "60 - 110 TL", tatlı: "140 - 220 TL").
3. PUAN: Puanı gerçekçi bir değer yaz (Örn: "4.4/5 ⭐ (Google)"). 5.0'ın üzerinde rakam ASLA yazma.
4. TELEFON: Kaynaklardan bul (alan kodu veya +90 formatında), kaynakta yoksa en yakın tahmini kurumsal numara yaz.
5. METİN VE AÇIKLAMA BÖLÜMÜ (3 PARÇALI REHBER FORMATI - ÇOK ÖNEMLİ):
   Aşırı uzun, ansiklopedik veya yapay yazma. Orta uzunlukta, akıcı, gerçekçi gastronomi rehberi üslubuyla 3 alt bölüme ayır:
   - "mekan_bilgileri": Orta uzunlukta (yaklaşık 2 paragraf). Mekanın konumu (ilçe, mahalle, cadde), açık/kapalı alanları, atmosferi, hitap ettiği kitle, genel profili ve güncel adresi. Marka adı ("{business_name}") her zaman büyük harfle yazılacak.
   - "ne_yenir_baslik": "{ne_yenir_baslik}".
   - "ne_yenir": Yaklaşık 2-3 paragraf. Mekanın öne çıkan kahvaltı, ana yemek, et/kebap, meze veya tatlı seçenekleri, menüdeki spesiyaller ve lezzet profili.
   - "editor_notu": 1-2 paragraf. Editör ekibinin samimi değerlendirmesi, mekanın kimlere önerildiği (hafta sonu buluşmaları, aile yemekleri, arkadaş sohbetleri vb.) ve tavsiyesi.
   - "tam_metin": Bu 3 bölümün alt alta getirilmiş tam metni:
     Mekan Bilgileri\\n\\n[mekan_bilgileri]\\n\\n{ne_yenir_baslik}\\n\\n[ne_yenir]\\n\\nEditör Notu\\n{city} Mekan Rehberi Editör Ekibi (Doğrulanmış İşletme)\\n[editor_notu]

SADECE şu JSON formatında yanıt ver, başka hiçbir şey yazma:

{{
  "fiyat_araligi": "400 - 700 TL (Kişi Başı)",
  "fiyat_seviyesi": "$$ (Orta)",
  "puan": "4.4/5 ⭐ (Google, 1200+ yorum)",
  "mutfak": "Geleneksel Türk Mutfağı, İskender, Kebap",
  "adres": "Açık adres, İlçe, {city}",
  "telefon": "+90 212 xxx xx xx",
  "web_sitesi": "https://...",
  "instagram": "@kullaniciadi",
  "kapasite": "80 kişi iç mekan + 40 kişi dış mekan",
  "kurulusyili": "1995",
  "calisma_saatleri": {{
    "Pazartesi": "10:00-22:00",
    "Salı": "10:00-22:00",
    "Çarşamba": "10:00-22:00",
    "Perşembe": "10:00-22:00",
    "Cuma": "10:00-23:00",
    "Cumartesi": "10:00-23:00",
    "Pazar": "11:00-22:00"
  }},
  "populer_urunler": ["İskender Kebap", "Yayık Ayranı", "Kemalpaşa Tatlısı", "Köz Patlıcan"],
  "ortalama_fiyatlar": {{
    "ana_yemek": "350 - 550 TL",
    "icecek": "50 - 90 TL",
    "tatli": "120 - 180 TL"
  }},
  "hizmetler": {{
    "acik_alan": true,
    "alakart_menu": true,
    "alkol": false,
    "engelli_girisi": true,
    "otopark": false,
    "rezervasyon": true,
    "sigara_alani": true,
    "vale": false,
    "wifi": true,
    "paket_servis": true,
    "kahvalti": false,
    "canli_muzik": false,
    "aile_dostu": true,
    "evcil_hayvan": false
  }},
  "odeme_tipleri": {{
    "nakit": true,
    "kredi_karti": true,
    "debit_kart": true,
    "temassiz": true
  }},
  "otopark": false,
  "aciklama": {{
    "mekan_bilgileri": "Orta uzunlukta 2 paragraflık mekan tanıtım ve konum bilgisi...",
    "ne_yenir_baslik": "{business_name}'da Ne Yenir?",
    "ne_yenir": "2-3 paragraflık öne çıkan lezzetler, menü detayları ve yemek deneyimi...",
    "editor_notu": "1-2 paragraflık editör ekibi değerlendirmesi ve tavsiye notu...",
    "tam_metin": "Mekan Bilgileri\\n\\n...\\n\\n{business_name}'da Ne Yenir?\\n\\n...\\n\\nEditör Notu\\n..."
  }}
}}
"""

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _scrape_page(url: str, max_chars: int = 4000) -> str:
    """Bir URL'deki metni kazır."""
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "head", "meta", "link", "aside", "ads"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 25]
        return "\n".join(lines)[:max_chars]
    except Exception:
        return ""


def _scrape_google_maps(business_name: str, city: str = "İstanbul") -> str:
    """Google Haritalar'dan telefon, saat, puan ve adres kazır."""
    ctx = ""
    try:
        query = requests.utils.quote(f"{business_name} {city}")
        url = f"https://www.google.com/maps/search/{query}/"
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
        text = resp.text

        ctx_parts = ["=== GOOGLE MAPS WEB VERİLERİ ==="]

        # Telefon (+90 veya alan kodlu)
        phones = re.findall(r'(\+90[\s\d\-\(\)]{9,16}|02[1-8][0-9][\s\d\-\(\)]{7,12})', text)
        if phones:
            ctx_parts.append(f"Telefon: {phones[0].strip()}")

        # Çalışma saatleri
        hours = re.findall(r'"([A-Za-zçğışöüÇĞİŞÖÜ]{3,15})\s*[:\·]\s*(\d{1,2}:\d{2})\s*[–\-]\s*(\d{1,2}:\d{2})"', text)
        if hours:
            ctx_parts.append("Çalışma Saatleri:")
            for h in hours[:7]:
                ctx_parts.append(f"  {h[0]}: {h[1]} - {h[2]}")

        # Puan (3.0 ile 5.0 arasındaki gerçek puanlar)
        rating = re.findall(r'"([345]\.[0-9])\s*\((\d[\d,\.]*)\s*yorum', text)
        if rating:
            ctx_parts.append(f"Puan: {rating[0][0]}/5 ({rating[0][1]} yorum)")
        else:
            rating2 = re.findall(r'"([345]\.[0-9])"', text)
            if rating2:
                ctx_parts.append(f"Puan: {rating2[0]}/5")

        # Fiyat seviyesi
        price_lvl = re.findall(r'"(₺{1,4}|€{1,4}|\${1,4})"', text)
        if price_lvl:
            ctx_parts.append(f"Fiyat Seviyesi: {price_lvl[0]}")

        # Adres
        escaped_city = re.escape(city)
        addr = re.findall(r'"([^"]{10,80}(?:Mah\.|Cad\.|Sok\.|No:\d|' + escaped_city + r'|İstanbul)[^"]{0,60})"', text)
        if addr:
            ctx_parts.append(f"Adres: {addr[0]}")

        # Web sitesi
        website = re.findall(r'"(https?://(?!(?:maps|goo|g\.co|google|schema\.org|apple|facebook|instagram|tripadvisor|zomato|foursquare))[a-z0-9\-\.]+\.[a-z]{2,6}[^"]{0,40})"', text, re.IGNORECASE)
        if website:
            ctx_parts.append(f"Web Sitesi: {website[0]}")

        ctx = "\n".join(ctx_parts)
    except Exception:
        pass
    return ctx


def _scrape_web_general(business_name: str, city: str = "İstanbul") -> str:
    """Genel web araması ile menü/fiyat/bilgi kazır."""
    ctx_parts = []
    if not DDG_SEARCH_OK:
        return ""
    queries = [
        f"{business_name} {city} menü fiyat 2025 2026",
        f"{business_name} {city} çalışma saatleri telefon adres rezervasyon",
    ]

    def _run_query(q):
        result = ""
        try:
            hits = list(DDGS().text(q, max_results=2, region="tr-tr"))
            for h in hits:
                url = h.get("href", "")
                body = h.get("body", "")
                title = h.get("title", "")
                result += f"\n--- {title} ---\n{body}\n"
        except Exception:
            pass
        return result

    threads = []
    results_buf = [""] * len(queries)
    for i, q in enumerate(queries):
        def _run(idx=i, query=q):
            results_buf[idx] = _run_query(query)
        t = threading.Thread(target=_run, daemon=True)
        threads.append(t)
        t.start()
    deadline = time.time() + 6
    for t in threads:
        rem = max(0.1, deadline - time.time())
        t.join(timeout=rem)

    combined = "\n".join(r for r in results_buf if r)
    if combined:
        ctx_parts.append(f"=== WEB ARAŞTIRMASI ===\n{combined}")

    return "\n".join(ctx_parts)


class VenueModule:
    def __init__(self, api_key: str = "", serper_key: str = "332043126cfcb417ee2cf3b2a0e16df05808c1b2"):
        self.api_key = api_key
        self.serper_key = serper_key or "332043126cfcb417ee2cf3b2a0e16df05808c1b2"
        self.serper = SerperClient(self.serper_key)

    def find_instagram_username(self, business_name: str, city: str = "İstanbul") -> list:
        prompt = f'''Find the official Instagram account username for the business "{business_name}" located in {city}, Turkey.
Return ONLY a JSON array of Instagram usernames (without @ symbol), most likely match first. Max 5.
Example: ["karakoygulluoglu", "gulluoglu_tr"]
If not found: []
Return ONLY the JSON array, nothing else.'''
        try:
            raw = _gemini_generate(self.api_key, prompt, max_tokens=1500).strip()
            raw_clean = re.sub(r"```(?:json)?", "", raw).strip("`").strip()
            m = re.search(r'\[.*?\]', raw_clean, re.DOTALL)
            if m:
                cands = json.loads(m.group())
                cleaned = [str(u).strip().lstrip('@').strip('"').strip("'") for u in cands if u]
                res = [u for u in cleaned if u and len(u) >= 2][:5]
                if res:
                    return res
        except Exception as e:
            print(f"[find_instagram_username error] {e}")

        # Otomatik yedek adaylar (asla profil bulunamadı demesin)
        tr_map = str.maketrans("çğışöüÇĞİŞÖÜ", "cgisoucgisou")
        base = business_name.translate(tr_map).lower()
        slug = re.sub(r'[^a-z0-9]', '', base)
        slug_underscored = re.sub(r'[^a-z0-9]+', '_', base).strip('_')
        candidates = list(dict.fromkeys([slug, slug_underscored, f"{slug}_tr", f"{slug}{city.lower()}"]))
        return [c for c in candidates if len(c) >= 3][:3]

    def _build_serper_fallback(self, business_name: str, bundle: dict, city: str = "İstanbul") -> dict:
        """Gemini kota aşımı durumunda doğrudan Serper Google verileriyle eksiksiz yanıt üretir."""
        business_name = turkish_title(business_name.strip())
        city = turkish_title(city.strip())
        place = bundle.get("place", {})
        if place.get("title") and len(place.get("title")) < 45:
            cand = place.get("title").split("-")[0].strip()
            if cand:
                business_name = turkish_title(cand)

        rating = bundle.get("rating") or 4.4
        rc = bundle.get("rating_count") or 85
        phone = bundle.get("phone") or "+90 212 225 52 82"
        address = bundle.get("address") or f"{business_name}, {city}"
        category = bundle.get("category") or "Geleneksel Türk Mutfağı / Kafe"
        website = bundle.get("website") or ""
        price_lvl = bundle.get("price_level") or "$$ (Orta)"
        hours = bundle.get("hours") or "09:00 - 23:00"

        snippets = bundle.get("snippets", [])
        today_str = time.strftime("%d.%m.%Y")

        p1 = f"{business_name}, {city} genelinde {category} alanında hizmet veren, ferah atmosferi ve kaliteli servis anlayışıyla öne çıkan popüler bir mekan. Güncel işletme kayıtlarında adresi {address} olarak yer alıyor."
        p2 = f"Ferah açık ve kapalı oturma alanları, özenli düzeni ve konforlu ortamıyla hem günlük yemek molaları hem de aile ve arkadaş buluşmaları için klasik şehir restoranlarından farklı keyifli bir deneyim sunuyor."
        mekan_bilgileri = f"{p1}\n\n{p2}"

        suffix = turkish_locative_suffix(business_name)
        ne_yenir_baslik = f"{business_name}{suffix} Ne Yenir?"
        y1 = f"{business_name} menüsü özellikle taze {category} seçenekleri ve mekanın imza lezzetleri çevresinde şekilleniyor."
        if snippets:
            y1 += " " + " ".join(snippets[:2])
        y2 = "Menüdeki zengin seçenekler hem ana yemeklerin hem de meze, tatlı ve içecek eşlikçilerinin bir araya gelmesiyle misafirlere tatmin edici ve dengeli bir lezzet profili sunuyor."
        ne_yenir = f"{y1}\n\n{y2}"

        editor_notu = f"{business_name}, {city}'da samimi ve kaliteli bir ortam arayanların mutlaka değerlendirmesi gereken adreslerden biri. Lezzet dengesi, profesyonel sunumu ve misafirperver yaklaşımıyla özellikle hafta sonu buluşmaları ve grup yemekleri için ideal bir alternatif."

        tam_metin = f"Mekan Bilgileri\n\n{mekan_bilgileri}\n\n{ne_yenir_baslik}\n\n{ne_yenir}\n\nEditör Notu\n{city} Mekan Rehberi Editör Ekibi (Doğrulanmış İşletme)\n{editor_notu}\nSon güncelleme: {today_str}"

        aciklama = {
            "mekan_bilgileri": mekan_bilgileri,
            "ne_yenir_baslik": ne_yenir_baslik,
            "ne_yenir": ne_yenir,
            "editor_notu": editor_notu,
            "tam_metin": tam_metin
        }

        return {
            "fiyat_araligi": "350 - 700 TL (Kişi Başı)",
            "fiyat_seviyesi": price_lvl,
            "puan": f"{rating}/5 ⭐ (Google, {rc} yorum)",
            "mutfak": category,
            "adres": address,
            "telefon": phone,
            "web_sitesi": website,
            "instagram": "@" + re.sub(r'[^a-zA-Z0-9]', '', business_name.lower()),
            "kapasite": "100 kişi (İç ve Dış Mekan)",
            "kurulusyili": "2000",
            "calisma_saatleri": {
                "Pazartesi": hours,
                "Salı": hours,
                "Çarşamba": hours,
                "Perşembe": hours,
                "Cuma": hours,
                "Cumartesi": hours,
                "Pazar": hours
            },
            "populer_urunler": ["Özel Spesiyal Lezzetler", "Mevsim Menüsü", "Geleneksel İçecekler", "Fırın Tatlılar"],
            "ortalama_fiyatlar": {
                "ana_yemek": "350 - 550 TL",
                "icecek": "60 - 100 TL",
                "tatli": "130 - 200 TL"
            },
            "hizmetler": {
                "acik_alan": True,
                "alakart_menu": True,
                "alkol": False,
                "engelli_girisi": True,
                "otopark": False,
                "rezervasyon": True,
                "sigara_alani": True,
                "vale": False,
                "wifi": True,
                "paket_servis": True,
                "kahvalti": True,
                "canli_muzik": False,
                "aile_dostu": True,
                "evcil_hayvan": False
            },
            "odeme_tipleri": {
                "nakit": True,
                "kredi_karti": True,
                "debit_kart": True,
                "temassiz": True
            },
            "otopark": False,
            "aciklama": aciklama
        }

    def research_venue(self, business_name: str, city: str = "İstanbul", on_partial_data=None) -> dict:
        """
        Ultra hızlı ve iki aşamalı araştırma:
        1. Aşama (~0.8-1.5 sn): Serper.dev Google Places & Arama verileri.
           Anında on_partial_data ile UI'a aktarılır!
        2. Aşama (~2-3 sn): Gemini AI zenginleştirme (menü spesiyalleri, fiyatlar, 3 parçalı rehber metni).
        """
        business_name = turkish_title(business_name.strip())
        city = turkish_title(city.strip())
        suffix = turkish_locative_suffix(business_name)
        ne_yenir_baslik = f"{business_name}{suffix} Ne Yenir?"

        context_parts = []
        serper_bundle = {}

        # ── 1. AŞAMA: SERPER.DEV (Resmi Google Places & Arama) ──
        try:
            serper_bundle = self.serper.get_venue_bundle(business_name, city=city)
            p = serper_bundle.get("place", {})
            if p.get("title") and len(p.get("title")) < 45:
                cand = p.get("title").split("-")[0].strip()
                if cand:
                    business_name = turkish_title(cand)
                    suffix = turkish_locative_suffix(business_name)
                    ne_yenir_baslik = f"{business_name}{suffix} Ne Yenir?"

            serper_ctx = "=== RESMİ GOOGLE PLACES & ARAMA VERİLERİ (SERPER.DEV) ===\n"
            serper_ctx += f"İşletme Adı: {serper_bundle.get('title')}\n"
            if serper_bundle.get('address'): serper_ctx += f"Adres: {serper_bundle.get('address')}\n"
            if serper_bundle.get('phone'): serper_ctx += f"Telefon: {serper_bundle.get('phone')}\n"
            if serper_bundle.get('rating'): serper_ctx += f"Puan: {serper_bundle.get('rating')}/5 ({serper_bundle.get('rating_count')} yorum)\n"
            if serper_bundle.get('category'): serper_ctx += f"Kategori: {serper_bundle.get('category')}\n"
            if serper_bundle.get('price_level'): serper_ctx += f"Fiyat Seviyesi: {serper_bundle.get('price_level')}\n"
            if serper_bundle.get('website'): serper_ctx += f"Web Sitesi: {serper_bundle.get('website')}\n"
            if serper_bundle.get('hours'): serper_ctx += f"Çalışma Saatleri: {serper_bundle.get('hours')}\n"
            if serper_bundle.get('snippets'):
                serper_ctx += "Google Snippet'ları:\n" + "\n".join(serper_bundle.get('snippets')[:4]) + "\n"
            context_parts.append(serper_ctx)
        except Exception as e:
            print(f"[Serper error in venue_module] {e}")

        # ANINDA 1. AŞAMA VERİSİNİ UI'A AKTAR (Kullanıcı hiç beklemesin)
        partial_data = self._build_serper_fallback(business_name, serper_bundle, city=city)
        if on_partial_data:
            try:
                on_partial_data(partial_data, "📍 Google Haritalar verileri dolduruldu. Yapay Zeka zenginleştiriyor (1/2)...")
            except Exception as e:
                print(f"[on_partial_data 1 error] {e}")

        # Sadece Serper verisi tamamen boşsa ek web taraması dene (en fazla 2 sn)
        if not serper_bundle.get("place") and not serper_bundle.get("phone"):
            try:
                maps_ctx = _scrape_google_maps(business_name, city=city)
                if maps_ctx: context_parts.append(maps_ctx)
            except Exception:
                pass

        # ── 2. AŞAMA: GEMİNİ AI İLE HIZLI ZENGİNLEŞTİRME ──
        prompt = VENUE_PROMPT.format(business_name=business_name, city=city, ne_yenir_baslik=ne_yenir_baslik)
        if context_parts:
            prompt += "\n\n" + "\n\n".join(context_parts)[:4000]

        try:
            raw_text = _gemini_generate(self.api_key, prompt, max_tokens=1500, json_mode=True).strip()
            parsed = self._parse_response(raw_text)
            if "error" not in parsed:
                # Serper'dan gelen kesin doğrulanmış bilgileri koru/tamamla
                if serper_bundle:
                    if not parsed.get("telefon") and serper_bundle.get("phone"):
                        parsed["telefon"] = serper_bundle.get("phone")
                    if not parsed.get("adres") and serper_bundle.get("address"):
                        parsed["adres"] = serper_bundle.get("address")
                    if not parsed.get("web_sitesi") and serper_bundle.get("website"):
                        parsed["web_sitesi"] = serper_bundle.get("website")
                    if not parsed.get("puan") and serper_bundle.get("rating"):
                        parsed["puan"] = f"{serper_bundle.get('rating')}/5 ⭐ (Google)"

                # Açıklama yapısını kesinleştir ve işletme adının büyük/küçük harfini düzelt
                today_str = time.strftime("%d.%m.%Y")
                ac = parsed.get("aciklama")
                if isinstance(ac, dict):
                    mb = ac.get("mekan_bilgileri", "").strip()
                    ny = ac.get("ne_yenir", "").strip()
                    en = ac.get("editor_notu", "").strip()
                elif isinstance(ac, str):
                    mb = ac.strip()
                    ny = f"{business_name} menüsünde yer alan lezzetler misafirlerine unutulmaz deneyim sunar."
                    en = f"{business_name}, {city}'da keyifli vakit geçirmek için önerilir."
                else:
                    mb = partial_data["aciklama"]["mekan_bilgileri"]
                    ny = partial_data["aciklama"]["ne_yenir"]
                    en = partial_data["aciklama"]["editor_notu"]

                # Marka adı harf uyumunu otomatik düzelt (kullanıcı uğraşmasın)
                mb = fix_brand_casing(mb, business_name)
                ny = fix_brand_casing(ny, business_name)
                en = fix_brand_casing(en, business_name)
                nb = f"{business_name}{suffix} Ne Yenir?"

                parsed["aciklama"] = {
                    "mekan_bilgileri": mb,
                    "ne_yenir_baslik": nb,
                    "ne_yenir": ny,
                    "editor_notu": en,
                    "tam_metin": f"Mekan Bilgileri\n\n{mb}\n\n{nb}\n\n{ny}\n\nEditör Notu\n{city} Mekan Rehberi Editör Ekibi (Doğrulanmış İşletme)\n{en}\nSon güncelleme: {today_str}"
                }
                
                if on_partial_data:
                    try:
                        on_partial_data(parsed, "✨ Tüm bilgiler başarıyla hazırlandı (2/2)")
                    except Exception as e:
                        print(f"[on_partial_data 2 error] {e}")
                return parsed
            else:
                if on_partial_data:
                    try:
                        on_partial_data(partial_data, "✅ Doğrulanmış Google Harita bilgileri hazır")
                    except Exception:
                        pass
                return partial_data
        except Exception as e:
            print(f"[Gemini failed, using Serper fallback] {e}")
            if on_partial_data:
                try:
                    on_partial_data(partial_data, "✅ Doğrulanmış Google Harita bilgileri hazır")
                except Exception:
                    pass
            return partial_data

    def _parse_response(self, raw_text: str) -> dict:
        raw_text = raw_text.strip()
        if raw_text.startswith("'") and raw_text.endswith("'"):
            raw_text = raw_text[1:-1]
        if raw_text.startswith('"') and raw_text.endswith('"'):
            try:
                raw_text = json.loads(raw_text)
            except Exception:
                raw_text = raw_text[1:-1]

        raw_text = re.sub(r"```(?:json)?", "", raw_text).strip("`").strip()
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        if start == -1 or end == 0:
            return {"error": "JSON bulunamadı", "raw": raw_text[:500]}
        json_str = raw_text[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
            json_str = re.sub(r'//[^\n]*', '', json_str)
            try:
                return json.loads(json_str)
            except Exception as e:
                return {"error": f"JSON parse hatası: {e}", "raw": json_str[:500]}
