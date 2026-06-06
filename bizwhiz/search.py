"""
search.py -- Business search and email scraping for BizWhiz
"""
import re, requests
from bs4 import BeautifulSoup
from geopy.geocoders import Nominatim

_FAKE_TLDS = {
    "png","jpg","jpeg","gif","webp","svg","ico","bmp","tiff",
    "css","js","ts","jsx","tsx","map","json","xml","yaml","yml",
    "woff","woff2","ttf","eot","otf","mp4","mp3","wav","avi",
    "mov","webm","zip","tar","gz","pdf","doc","docx","xls","xlsx",
    "php","html","htm","aspx","rb","py"
}
_JUNK_DOMAINS = {
    "example.com","domain.com","email.com","sentry.io","w3.org",
    "schema.org","yourdomain.com","company.com","yoursite.com",
    "mysite.com","yourcompany.com","test.com","placeholder.com"
}
_JUNK_FRAGMENTS = {"sentry.","wixpress.","squarespace.","wordpress.","shopify.","ingest."}
_HEX_RE = re.compile(r"^[0-9a-f]{16,}$")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _looks_like_email(em):
    if "@" not in em:
        return False
    local, domain = em.rsplit("@", 1)
    if _HEX_RE.match(local):
        return False
    if len(local) > 50:
        return False
    if chr(92) in local or "/" in local:
        return False
    if "_" in domain:
        return False
    for frag in _JUNK_FRAGMENTS:
        if frag in domain:
            return False
    if domain.lower() in _JUNK_DOMAINS:
        return False
    parts = domain.split(".")
    if len(parts) < 2:
        return False
    tld = parts[-1].lower()
    if tld in _FAKE_TLDS:
        return False
    second = parts[-2].lower()
    if second and second[0].isdigit():
        return False
    return True


def get_coordinates(zip_code):
    geo = Nominatim(user_agent="bizwhiz_app")
    loc = geo.geocode({"postalcode": zip_code, "country": "US"})
    if not loc:
        raise ValueError(f"Could not find coordinates for ZIP: {zip_code}")
    return loc.latitude, loc.longitude


def search_nearby_businesses(lat, lon, radius_meters, business_type):
    query = f"""
    [out:json][timeout:30];
    (
      node["name"]["shop"~"{business_type}",i](around:{radius_meters},{lat},{lon});
      node["name"]["amenity"~"{business_type}",i](around:{radius_meters},{lat},{lon});
      node["name"]["office"~"{business_type}",i](around:{radius_meters},{lat},{lon});
      node["name"]["craft"~"{business_type}",i](around:{radius_meters},{lat},{lon});
      node["name"]["business"~"{business_type}",i](around:{radius_meters},{lat},{lon});
    );
    out body;
    """
    resp = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query}, timeout=35
    )
    resp.raise_for_status()
    results = []
    for el in resp.json().get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name","").strip()
        if not name:
            continue
        phone   = tags.get("phone") or tags.get("contact:phone","")
        website = tags.get("website") or tags.get("contact:website","")
        if website and not website.startswith("http"):
            website = "https://" + website
        street  = tags.get("addr:street","")
        city    = tags.get("addr:city","")
        state   = tags.get("addr:state","")
        address = ", ".join(p for p in [street, city, state] if p)
        results.append({
            "name": name, "website": website, "phone": phone,
            "address": address, "emails": ""
        })
    return results


def find_email_on_website(url, timeout=8):
    if not url:
        return []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        found = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("mailto:"):
                em = href[7:].split("?")[0].strip().lower()
                if _looks_like_email(em):
                    found.add(em)
        for em in _EMAIL_RE.findall(resp.text):
            em = em.lower()
            if _looks_like_email(em):
                found.add(em)
        return list(found)[:5]
    except Exception:
        return []
