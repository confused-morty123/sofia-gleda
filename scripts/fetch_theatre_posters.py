#!/usr/bin/env python3
"""Sofia Gleda — real poster art for the theatre listings.

The previous version looked for thumbnails on theatre.art.bg's *day* pages.
Those pages carry no images at all, which is why it reported 0/118 every week.
Posters do exist, in two places, and this fetches both:

  1. programata.bg — a WordPress site whose "Постановки" category holds one post
     per production with a portrait poster as its featured image (typically
     735x1050). Read through the REST API rather than scraped: titles and image
     URLs arrive as JSON, so there is no markup to break.

  2. theatre.art.bg — each production has a page whose og:image is the poster on
     theatre.peakview.bg. The slug in the URL is decorative: the site resolves
     /<anything>_<productionId>_<theatreId>_<cityId>, and the day pages already
     hand us those ids inside their kupi-bilet.php links. So we harvest ids from
     the day pages and read one production page per still-missing show.

Writes theatre_posters.json: {showId: "https://…"}. Results are MERGED with the
previous file — a source being down can never take a poster away, it can only
add. Unmatched shows keep the app's generated SVG art.

    python3 scripts/fetch_theatre_posters.py
    python3 scripts/fetch_theatre_posters.py --only uroci --verbose
    python3 scripts/fetch_theatre_posters.py --source programata --dry-run

A title is only accepted on an exact normalised match, or on a containment
match long enough not to be a coincidence. A wrong poster is worse than none.
"""
from __future__ import annotations
import argparse, json, os, re, sys, pathlib, datetime as dt
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netfetch import Fetcher                                   # noqa: E402
from posterpolicy import Catalogue, filter_map, is_local_poster  # noqa: E402

