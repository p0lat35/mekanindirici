"""
cache_manager.py — Yerel Önbellek Yöneticisi (API Kredisi Tasarrufu)
Daha önce aranan mekanların bilgilerini ve fotoğraflarını bilgisayarda saklar.
Tekrar arandığında API'lere istek atmadan anında yerel diskten yanıt verir.
"""

import os
import json
import re
import threading
from datetime import datetime

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

def _normalize_key(text: str) -> str:
    """Türkçe karakterleri ve özel işaretleri temizleyerek dosya adı üretir."""
    tr_map = str.maketrans("çğışöüÇĞİŞÖÜ", "cgisoucgisou")
    cleaned = text.translate(tr_map).lower()
    return re.sub(r'[^a-z0-9]+', '_', cleaned).strip('_')


class CacheManager:
    _lock = threading.Lock()

    @staticmethod
    def _init_dir():
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR, exist_ok=True)

    @classmethod
    def _get_file_path(cls, business_name: str, city: str) -> str:
        cls._init_dir()
        city_slug = _normalize_key(city or "istanbul")
        name_slug = _normalize_key(business_name)
        filename = f"{city_slug}_{name_slug}.json"
        return os.path.join(CACHE_DIR, filename)

    @classmethod
    def get(cls, business_name: str, city: str = "İstanbul") -> dict | None:
        """
        Önbellekten mekan verisini ve fotoğrafları okur.
        Varsa dict döner, yoksa None.
        """
        file_path = cls._get_file_path(business_name, city)
        with cls._lock:
            if not os.path.exists(file_path):
                return None
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                print(f"[Cache Read Error] {e}")
                return None

    @classmethod
    def save(cls, business_name: str, city: str, venue_data: dict = None, posts: list = None):
        """
        Mekan verilerini ve/veya fotoğrafları önbelleğe kaydeder ya da günceller.
        """
        file_path = cls._get_file_path(business_name, city)
        with cls._lock:
            cached = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                except Exception:
                    cached = {}

            cached["business_name"] = business_name
            cached["city"] = city
            cached["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if venue_data:
                cached["venue_data"] = venue_data
            if posts is not None:
                cached["posts"] = posts

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(cached, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[Cache Write Error] {e}")

    @classmethod
    def delete(cls, business_name: str, city: str):
        """Önbelleği temizle (Yeniden aramak istendiğinde)."""
        file_path = cls._get_file_path(business_name, city)
        with cls._lock:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

    @classmethod
    def list_cached(cls) -> list[dict]:
        """Tüm kayıtlı önbellek mekanlarını tarihe göre sıralı listeler."""
        cls._init_dir()
        items = []
        with cls._lock:
            try:
                for fn in os.listdir(CACHE_DIR):
                    if fn.endswith(".json"):
                        fp = os.path.join(CACHE_DIR, fn)
                        try:
                            with open(fp, "r", encoding="utf-8") as f:
                                d = json.load(f)
                                items.append({
                                    "business_name": d.get("business_name", ""),
                                    "city": d.get("city", "İstanbul"),
                                    "updated_at": d.get("updated_at", ""),
                                    "photo_count": len(d.get("posts", [])),
                                    "has_venue": bool(d.get("venue_data")),
                                })
                        except Exception:
                            continue
            except Exception:
                pass
        items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return items
