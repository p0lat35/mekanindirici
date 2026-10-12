"""
server.py
Mekan & Restoran İndirici — Modern Flask Web Sunucusu
cPanel / VPS / Render / Yerel çalışmaya %100 uyumludur.
"""

import os
import re
import io
import json
import time
import zipfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, render_template, request, jsonify, send_file, Response
import requests
from PIL import Image, ImageOps

from venue_module import VenueModule, turkish_title, turkish_locative_suffix
from instagram_module import InstagramModule
from cache_manager import CacheManager
from serper_client import SerperClient

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(new_data):
    s = load_settings()
    s.update(new_data)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Settings Save Error] {e}")


# Modül örnekleri
settings = load_settings()
SERPER_KEY = settings.get("serper_key", "332043126cfcb417ee2cf3b2a0e16df05808c1b2")
GEMINI_KEY = settings.get("gemini_key", "")

instagram_module = InstagramModule(serper_key=SERPER_KEY)
venue_module = VenueModule(api_key=GEMINI_KEY, serper_key=SERPER_KEY) if GEMINI_KEY else None


@app.route("/")
def index():
    """Ana sayfa"""
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    """API anahtarları ve durum bilgisi"""
    curr_settings = load_settings()
    has_gemini = bool(curr_settings.get("gemini_key"))
    return jsonify({
        "status": "ok",
        "has_gemini": has_gemini,
        "gemini_key_masked": (curr_settings.get("gemini_key", "")[:8] + "...") if has_gemini else "",
        "last_city": curr_settings.get("last_city", "İstanbul")
    })


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """API anahtarlarını kaydet"""
    global venue_module, GEMINI_KEY, SERPER_KEY, instagram_module
    data = request.get_json() or {}
    gemini_key = data.get("gemini_key", "").strip()
    serper_key = data.get("serper_key", "").strip() or SERPER_KEY

    updates = {}
    if gemini_key:
        updates["gemini_key"] = gemini_key
        GEMINI_KEY = gemini_key
        venue_module = VenueModule(api_key=GEMINI_KEY, serper_key=serper_key)
    if serper_key:
        updates["serper_key"] = serper_key
        SERPER_KEY = serper_key
        instagram_module = InstagramModule(serper_key=SERPER_KEY)

    save_settings(updates)
    return jsonify({"success": True, "message": "Ayarlar başarıyla kaydedildi!"})


@app.route("/api/search", methods=["POST"])
def search_venue():
    """Mekan bilgilerini (AI açıklamaları, hizmetler, saatler) çeker."""
    global venue_module
    data = request.get_json() or {}
    venue_name = data.get("venue_name", "").strip()
    city = data.get("city", "İstanbul").strip()
    force_refresh = bool(data.get("force_refresh", False))

    if not venue_name:
        return jsonify({"error": "Mekan adı boş olamaz"}), 400

    clean_name = turkish_title(venue_name)

    # 1. Önbellek kontrolü
    cached = CacheManager.get(clean_name, city)
    if cached and not force_refresh and cached.get("venue_data"):
        v_data = cached.get("venue_data")
        return jsonify({
            "is_cached": True,
            "updated_at": cached.get("updated_at", ""),
            "venue_name": clean_name,
            "city": city,
            "data": v_data
        })

    # 2. Canlı API sorgusu
    curr_settings = load_settings()
    gemini_key = curr_settings.get("gemini_key", "")
    if not gemini_key:
        return jsonify({"error": "Lütfen önce Google Gemini API anahtarınızı girin."}), 400

    if not venue_module:
        venue_module = VenueModule(api_key=gemini_key, serper_key=SERPER_KEY)

    try:
        res = venue_module.research_venue(clean_name, city=city)
        if res:
            # aciklama objesini düzleştir
            ac = res.get("aciklama")
            if isinstance(ac, dict):
                res["aciklama_bilgiler"] = ac.get("mekan_bilgileri") or ""
                res["ne_yenir_baslik"] = ac.get("ne_yenir_baslik") or ""
                res["aciklama_neyenir"] = ac.get("ne_yenir") or ""
                res["aciklama_editor"] = ac.get("editor_notu") or ""
            elif isinstance(ac, str):
                res["aciklama_bilgiler"] = ac

            # Önbelleğe al
            CacheManager.save(clean_name, city, venue_data=res)
            return jsonify({
                "is_cached": False,
                "updated_at": time.strftime("%d.%m.%Y %H:%M"),
                "venue_name": clean_name,
                "city": city,
                "data": res
            })
        else:
            return jsonify({"error": "Mekan bilgileri alınamadı. Lütfen mekan adını kontrol edin."}), 404
    except Exception as e:
        return jsonify({"error": f"Hata oluştu: {str(e)}"}), 500


@app.route("/api/photos", methods=["POST"])
def search_photos():
    """Mekanın fotoğraflarını getirir."""
    data = request.get_json() or {}
    venue_name = data.get("venue_name", "").strip()
    city = data.get("city", "İstanbul").strip()
    force_refresh = bool(data.get("force_refresh", False))

    if not venue_name:
        return jsonify({"error": "Mekan adı boş olamaz"}), 400

    clean_name = turkish_title(venue_name)

    # Önbellek kontrolü
    cached = CacheManager.get(clean_name, city)
    if cached and not force_refresh and cached.get("posts"):
        return jsonify({
            "is_cached": True,
            "photos": cached.get("posts")
        })

    try:
        photos = instagram_module.get_posts(clean_name, city=city, max_posts=36)
        if photos:
            CacheManager.save(clean_name, city, posts=photos)
        return jsonify({
            "is_cached": False,
            "photos": photos
        })
    except Exception as e:
        return jsonify({"error": f"Fotoğraf arama hatası: {str(e)}"}), 500


