#!/usr/bin/env python3
"""
Bivouac inventory for Austria, Switzerland, Italy, Slovenia and the Bavarian Alps.

Pulls every unstaffed mountain shelter from OpenStreetMap with coordinates and
altitude, matches it against camptocamp.org, then tries to attach a picture and
a link to each one.

Picture sources, in order of confidence:
  1. OSM  image=*                 direct URL, mapper-supplied
  2. OSM  wikimedia_commons=File: resolved via Commons Special:FilePath
  3. Wikidata P18                 for objects carrying a wikidata=* tag
  4. camptocamp.org               first photo of the matched waypoint
  5. Commons geosearch (optional) nearest georeferenced files, --commons

Usage (from anywhere, paths are relative to this file)
  python3 scripts/bivouacs_pipeline.py                  # all countries
  python3 scripts/bivouacs_pipeline.py --commons        # add the geosearch pass (slow)
  python3 scripts/bivouacs_pipeline.py --countries SI   # subset
  python3 scripts/bivouacs_pipeline.py --refresh        # ignore the cache, re-query
  python3 scripts/bivouacs_pipeline.py --no-c2c         # skip camptocamp

Every Overpass and camptocamp answer is cached under scripts/cache/, so a failed
country does not cost you the ones that already succeeded. Re-running resumes.

Output
  bivouacs.json               compact list the map loads (repo root)
  scripts/out/bivouacs.csv    everything, with evidence columns for auditing
  scripts/out/bivouacs.geojson

Licence note: OSM data is ODbL, camptocamp content is CC BY-SA, Commons images
carry their own per-file licence. The map links every picture to its page.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

UA = "bivouac-inventory/1.0 (https://github.com/kyrylogy/hut-availability)"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "out")
WEB_JSON = os.path.join(HERE, "..", "bivouacs.json")

# The main instance has current data but refuses this query under load with an
# immediate 503. The mirrors answer reliably but lag by weeks. So: hammer the
# main instance politely first, fall back only when it will not cooperate.
PRIMARY = "https://overpass-api.de/api/interpreter"
MIRRORS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
PRIMARY_ATTEMPTS = 4          # with backoff below, ~2 minutes of trying
BACKOFF = [10, 20, 40, 60]

COUNTRIES = ["AT", "CH", "IT", "SI", "DE"]

# Whole-country queries, except Germany: only the Alpine strip, otherwise every
# forest Schutzhütte between Munich and Hamburg comes back as wilderness_hut.
BBOX = {"DE": "(47.25,9.5,47.8,13.2)"}

QUERY = """
[out:json][timeout:600];
area["ISO3166-1"="{cc}"][admin_level=2]->.a;
(
  nwr["tourism"="wilderness_hut"](area.a){bbox};
  nwr["tourism"="alpine_hut"](area.a){bbox};
  nwr["amenity"="shelter"]["shelter_type"="basic_hut"](area.a){bbox};
);
out center tags;
"""

# "bivouac" in the four local languages, plus the abbreviations used on signs.
NAME_RE = re.compile(
    r"(bivacco|bivacchi|bivak|biwak|biwakschachtel|bivouac|\bbiv\.|\bbiv\b)",
    re.IGNORECASE,
)

# Things tagged like shelters that nobody on a hiking map wants.
HUNTING_RE = re.compile(r"(lovsk|jagd|jäger|jaeger|caccia|cacciatori)", re.IGNORECASE)

# camptocamp.org, EPSG:3857 bbox covering Italy, the Alps and Slovenia.
C2C_API = "https://api.camptocamp.org"
C2C_TYPES = "hut,shelter,bivouac"
C2C_BBOX = (5.9, 36.6, 17.2, 48.3)     # lon_min, lat_min, lon_max, lat_max
C2C_COUNTRY = {"Austria": "AT", "Switzerland": "CH", "Italy": "IT",
               "Slovenia": "SI", "Germany": "DE"}

# Words every second shelter name contains; useless for telling two apart.
GENERIC = {
    "bivacco", "bivacchi", "bivak", "biwak", "biwakschachtel", "bivouac",
    "rifugio", "capanna", "cabane", "hutte", "huette", "koca", "casera", "baita",
    "winterraum", "invernale", "locale", "ricovero", "alpine", "alpe", "della",
    "delle", "dello", "degli", "sezione", "planinska", "zavetisce", "schutzhutte",
    "notunterkunft", "unterstand", "selbstversorgerhutte", "nuovo", "vecchio",
}


# ---------------------------------------------------------------- http helpers

def _json(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(url, timeout=60, tries=3):
    for i in range(tries):
        try:
            return _json(urllib.request.Request(url, headers={"User-Agent": UA}), timeout)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def overpass(query):
    """Return (payload, endpoint). Primary first with backoff, then mirrors."""
    body = ("data=" + urllib.parse.quote(query)).encode()
    last = None

    for i in range(PRIMARY_ATTEMPTS):
        try:
            req = urllib.request.Request(PRIMARY, data=body, headers={"User-Agent": UA})
            return _json(req, 600), PRIMARY
        except Exception as e:
            last = e
            print(f"     primary attempt {i+1}/{PRIMARY_ATTEMPTS}: {e}", file=sys.stderr)
            time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])

    for url in MIRRORS:
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            d = _json(req, 600)
            if d.get("elements"):
                return d, url
            print(f"     {host(url)}: empty result, trying next", file=sys.stderr)
        except Exception as e:
            last = e
            print(f"     {host(url)}: {e}", file=sys.stderr)
            time.sleep(5)

    raise RuntimeError(f"every endpoint failed: {last}")


def host(url):
    return url.split("/")[2]


def load_cache(name, default):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_cache(name, data):
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, name), "w", encoding="utf-8") as f:
        json.dump(data, f)


# ----------------------------------------------------------------- conversions

def commons_url(filename):
    """Commons file title -> URL that always resolves to the current image."""
    if not filename:
        return ""
    f = filename.strip()
    for prefix in ("File:", "Datei:", "Immagine:", "Slika:", "Fichier:"):
        if f.startswith(prefix):
            f = f[len(prefix):]
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(
        f.replace(" ", "_")
    )


def commons_page(file_url):
    """Special:FilePath URL -> the file's description page (licence, author)."""
    return file_url.replace("/wiki/Special:FilePath/", "/wiki/File:")


