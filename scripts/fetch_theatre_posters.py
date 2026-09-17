#!/usr/bin/env python3
"""Best-effort theatre posters from theatre.art.bg (the Sofia theatre aggregator).

Each day page — https://theatre.art.bg/?date=YYYY-MM-DD&city=20 — lists that
day's performances, each with a thumbnail hosted on theatre.peakview.bg. We walk
the app's date window, harvest (title -> image) pairs, match titles back to the
SHOWS in index.html, upgrade the thumbnail to the largest size the CDN serves,
and write theatre_posters.json: {showId: "https://...poster.jpg"}.

Coverage is expected to be partial; every miss keeps the app's generated SVG.

    python3 scripts/fetch_theatre_posters.py            # -> theatre_posters.json

A run that finds nothing keeps any previous theatre_posters.json.
"""
import json, os, re, time, ssl, sys, pathlib, urllib.request
try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl._create_unverified_context()
try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install beautifulsoup4 lxml")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 SofiaGleda/1.0"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT  = ROOT / "theatre_posters.json"
DAY_URL = "https://theatre.art.bg/?date={date}&city=20"      # city 20 = Sofia
POLITE = 0.4

# Hand-verified theatre posters pulled directly from each theatre's own site
# (more reliable than the theatre.art.bg aggregator). These always survive a
# refresh: they win over scraped values and are never dropped by a lean run.
# Add to this map as more venues are harvested and confirmed by hand.
SEED = {
    "albion": "https://mlt.bg/img/upl/4/images/ALBION-1080x1350%281%29.jpg",
    "ariya-na-sapernicata": "https://nationaltheatre.bg/storage/shows/310b6ce43729762add40dfcb142f9b42cc5.jpg",
    "az-plashtam": "https://nationaltheatre.bg/storage/shows/2686614cf0b5ee552142e6da474008d8e6.jpg",
    "az-sam-sofia": "https://iamsofia.bg/wp-content/uploads/2025/08/IamSofia2025.webp",
    "bashtata": "https://nationaltheatre.bg/storage/shows/6635392173eb94e9023a68ea3568f7e031.jpg",
    "bebe-na-borda": "https://nationaltheatre.bg/storage/shows/251bcdc7f27ff4b6004406d0e1d782767b6.jpeg",
    "beket": "https://theatre.art.bg/img/photos/BIG16977942351394359146_3654890174830858_4834048547935270464_n.jpg",
    "belezhkite": "https://nationaltheatre.bg/storage/shows/1342cf8335f4de0391935a4ded9424156ca.jpg",
    "bezkraynite-sceni": "https://nationaltheatre.bg/storage/shows/270c39921ed36ba07d58d3fa60876019dc8.jpg",
    "biologichen-otpadak": "https://theatre.art.bg/img/photos/BIG17570573993481702627_10227142027367746_4187715706622476904_n.jpg",
    "bogat-na-kasapnicata": "https://nationaltheatre.bg/storage/shows/27f50f3c6292c4a6d24e0ed261b4a7af41.jpg",
    "bozhe-moy": "https://nationaltheatre.bg/storage/shows/228e9a44e0b5ae7f065a4d01f40f437aae5.jpg",
    "bremenskite": "https://theatrevazrajdane.bg/wp-content/uploads/2025/02/tv-bremenskite-muzikanti-web-1.png",
    "bring-the-heat": "https://toplocentrala.bg/attachments/Event/913/main/IMG-1813_thumb-detail.jpeg",
    "bul-terier": "https://nationaltheatre.bg/storage/shows/301e9dc7c16fa8de39ea2dfd994d13c052b.jpg",
    "chastici-zhena": "https://nationaltheatre.bg/storage/shows/184760182c2015b3b856a5b5efc5c1c7728.jpg",
    "cinelibri": "https://www.cinelibri.com/wp-content/uploads/2026/06/website-key-visual-2026.jpg",
    "creve-coeur": "https://nationaltheatre.bg/storage/shows/1799688cd8ab0a90d683711f82ede27d29c.jpg",
    "cvetat-na-dalbokite": "https://nationaltheatre.bg/storage/shows/560c77f9d22c89d9ebe98356b90a3b2ca9.jpg",
    "devetdeset": "https://mlt.bg/img/upl/4/images/90-1080x1350_800.jpg",
    "dishay": "https://theatre.art.bg/img/photos/BIG16666081644LUNGS-1080x1350-02-min.jpg",
    "doktor-dulital": "https://theatre.art.bg/img/photos/BIG17875687681PLOVDIV%20SMALL.JPG",
    "dostoevski": "https://theatre.art.bg/img/photos/BIG15107443871_DSC5141s.jpg",
    "drakoncheto": "https://theatre.art.bg/img/photos/BIG17371173591drakonche%20sajttttt.jpg",
    "duhat-na-poeta": "https://nationaltheatre.bg/storage/shows/29e9cd6d03ee739375a56fbddea9031b18.jpg",
    "dvama-v-delirium": "https://theatrevazrajdane.bg/wp-content/uploads/2026/08/POSTER_FINAL-scaled.jpg",
    "dve": "https://nationaltheatre.bg/storage/shows/487876138be5d3d696bd83e4358fcce6d4.jpg",
    "dvuboy": "https://nationaltheatre.bg/storage/shows/1394e8f8b3bea93a34551b7a0f447ccc4fb.jpg",
    "edni-momicheta": "https://nationaltheatre.bg/storage/shows/163df9dd82227cadc10e2521afc85788037.jpg",
    "ee": "https://mlt.bg/img/upl/4/images/EE_facbook-post_1200x630.png",
    "elementarnite-chastici": "https://nationaltheatre.bg/storage/shows/24055be39a589fe1342e31e17bf38d45313.png",
    "esenna-sonata": "https://nationaltheatre.bg/storage/shows/308d7481da675e6fc24d8aedce9153133d3.jpg",
    "falshiviyat-orkestar": "https://theatre.art.bg/img/photos/BIG17875782871LAMUET%20SMALL.JPG",
    "feyata-vanilia": "https://mlt.bg/img/upl/4/images/poster_70x100.png",
    "fizika-na-tagata": "https://nationaltheatre.bg/storage/shows/31703b62684f451aab0e18e7d9a1efb3d29.jpg",
    "frankenshtayn": "https://theatre.art.bg/img/photos/BIG17875673561frank%20small.JPG",
    "glembaevi": "https://nationaltheatre.bg/storage/shows/2981e88a4b16d1a9cb7be88354f72b8a124.jpg",
    "golemanov": "https://nationaltheatre.bg/storage/shows/176f35e9dc2b52c3a1a6638c4fa59e68d83.jpg",
    "golyamata-shapka": "https://theatre.art.bg/img/photos/BIG17875682471STARA%20SMALL.jpg",
    "haos": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_407/_MG_5257.jpg?f=59843",
    "hipotetichno": "https://nationaltheatre.bg/storage/shows/24132fb1a25ff08baf44d5d3e5338539f80.jpg",
    "hitranka": "https://cmart.info/wp-content/uploads/2025/08/ekranna-snimka-2025-08-30-113117.png",
    "idealniyat-mazh": "https://nationaltheatre.bg/storage/shows/6996a893b4f8a7a35d7b78a9a2e5a9b480.jpg",
    "kakto-v-nay-dobrite-dni": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_646/1024-dnite.jpg?f=71818",
    "kaligula": "https://nationaltheatre.bg/storage/shows/72ff76e9c1ed157f4171f6aa17cbeb54fc.jpg",
    "karakondzhul": "https://nationaltheatre.bg/storage/shows/95b13779adcb0763a200f6dfb599e3df20.jpg",
    "kaspar": "https://toplocentrala.bg/attachments/Event/1039/main/website-kaspar_thumb-detail.jpg",
    "kogato-gram-udari": "https://nationaltheatre.bg/storage/shows/31ee56ede2054fad86998cf406cadb6fa7.jpg",
    "kolko-e-vazhno": "https://nationaltheatre.bg/storage/shows/49ea7c15eefd9744e74e0c69dbcb971aac.jpg",
    "kontrabasat": "https://nationaltheatre.bg/storage/shows/50707ca227f4394f77c0547d2427388bba.jpg",
    "kovarstvo-i-lyubov": "https://nationaltheatre.bg/storage/shows/22610438f766c5d123deddda7dbf8d92790.jpg",
    "kuklen-dom-2": "https://nationaltheatre.bg/storage/shows/2561600b39ab0e47b4b261fa228cb3b21e4.jpg",
    "lamyata": "https://theatre.art.bg/img/photos/BIG17875685451Lamiata%20SMALL.jpg",
    "lisicheta": "https://nationaltheatre.bg/storage/shows/34074ecc8ef6d79d6572a4dc51db7cf3b1.jpg",
    "malkata-angliya": "https://nationaltheatre.bg/storage/shows/304ba9f9578b4e919b9c1cbafd1a2dc445d.jpg",
    "medeya": "https://nationaltheatre.bg/storage/shows/257bd0afd723a7d282d77ab2815962c295a.jpg",
    "merilin": "https://nationaltheatre.bg/storage/shows/2911c4ca4fb8edd77e88fd400263f75f18d.jpg",
    "mrak-na-kraya": "https://nationaltheatre.bg/storage/shows/26284b4e604b69d94851c359f8c43c6d40d.jpg",
    "narodat-na-vazov": "https://nationaltheatre.bg/storage/shows/155ccecf955f6275a6fc747689cdbcc01c3.jpg",
    "nechovek": "https://nationaltheatre.bg/storage/shows/266ce56b64a53de02d92ec0ac3db56dfd05.png",
    "nevedenie": "https://nationaltheatre.bg/storage/shows/1877669a358be68c4204555f0d77c1be439.jpg",
    "nyakoy-shte-doyde": "https://nationaltheatre.bg/storage/shows/2603f23f6b3ae92033b7ed720ab98f867a0.jpg",
    "o-ti-koyato": "https://nationaltheatre.bg/storage/shows/1241aa62a18a5ff9f7337654ffdae24588e.jpg",
    "oasis-screening": "https://softwareforcinema.com/f/movies/q/7/7ba25f6fad48c2ba6390f9e5800fca33.jpeg",
    "ob-varzan": "https://theatrevazrajdane.bg/wp-content/uploads/2024/11/otvarzan-tv-web.jpg",
    "obiknoveno-chudo": "https://theatrevazrajdane.bg/wp-content/uploads/2025/05/tv-obiknoveno-chudo-web-1.png",
    "obir": "https://nationaltheatre.bg/storage/shows/52aafe4beb2d213586c3445ddeb0dbe014.jpg",
    "opit-za-letene": "https://nationaltheatre.bg/storage/shows/6445859b9d3ec75d4798fb8a337729dde7.jpg",
    "orazhiyata-i-chovekat": "https://nationaltheatre.bg/storage/shows/25824dca0828c2bbb772cd6187708a6188f.jpg",
    "orfey": "https://nationaltheatre.bg/storage/shows/183e0072862bef14a4f8f164f51ba572664.jpg",
    "otmyana": "https://theatrevazrajdane.bg/wp-content/uploads/2024/02/otmiana-plakat-web-3.png",
    "panair-kukli": "https://theatre.art.bg/img/photos/BIG17875751581705717946_1605601208242560_4278550035180313176_n.jpg",
    "panair-na-kuklite": "https://sofiapuppet.com/img/upl/10/images/PF%2026%20STORY%20ZA%20FACE%281%29.jpg",
    "patyat-kam-afrodita": "https://nationaltheatre.bg/storage/shows/53a463f058d873ed6b73388f7efcd74425.jpg",
    "petrovi": "https://nationaltheatre.bg/storage/shows/21896537f742a18038a69548af7cd69b356.jpg",
    "piano-v-trevata": "https://nationaltheatre.bg/storage/shows/158764a6bd6d8367d2c403c599dd82a9cb6.jpg",
    "plach-na-angel": "https://nationaltheatre.bg/storage/shows/13867a3e8586b749968e403bc9d54bff14c.jpg",
    "posledna-stapka": "https://nationaltheatre.bg/storage/shows/2725309d4fe1f1d4a6ecbd2e7f6f8841e36.jpg",
    "posledniyat-strasten": "https://sofiatheatre.eu/uploads/repertoires/oEtQYCVZpunNHjLh1kDbg3Bo2IXWuVWr8JvIBk2U.jpg",
    "razhodka-gogol": "https://nationaltheatre.bg/storage/shows/214a1c8004f1bd24ea0758bc597ec1878ea.jpg",
    "razlichniyat": "https://nationaltheatre.bg/storage/shows/30210f8b3a9346b4a28098db8fbb37489d0.jpg",
    "rozenkranc": "https://nationaltheatre.bg/storage/shows/2743f1aef51ccbe2343ab091a8a64446b58.jpg",
    "sazvezdiya": "https://nationaltheatre.bg/storage/shows/25283225c5692fd71d7bbd6af9fcc8521d4.jpg",
    "sequence": "https://toplocentrala.bg/attachments/Event/1161/main/772685810-1654273536698663-7681475029018765099-n_thumb-detail.jpg",
    "skaperniкat": "https://nationaltheatre.bg/storage/shows/312798660a7f1f102cf325cafdc338e6995.jpg",
    "slon-v-stayata": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_609/slon-1024x1024.jpg?f=48624",
    "sluchayat-dzhem": "https://zadkanala.bg/sites/default/files/styles/large750x/public/DSC09007_0_0.jpg?itok=PdIUm8t7",
    "sneakpeak": "https://toplocentrala.bg/attachments/Event/1165/main/viber-image-2026-09-01-14-25-35-586_thumb-detail.jpg",
    "sneakpeak-fest": "https://toplocentrala.bg/attachments/Event/1165/main/viber-image-2026-09-01-14-25-35-586.jpg",
    "snow-white": "https://toplocentrala.bg/attachments/Event/1154/main/fuck-it-heart-rage_thumb-detail.jpg",
    "strah-za-opitomyavane": "https://nationaltheatre.bg/storage/shows/132324584307fe7d89ddf83944d6133d6d8.jpg",
    "svrahpredel": "https://nationaltheatre.bg/storage/shows/318fe759e33c840889582fff86c5d2c47bc.jpg",
    "tam": "https://nationaltheatre.bg/storage/shows/297f91e4c1b0644874b095b1a35b36a9039.jpg",
    "tartyuf": "https://zadkanala.bg/sites/default/files/91c15ab6-415b-4c8a-8307-49e10336a78e.jpg",
    "teatar": "https://nationaltheatre.bg/storage/shows/29394ef0e98855188a3c47a6e5af7d2edb1.jpg",
    "teatar-lyubov-moya": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_413/_MG_8079.jpg?f=68755",
    "teremin": "https://nationaltheatre.bg/storage/shows/41f9b657006d660b01f90f76d2bdee9134.jpg",
    "tochka": "https://theatre.art.bg/img/photos/BIG17436834091487881228_1219784123490939_477441199242770342_n.jpg",
    "tortila-flet": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_564/1024-1024.jpeg?f=76208",
    "trima-krale": "https://theatrevazrajdane.bg/wp-content/uploads/2026/01/YB-Posters-1000x700-3mmBleed-05-pdf.jpg",
    "uroci": "https://sofiatheatre.eu/uploads/repertoires/oTFkly25gdxzbfYhfUK7obSoSNP3A8ry5ggaAXQ6.jpg",
    "vakhanki": "https://nationaltheatre.bg/storage/shows/319b661e20155c459b8125446c1ba61774d.jpg",
    "velika": "https://zadkanala.bg/sites/default/files/styles/large750x/public/IMG_3213.jpeg?itok=uKtP9ESH",
    "velikdensko-vino": "https://nationaltheatre.bg/storage/shows/19501372a734dda95066d315e18e488711f.jpg",
    "venecianskiyat": "https://nationaltheatre.bg/storage/shows/24510bc857a7f4c179e91cadbde49dee89c.jpg",
    "vinovniyat": "https://nationaltheatre.bg/storage/shows/45cf575b1e23962e49130a0c26148fb904.jpg",
    "violonchelo": "https://nationaltheatre.bg/storage/shows/133c0e4e8f65f5535288de5e300cd133873.jpg",
    "vlyubenite": "https://nationaltheatre.bg/storage/shows/30387d2f47229c0443a7011168ba87352fc.jpg",
    "vzeto-ot-interneta": "https://toplocentrala.bg/attachments/Event/1098/main/Plakat-Marion-bleed-jpg_thumb-detail.jpg",
    "za-yavleniyata": "https://theatre.art.bg/img/photos/BIG17368357254Messenger_creation_21B2FAB2-DD7A-4B53-8A44-F8EEB16691DE.jpeg",
    "zaeshka-dupka": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_425/photo%20Simon%20(52).jpg?f=50235",
    "zasekreteno": "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_533/1024x1024.jpg?f=45193",
    "zhenata-konbini": "https://nationaltheatre.bg/storage/shows/300066adc60871ffe3e80cdbee9a590d8a4.jpg",
    "zhirafi": "https://theatre.art.bg/img/photos/BIG17875776561Girafes%20Xirrquiteula%20(1).jpg",
}


