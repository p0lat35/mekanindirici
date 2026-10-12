"""
serper_client.py — Google Serper.dev API İstemcisi
Doğrudan resmi Google Haritalar (Places), Google Arama (Organic) ve Google Görseller (Images) verisi sağlar.
"""

import requests
import re
import json

SERPER_API_URL = "https://google.serper.dev"

class SerperClient:
    def __init__(self, api_key: str = "332043126cfcb417ee2cf3b2a0e16df05808c1b2"):
        self.api_key = api_key or "332043126cfcb417ee2cf3b2a0e16df05808c1b2"

    @property
    def headers(self):
        return {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }

    def get_place(self, query: str, city: str = "İstanbul") -> dict:
        """Google Places (Haritalar) üzerinden işletme bilgilerini çeker."""
        try:
            city_str = f" {city}" if city and city.lower() not in query.lower() else ""
            resp = requests.post(
                f"{SERPER_API_URL}/places",
                headers=self.headers,
                json={"q": f"{query}{city_str}", "gl": "tr", "hl": "tr"},
                timeout=8
            )
            if resp.status_code == 200:
                places = resp.json().get("places", [])
                if places:
                    return places[0]
        except Exception as e:
            print(f"[Serper Places Error] {e}")
        return {}

    def search_organic(self, query: str, num: int = 5) -> list:
        """Google organik arama sonuçlarını ve snippet'larını çeker."""
        try:
            resp = requests.post(
                f"{SERPER_API_URL}/search",
                headers=self.headers,
                json={"q": query, "gl": "tr", "hl": "tr", "num": num},
                timeout=8
            )
            if resp.status_code == 200:
                return resp.json().get("organic", [])
        except Exception as e:
            print(f"[Serper Search Error] {e}")
        return []

    def get_images(self, query: str, city: str = "İstanbul", num: int = 20) -> list:
        """Google Görseller üzerinden mekan fotoğraflarını çeker."""
        results = []
        try:
            city_str = f" {city}" if city and city.lower() not in query.lower() else ""
            resp = requests.post(
                f"{SERPER_API_URL}/images",
                headers=self.headers,
                json={"q": f"{query}{city_str}", "gl": "tr", "hl": "tr", "num": num},
                timeout=8
            )
            if resp.status_code == 200:
                imgs = resp.json().get("images", [])
                for im in imgs:
                    u = im.get("imageUrl", "")
                    if u and len(u) > 25:
                        results.append({
                            "image": u,
                            "date": "Google / Serper",
                            "title": im.get("title", "")
                        })
        except Exception as e:
            print(f"[Serper Images Error] {e}")
        return results

    def get_venue_bundle(self, business_name: str, city: str = "İstanbul") -> dict:
        """
        Mekanın tüm Google Harita, Açılış Saati, Telefon ve Fiyat verisini paralel ve çok hızlı toplar.
        """
        from concurrent.futures import ThreadPoolExecutor

        place = {}
        organic = []

        city_str = f" {city}" if city and city.lower() not in business_name.lower() else ""
        search_query = f"{business_name}{city_str} telefon çalışma saatleri menü fiyat"

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_place = executor.submit(self.get_place, business_name, city)
            fut_organic = executor.submit(self.search_organic, search_query, 6)
            try:
                place = fut_place.result(timeout=6) or {}
            except Exception:
                pass
            try:
                organic = fut_organic.result(timeout=6) or []
            except Exception:
                pass

        snippets = [o.get("snippet", "") for o in organic if o.get("snippet")]
        all_text = " ".join(snippets)

        # Telefon tespiti
        phones = re.findall(r'(?:\+90|0)[\s\d\-\(\)]{9,15}', all_text)
        phone = phones[0].strip() if phones else place.get("phoneNumber")

        # Çalışma saatleri tespiti
        hours_match = re.findall(r'(\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2})', all_text)
        hours_str = hours_match[0] if hours_match else "10:00 - 23:00"

        return {
            "place": place,
            "title": place.get("title", business_name),
            "address": place.get("address"),
            "rating": place.get("rating"),
            "rating_count": place.get("ratingCount"),
            "price_level": place.get("priceLevel"),
            "category": place.get("category"),
            "website": place.get("website"),
            "phone": phone,
            "hours": hours_str,
            "snippets": snippets,
            "all_text": all_text
        }