def osm_image(tag):
    """image=* is free text. Keep it only when a browser can show it inline."""
    v = (tag or "").strip()
    if v.startswith("File:"):
        return commons_url(v)
    m = re.match(r"https?://commons\.wikimedia\.org/wiki/(File:.+)$", v)
    if m:
        return commons_url(urllib.parse.unquote(m.group(1)))
    if re.match(r"https?://", v) and re.search(r"\.(jpe?g|png|webp)(\?|$)", v, re.I):
        return v
    return ""


def to_float(v):
    """OSM ele values are messy: '2 987', '2987 m', '2987,0'."""
    if not v:
        return None
    s = str(v).replace(",", ".").replace("m", "").replace(" ", "").strip()
    s = s.replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def merc_to_lonlat(x, y):
    r = 20037508.342789244
    return x / r * 180, math.degrees(math.atan(math.exp(y / r * math.pi))) * 2 - 90


def lonlat_to_merc(lon, lat):
    r = 20037508.342789244
    return lon / 180 * r, math.log(math.tan(math.radians(90 + lat) / 2)) / math.pi * r


def dist_m(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742000 * math.asin(math.sqrt(a))


def name_tokens(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return {w for w in re.split(r"[^a-z]+", s) if len(w) >= 4 and w not in GENERIC}


def names_match(a, b):
    """Shared distinctive word, allowing German compounds (Anna ~ Annahütte)."""
    ta, tb = name_tokens(a), name_tokens(b)
    return any(x in y or y in x for x in ta for y in tb)


def classify(tags):
    """(is_bivouac, evidence). Evidence stays in the CSV so rows are auditable."""
    ev = []
    if tags.get("shelter_type") == "basic_hut":
        ev.append("shelter_type")
    if tags.get("tourism") == "wilderness_hut":
        ev.append("wilderness_hut")
    name = " ".join(v for k, v in tags.items() if k == "name" or k.startswith("name:"))
    if NAME_RE.search(name):
        ev.append("name")
    return bool(ev), "+".join(ev)


def reject(tags):
    """Reason to leave a shelter off the map, or '' to keep it."""
    if not tags.get("name"):
        return "unnamed"
    if tags.get("tourism") == "hunting_lodge" or HUNTING_RE.search(tags["name"]):
        return "hunting"
    if tags.get("access") in ("private", "no"):
        return "private"
    if tags.get("disused") == "yes" or tags.get("abandoned") == "yes":
        return "disused"
    return ""


# --------------------------------------------------------------------- fetches

def fetch_country(cc, refresh=False):
    name = f"{cc}.json"
    if not refresh:
        d = load_cache(name, None)
        if d is not None:
            print(f"     {cc}: {len(d['elements']):5d}  (cached)", file=sys.stderr)
            return d

    d, src = overpass(QUERY.format(cc=cc, bbox=BBOX.get(cc, "")))
    save_cache(name, d)
    stamp = d.get("osm3s", {}).get("timestamp_osm_base", "?")
    print(f"     {cc}: {len(d['elements']):5d}  via {host(src)}  data {stamp}",
          file=sys.stderr)
    return d


def wikidata_images(qids):
    """qid -> Commons filename, from P18. 50 ids per request is the API limit."""
    out = {}
    qids = sorted(set(q for q in qids if re.fullmatch(r"Q\d+", q or "")))
    for i in range(0, len(qids), 50):
        chunk = qids[i:i + 50]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
               "&props=claims&ids=" + "|".join(chunk))
        try:
            d = get(url)
        except Exception as e:
            print(f"     wikidata chunk {i}: {e}", file=sys.stderr)
            continue
        for qid, ent in (d.get("entities") or {}).items():
            for claim in (ent.get("claims") or {}).get("P18", []):
                try:
                    out[qid] = claim["mainsnak"]["datavalue"]["value"]
                    break
                except (KeyError, TypeError):
                    pass
        time.sleep(0.4)
        print(f"     wikidata {min(i+50, len(qids))}/{len(qids)}", file=sys.stderr)
    return out


def c2c_waypoints(refresh=False):
    """Every hut, shelter and bivouac camptocamp knows in the bbox, as dicts."""
    if not refresh:
        cached = load_cache("c2c.json", None)
        if cached is not None:
            print(f"     c2c: {len(cached):5d}  (cached)", file=sys.stderr)
            return cached

    x0, y0 = lonlat_to_merc(C2C_BBOX[0], C2C_BBOX[1])
    x1, y1 = lonlat_to_merc(C2C_BBOX[2], C2C_BBOX[3])
    bbox = ",".join(str(int(v)) for v in (x0, y0, x1, y1))
    out, offset = [], 0
    while True:
        d = get(f"{C2C_API}/waypoints?wtyp={C2C_TYPES}&bbox={bbox}"
                f"&limit=100&offset={offset}", timeout=60)
        for doc in d["documents"]:
            try:
                x, y = json.loads(doc["geometry"]["geom"])["coordinates"][:2]
            except (KeyError, TypeError, ValueError):
                continue
            lon, lat = merc_to_lonlat(x, y)
            country = ""
            for a in doc.get("areas") or []:
                if a.get("area_type") == "country":
                    titles = {l["lang"]: l["title"] for l in a.get("locales", [])}
                    country = C2C_COUNTRY.get(titles.get("en", ""), "")
            locs = doc.get("locales") or [{}]
            out.append({
                "id": doc["document_id"],
                "type": doc.get("waypoint_type", ""),
                "name": (locs[0].get("title") or "").strip(),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "ele": doc.get("elevation"),
                "country": country,
            })
        offset += 100
        print(f"     c2c {min(offset, d['total'])}/{d['total']}", file=sys.stderr)
        if offset >= d["total"] or not d["documents"]:
            break
        time.sleep(0.3)
    save_cache("c2c.json", out)
    return out


def c2c_image(doc_id, name, details):
    """(thumb_url, page_url) for the best photo of a c2c waypoint, or ('', '')."""
    key = str(doc_id)
    if key not in details:
        try:
            d = get(f"{C2C_API}/waypoints/{doc_id}", timeout=30, tries=2)
            details[key] = [
                {"id": im["document_id"], "file": im.get("filename") or "",
                 "title": ((im.get("locales") or [{}])[0].get("title") or "")}
                for im in (d.get("associations") or {}).get("images", [])
            ]
        except Exception as e:
            print(f"     c2c {doc_id}: {e}", file=sys.stderr)
            return "", ""
        time.sleep(0.2)

    images = [im for im in details[key] if im["file"]]
    if not images:
        return "", ""
    # A waypoint's photos include the approach, the view and the toilet. Prefer
    # one whose caption names the shelter.
    best = next((im for im in images if names_match(im["title"], name)
                 or NAME_RE.search(im["title"])), images[0])
    stem, ext = os.path.splitext(best["file"])
    return (f"https://media.camptocamp.org/c2corg-active/{stem}MI{ext}",
            f"https://www.camptocamp.org/images/{best['id']}")


def commons_nearby(lat, lon, name="", radius=300, limit=20):
    """Georeferenced Commons files near a point.

    Raw proximity is noisy: within 300 m of a bivouac you mostly get wildflowers
    and summit panoramas. So rank by title instead of taking the first hit, and
    return (good, weak) so the caller can decide how much noise to accept.
    """
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
           f"&list=geosearch&gscoord={lat}|{lon}&gsradius={radius}"
           f"&gslimit={limit}&gsnamespace=6")
    try:
        d = get(url, timeout=30, tries=2)
    except Exception:
        return [], []

    titles = [p["title"] for p in (d.get("query") or {}).get("geosearch", [])]
    words = [w.lower() for w in re.split(r"\W+", name) if len(w) > 3]
    good, weak = [], []
    for t in titles:
        low = t.lower()
        if (words and any(w in low for w in words)) or NAME_RE.search(low) \
                or re.search(r"(koča|hut|hütte|rifugio|capanna|cabane)", low):
            good.append(t)
        else:
            weak.append(t)
    return good, weak