def grab(data, name):
    i = data.find("const " + name + "="); j = data.find("=", i) + 1
    depth = 0; start = None
    for k in range(j, len(data)):
        c = data[k]
        if c in "[{":
            if depth == 0: start = k
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(data[start:k + 1])


def norm(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[„“”\"'’«»\.\,\!\?\:\;\-–—\(\)\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get(url, binary=False):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            return r.status if binary else r.read().decode("utf-8", "replace")
    except Exception:
        return None


def full_size(thumb, pattern):
    """Apply the resolved size pattern to a `150_`-prefixed thumbnail URL."""
    if pattern == "strip":
        return re.sub(r"/(\d+)_([^/]+)$", r"/\2", thumb)
    if pattern.isdigit():
        return re.sub(r"/(\d+)_([^/]+)$", rf"/{pattern}_\2", thumb)
    return thumb                                              # "keep"


def resolve_pattern(sample):
    """Probe once for the biggest size the CDN actually serves for this thumb."""
    for pat in ("strip", "800", "500", "300"):
        cand = full_size(sample, pat)
        if cand != sample and get(cand, binary=True) == 200:
            return pat
        time.sleep(0.2)
    return "keep"


def harvest(html):
    """Return [(title, thumb_url)] from a day page."""
    soup = BeautifulSoup(html, "lxml")
    pairs = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if "peakview.bg" not in src:
            continue
        src = ("https:" + src) if src.startswith("//") else src
        # title: prefer alt, else nearest anchor text, else nearest heading
        title = (img.get("alt") or "").strip()
        if not title:
            a = img.find_parent("a") or img.find_next("a")
            if a:
                title = a.get_text(" ", strip=True)
        if 2 < len(title) < 160:
            pairs.append((title, src))
    return pairs


def main():
    data = HTML.read_text(encoding="utf-8").split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]
    shows = grab(data, "SHOWS")
    src_all = HTML.read_text(encoding="utf-8")
    win = re.search(r'"?window"?\s*:\s*\{\s*"?from"?\s*:\s*"(\d{4}-\d\d-\d\d)"\s*,\s*"?to"?\s*:\s*"(\d{4}-\d\d-\d\d)"', src_all)
    if not win:
        sys.exit("could not find snapshot window")
    import datetime as dt
    d0, d1 = dt.date.fromisoformat(win.group(1)), dt.date.fromisoformat(win.group(2))
    days = [(d0 + dt.timedelta(days=i)) for i in range((d1 - d0).days + 1)]

    title_to_id = {}
    for s in shows:
        title_to_id[norm(s.get("title"))] = s["id"]
        if s.get("titleEn"):
            title_to_id[norm(s["titleEn"])] = s["id"]

    out, pattern = {}, None
    for day in days:
        if len(out) >= len(title_to_id):
            break                                             # got everything
        page = get(DAY_URL.format(date=day.isoformat()))
        if not page:
            continue
        for title, thumb in harvest(page):
            sid = title_to_id.get(norm(title))
            if not sid or sid in out:
                continue
            if pattern is None:
                pattern = resolve_pattern(thumb)
                print(f"  poster size pattern: {pattern}")
            out[sid] = full_size(thumb, pattern)
            print(f"  {sid:22s} -> {out[sid][:72]}")
        time.sleep(POLITE)

    # Merge: previously-harvested posters (base) < this run's scrape < hand-verified
    # SEED (always wins). This way a lean run never loses coverage and the verified
    # posters survive every refresh.
    merged = {}
    if OUT.exists():
        try:
            merged.update({k: v for k, v in json.load(open(OUT, encoding="utf-8")).items() if v})
        except Exception:
            pass
    merged.update(out)
    merged.update(SEED)
    json.dump(merged, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nDONE: {len(out)}/{len(shows)} scraped, {len(merged)} total (with seed) -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