@app.route("/api/image-proxy", methods=["GET"])
def image_proxy():
    """Görseli sunucu üzerinden proxyleyerek CORS ve Hotlink engellerini aşar."""
    img_url = request.args.get("url", "").strip()
    if not img_url:
        return "URL eksik", 400
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }
        r = requests.get(img_url, headers=headers, timeout=8, stream=True)
        if r.status_code != 200:
            return "Görsel sunucusundan hata", r.status_code
        ct = r.headers.get("content-type", "image/jpeg")
        return Response(r.iter_content(chunk_size=8192), content_type=ct)
    except Exception as e:
        return f"Görsel çekilemedi: {e}", 502


@app.route("/api/cover-crop", methods=["POST"])
def crop_cover():
    """Verilen görsel URL'sini 1200x675 HD web formatına kırpar ve doğrudan JPEG döndürür."""
    data = request.get_json() or {}
    img_url = data.get("image_url", "").strip()
    if not img_url:
        return jsonify({"error": "Görsel URL'si boş"}), 400

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }
        r = requests.get(img_url, headers=headers, timeout=12)
        r.raise_for_status()

        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        # 1200x675 tam oranında merkezden kırp ve ölçekle
        cover = ImageOps.fit(img, (1200, 675), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        out_io = io.BytesIO()
        cover.save(out_io, format="JPEG", quality=95)
        out_io.seek(0)

        return send_file(out_io, mimetype="image/jpeg")
    except Exception as e:
        print(f"[Cover Crop Error] {e}")
        return jsonify({"error": f"Görsel işleme hatası: {str(e)}"}), 500


@app.route("/api/download-cover", methods=["GET"])
def download_cover():
    """1200x675 kapak görselini indirme olarak tetikler."""
    img_url = request.args.get("url", "").strip()
    venue_name = request.args.get("name", "mekan").strip()

    if not img_url:
        return "Görsel linki eksik", 400

    slug = re.sub(r'[^a-zA-Z0-9]', '_', venue_name.lower()).strip('_') or "mekan"
    filename = f"{slug}_kapak_1200x675.jpg"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        r = requests.get(img_url, headers=headers, timeout=12)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        cover = ImageOps.fit(img, (1200, 675), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        out_io = io.BytesIO()
        cover.save(out_io, format="JPEG", quality=95)
        out_io.seek(0)

        return send_file(out_io, mimetype="image/jpeg", as_attachment=True, download_name=filename)
    except Exception as e:
        return f"Hata: {e}", 500


@app.route("/api/download-zip", methods=["POST"])
def download_zip():
    """Seçilen fotoğrafları ve 1200x675 kapak fotoğrafını anında ZIP olarak paketler ve indirir."""
    data = request.get_json() or {}
    venue_name = data.get("venue_name", "mekan")
    photo_urls = data.get("photo_urls", [])
    cover_url = data.get("cover_url", "")

    if not photo_urls and not cover_url:
        return jsonify({"error": "İndirilecek fotoğraf seçilmedi"}), 400

    slug = re.sub(r'[^a-zA-Z0-9]', '_', venue_name.lower()).strip('_') or "mekan"
    zip_filename = f"{slug}_fotograflar.zip"

    zip_buffer = io.BytesIO()
    headers = {"User-Agent": "Mozilla/5.0"}

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 1. 1200x675 Kapak Görseli
        if cover_url:
            try:
                r = requests.get(cover_url, headers=headers, timeout=10)
                if r.status_code == 200:
                    c_img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    c_cover = ImageOps.fit(c_img, (1200, 675), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                    c_io = io.BytesIO()
                    c_cover.save(c_io, "JPEG", quality=95)
                    zipf.writestr(f"{slug}/00_kapak_1200x675.jpg", c_io.getvalue())
            except Exception as e:
                print(f"[Cover zip error] {e}")

        # 2. Seçilen diğer fotoğraflar
        def _fetch_img(idx, u):
            try:
                resp = requests.get(u, headers=headers, timeout=10)
                if resp.status_code == 200:
                    return idx, resp.content
            except Exception:
                pass
            return idx, None

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda p: _fetch_img(p[0], p[1]), enumerate(photo_urls, 1)))

        for idx, content in results:
            if content:
                zipf.writestr(f"{slug}/foto_{idx:02d}.jpg", content)

    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name=zip_filename)


@app.route("/api/cache/list", methods=["GET"])
def list_cache():
    """Önbellekteki kayıtlı mekanları döndürür."""
    items = CacheManager.list_cached()
    return jsonify({"items": items})


@app.route("/api/cache/delete", methods=["POST"])
def delete_cache():
    """Önbellekten mekan siler."""
    data = request.get_json() or {}
    venue_name = data.get("venue_name", "")
    city = data.get("city", "")
    if venue_name and city:
        CacheManager.delete(venue_name, city)
        return jsonify({"success": True})
    return jsonify({"error": "Eksik parametre"}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"==================================================")
    print(f"  Mekan & Restoran İndirici Web Sunucusu Aktif!")
    print(f"  Tarayıcıdan açın: http://localhost:{port}")
    print(f"==================================================")

    def _open_browser():
        time.sleep(1.2)
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    import threading
    threading.Thread(target=_open_browser, daemon=True).start()

    app.run(host="0.0.0.0", port=port, debug=False)