# ------------------------------------------------------------------------ steps

def build_rows(els, keep_all):
    rows, seen, dropped = [], set(), {}
    for e in els:
        t = e.get("tags", {})
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue

        is_biv, ev = classify(t)
        if not is_biv and not keep_all:
            continue
        why = reject(t)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue

        # One physical shelter can be mapped as a node and as a building way.
        key = (t.get("name", ""), round(float(lat), 4), round(float(lon), 4))
        if key in seen:
            continue
        seen.add(key)

        img = osm_image(t.get("image"))
        src = "osm:image" if img else ""
        if not img and t.get("wikimedia_commons", "").startswith("File:"):
            img = commons_url(t["wikimedia_commons"])
            src = "osm:commons"

        rows.append({
            "country": e.get("_cc", ""),
            "name": t.get("name", ""),
            "alt_name": t.get("alt_name", ""),
            "is_bivouac": is_biv,
            "evidence": ev,
            "lat": round(float(lat), 6),
            "lon": round(float(lon), 6),
            "ele_m": to_float(t.get("ele")) or "",
            "capacity": t.get("capacity") or t.get("beds", ""),
            "operator": t.get("operator", ""),
            "tourism": t.get("tourism", ""),
            "shelter_type": t.get("shelter_type", ""),
            "website": t.get("website") or t.get("contact:website", ""),
            "wikidata": t.get("wikidata", ""),
            "image_url": img,
            "image_page": commons_page(img) if "commons.wikimedia.org" in img else img,
            "image_source": src,
            "image_alt": "",
            "c2c_id": "",
            "c2c_url": "",
            "c2c_dist_m": "",
            "osm_type": e["type"],
            "osm_id": e["id"],
            "osm_url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
        })
    print(f"     {len(rows)} kept, dropped {dropped}", file=sys.stderr)
    return rows