# Hand-verified posters, pulled directly from each theatre's own site and checked
# by eye. More reliable than either aggregator, so they win the merge — but they
# are no longer exempt from posterpolicy. SEED being exempt is exactly why one
# wrong hand-added URL put another film's artwork on the Oasis screening and
# survived every weekly refresh.
SEED = {
    # sofia-th + satira — own-domain / readable-filename posters,
    # all verified 200/image on 2026-10-06.
    "100-godini-georgi-parcalev": "https://storage.googleapis.com/is-satira/images/000/007/843/image/05_2026_Partsalev_484X686mm_1.jpg",
    "ah-tezi-privideniya": "https://storage.googleapis.com/is-satira/images/000/007/670/image/02_2026_Prividenia_486X686mm.jpg",
    "bezopasni-vrazki": "https://storage.googleapis.com/is-satira/images/000/007/437/image/PLAKATI%20686-986%20mm%20_BEZOPASNI_VRAZKI_FINAL.jpg",
    "bradatata-grafinya": "https://storage.googleapis.com/is-satira/images/000/007/470/image/PLAKATI%20486-686%20mm%20_BRADATATA_GRAFINIA_V2.jpg",
    "bushon-za-smyana": "https://storage.googleapis.com/is-satira/images/000/006/771/image/PLAKATI%20686-986%20mm%20_BUSHON%20ZA%20SMQNA_2.jpg",
    "chehov-uzhas": "https://storage.googleapis.com/is-satira/images/000/007/687/image/%D0%A7%D0%95%D0%A5%D0%9E%D0%92%20%D1%83%D0%B6%D0%B0%D1%81.jpg",
    "chudnite-priklyucheniya-na-pinokio": "https://sofiatheatre.eu/uploads/repertoires/2zjojMTsNUqWYf9YfKtNWpl9hk3MYuHBSfyU5n76.jpg",
    "da-ostanem-samo-priyateli-16": "https://storage.googleapis.com/is-satira/images/000/007/560/image/PLAKATI%20486-686%20mm%20_DA%20OSTANEM%20SAMO%20PRIATELI-.jpg",
    "denyat-na-glarusa": "https://storage.googleapis.com/is-satira/images/000/007/689/image/Glarus_680X980mm.jpg",
    "etarvi": "https://storage.googleapis.com/is-satira/images/000/007/650/image/02_2026_Etarvi_680X980mm.jpg",
    "feyata-ot-zaharnicata": "https://sofiatheatre.eu/uploads/repertoires/TMkA4h9xl8MzsYD3Hn23S3kEYWkMPV1jCAazQCrL.jpg",
    "gospozha-ministershata": "https://sofiatheatre.eu/uploads/repertoires/yLHlf9Yl2pAI8S4H8swVHs5SJUkyKY3EjvWMQ5lv.jpg",
    "iskam-vashiya-mazh": "https://storage.googleapis.com/is-satira/images/000/007/265/image/PLAKATI%20686-986%20mm%20_ISKAM%20VASHIQ%20MAJ.jpg",
    "izvanzemni-strasti": "https://storage.googleapis.com/is-satira/images/000/007/270/image/PLAKATI%20686-986%20mm%20_IZVANZEMNI%20STRASTI.jpg",
    "kogato-rozite-tancuvat": "https://storage.googleapis.com/is-satira/images/000/006/944/image/PLAKATI%20686-986%20mm%20_KOGATO%20ROZITE%20TANCUVAT.jpg",
    "koshnici": "https://storage.googleapis.com/is-satira/images/000/006/989/image/PLAKATI%20686-986%20mm%20_KOSHNICI.jpg",
    "krevat-za-dvama-totalno-bedstvie-14": "https://storage.googleapis.com/is-satira/images/000/007/268/image/PLAKATI%20686-986%20mm%20_KREVAT%20ZA%20DVAMA.jpg",
    "krivorazbranata-civilizaciya": "https://storage.googleapis.com/is-satira/images/000/007/142/image/KRIVORAZBRANA%20CIVILIZACIQ_PLAKAT%20686X986mm-68G.jpg",
    "lucifer-16": "https://storage.googleapis.com/is-satira/images/000/007/272/image/PLAKATI%20686-986%20mm%20_LUCIFER.jpg",
    "ludi-za-vrazvane-16": "https://storage.googleapis.com/is-satira/images/000/007/640/image/PLAKATI%20686-986%20mm%20_LUDI%20ZA%20VRAZVANE_68G.jpg",
    "lyubov-i-drugi-avarii": "https://sofiatheatre.eu/uploads/repertoires/WFsbZNCxBuX2hZF9ZaRpf07vGFbX81EcCObxyoby.jpg",
    "mayka-kurazh-i-neynite-deca": "https://storage.googleapis.com/is-satira/images/000/006/680/image/PLAKATI%20686-986%20mm%20_MAIKA%20KURAJ_page-0001.jpg",
    "mazhatmievinoven": "https://storage.googleapis.com/is-satira/images/000/007/255/image/PLAKATI%20686-986%20mm%20_MAJATMIEVINOVEN.jpg",
    "nachaloto-na-kraya": "https://storage.googleapis.com/is-satira/images/000/007/795/image/04_2026_%D0%9D%D0%B0%D1%87%D0%B0%D0%BB%D0%BE%D1%82%D0%BE%20%D0%BD%D0%B0%20%D0%BA%D1%80%D0%B0%D1%8F_680X980mm-3.jpg",
    "nevinno-malko-ubiystvo": "https://sofiatheatre.eu/uploads/repertoires/ARKUdkujU1VwtbXcxbe70O4ZdcI34M31d3Sy4hEL.jpg",
    "novite-drehi-na-kralya": "https://storage.googleapis.com/is-satira/images/000/007/396/image/PLAKATI%20686-986%20mm%20_NOVITE_DREHI_FINAL_HI_201025.jpg",
    "nyama-problem": "https://storage.googleapis.com/is-satira/images/000/007/622/image/PLAKATI%20486-686%20mm%20_NIAMA_PROBLEM_230126%20%281%29_page-0001.jpg",
    "oskar": "https://storage.googleapis.com/is-satira/images/000/007/263/image/PLAKAT-686X986mm_OSKAR-68G.jpg",
    "perfektni-nepoznati": "https://sofiatheatre.eu/uploads/repertoires/uPr5FsSVModwTsvU67ZGiUHZatPD9VZnM2PoECTS.jpg",
    "pokana-za-vecherya": "https://sofiatheatre.eu/uploads/repertoires/JRxCjZUYMbrLFkKdaZtLGvgeFX9bXFAvja1d1Efi.jpg",
    "politichesko-satirichno-kabare-fokus-mok": "https://storage.googleapis.com/is-satira/images/000/007/910/image/814926074_10235523000319181_9073828224300136325_n.jpg",
    "pomosht-imam-dve-deca": "https://storage.googleapis.com/is-satira/images/000/007/262/image/PLAKATI%20686-986%20mm%20_POMOSHT%20IMAM%20DVE%20DECA.jpg",
    "postoyannata-sapruga-ocharovatelni-gresh": "https://storage.googleapis.com/is-satira/images/000/007/254/image/PLAKATI%20686-986%20mm%20_POSTOQNNATA%20SAPRUGA_68G.jpg",
    "primadoni": "https://storage.googleapis.com/is-satira/images/000/007/507/image/%D0%BF%D1%80%D0%B8%D0%BC%D0%B0%D0%B4%D0%BE%D0%BD%D0%B8%20%D0%BE%D1%80%D0%B0%D0%B7%D0%BC%D0%B5%D1%80%D0%B5%D0%BD.jpg",
    "provincialni-anekdoti": "https://storage.googleapis.com/is-satira/images/000/007/258/image/PLAKATI%20686-986%20mm%20_PROVINCIALNI%20ANEKDOTI.jpg",
    "razgnevenite": "https://storage.googleapis.com/is-satira/images/000/007/907/image/813757123_10235522229059900_454187274889936495_n.jpg",
    "robi-uilyams-beshe-tuk": "https://storage.googleapis.com/is-satira/images/000/007/558/image/%D0%A0%D0%A3%20%D0%B1%D0%B5%D1%88%D0%B5%20%D1%82%D1%83%D0%BA.jpg",
    "sama-zhena": "https://storage.googleapis.com/is-satira/images/000/007/273/image/PLAKATI%20686-986%20mm%20_SAMA%20JENA_68G.jpg",
    "shaferki-zavinagi": "https://storage.googleapis.com/is-satira/images/000/007/435/image/PLAKATI%20686-986%20mm%20_SHAFERKI%20ZA%20VINAGI_68.jpg",
    "shum-zad-kulisite": "https://storage.googleapis.com/is-satira/images/000/007/818/image/05_2026_Shum%20zad%20kulisite_680X980mm.jpg",
    "skriti-limonki": "https://sofiatheatre.eu/uploads/repertoires/vbzpH2nWYdAT956ickTcufSpBKlRYQiKEdRjdQd7.jpg",
    "smyah-na-sboguvane": "https://storage.googleapis.com/is-satira/images/000/006/769/image/PLAKATI%20686-986%20mm_SMQH%20NA%20SBOGUVANE_page-0001.jpg",
    "svetici-i-perverznici-16": "https://storage.googleapis.com/is-satira/images/000/007/261/image/PLAKATI%20686-986%20mm%20_SVETICI%20I%20PERVERZNICI_68G.jpg",
    "tap-optimist": "https://storage.googleapis.com/is-satira/images/000/007/257/image/PLAKATI%20686-986%20mm%20_TUP%20OPTIMIST.jpg",
    "tom-soyer-i-hak-fin": "https://storage.googleapis.com/is-satira/images/000/007/588/image/PLAKATI%20686-986%20mm%20_tom_soyer_FIN_03_page-0001.jpg",
    "uzhasnite-roditeli": "https://sofiatheatre.eu/uploads/repertoires/Oj564CDZc393VnM2FPMBc3LnJIAQWqmIS3sZz9Ho.jpg",
    "vecherya-za-tapaci": "https://storage.googleapis.com/is-satira/images/000/007/271/image/PLAKATI%20686-986%20mm%20_VECHERQ%20ZA%20TAPACI.jpg",
    "vsichki-obichat-gari": "https://storage.googleapis.com/is-satira/images/000/007/256/image/PLAKATI%20686-986%20mm%20_VSICHKI%20OBICHAT%20GARI.jpg",
    "zhenata-pita-chatgpt": "https://storage.googleapis.com/is-satira/images/000/007/996/image/10_2026_Jenata%20pita%20ChatGPT.jpg",
    # Театър ARTVENT — posters read directly off each /event page's inline art
    # (artvent.bg/images/events/<file>), NOT og:image which is always the site
    # logo. Keyed by the catalogue show id; all 19 verified 200/image on
    # 2026-10-06. petk-vecher has no current performance, so no live poster.
    "chamokria": "https://artvent.bg/images/events/1618ba08018f8b.jpg",
    "tova-ne-go-kazvay": "https://artvent.bg/images/events/wsHunMvDpIjrbrLEG45MGOlolVBgu4gQmzMC0YmN.png",
    "ne-zalagai-na-anglicanite": "https://artvent.bg/images/events/TBYH6fNTtGO5gl44a9bCBYrB7ve08TGoEqCjPfr7.jpg",
    "zaklevas-li-se-v-decata": "https://artvent.bg/images/events/LTXzM1xaTFFTFQyUegjOqBvkIUmLKdDf4jRWAdDo.jpg",
    "otchayani-sapruzi": "https://artvent.bg/images/events/1609001467e984.png",
    "ssedite-otgore": "https://artvent.bg/images/events/1634e6ebbecf0b.jpg",
    "izvnredno-lyubovno": "https://artvent.bg/images/events/uyNAW2d91KUAdaLalXUIzvyqfkavHslvySm5HHcL.jpg",
    "xeitieroseksualen": "https://artvent.bg/images/events/Yb2mRLQLAQY9BvOgvrXpnBzjM4HrD3zSVqaVCCbX.jpg",
    "ostavam-za-malko": "https://artvent.bg/images/events/lMqub6KCbnQUkhXrRDY6zN97TXa9j5WeFLO8yI3q.jpg",
    "kod-zielto": "https://artvent.bg/images/events/c0FSna4Yybvf0skylKa3t7DOEj1VElZhNBC0kADO.jpg",
    "ogledalce-ogledalce": "https://artvent.bg/images/events/DE9WSs0ZZu05Qr0bNUyomKxiyD07FYPgqgDMmrwi.jpg",
    "otcaiani-siepruzi-2-brakuvani": "https://artvent.bg/images/events/HGMTxeua2ersyh6muchLZgaeOzbXh6XhzfEQYgRK.jpg",
    "i-tova-shhe-mine": "https://artvent.bg/images/events/DbpWQzrOIc4odpo2DRnjaG6ysRPK0GuB9DeOkHhm.jpg",
    "sievierseni-natrapnici": "https://artvent.bg/images/events/8YuiDGCIeNjZYL3VwvzJs7UkV2Y36BbXNctdFKp2.jpg",
    "bez-garanciia": "https://artvent.bg/images/events/XL6ADwK7UnsziQk2LagWFNufcjbQ4lx63iafjmES.png",
    "inter-alia": "https://artvent.bg/images/events/9UcrsyRwbmVG5ZS6l2FHI4gHeHAcHIt7a3Fi18xR.jpg",
    "miasto-nareceno-drugade": "https://artvent.bg/images/events/1aCUlezs920ZYyS2uxcLQkwMqvf0XS2NgLzQjuFd.jpg",
    "az-koiato-te-obica": "https://artvent.bg/images/events/kCgePZt1dJkOr492HEepEzW2FLm8SnB8OqbhCMCM.png",
    "nedelya-sutrin": "https://artvent.bg/images/events/164edb9d138ada.png",
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
    # ---- 2026-10-06 harvest: venue-own posters for the named gaps -----------
    # Театър „Сълза и смях" (salzaismyah.bg) — the 6 shows moved here from the
    # retired Мелпомена venue; posters off each play's own page, verified by eye.
    "bashta-mi-se-kazva-mariya": "https://www.salzaismyah.bg/uploads/events_image/1a9c903a668c15335255b7d0128374da.jpg",
    "bashta-mi-se-kazva-mariya-na-selo": "https://www.salzaismyah.bg/uploads/events_image/b8c9ad0376208df5924c205757e49ae4.jpg",
    "ot-krasta-nadolu": "https://www.salzaismyah.bg/uploads/events_image/c890684b25f815c73eeeec73b0290d29.jpg",
    "vrazhalec": "https://www.salzaismyah.bg/uploads/events_image/d72f9590e3d6ef6b29438aca6b7fcaf1.jpg",
    "kuchki": "https://www.salzaismyah.bg/uploads/events_image/5d13b15958c6a9b7ee80ceb9d573bd1b.jpg",
    "zhena-mi-se-kazva-boris": "https://www.salzaismyah.bg/uploads/events_image/9eb69f128549a8bb8e539c3962245d21.jpg",
    # Театър „Възраждане" (theatrevazrajdane.bg) — evening + children repertoire.
    "tri-sestri": "https://theatrevazrajdane.bg/wp-content/uploads/2024/02/tri-sestri-plakat-edits.png",
    "chestna-musketarska": "https://theatrevazrajdane.bg/wp-content/uploads/2026/05/chestna-musketarska-web-1.png",
    "vsichki-strahotni-neshta": "https://theatrevazrajdane.bg/wp-content/uploads/2026/04/VS-50x70-1-pdf.jpg",
    "kvartet-za-dvama": "https://theatrevazrajdane.bg/wp-content/uploads/2024/01/2025-kvartet-za-dvama-web.png",
    "labirint": "https://theatrevazrajdane.bg/wp-content/uploads/2024/02/labirint-web-tv.jpg",
    "alo-alo": "https://theatrevazrajdane.bg/wp-content/uploads/2024/01/alo-alo-web.jpg",
    "prelestite-na-iznevyarata": "https://theatrevazrajdane.bg/wp-content/uploads/2026/10/%D0%9F%D1%80%D0%B5%D0%BB%D0%B5%D1%81%D1%82%D0%B8%D1%82%D0%B5-%D0%BD%D0%B0-%D0%B8%D0%B7%D0%BD%D0%B5%D0%B2%D1%8F%D1%80%D0%B0%D1%82%D0%B0.jpg",
    "podaraci-za-dyado-koleda": "https://theatrevazrajdane.bg/wp-content/uploads/2024/01/podaratsi-za-diado-koleda-web.jpg",
    # I AM Studio (iamstudio.bg) — each show's declared og:image.
    "spasyavaneto-na-dzheysi": "https://iamstudio.bg/wp-content/uploads/2026/09/1-scaled-thegem-blog-timeline-large.png",
    "dokato-chakame-godo": "https://iamstudio.bg/wp-content/uploads/2026/05/fb_img_1779533222091-thegem-blog-timeline-large.jpg",
    "kay-tigarat-ot-golyamoto-ezero": "https://iamstudio.bg/wp-content/uploads/2026/02/kaj_png1-scaled-thegem-blog-timeline-large.png",
    "sofiya-i-hvarchashtoto-krevatche": "https://iamstudio.bg/wp-content/uploads/2026/03/2000x2000_post-thegem-blog-timeline-large.png",
    "asansyorat": "https://iamstudio.bg/wp-content/uploads/2026/01/dsc08816-scaled-thegem-blog-timeline-large.jpg",
    "obyknovennye-istorii": "https://iamstudio.bg/wp-content/uploads/2025/12/ru_poster_a3-scaled-thegem-blog-timeline-large.jpg",
    # Театър „Българска армия" (tba.art.bg) — self-signed host; fetched server-side
    # with the cert ignored and re-hosted locally. First BIG image in each play's
    # lightbox gallery; all verified HTTP 200 via curl -k, spaces %20-encoded.
    "mizantrop": "https://www.tba.art.bg/img/photos/BIG16673887461mizantrop-poster-preview2.jpg",
    "mnogo-shum-za-nishto": "https://www.tba.art.bg/img/photos/BIG16632450481mnogo%206um%20100.jpg",
    "urok-po-balgarski": "https://www.tba.art.bg/img/photos/BIG17153478171NEW-urok_po_bg_100x70cm.jpg",
    "baseynat": "https://www.tba.art.bg/img/photos/BIG17285619051poster-small(website).jpg",
    "lyubov-lyubov-lyubov": "https://www.tba.art.bg/img/photos/BIG15706172441Love-plakat.png",
    "stapka-po-stapka": "https://www.tba.art.bg/img/photos/BIG17621745853stupka%20plakat.jpg",
    "rodnini-gostuva-dkt-haskovo": "https://www.tba.art.bg/img/photos/BIG17755549341RODNINI1.jpg",
    "cherna-komediya": "https://www.tba.art.bg/img/photos/BIG15712192181Cherna%20komedia-Plakat-69x99-final.jpg",
    "hotel-mezhdu-toya-i-onya-svyat": "https://www.tba.art.bg/img/photos/BIG16354931802image_6483441.JPG",
    "kolko-e-vazhno-da-badesh-seriozen": "https://www.tba.art.bg/img/photos/BIG16981377131kolkoevajno-nov.png",
    "otkachena-familiya-gostuva-odt-apostol-k": "https://www.tba.art.bg/img/photos/BIG17664084081OTKACHENA%20PLAKAT.jpg",
    "tyalo-v-led-gostuva-dt-ruse": "https://www.tba.art.bg/img/photos/BIG17888594961Tyalo%20v%20led%20POSTER.jpg",
    "botevata-lyubov-gostuva-dkt-vraca": "https://www.tba.art.bg/img/photos/BIG17881804321botev_web.png",
    "gramofonat-gostuva-viktor-kalev": "https://www.tba.art.bg/img/photos/BIG178639300511.jpg",
    "krayat-sled-teb-gostuva-dt-burgas": "https://www.tba.art.bg/img/photos/BIG17765842081Krayat%20sled%20teb%20poster%20web.png",
    "zhenitba-gostuva-dt-blagoevgrad": "https://www.tba.art.bg/img/photos/BIG17881807531jenitba.jpg",
    "barierata-gostuva-dt-plovdiv": "https://www.tba.art.bg/img/photos/BIG17882655761657847789_1361845392644878_3775012309256793663_n.jpg",
    "nie-duhovata-muzika-gostuva-rodopski-tea": "https://www.tba.art.bg/img/photos/BIG17882595361690a1b43f2efb149a20d0338_GVIg_371x519.jpg",
    "priklyucheniyata-na-dobriya-voynik-shvey": "https://www.tba.art.bg/img/photos/BIG17897153247shvejk.jpg",
    "pet-zheni-v-ednakvi-rokli": "https://www.tba.art.bg/img/photos/BIG176217839735%20jeni%20poster-small(website).jpg",
    "madam-bovari-gostuva-dt-yambol": "https://www.tba.art.bg/img/photos/BIG17906822441Bovari%20poster%20BG%20Armiya%201x1.jpg",
    "nestinari-gostuva-kt-burgas": "https://www.tba.art.bg/img/photos/BIG17882630571111_optimized_950.jpg",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT  = ROOT / "theatre_posters.json"

PROGRAMATA = "https://programata.bg/wp-json/wp/v2"
POSTANOVKI_SLUG = "postanovki"
POSTANOVKI_FALLBACK_ID = 143              # resolved at runtime; this is the 2026 value
ART_DAY = "https://theatre.art.bg/?date={date}&city=20"
ART_PROD = "https://theatre.art.bg/x_{prod}_{theatre}_{city}"

# Words that carry no identity, so two titles differing only by these still match.
NOISE = {"спектакъл", "постановка", "театър", "театъра", "премиера", "нова",
         "the", "a", "an", "и", "на", "в", "с", "за"}


# ------------------------------------------------------------------ the app
def grab(src, name):
    """Pull `const NAME=[...]` / `{...}` out of index.html by bracket matching."""
    i = src.find("const " + name + "=")
    if i < 0:
        return None
    j = src.find("=", i) + 1
    depth, start = 0, None
    for k in range(j, len(src)):
        c = src[k]
        if c in "[{":
            if depth == 0:
                start = k
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(src[start:k + 1])
    return None


def norm(s):
    s = (s or "").lower().replace("ё", "е").replace("ʻ", "")
    s = re.sub(r"[„“”\"'’‘«»`\.\,\!\?\:\;\-–—_\(\)\[\]/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s):
    return {w for w in norm(s).split() if w not in NOISE and len(w) > 1}


def same_title(a, b):
    """Conservative: exact, or one contained in the other and long enough, or
    identical significant-word sets. Anything looser invites wrong posters."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 8 and (long.startswith(short + " ") or short in long.split(" | ")):
        return True
    ta, tb = tokens(a), tokens(b)
    return bool(ta) and ta == tb


class Matcher:
    """Index of show titles -> show id, with a single conservative lookup."""

    def __init__(self, shows):
        self.shows = shows
        self.exact = {}
        for s in shows:
            for t in (s.get("title"), s.get("titleEn")):
                if t:
                    self.exact.setdefault(norm(t), s["id"])

    def find(self, title):
        hit = self.exact.get(norm(title))
        if hit:
            return hit
        cands = [s["id"] for s in self.shows
                 if same_title(title, s.get("title")) or
                 (s.get("titleEn") and same_title(title, s["titleEn"]))]
        return cands[0] if len(cands) == 1 else None      # ambiguity = no match


# --------------------------------------------------------------- programata
def programata_category_id(net):
    data = net.json(f"{PROGRAMATA}/categories?slug={POSTANOVKI_SLUG}&_fields=id,name,count")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("id"):
        print(f"  category '{POSTANOVKI_SLUG}' = {data[0]['id']} "
              f"({data[0].get('count', '?')} posts)")
        return data[0]["id"]
    print(f"  could not resolve the category — falling back to {POSTANOVKI_FALLBACK_ID}")
    return POSTANOVKI_FALLBACK_ID


def media_urls(net, media_ids):
    """Resolve attachment ids to source URLs, 100 at a time."""
    out = {}
    ids = [str(i) for i in media_ids if i]
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = net.json(f"{PROGRAMATA}/media?include={','.join(chunk)}"
                        f"&per_page=100&_fields=id,source_url,media_details",
                        attempts=2)
        if not isinstance(data, list):
            continue
        for m in data:
            url = m.get("source_url")
            if not url:
                continue
            det = m.get("media_details") or {}
            out[m["id"]] = {"url": url, "w": det.get("width"), "h": det.get("height")}
    return out


def from_programata(net, matcher, wanted, pages=6, per_page=100):
    """Walk the newest productions, then search by name for whatever is left."""
    cat = programata_category_id(net)
    found, media_for = {}, {}

    for page in range(1, pages + 1):
        if not wanted:
            break
        posts = net.json(f"{PROGRAMATA}/posts?categories={cat}&per_page={per_page}"
                         f"&page={page}&orderby=date&order=desc"
                         f"&_fields=id,link,title,featured_media")
        if not isinstance(posts, list) or not posts:
            break
        for p in posts:
            title = ((p.get("title") or {}).get("rendered") or "")
            title = re.sub(r"<[^>]+>", "", title).strip()
            sid = matcher.find(title)
            if sid and sid in wanted and p.get("featured_media"):
                media_for[p["featured_media"]] = (sid, title, p.get("link"))
        if len(posts) < per_page:
            break

    # anything still missing: ask the search endpoint by name
    still = [s for s in matcher.shows
             if s["id"] in wanted and s["id"] not in {v[0] for v in media_for.values()}]
    for s in still:
        q = requests_quote(s.get("title") or "")
        if not q:
            continue
        if net.budget_spent():
            print("  time budget reached — stopping the per-title search", file=sys.stderr)
            break
        posts = net.json(f"{PROGRAMATA}/posts?search={q}&per_page=5"
                         f"&_fields=id,link,title,featured_media", attempts=2)
        if not isinstance(posts, list):
            continue
        for p in posts:
            title = re.sub(r"<[^>]+>", "", (p.get("title") or {}).get("rendered") or "").strip()
            if norm(title) == norm(s.get("title")) and p.get("featured_media"):
                media_for[p["featured_media"]] = (s["id"], title, p.get("link"))
                break

    resolved = media_urls(net, media_for.keys())
    for mid, (sid, title, link) in media_for.items():
        info = resolved.get(mid)
        if not info:
            continue
        found[sid] = info["url"]
        shape = ""
        if info.get("w") and info.get("h"):
            shape = f"  {info['w']}x{info['h']}" + (" portrait" if info["h"] > info["w"] else "")
        print(f"  [programata] {sid:24s} {title[:32]:32s}{shape}")
    return found


def requests_quote(s):
    import urllib.parse
    return urllib.parse.quote(s.strip())


# ------------------------------------------------------- local re-hosting
POSTERS_DIR = ROOT / "posters"
POSTER_MAXPX = 800          # longest edge; cards show these small, detail-sheet modest
POSTER_JPEG_Q = 82
EXT_BY_TYPE = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
               "image/webp": "webp", "image/gif": "gif", "image/avif": "avif"}


def _store_poster(src, pid):
    """Downscale and re-encode the downloaded image at `src` into webapp/posters/,
    so a 20 MB source poster does not ship to every visitor. Photos become JPEG;
    anything with real transparency stays PNG. Returns 'posters/<id>.<ext>' and
    removes any stale sibling left under a different extension."""
    from PIL import Image
    im = Image.open(src)
    im.load()
    w, h = im.size
    if max(w, h) > POSTER_MAXPX:
        k = POSTER_MAXPX / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if alpha:
        im = im.convert("RGBA")
        ext = "png"
    else:
        im = im.convert("RGB")
        ext = "jpg"
    dest = POSTERS_DIR / f"{pid}.{ext}"
    for stale in POSTERS_DIR.glob(f"{pid}.*"):
        if stale != dest:
            stale.unlink()
    if ext == "png":
        im.save(dest, optimize=True)
    else:
        im.save(dest, quality=POSTER_JPEG_Q, optimize=True, progressive=True)
    return f"posters/{dest.name}"


def rehost(merged, net, refresh=False):
    """Download every remote poster once and serve it from webapp/posters/, so it
    loads same-origin over the app's own HTTPS. Cert-blocked (tba), mixed http://
    and hotlink-protected images all fail in-browser otherwise. The repo-relative
    "posters/<id>.<ext>" path replaces the remote URL; on a download failure the
    remote URL is kept as a fallback rather than dropped."""
    POSTERS_DIR.mkdir(parents=True, exist_ok=True)
    out, kept_remote = {}, []
    for pid, url in merged.items():
        if is_local_poster(url):
            out[pid] = url
            continue
        existing = sorted(POSTERS_DIR.glob(f"{pid}.*"))
        if existing and not refresh:
            out[pid] = f"posters/{existing[0].name}"   # already fetched (e.g. in the repo)
            continue
        tmp = POSTERS_DIR / f"{pid}.orig"
        if not net.download(url, tmp):
            out[pid] = url
            kept_remote.append(pid)
            continue
        try:
            out[pid] = _store_poster(tmp, pid)
        except Exception as e:
            out[pid] = url
            kept_remote.append(pid)
            print(f"  ! could not re-encode {pid}: {e}", file=sys.stderr)
        finally:
            tmp.unlink(missing_ok=True)
    local_n = sum(1 for v in out.values() if is_local_poster(v))
    print(f"re-hosted {local_n}/{len(out)} posters under {POSTERS_DIR.name}/")
    if kept_remote:
        shown = ", ".join(sorted(kept_remote)[:8]) + (" …" if len(kept_remote) > 8 else "")
        print(f"  {len(kept_remote)} could not be fetched, kept remote: {shown}")
    return out


# ------------------------------------------------------------ theatre.art.bg
ID_RE = re.compile(r"kupi-bilet\.php\?[^\"'>]*\bid=(\d+)[^\"'>]*\btheatre=(\d+)"
                   r"(?:[^\"'>]*\bcity=(\d+))?", re.I)
OG_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)
OG_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I)


def harvest_ids(net, days, matcher, wanted, verbose=False):
    """(showId -> (productionId, theatreId, cityId)) from the day pages."""
    ids = {}
    for day in days:
        if not wanted - set(ids) or net.budget_spent():
            break
        html = net.text(ART_DAY.format(date=day.isoformat()))
        if not html:
            continue
        # each listing row: <a href="…kupi-bilet.php?…id=X&theatre=Y…">TITLE</a>
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']*kupi-bilet\.php[^"\']*)["\'][^>]*>(.*?)</a>',
                             html, re.S | re.I):
            href, label = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
            got = ID_RE.search(href)
            if not got:
                continue
            sid = matcher.find(label)
            if sid and sid in wanted and sid not in ids:
                ids[sid] = (got.group(1), got.group(2), got.group(3) or "20")
                if verbose:
                    print(f"  [art.bg] {sid:24s} id={got.group(1)} theatre={got.group(2)}")
    return ids


def from_artbg(net, matcher, wanted, days, verbose=False):
    found = {}
    ids = harvest_ids(net, days, matcher, wanted, verbose)
    for sid, (prod, theatre, city) in ids.items():
        if net.budget_spent():
            print("  time budget reached — stopping", file=sys.stderr)
            break
        html = net.text(ART_PROD.format(prod=prod, theatre=theatre, city=city),
                        attempts=2)
        if not html:
            continue
        m = OG_RE.search(html) or OG_RE2.search(html)
        if not m:
            continue
        url = m.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http") and re.search(r"\.(jpe?g|png|webp)$", url, re.I):
            found[sid] = url
            print(f"  [art.bg]     {sid:24s} {url.rsplit('/', 1)[-1][:50]}")
    return found


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["all", "programata", "artbg"], default="all")
    ap.add_argument("--only", help="comma-separated show ids, for debugging one match")
    ap.add_argument("--refresh", action="store_true",
                    help="re-resolve shows that already have a poster")
    ap.add_argument("--days", type=int, default=45,
                    help="how many days of theatre.art.bg listings to walk (default 45)")
    ap.add_argument("--dry-run", action="store_true", help="do not write the file")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--budget", type=int, default=900,
                    help="seconds of network time before giving up gracefully "
                         "(default 900; keeps a CI run bounded)")
    args = ap.parse_args()

    src = HTML.read_text(encoding="utf-8")
    body = src.split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]
    shows = grab(body, "SHOWS") or []
    if not shows:
        sys.exit("no SHOWS found in index.html")

    previous = {}
    if OUT.exists():
        try:
            previous = {k: v for k, v in json.load(open(OUT, encoding="utf-8")).items() if v}
        except Exception:
            previous = {}

    want = {s["id"] for s in shows}
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
    if not args.refresh:
        want -= set(previous) | set(SEED)

    print(f"{len(shows)} shows in the catalogue, {len(previous)} already have art, "
          f"{len(want)} to resolve")
    if not want:
        print("nothing to do")
        return 0

    net = Fetcher(verbose=True, budget_seconds=args.budget)
    matcher = Matcher(shows)
    found = {}

    if args.source in ("all", "programata"):
        print("\n--- programata.bg (WordPress REST) ---")
        found.update(from_programata(net, matcher, want))

    remaining = want - set(found)
    if remaining and args.source in ("all", "artbg"):
        print(f"\n--- theatre.art.bg ({len(remaining)} still missing) ---")
        win = re.search(r'"?window"?\s*:\s*\{\s*"?from"?\s*:\s*"(\d{4}-\d\d-\d\d)"', src)
        start = dt.date.fromisoformat(win.group(1)) if win else dt.date.today()
        days = [start + dt.timedelta(days=i) for i in range(args.days)]
        found.update(from_artbg(net, matcher, remaining, days, args.verbose))

    # previous file < this run's scrape < hand-verified SEED, and every tier
    # passes through posterpolicy before it is allowed in.
    catalogue = None
    try:
        catalogue = Catalogue.from_html(HTML)
    except Exception as e:
        print(f"  (catalogue unreadable, provenance checks only: {e})")

    policy_log = []
    prev_ok, _ = filter_map(previous, catalogue, "the previous file", policy_log)
    new_ok, _ = filter_map(found, catalogue, "this run's scrape", policy_log)
    seed_ok, _ = filter_map(SEED, catalogue, "SEED", policy_log)

    merged = {}
    merged.update(prev_ok)
    merged.update(new_ok)
    merged.update(seed_ok)

    if policy_log:
        print("\nposter policy:")
        for line in policy_log:
            print(line)

    net.print_report()
    print(f"\nnew this run: {len(new_ok)}   rejected by policy: {len(policy_log)}   "
          f"total: {len(merged)}/{len(shows)} shows with art")
    if args.dry_run:
        print("dry run — theatre_posters.json not written")
        return 0
    merged = rehost(merged, net, refresh=args.refresh)
    json.dump(merged, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1,
              sort_keys=True)
    print(f"wrote {OUT.name}")
    return 0


def audit():
    """Check what is already stored, and the SEED table, without any network."""
    catalogue = Catalogue.from_html(HTML)
    stored = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
    log = []
    kept, dropped = filter_map(stored, catalogue, "theatre_posters.json", log)
    print(f"{len(stored)} stored posters: {len(kept)} pass, {len(dropped)} would be dropped")
    seed_kept, seed_dropped = filter_map(SEED, catalogue, "SEED", log)
    print(f"{len(SEED)} SEED entries: {len(seed_kept)} pass, {len(seed_dropped)} would be dropped")
    for line in log:
        print(line)
    for eid in catalogue.event_by_id:
        film = catalogue.mirrors_film(eid)
        if film:
            print(f"  note: event {eid!r} mirrors film {film!r} — SHOWALIAS covers it")
    return 1 if (dropped or seed_dropped) else 0


if __name__ == "__main__":
    if "--audit" in sys.argv:
        sys.exit(audit())
    sys.exit(main())
