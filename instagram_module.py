"""
instagram_module.py — ULTRA Photo Engine v5 (100% Canlı & Doğrulanmış Görseller)
Kaynaklar: Yandex Görseller (Harita & Mekan CDN) + DuckDuckGo Görseller (TripAdvisor & 4sq)
Tüm URL'ler arka planda test edilir; 404 veren veya bozuk olanlar ASLA listeye eklenmez.
"""

import os
import re
import io
import threading
import requests
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

try:
    from PIL import Image, ImageOps
except ImportError:
    pass

try:
    from ddgs import DDGS
    DDG_OK = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDG_OK = True
    except ImportError:
        DDG_OK = False

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8",
}


def _verify_image_url(url: str, timeout: float = 4.0) -> bool:
    """Görsel URL'sinin gerçekten 200 OK ve geçerli bir görsel olduğunu doğrular."""
    if not url or len(url) < 15:
        return False
    try:
        # Önce hafif HEAD isteği at
        r = requests.head(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            ct = r.headers.get("content-type", "").lower()
            if "image" in ct or "octet-stream" in ct or not ct:
                return True
        # HEAD desteklemeyen sunucular için GET range dene
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-2048"}, timeout=timeout, stream=True)
        if r.status_code in (200, 206):
            ct = r.headers.get("content-type", "").lower()
            if "image" in ct or "octet-stream" in ct or not ct:
                return True
    except Exception:
        pass
    return False


SKIP_KEYWORDS = [
    "logo", "icon", "avatar", "profile", "banner", "button", "pixel",
    "map", "harita", "vector", "stock", "clipart", "placeholder", "tabela",
    "qr", "screenshot", "thumb", "badge", "emoji", "gif", "advert", "reklam"
]


def _score_image_candidate(item: dict, venue_name: str, city: str) -> int:
    """Görselin mekana uygunluğunu ve alaka düzeyini puanlar."""
    url = (item.get("image") or "").lower()
    title = (item.get("title") or "").lower()
    source = (item.get("sourceUrl") or "").lower() + " " + (item.get("date") or "").lower()
    combined = f"{url} {title} {source}"

    # Negatif kelimeler varsa elenir
    if any(k in combined for k in SKIP_KEYWORDS):
        return -100

    score = 10
    # İşletme adının kelimeleri geçiyorsa büyük puan
    tokens = [t.lower() for t in venue_name.replace('"', '').split() if len(t) >= 3]
    for token in tokens:
        if token in combined:
            score += 35

    # Güvenilir gastronomi ve mekan platformları
    if "tripadvisor" in combined or "media-cdn.tripadvisor.com" in combined:
        score += 35
    if "4sqi" in combined or "foursquare" in combined:
        score += 30
    if "zomato" in combined or "yemeksepeti" in combined:
        score += 25
    if "googleusercontent" in combined or "lh3.googleusercontent" in combined:
        score += 25
    if "yandex" in combined:
        score += 15

    # Mekan / Yemek / İç-Dış mekan anahtar kelimeleri
    positive_words = ["restoran", "restaurant", "mekan", "lezzet", "menu", "menü", "yemek", "interior", "exterior", "tatli", "kahvalti", "cafe", "kafe", "mutfak"]
    for pw in positive_words:
        if pw in combined:
            score += 10

    return score


def _scrape_yandex_images(query: str, max_results: int = 25) -> list:
    """Yandex Görseller'den yüksek çözünürlüklü mekan ve harita fotoğrafları çeker."""
    results = []
    try:
        url = f"https://yandex.com.tr/gorsel/search?text={urllib.parse.quote(query)}"
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
        # JSON ve HTML içindeki img_href origin linklerini yakala
        matches = re.findall(r'"img_href":"(https?://[^"]+)"', r.text)
        if not matches:
            matches = re.findall(r'&quot;img_href&quot;:&quot;(https?://[^&]+)&quot;', r.text)
        
        for u in matches:
            u_clean = u.replace("\\/", "/").strip()
            if not any(s in u_clean.lower() for s in SKIP_KEYWORDS) and len(u_clean) > 25:
                results.append({"image": u_clean, "date": "Yandex / Mekan", "title": query})
            if len(results) >= max_results:
                break
    except Exception as e:
        print(f"[Yandex Error] {e}")
    return results


def _scrape_ddg_images(query: str, max_results: int = 25) -> list:
    """DuckDuckGo üzerinden TripAdvisor, Foursquare ve yemek sitelerinden görsel çeker."""
    results = []
    if not DDG_OK:
        return results
    try:
        imgs = list(DDGS().images(
            query,
            region="tr-tr",
            safesearch="moderate",
            max_results=max_results
        ))
        for d in imgs:
            u = d.get("image", "")
            title = d.get("title", "")
            w = d.get("width", 0)
            try:
                w_int = int(w) if w else 0
            except (ValueError, TypeError):
                w_int = 0

            if u and (w_int >= 250 or w_int == 0) and not any(s in u.lower() for s in SKIP_KEYWORDS):
                src = "TripAdvisor" if "tripadvisor" in u else ("Foursquare" if "4sqi" in u else "Web Arama")
                results.append({"image": u, "date": src, "title": title, "sourceUrl": d.get("url", "")})
    except Exception as e:
        print(f"[DDG Error] {e}")
    return results


from serper_client import SerperClient

class InstagramModule:
    def __init__(self, serper_key: str = "332043126cfcb417ee2cf3b2a0e16df05808c1b2"):
        self.serper_key = serper_key or "332043126cfcb417ee2cf3b2a0e16df05808c1b2"
        self.serper = SerperClient(self.serper_key)

    def login(self, username, password):
        pass

    def load_profile_by_username(self, username: str) -> dict:
        return {
            "username": username,
            "full_name": f"{username.capitalize()} (Web)",
            "biography": "",
            "followers": 0,
            "profile_pic_url": None
        }

    def get_posts(self, query: str, city: str = "İstanbul", max_posts: int = 35, on_photo_found=None) -> list:
        """
        Tüm kaynaklardan paralel topla, mekana en uygun olanları puanlayıp sırala ve doğrula.
        Kaynaklar: Serper Google Images + Yandex Görseller + DDG (TripAdvisor / 4sq)
        """
        clean_q = query.lstrip("@").strip()
        candidates = []
        seen = set()
        lock = threading.Lock()

        # 1. Serper Google Images (resmi Google görselleri - tırnaklı tam eşleşme)
        def _fetch_serper():
            try:
                s_imgs = self.serper.get_images(f'"{clean_q}" {city} restoran', city=city, num=25)
                with lock:
                    for it in s_imgs:
                        u = it.get("image", "")
                        if u and u not in seen:
                            seen.add(u)
                            candidates.append(it)
            except Exception:
                pass

        def _fetch_yandex_interior():
            try:
                for it in _scrape_yandex_images(f'"{clean_q}" {city} mekan restoran', max_results=18):
                    u = it.get("image", "")
                    with lock:
                        if u and u not in seen:
                            seen.add(u)
                            candidates.append(it)
            except Exception:
                pass

        def _fetch_yandex_food():
            try:
                for it in _scrape_yandex_images(f'"{clean_q}" {city} yemek lezzet menü', max_results=18):
                    u = it.get("image", "")
                    with lock:
                        if u and u not in seen:
                            seen.add(u)
                            candidates.append(it)
            except Exception:
                pass

        def _fetch_ddg_general():
            try:
                for it in _scrape_ddg_images(f'"{clean_q}" {city} mekan', max_results=16):
                    u = it.get("image", "")
                    with lock:
                        if u and u not in seen:
                            seen.add(u)
                            candidates.append(it)
            except Exception:
                pass

        def _fetch_ddg_travel():
            try:
                for it in _scrape_ddg_images(f'"{clean_q}" {city} tripadvisor OR foursquare', max_results=15):
                    u = it.get("image", "")
                    with lock:
                        if u and u not in seen:
                            seen.add(u)
                            candidates.append(it)
            except Exception:
                pass

        # Paralel arama görevleri
        tasks = [_fetch_serper, _fetch_yandex_interior, _fetch_yandex_food, _fetch_ddg_general, _fetch_ddg_travel]
        threads = [threading.Thread(target=fn, daemon=True) for fn in tasks]
        for t in threads:
            t.start()

        # Maksimum 3.5 sn bekle (Serper ve Yandex 1-1.5 sn'de biter)
        import time
        deadline = time.time() + 3.5
        for t in threads:
            rem = max(0.05, deadline - time.time())
            t.join(timeout=rem)

        print(f"[{clean_q}] Toplanan ham görsel adayı: {len(candidates)}")

        # ── ALAKA DÜZEYİNE GÖRE PUANLA VE SIRALA (İlgisizleri ele) ──
        filtered_candidates = []
        for it in candidates:
            sc = _score_image_candidate(it, clean_q, city)
            if sc > 0:  # Negatif veya alakasız olanları at
                it["_score"] = sc
                filtered_candidates.append(it)

        # En yüksek puanlı ve en alakalı fotoğraflar en başa gelir
        filtered_candidates.sort(key=lambda x: x.get("_score", 0), reverse=True)
        if not filtered_candidates:
            filtered_candidates = candidates  # Yedek

        # ── CANLILIK & DOĞRULAMA KONTROLÜ + HIZLI İŞLEME ──
        verified_posts = []

        def _verify_worker(item):
            # Hedef sayıya ulaşıldıysa gereksiz tarama yapma
            if len(verified_posts) >= max_posts:
                return
            u = item.get("image", "")
            if _verify_image_url(u, timeout=2.0):
                with lock:
                    if len(verified_posts) < max_posts:
                        post = {
                            "url": u,
                            "shortcode": f"web_{len(verified_posts)}",
                            "caption": "",
                            "date": item.get("date", "Mekan"),
                        }
                        verified_posts.append(post)
                        if on_photo_found:
                            try:
                                on_photo_found(post, len(verified_posts))
                            except Exception:
                                pass

        with ThreadPoolExecutor(max_workers=20) as executor:
            executor.map(_verify_worker, filtered_candidates[:45])

        print(f"[{clean_q}] Doğrulanmış en alakalı çalışan görsel sayısı: {len(verified_posts)}")
        return verified_posts

    def download_image(self, url: str, shortcode: str) -> str:
        """Resmi web'den indirip 'downloads/' klasörüne kaydeder."""
        if not os.path.exists("downloads"):
            os.makedirs("downloads")
        file_path = os.path.join("downloads", f"{shortcode}.jpg")
        if os.path.exists(file_path):
            return file_path
        try:
            resp = requests.get(url, stream=True, timeout=12, headers=BROWSER_HEADERS)
            resp.raise_for_status()
            with open(file_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return file_path
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise ValueError(f"Görsel indirilemedi: {e}")

    def download_posts(self, posts: list, target_dir: str, callback=None) -> list:
        """Seçilen fotoğrafları belirtilen klasöre indirir."""
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)

        downloaded = []
        total = len(posts)
        for idx, post in enumerate(posts):
            url = post.get("url", "")
            if not url:
                continue
            filename = f"foto_{idx+1}.jpg"
            file_path = os.path.join(target_dir, filename)
            try:
                resp = requests.get(url, headers=BROWSER_HEADERS, timeout=12)
                resp.raise_for_status()
                with open(file_path, "wb") as f:
                    f.write(resp.content)
                downloaded.append(file_path)
            except Exception as e:
                print(f"[download_posts error] {e}")
            if callback:
                callback(idx + 1, total, filename)
        return downloaded

    def submit_2fa(self, code: str):
        pass