def dedupe_nearby(rows, radius=150):
    """Same name within 150 m is the same shelter mapped twice. Keep the richer."""
    def richness(r):
        return sum(1 for k in ("ele_m", "capacity", "operator", "website",
                               "wikidata", "image_url") if r[k])

    kept, by_name = [], {}
    for r in sorted(rows, key=richness, reverse=True):
        k = r["name"].strip().lower()
        if any(dist_m(r["lat"], r["lon"], o["lat"], o["lon"]) < radius
               for o in by_name.get(k, [])):
            continue
        by_name.setdefault(k, []).append(r)
        kept.append(r)
    if len(kept) != len(rows):
        print(f"     merged {len(rows) - len(kept)} near-duplicates", file=sys.stderr)
    return kept


def match_c2c(rows, waypoints, countries):
    """Attach camptocamp ids to OSM rows; add c2c bivouacs OSM does not have.

    A shared distinctive name word allows up to 300 m of disagreement between
    the two databases. Without one, only a c2c bivouac or shelter within 80 m
    counts, so a bivouac never inherits the staffed rifugio next door.
    """
    pairs = []
    for i, r in enumerate(rows):
        for w in waypoints:
            if abs(w["lat"] - r["lat"]) > 0.003 or abs(w["lon"] - r["lon"]) > 0.005:
                continue
            d = dist_m(r["lat"], r["lon"], w["lat"], w["lon"])
            same = names_match(r["name"], w["name"])
            if (same and d <= 300) or (d <= 80 and w["type"] in ("bivouac", "shelter")):
                pairs.append((0 if same else 1, d, i, w))

    used_rows, used_c2c = set(), set()
    for _, d, i, w in sorted(pairs, key=lambda p: (p[0], p[1])):
        if i in used_rows or w["id"] in used_c2c:
            continue
        used_rows.add(i)
        used_c2c.add(w["id"])
        rows[i]["c2c_id"] = w["id"]
        rows[i]["c2c_url"] = f"https://www.camptocamp.org/waypoints/{w['id']}"
        rows[i]["c2c_dist_m"] = round(d)
        if not rows[i]["ele_m"] and w["ele"]:
            rows[i]["ele_m"] = float(w["ele"])

    added = 0
    for w in waypoints:
        if (w["id"] in used_c2c or w["type"] not in ("bivouac", "shelter")
                or w["country"] not in countries or not w["name"]):
            continue
        # Unmatched but near an OSM row: probably the same place under another
        # name. Better to miss one than to draw two pins on top of each other.
        if any(abs(r["lat"] - w["lat"]) < 0.002 and abs(r["lon"] - w["lon"]) < 0.003
               for r in rows):
            continue
        rows.append({
            "country": w["country"], "name": w["name"], "alt_name": "",
            "is_bivouac": True, "evidence": f"c2c:{w['type']}",
            "lat": w["lat"], "lon": w["lon"], "ele_m": float(w["ele"] or 0) or "",
            "capacity": "", "operator": "", "tourism": "", "shelter_type": "",
            "website": "", "wikidata": "", "image_url": "", "image_page": "",
            "image_source": "", "image_alt": "", "c2c_id": w["id"],
            "c2c_url": f"https://www.camptocamp.org/waypoints/{w['id']}",
            "c2c_dist_m": 0, "osm_type": "", "osm_id": "", "osm_url": "",
        })
        added += 1
    print(f"     c2c matched {len(used_rows)}, added {added} c2c-only", file=sys.stderr)


def web_record(r):
    """What the map needs, empty fields omitted to keep the file small."""
    img = r["image_url"]
    if "commons.wikimedia.org/wiki/Special:FilePath/" in img:
        img += "?width=480"       # Commons originals run to 10 MB
    rec = {
        "name": r["name"],
        "lat": r["lat"],
        "lng": r["lon"],
        "altitude": int(r["ele_m"]) if r["ele_m"] else None,
        "country": r["country"],
        "capacity": r["capacity"],
        "operator": r["operator"],
        "picture": img,
        "picturePage": r["image_page"],
        "pictureSource": r["image_source"].split(":")[0],
        "website": r["website"],
        "c2cUrl": r["c2c_url"],
        "osmUrl": r["osm_url"],
    }
    return {k: v for k, v in rec.items() if v not in ("", None)}


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", nargs="+", default=COUNTRIES)
    ap.add_argument("--no-c2c", action="store_true", help="skip camptocamp.org")
    ap.add_argument("--commons", action="store_true",
                    help="geosearch Commons for bivouacs still lacking a picture")
    ap.add_argument("--commons-radius", type=int, default=300)
    ap.add_argument("--commons-loose", action="store_true",
                    help="also accept nearby files whose title does not mention "
                         "the shelter (noisy: flowers, panoramas)")
    ap.add_argument("--refresh", action="store_true", help="ignore the caches")
    ap.add_argument("--all", action="store_true",
                    help="keep staffed huts too, not just bivouacs")
    args = ap.parse_args()

    print("1/5  Overpass", file=sys.stderr)
    els = []
    for cc in args.countries:
        try:
            d = fetch_country(cc, args.refresh)
        except Exception as e:
            print(f"     {cc}: FAILED, skipping ({e})", file=sys.stderr)
            continue
        for e in d["elements"]:
            e["_cc"] = cc
        els.extend(d["elements"])
    print(f"     {len(els)} raw elements", file=sys.stderr)

    print("2/5  classify", file=sys.stderr)
    rows = dedupe_nearby(build_rows(els, args.all))

    if not args.no_c2c:
        print("3/5  camptocamp", file=sys.stderr)
        match_c2c(rows, c2c_waypoints(args.refresh), set(args.countries))
    else:
        print("3/5  camptocamp skipped", file=sys.stderr)

    print("4/5  pictures", file=sys.stderr)
    p18 = wikidata_images([r["wikidata"] for r in rows if r["wikidata"]])
    for r in rows:
        if not r["image_url"] and r["wikidata"] in p18:
            r["image_url"] = commons_url(p18[r["wikidata"]])
            r["image_page"] = commons_page(r["image_url"])
            r["image_source"] = "wikidata:P18"

    todo = [r for r in rows if not r["image_url"] and r["c2c_id"]]
    if todo:
        details = {} if args.refresh else load_cache("c2c_images.json", {})
        print(f"     camptocamp photos for {len(todo)} rows", file=sys.stderr)
        for i, r in enumerate(todo, 1):
            thumb, page = c2c_image(r["c2c_id"], r["name"], details)
            if thumb:
                r["image_url"], r["image_page"], r["image_source"] = thumb, page, "c2c"
            if i % 100 == 0:
                save_cache("c2c_images.json", details)
                print(f"     {i}/{len(todo)}", file=sys.stderr)
        save_cache("c2c_images.json", details)

    if args.commons:
        todo = [r for r in rows if not r["image_url"]]
        print(f"     commons geosearch for {len(todo)} rows"
              f" (~{len(todo) * 0.35 / 60:.0f} min)", file=sys.stderr)
        for i, r in enumerate(todo, 1):
            good, weak = commons_nearby(r["lat"], r["lon"], r["name"],
                                        args.commons_radius)
            if good:
                r["image_url"] = commons_url(good[0])
                r["image_source"] = f"commons:name-match<{args.commons_radius}m"
                r["image_alt"] = " | ".join(commons_url(t) for t in good[1:4])
            elif weak and args.commons_loose:
                # Title says nothing about a shelter. Could be the scenery, the
                # summit, or a flower. Flagged so you can filter it back out.
                r["image_url"] = commons_url(weak[0])
                r["image_source"] = f"commons:proximity-only<{args.commons_radius}m"
                r["image_alt"] = " | ".join(commons_url(t) for t in weak[1:4])
            if r["image_url"]:
                r["image_page"] = commons_page(r["image_url"])
            time.sleep(0.25)
            if i % 100 == 0:
                print(f"     {i}/{len(todo)}", file=sys.stderr)

    print("5/5  write", file=sys.stderr)
    rows.sort(key=lambda r: (r["country"], -(r["ele_m"] or 0), r["name"]))

    if not rows:
        print("nothing to write", file=sys.stderr)
        return

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "bivouacs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with open(os.path.join(OUT, "bivouacs.geojson"), "w", encoding="utf-8") as f:
        json.dump({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")},
            } for r in rows],
        }, f, ensure_ascii=False)

    with open(WEB_JSON, "w", encoding="utf-8") as f:
        json.dump([web_record(r) for r in rows], f, ensure_ascii=False,
                  separators=(",", ":"))

    with_img = sum(1 for r in rows if r["image_url"])
    print(f"\n{len(rows)} rows, {with_img} with a picture "
          f"({100 * with_img // len(rows)}%)", file=sys.stderr)
    for cc in args.countries:
        n = sum(1 for r in rows if r["country"] == cc)
        m = sum(1 for r in rows if r["country"] == cc and r["image_url"])
        c = sum(1 for r in rows if r["country"] == cc and r["c2c_url"])
        print(f"  {cc}: {n:5d}  pictures {m:4d}  c2c {c:4d}", file=sys.stderr)


if __name__ == "__main__":
    main()
