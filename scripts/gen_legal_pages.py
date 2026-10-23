#!/usr/bin/env python3
"""Write the legal pages: terms of use, privacy policy and cookie policy, in Bulgarian and English.

    python3 scripts/gen_legal_pages.py

    legal/terms/   legal/privacy/   legal/cookies/          Bulgarian
    legal/en/terms/  legal/en/privacy/  legal/en/cookies/   English

Static pages with no JavaScript at all (the cookie banner lives in the app's own script).
Every page's <head> comes from the one HEAD template below, so a Content-Security-Policy
meta is added once, there. The font block is fenced with <!-- FONTS --> ... <!-- /FONTS -->
and matches the app's font loading (without the onload switch, which would be inline JS).
The styles are inline in each page: the service worker serves same-origin static files
cache-first until its CACHE_VERSION changes, while pages are network-first, so a shared
stylesheet could stay stale for returning visitors.

Run it again after changing:
  - the texts below;
  - SITE_URL in scripts/site_config.py (the domain move);
  - CONSENT_VERSION in src/sofia-screen.artifact.html (a policy change: the policies show it
    as their date and the banner asks again);
  - the font block (here or in the app: the privacy policy names Google Fonts only while a
    page still loads them);
  - icons/tmdb.svg (TMDB's logo; the terms page shows it once the file is there).
verify_build.py fails while a page holds a leftover placeholder ({CONTACT_…} or {OPERATOR_…}),
misses the TMDB logo, or is out of step with the app (version, links, CSP, documented storage).

The site is maintained by a private individual, free and without ads or payments. By the
owner's decisions the texts name no person and give no email address (2026-10-15), and the site
offers no contact channel at all (2026-10-22: the GitHub issues page is no longer named). The
privacy policy says so plainly and explains how a visitor exercises their rights themselves
(clearing the site's data in the browser), that GitHub (the host), TMDB and the venues' sites
have their own policies, and that the right to complain to the КЗЛД stays. The GitHub Pages
hosting disclosure stays: it is where the site runs and who logs visits, not a contact. They
claim no duty that only a trader or company would have. A lawyer should review them.
"""
import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from site_config import SITE_URL  # noqa: E402

UI_SRC = ROOT / "src" / "sofia-screen.artifact.html"
OUT = ROOT / "legal"
TMDB_LOGO = ROOT / "icons" / "tmdb.svg"
TERMS_UPDATED = "2026-10-22"

DOCS = ("terms", "privacy", "cookies")
LANGS = ("bg", "en")

# The app's self-hosted fonts (fonts/, SIL OFL): the same stylesheet, so no font request
# leaves the site from a legal page either. Filled before [[ROOT]] (see tpl's call).
FONTS = """<!-- FONTS -->
<link rel="stylesheet" href="[[ROOT]]fonts/fonts.css">
<!-- /FONTS -->"""

# The one <head> for every legal page. A CSP meta goes right after the charset line.
HEAD = """<!doctype html>
<html lang="[[LANG]]">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0C0A0F">
<title>[[TITLE]] | Sofia Gleda</title>
<meta name="description" content="[[DESC]]">
<link rel="canonical" href="[[CANONICAL]]">
<link rel="alternate" hreflang="bg" href="[[URL_BG]]">
<link rel="alternate" hreflang="en" href="[[URL_EN]]">
<link rel="alternate" hreflang="x-default" href="[[URL_BG]]">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Sofia Gleda">
<meta property="og:url" content="[[CANONICAL]]">
<meta property="og:title" content="[[TITLE]]">
<meta property="og:description" content="[[DESC]]">
<meta property="og:image" content="[[SITE]]/share/share-preview.jpg">
<meta property="og:locale" content="[[OG_LOCALE]]">
<link rel="icon" href="[[ROOT]]icons/icon-192.png" sizes="192x192" type="image/png">
<link rel="apple-touch-icon" href="[[ROOT]]icons/apple-touch-icon.png">
[[FONTS]]
<style>
[[CSS]]</style>
</head>
"""

BODY = """<body>
<a class="lg-skip" href="#main">[[SKIP]]</a>
<header class="lg-bar"><div class="lg-wrap lg-bar-in">
<a class="lg-brand" href="[[ROOT]]" aria-label="[[HOME]]"><img src="[[ROOT]]logo.svg" alt="Sofia Gleda" width="154" height="30"></a>
<a class="lg-lang" href="[[OTHER_URL]]" hreflang="[[OTHER_LANG]]" lang="[[OTHER_LANG]]">[[OTHER_NAME]]</a>
</div></header>
<main id="main" class="lg-wrap">
<nav class="lg-docs" aria-label="[[DOCS_LABEL]]">
[[DOC_LINKS]]
</nav>
<h1>[[TITLE]]</h1>
<p class="lg-updated">[[UPDATED_LABEL]] <time datetime="[[UPDATED_ISO]]">[[UPDATED_TEXT]]</time></p>
[[CONTENT]]
</main>
<footer class="lg-foot"><div class="lg-wrap">
<nav class="lg-foot-nav" aria-label="[[FOOT_LABEL]]">
<a href="[[ROOT]]">[[BACK]]</a>
<a href="[[ROOT]]#cookies">[[COOKIE_SETTINGS]]</a>
</nav>
<p>[[FOOT_LINE]]</p>
</div></footer>
</body>
</html>
"""

UI = {
    "bg": {
        "skip": "Към съдържанието", "home": "Sofia Gleda, към програмата",
        "other_name": "English", "docs_label": "Правна информация", "foot_label": "Връзки",
        "updated": "Последна актуализация:", "back": "Към програмата",
        "cookie_settings": "Настройки за бисквитки",
        "foot_line": "Sofia Gleda е агрегатор и не е свързан с кината и театрите, чиито програми показва.",
        "months": ["януари", "февруари", "март", "април", "май", "юни", "юли", "август",
                   "септември", "октомври", "ноември", "декември"],
        "og_locale": "bg_BG",
        "titles": {"terms": "Общи условия", "privacy": "Политика за поверителност",
                   "cookies": "Политика за бисквитките"},
        "nav": {"terms": "Общи условия", "privacy": "Поверителност", "cookies": "Бисквитки"},
        "desc": {
            "terms": "Общи условия за ползване на Sofia Gleda, афиша на кината и театрите в София.",
            "privacy": "Как Sofia Gleda обработва лични данни: хостинг, изображения от други сайтове, трейлъри от YouTube "
                       "и правата ви.",
            "cookies": "Sofia Gleda не поставя бисквитки. Какво пази сайтът в браузъра ви и защо.",
        },
    },
    "en": {
        "skip": "Skip to content", "home": "Sofia Gleda, back to the listings",
        "other_name": "Български", "docs_label": "Legal", "foot_label": "Links",
        "updated": "Last updated:", "back": "Back to the listings",
        "cookie_settings": "Cookie settings",
        "foot_line": "Sofia Gleda is an aggregator and is not affiliated with the cinemas and theatres "
                     "whose programmes it shows.",
        "months": ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"],
        "og_locale": "en_GB",
        "titles": {"terms": "Terms of use", "privacy": "Privacy policy", "cookies": "Cookie policy"},
        "nav": {"terms": "Terms of use", "privacy": "Privacy", "cookies": "Cookies"},
        "desc": {
            "terms": "Terms of use of Sofia Gleda, the listings of Sofia's cinemas and theatres.",
            "privacy": "How Sofia Gleda handles personal data: hosting, images from other websites, YouTube trailers "
                       "and your rights.",
            "cookies": "Sofia Gleda sets no cookies. What the site keeps in your browser, and why.",
        },
    },
}

# ----------------------------------------------------------------------------------- texts
# [[TERMS]] / [[PRIVACY]] / [[COOKIES]] are the sibling pages in the same language, [[TMDB_LOGO]] the logo (when icons/tmdb.svg exists), [[FONTS_NOTE]] the font
# paragraph (Google Fonts or self-hosted, whichever the pages load).

TERMS_BG = """
<p class="lg-lead">Тези условия уреждат ползването на сайта Sofia Gleda. Като ползвате сайта, вие ги приемате. Ако не сте съгласни с тях, не ползвайте сайта.</p>
<section class="lg-key" aria-labelledby="nakratko">
<h2 id="nakratko">Накратко</h2>
<ul>
<li>Sofia Gleda е агрегатор: събира на едно място програмите на кината и театрите в София от техните сайтове и от други публични източници.</li>
<li>Не сме свързани с никое кино, театър, фестивал или билетна система. Имената и марките им принадлежат на съответните притежатели.</li>
<li>Сайтът е създаден с помощта на изкуствен интелект, а програмата се събира и обработва автоматично. Затова в нея може да има грешки.</li>
<li>Билети се купуват на сайтовете на кината, театрите и билетните системи и по техните условия.</li>
<li>Доколкото българското право допуска това, не отговаряме за грешки в програмата или при покупката на билети. Отговорността за умисъл и груба небрежност не може да бъде изключена.</li>
<li>Преди да купите билет или да тръгнете, проверете датата, часа и мястото при организатора.</li>
</ul>
</section>

<h2 id="koy">1. Кой поддържа сайта</h2>
<p>Sofia Gleda се поддържа от физическо лице, без търговска цел. Сайтът е безплатен, няма реклами и не приема плащания. В тези условия „ние“ означава лицето, което поддържа сайта. Сайтът не посочва име, имейл адрес или друг начин за връзка (вижте т. 14).</p>

<h2 id="kakvo">2. Какво предлага сайтът</h2>
<p>Sofia Gleda показва програмата на кината и театрите в София: заглавия, дати, часове, зали, цени (когато са известни), описания, плакати и оценки. Не продаваме билети, не приемаме резервации и не организираме прожекции или представления. Не е нужна регистрация.</p>
<p>Сайтът се предоставя във вида, в който е. Можем да променяме съдържанието и функциите му, както и временно или окончателно да спрем работата му.</p>

<h2 id="svarzanost">3. Не сме свързани с кината и театрите</h2>
<p>Sofia Gleda не е свързан с кината, театрите, фестивалите, разпространителите и билетните системи, чиито програми показва, и не действа от тяхно име. Те не са одобрили сайта и не отговарят за съдържанието му. Имената, логата, марките, плакатите и снимките принадлежат на съответните притежатели и се показват само за да се посочи какво, къде и кога се играе.</p>

<h2 id="iztochnik">4. Откъде идва информацията</h2>
<p>Програмата се събира автоматично всеки ден от сайтовете на кината, театрите и фестивалите, а за някои заглавия и от programata.bg и theatre.art.bg. След това се обработва автоматично: заглавията се свързват с данни от TMDB и OMDb, а текстовете, които съществуват само на български, се превеждат машинно на английски с DeepL.</p>
<p>При автоматичното събиране и обработване могат да възникнат грешки: неточен или остарял час, пропусната, отменена или преместена прожекция или представление, грешна цена, описание, плакат, оценка или превод. Часовете, отбелязани като предварителни, още не са потвърдени от самото място.</p>

<h2 id="proverka">5. Проверявайте при организатора</h2>
<p>Меродавна е информацията, която публикуват кината, театрите и организаторите. Преди да купите билет или да тръгнете, проверете датата, часа, залата и цената на техния сайт или на касата. Тази проверка е ваша отговорност.</p>

<h2 id="ii">6. Изкуствен интелект</h2>
<p>Сайтът е създаден и се поддържа с помощта на инструменти с изкуствен интелект. С тях са написани кодът и голяма част от логиката, която събира, свързва и подрежда програмата. Програмата се публикува автоматично, без човек да проверява всеки ред. Изкуственият интелект може да греши, затова ползвайте сайта като удобен ориентир, а не като официален източник.</p>

<h2 id="bileti">7. Билети и други сайтове</h2>
<p>Бутоните и връзките като „Купи билет“, „Покажи на картата“ и „Добави в календара“ водят към сайтове и услуги на други лица. Билетите се купуват там, на цените и по условията на продавача. Ние не сме страна по договора за покупка, не приемаме плащания и не отговаряме за наличността на места, цените, отмяната и връщането на билети, нито за съдържанието и работата на тези сайтове.</p>

<h2 id="otgovornost">8. Отговорност</h2>
<p>Доколкото приложимото право позволява, не отговаряме за вреди от грешки, непълноти или забавяне на информацията в сайта, от прекъсване на работата му или от решения, взети въз основа на тази информация, включително купен билет, пътуване или пропусната прожекция или представление.</p>
<p>Това не важи при умисъл или груба небрежност, за които отговорността не може да бъде изключена или ограничена (чл. 94 от Закона за задълженията и договорите), нито в другите случаи, в които законът не допуска ограничаване на отговорността.</p>

<h2 id="iztochnici">9. Източници на данни и авторски права</h2>
<p>Програми: сайтовете на кината, театрите и фестивалите, programata.bg и theatre.art.bg.</p>
<div class="lg-attr">[[TMDB_LOGO]]<p>Данни и изображения за филмите: TMDB (The Movie Database). Този сайт използва TMDB и приложните програмни интерфейси (API) на TMDB, но не е подкрепен, сертифициран или по друг начин одобрен от TMDB.</p>
<p lang="en">This website uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.</p></div>
<p>Оценки от IMDb и Rotten Tomatoes: чрез OMDb API (<a href="https://www.omdbapi.com/">omdbapi.com</a>), чието съдържание се разпространява под лиценз <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a>. IMDb и Rotten Tomatoes са марки на съответните им притежатели.</p>
<p>Машинни преводи на английски: DeepL (<a href="https://www.deepl.com/">deepl.com</a>).</p>
<p>Плакатите, снимките и описанията остават собственост на авторите и притежателите на правата. Показваме ги само за да представим програмата.</p>

<h2 id="danni">10. Лични данни и бисквитки</h2>
<p>Как се обработват личните данни и какво се пази в браузъра ви, е описано в <a href="[[PRIVACY]]">Политиката за поверителност</a> и в <a href="[[COOKIES]]">Политиката за бисквитките</a>.</p>

<h2 id="polzvane">11. Ползване на сайта</h2>
<p>Можете да ползвате сайта свободно за лични, нетърговски цели. Не го ползвайте по начин, който нарушава закона или пречи на работата му.</p>

<h2 id="promeni">12. Промени в условията</h2>
<p>Можем да променяме тези условия, когато променяме сайта или източниците на данни или когато законът го изисква. Новата версия се публикува на тази страница с новата дата на последна актуализация и се прилага от публикуването ѝ.</p>

<h2 id="pravo">13. Приложимо право и спорове</h2>
<p>За тези условия се прилага българското право. Споровете се решават от компетентния български съд. Това не засяга правата, които ви дават императивните разпоредби на държавата, в която живеете, включително правото да се обърнете към съд там, когато законът го допуска.</p>

<h2 id="vrazka">14. Няма канал за връзка</h2>
<p>Sofia Gleda няма канал за връзка: сайтът не посочва имейл адрес, телефон, пощенски адрес или страница за съобщения и не приема въпроси, сигнали или искания. За датата, часа, цената и билетите се обръщайте към съответното кино, театър или организатор (т. 5). Как да упражните правата си, свързани с личните данни, е описано в <a href="[[PRIVACY]]">Политиката за поверителност</a>.</p>
"""

TERMS_EN = """
<p class="lg-lead">These terms govern the use of the Sofia Gleda website. By using the site, you accept them. If you do not agree with them, please do not use the site.</p>
<section class="lg-key" aria-labelledby="in-short">
<h2 id="in-short">In short</h2>
<ul>
<li>Sofia Gleda is an aggregator: it gathers the programmes of Sofia's cinemas and theatres in one place, from their own websites and other public sources.</li>
<li>We are not affiliated with any cinema, theatre, festival or ticketing service. Their names and marks belong to their owners.</li>
<li>The site is built with the help of AI, and the listings are collected and processed automatically. They can contain mistakes.</li>
<li>Tickets are bought on the websites of the cinemas, theatres and ticket sellers, under their terms.</li>
<li>As far as Bulgarian law allows, we are not liable for mistakes in the listings or in ticket purchases. Liability for intent and gross negligence cannot be excluded.</li>
<li>Before you buy a ticket or set off, check the date, time and venue with the organiser.</li>
</ul>
</section>

<h2 id="who">1. Who runs the site</h2>
<p>Sofia Gleda is run by a private individual, on a non-commercial basis. The site is free, carries no ads and takes no payments. In these terms, “we” means the person who runs the site. The site gives no name, email address or other means of contact (see section 14).</p>

<h2 id="what">2. What the site offers</h2>
<p>Sofia Gleda shows what is on at Sofia's cinemas and theatres: titles, dates, times, halls, prices (where known), descriptions, posters and ratings. We do not sell tickets, take bookings or organise screenings or performances. No registration is needed.</p>
<p>The site is provided as is. We may change its content and features, and we may suspend or close it.</p>

<h2 id="affiliation">3. No affiliation with cinemas and theatres</h2>
<p>Sofia Gleda is not affiliated with the cinemas, theatres, festivals, distributors or ticketing services whose programmes it shows, and does not act on their behalf. They have not endorsed the site and are not responsible for its content. Names, logos, trademarks, posters and photos belong to their owners and are shown only to say what is on, where and when.</p>

<h2 id="sources-of-listings">4. Where the information comes from</h2>
<p>The listings are collected automatically every day from the websites of the cinemas, theatres and festivals, and for some titles from programata.bg and theatre.art.bg. They are then processed automatically: titles are matched with data from TMDB and OMDb, and texts that exist only in Bulgarian are machine-translated into English with DeepL.</p>
<p>Automatic collection and processing can produce mistakes: a wrong or outdated time, a missing, cancelled or moved screening or performance, or a wrong price, description, poster, rating or translation. Times marked as preliminary have not yet been confirmed by the venue.</p>

<h2 id="check">5. Check with the organiser</h2>
<p>The information published by the cinemas, theatres and organisers prevails. Before you buy a ticket or set off, check the date, time, hall and price on their website or at the box office. Checking is your responsibility.</p>

<h2 id="ai">6. Artificial intelligence</h2>
<p>The site was built and is maintained with the help of AI tools. They wrote the code and much of the logic that collects, matches and orders the listings. The listings are published automatically, without a person checking every line. AI can make mistakes, so treat the site as a handy guide, not as an official source.</p>

<h2 id="tickets">7. Tickets and other websites</h2>
<p>Buttons and links such as “Buy a ticket”, “Show on the map” and “Add to calendar” lead to websites and services run by others. Tickets are bought there, at the seller's prices and under the seller's terms. We are not a party to the purchase, take no payments and are not responsible for seat availability, prices, cancellations or refunds, or for the content and operation of those websites.</p>

<h2 id="liability">8. Liability</h2>
<p>As far as the applicable law allows, we are not liable for loss caused by mistakes, gaps or delays in the information on the site, by interruptions of the site, or by decisions made on the basis of that information, including a ticket bought, a journey made or a screening or performance missed.</p>
<p>This does not apply to intent or gross negligence, for which liability cannot be excluded or limited (Article 94 of the Bulgarian Obligations and Contracts Act), or to any other case in which the law does not allow liability to be limited.</p>

<h2 id="sources">9. Data sources and copyright</h2>
<p>Listings: the websites of the cinemas, theatres and festivals, programata.bg and theatre.art.bg.</p>
<div class="lg-attr">[[TMDB_LOGO]]<p>Film data and images: TMDB (The Movie Database). This website uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.</p></div>
<p>IMDb and Rotten Tomatoes ratings: via the OMDb API (<a href="https://www.omdbapi.com/">omdbapi.com</a>), whose content is licensed under <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a>. IMDb and Rotten Tomatoes are trademarks of their owners.</p>
<p>Machine translation into English: DeepL (<a href="https://www.deepl.com/">deepl.com</a>).</p>
<p>Posters, photos and descriptions remain the property of their authors and rights holders. We show them only to present the listings.</p>

<h2 id="data">10. Personal data and cookies</h2>
<p>How personal data is handled and what is kept in your browser is described in the <a href="[[PRIVACY]]">Privacy policy</a> and the <a href="[[COOKIES]]">Cookie policy</a>.</p>

<h2 id="use">11. Using the site</h2>
<p>You may use the site freely for personal, non-commercial purposes. Do not use it in a way that breaks the law or disrupts its operation.</p>

<h2 id="changes">12. Changes to these terms</h2>
<p>We may change these terms when we change the site or its data sources, or when the law requires it. The new version is published on this page with a new “last updated” date and applies from publication.</p>

<h2 id="law">13. Governing law and disputes</h2>
<p>These terms are governed by Bulgarian law. Disputes are settled by the competent Bulgarian court. This does not affect the rights that mandatory rules of your country of residence give you, including the right to go to court there where the law allows it.</p>

<h2 id="contact">14. No contact channel</h2>
<p>Sofia Gleda has no contact channel: the site gives no email address, phone number, postal address or page for messages, and takes no questions, reports or requests. For dates, times, prices and tickets, contact the cinema, theatre or organiser (section 5). How to exercise your rights over personal data is described in the <a href="[[PRIVACY]]">Privacy policy</a>.</p>
"""

PRIVACY_BG = """
<p class="lg-lead">Тази политика описва какви лични данни се обработват, когато ползвате Sofia Gleda, с каква цел и на какво основание, както и какви права имате. Тя е съобразена с Общия регламент относно защитата на данните (Регламент (ЕС) 2016/679, по-нататък „ОРЗД“) и със Закона за защита на личните данни.</p>
<section class="lg-key" aria-labelledby="nakratko">
<h2 id="nakratko">Накратко</h2>
<ul>
<li>Сайтът няма потребителски профили, формуляри, статистика за посещенията и реклами.</li>
<li>Не пазим лични данни на сървър, който управляваме. Настройките ви се пазят само в браузъра ви и не ни се изпращат.</li>
<li>Не поставяме бисквитки.</li>
<li>При всяко посещение хостинг доставчикът GitHub записва IP адреса ви от съображения за сигурност.</li>
<li>Плакатите се зареждат директно от TMDB и от сайтовете на кината и фестивалите, затова TMDB и тези сайтове също получават IP адреса ви.</li>
<li>Трейлърите на филмите идват от YouTube и се зареждат едва когато натиснете бутона за пускане. Дотогава нищо не се изпраща към YouTube или Google.</li>
<li>Сайтът няма канал за връзка. Правата си упражнявате сами, като изчистите данните на сайта в браузъра си (т. 9).</li>
</ul>
</section>

<h2 id="administrator">1. Администратор на личните данни</h2>
<p>Администратор е физическото лице, което поддържа Sofia Gleda без търговска цел. Сайтът не посочва име, имейл адрес или друг начин за връзка и не приема запитвания (вижте т. 8). Длъжностно лице по защита на данните не е определено, защото чл. 37 от ОРЗД не го изисква при обработване като това.</p>

<h2 id="ne">2. Какво не правим</h2>
<p>Не изискваме регистрация и не събираме чрез сайта имена, имейл адреси или телефони. Нямаме база данни и не пазим лични данни на сървър, който управляваме. Не използваме инструменти за статистика, проследяване или реклама. Не профилираме посетителите и не вземаме автоматизирани решения по смисъла на чл. 22 от ОРЗД. Не продаваме данни и не ги предоставяме за маркетинг.</p>

<h2 id="hosting">3. Посещение на сайта (хостинг)</h2>
<p>Сайтът се хоства в GitHub Pages, услуга на GitHub. Когато отворите страница, браузърът ви изпраща на сървърите на GitHub IP адреса ви и технически данни, например вида на браузъра и адреса на страницата. Според документацията на GitHub IP адресът на всеки посетител се записва и съхранява от съображения за сигурност. Нямаме достъп до тези записи. GitHub ги обработва по своята <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">декларация за поверителност</a> и сам определя колко дълго да ги пази.</p>
<p>Основание: легитимният интерес сайтът да бъде достъпен и защитен (чл. 6, пар. 1, буква „е“ от ОРЗД).</p>
<p>GitHub, Inc. се намира в САЩ и е сертифициран по Рамката за защита на личните данни в отношенията между ЕС и САЩ, за която Европейската комисия е приела решение за адекватност (Решение за изпълнение (ЕС) 2023/1795).</p>
[[FONTS_NOTE]]

<h2 id="izobrazhenia">4. Изображения от други сайтове</h2>
<p>Плакатите и снимките на филмите се зареждат директно от сървърите на TMDB (image.tmdb.org), а някои плакати и от сайтовете на кината, фестивалите и programata.bg. Плакатите на театрите се зареждат от нашия сайт. За да покаже изображение от друг сайт, браузърът ви се свързва с него и му изпраща IP адреса ви, данни за браузъра и адреса на нашия сайт, без конкретната страница. Тези сайтове обработват данните по своите правила. TMDB се управлява от Xperi Inc. (САЩ).</p>
<p>Някои от тези сайтове поставят свои бисквитки. Подробности има в <a href="[[COOKIES]]">Политиката за бисквитките</a>.</p>
<p>Основание: легитимният интерес към програмата да се показват оригиналните плакати (чл. 6, пар. 1, буква „е“ от ОРЗД). Ако не желаете това, можете да спрете изображенията от други сайтове в браузъра си, например с разширение за блокиране на съдържание. Сайтът продължава да работи и показва свои заместващи плакати.</p>

<h2 id="trailari">5. Трейлъри от YouTube</h2>
<p>Някои филми имат трейлър в YouTube. Докато не натиснете бутона за пускане, сайтът не зарежда нищо от YouTube или Google: виждате само плаката, който идва от TMDB (т. 4). Когато натиснете бутона, сайтът вгражда плейъра на YouTube в режима му с подобрена поверителност (www.youtube-nocookie.com). Тогава браузърът ви се свързва с YouTube, а YouTube получава IP адреса ви, данни за браузъра и адреса на нашия сайт. Според Google в този режим YouTube не използва гледането, за да персонализира какво ви показва, но може да съхранява информация в браузъра ви и да показва реклами, които не са персонализирани. Когато спрете видеото или затворите филма, плейърът се премахва от страницата.</p>
<p>Сайтът използва услугите на YouTube API. С пускането на трейлър се прилагат <a href="https://www.youtube.com/t/terms">Условията за ползване на YouTube</a> и <a href="https://policies.google.com/privacy">Политиката за поверителност на Google</a>. YouTube се управлява от Google Ireland Limited (Ирландия) и Google LLC (САЩ), която е сертифицирана по Рамката за защита на личните данни в отношенията между ЕС и САЩ.</p>
<p>Основание: легитимният интерес да ви покажем трейлъра, който сами сте поискали с натискането на бутона (чл. 6, пар. 1, буква „е“ от ОРЗД). Ако не желаете връзка с YouTube, просто не пускайте трейлъра. Останалата част от сайта работи без него.</p>

<h2 id="vrazki">6. Връзки към други сайтове</h2>
<p>Когато отворите връзка към сайт за билети, карта (Google Maps) или календар (Google Calendar, Outlook), браузърът ви се свързва директно с него и оттам нататък се прилагат неговите правила. Файлът за Apple Календар (.ics) се създава в браузъра ви и не ни се изпраща.</p>

<h2 id="brauzar">7. Какво остава в браузъра ви</h2>
<p>Сайтът пази в браузъра ви езика, режима (кино или театър), любимите ви кина и театри и избора ви от съобщението за бисквитките. На основния адрес на сайта (sofiagleda.com) браузърът пази и копие на сайта, за да се отваря бързо и без интернет. Тези данни не ни се изпращат и не можем да ги видим. Изтривате ги сами, като изчистите данните на сайта в настройките на браузъра. Подробности има в <a href="[[COOKIES]]">Политиката за бисквитките</a>.</p>

<h2 id="vrazka">8. Няма канал за връзка</h2>
<p>Sofia Gleda не предлага начин за връзка: няма имейл адрес, телефон, формуляр или страница за съобщения. Сайтът не събира лични данни и не ги пази на сървър, който управляваме (т. 2), затова при нас няма данни за вас, до които да ви дадем достъп или които да коригираме или изтрием. Това, което сайтът пази за вас, е само в браузъра ви и го управлявате сами (т. 9). Данните, които GitHub, TMDB и сайтовете на кината и театрите получават, се уреждат от техните политики.</p>

<h2 id="prava">9. Вашите права и как да ги упражните</h2>
<p>Имате право да поискате достъп до личните си данни, коригирането или изтриването им и ограничаване на обработването им, както и да възразите срещу обработване, основано на легитимен интерес (чл. 15, 16, 17, 18 и 21 от ОРЗД). Право на преносимост (чл. 20 от ОРЗД) имате, когато обработването се основава на съгласие или на договор; в момента такова обработване няма. Ако в бъдеще поискаме съгласието ви за нещо, ще можете да го оттеглите по всяко време.</p>
<p>Ето какво означава това на практика:</p>
<ul>
<li>Не пазим лични данни на сървър, който управляваме, затова при нас няма данни, до които да ви дадем достъп или които да изтрием.</li>
<li>Настройките, любимите ви места и копието на сайта са само в браузъра ви. Изтривате ги сами, като изчистите данните на сайта (вижте по-долу и <a href="[[COOKIES]]">Политиката за бисквитките</a>). Това е изтриването по чл. 17 от ОРЗД: друго копие няма.</li>
<li>Записите на GitHub, хостинг доставчика на сайта, за посещенията се уреждат от <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">декларацията за поверителност на GitHub</a>; искания за тях отправяте към GitHub.</li>
<li>Данните, които получават TMDB и сайтовете на кината и театрите при зареждане на изображения, както и YouTube (Google), когато пуснете трейлър, се уреждат от техните политики; искания за тях отправяте към тях.</li>
<li>Тъй като сайтът няма канал за връзка и не води потребителски профили, той не приема искания. При нас няма данни, които да могат да бъдат свързани с вас (чл. 11 от ОРЗД).</li>
</ul>
<p>Как да изчистите данните на сайта:</p>
<ul>
<li>Chrome на компютър: щракнете иконата вляво от адреса, отворете настройките за сайта и изберете изтриване на данните.</li>
<li>Firefox: щракнете катинара вляво от адреса и изберете изчистване на бисквитките и данните на сайта.</li>
<li>Safari на Mac: в настройките на Safari отворете раздела за поверителност и управлението на данните на уебсайтовете, изберете sofiagleda.com и го премахнете.</li>
<li>iPhone и iPad: в Настройки отворете Safari, после Разширени и Данни на уебсайтовете и изтрийте sofiagleda.com. Ако сте добавили сайта към началния екран, премахнете и иконата.</li>
<li>Всеки браузър позволява и да изчистите данните на всички сайтове от настройките си.</li>
</ul>
<p>Така изтривате езика, режима, любимите места, записа за съобщението за бисквитките и копието на сайта. Сайтът продължава да работи, а съобщението за бисквитките се показва отново.</p>

<h2 id="zhalba">10. Жалба</h2>
<p>Ако смятате, че правата ви са нарушени, можете да подадете жалба до Комисията за защита на личните данни: София 1592, бул. „Проф. Цветан Лазаров“ № 2, <a href="mailto:kzld@cpdp.bg">kzld@cpdp.bg</a>, <a href="https://www.cpdp.bg/">www.cpdp.bg</a>. Жалбата се подава в срок 6 месеца от узнаването на нарушението, но не по-късно от две години от извършването му (чл. 38 от Закона за защита на личните данни). Можете да се обърнете и към надзорния орган в държавата от ЕС, в която живеете или работите (чл. 77 от ОРЗД), или към съда (чл. 39 от Закона за защита на личните данни).</p>

<h2 id="promeni">11. Промени</h2>
<p>При промяна публикуваме новата версия на тази страница с новата дата. При съществена промяна съобщението за бисквитките се показва отново.</p>
"""

PRIVACY_EN = """
<p class="lg-lead">This policy describes what personal data is processed when you use Sofia Gleda, for what purpose, on what legal basis, and what rights you have. It is written in line with the General Data Protection Regulation (Regulation (EU) 2016/679, the “GDPR”) and the Bulgarian Personal Data Protection Act.</p>
<section class="lg-key" aria-labelledby="in-short">
<h2 id="in-short">In short</h2>
<ul>
<li>The site has no user accounts, no forms, no visitor statistics and no ads.</li>
<li>We keep no personal data on any server we control. Your settings stay in your browser and are not sent to us.</li>
<li>We set no cookies.</li>
<li>On every visit, the hosting provider GitHub logs your IP address for security purposes.</li>
<li>Posters load directly from TMDB and from the websites of cinemas and festivals, so TMDB and those websites also receive your IP address.</li>
<li>Film trailers come from YouTube and load only when you press the play button. Until then nothing is sent to YouTube or Google.</li>
<li>The site has no contact channel. You exercise your rights yourself by clearing the site's data in your browser (section 9).</li>
</ul>
</section>

<h2 id="controller">1. Controller</h2>
<p>The controller is the private individual who maintains Sofia Gleda on a non-commercial basis. The site gives no name, email address or other means of contact and takes no enquiries (see section 8). No data protection officer has been appointed, because Article 37 GDPR does not require one for processing like this.</p>

<h2 id="not">2. What we do not do</h2>
<p>We do not require registration and do not collect names, email addresses or phone numbers through the site. We have no database and keep no personal data on any server we control. We use no analytics, tracking or advertising tools. We do not profile visitors or make automated decisions within the meaning of Article 22 GDPR. We do not sell data or share it for marketing.</p>

<h2 id="hosting">3. Visiting the site (hosting)</h2>
<p>The site is hosted on GitHub Pages, a GitHub service. When you open a page, your browser sends GitHub's servers your IP address and technical data such as the browser type and the page address. According to GitHub's documentation, every visitor's IP address is logged and stored for security purposes. We have no access to these logs. GitHub processes them under its <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">privacy statement</a> and decides how long to keep them.</p>
<p>Legal basis: our legitimate interest in keeping the site available and secure (Article 6(1)(f) GDPR).</p>
<p>GitHub, Inc. is based in the United States and is certified under the EU-U.S. Data Privacy Framework, for which the European Commission has adopted an adequacy decision (Implementing Decision (EU) 2023/1795).</p>
[[FONTS_NOTE]]

<h2 id="images">4. Images from other websites</h2>
<p>Film posters and photos load directly from TMDB's servers (image.tmdb.org), and some posters from the websites of cinemas, festivals and programata.bg. Theatre posters load from our own site. To show an image from another website, your browser connects to it and sends your IP address, browser details and the address of our site, without the specific page. Those websites process the data under their own rules. TMDB is run by Xperi Inc. (United States).</p>
<p>Some of these websites set their own cookies. See the <a href="[[COOKIES]]">Cookie policy</a> for details.</p>
<p>Legal basis: our legitimate interest in showing the original posters with the listings (Article 6(1)(f) GDPR). If you prefer not to load them, you can block images from other websites in your browser, for example with a content-blocking extension. The site keeps working and shows its own stand-in posters.</p>

<h2 id="trailers">5. Trailers from YouTube</h2>
<p>Some films have a trailer on YouTube. Until you press the play button, the site loads nothing from YouTube or Google: you see only the poster, which comes from TMDB (section 4). When you press it, the site embeds YouTube's player in its privacy-enhanced mode (www.youtube-nocookie.com). Your browser then connects to YouTube, and YouTube receives your IP address, browser details and the address of our site. According to Google, in this mode YouTube does not use the viewing to personalise what it shows you, but it may store information in your browser and show non-personalised ads. When you stop the video or close the film, the player is removed from the page.</p>
<p>This site uses YouTube API Services. When you play a trailer, the <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a> and the <a href="https://policies.google.com/privacy">Google Privacy Policy</a> apply. YouTube is run by Google Ireland Limited (Ireland) and Google LLC (United States), which is certified under the EU-U.S. Data Privacy Framework.</p>
<p>Legal basis: our legitimate interest in showing you the trailer you asked for by pressing the button (Article 6(1)(f) GDPR). If you prefer no connection to YouTube, simply do not play the trailer. The rest of the site works without it.</p>

<h2 id="links">6. Links to other websites</h2>
<p>When you open a link to a ticket website, a map (Google Maps) or a calendar (Google Calendar, Outlook), your browser connects to it directly and its own rules apply from then on. The Apple Calendar file (.ics) is created in your browser and is not sent to us.</p>

<h2 id="browser">7. What stays in your browser</h2>
<p>The site keeps your language, mode (cinema or theatre), favourite cinemas and theatres and your answer to the cookie notice in your browser. On the site's main address (sofiagleda.com), the browser also keeps a copy of the site so that it opens quickly and works offline. None of this is sent to us and we cannot see it. You delete it yourself by clearing the site's data in your browser settings. See the <a href="[[COOKIES]]">Cookie policy</a> for details.</p>

<h2 id="no-contact">8. No contact channel</h2>
<p>Sofia Gleda offers no way to contact it: no email address, phone number, form or page for messages. The site collects no personal data and keeps none on any server we control (section 2), so there is no data about you with us to give you, correct or delete. What the site keeps for you is only in your browser, and you manage it yourself (section 9). The data that GitHub, TMDB and the cinema and theatre websites receive is covered by their own policies.</p>

<h2 id="rights">9. Your rights and how to use them</h2>
<p>You have the right to request access to your personal data, its rectification or erasure and the restriction of its processing, and to object to processing based on legitimate interest (Articles 15, 16, 17, 18 and 21 GDPR). You have the right to data portability (Article 20 GDPR) where processing is based on consent or a contract; there is no such processing at present. If we ever ask for your consent, you will be able to withdraw it at any time.</p>
<p>In practice this means:</p>
<ul>
<li>We keep no personal data on any server we control, so there is nothing of yours with us to give you or to delete.</li>
<li>Your settings, favourite venues and the copy of the site are only in your browser. You delete them yourself by clearing the site's data (see below and the <a href="[[COOKIES]]">Cookie policy</a>). That is the erasure under Article 17 GDPR: there is no other copy.</li>
<li>The visit logs of GitHub, the site's host, are covered by <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub's privacy statement</a>; send requests about them to GitHub.</li>
<li>The data that TMDB and the cinema and theatre websites receive when images load, and YouTube (Google) when you play a trailer, is covered by their own policies; send requests about it to them.</li>
<li>Because the site has no contact channel and keeps no user accounts, it takes no requests. We hold no data that could be linked to you (Article 11 GDPR).</li>
</ul>
<p>How to clear the site's data:</p>
<ul>
<li>Chrome on a computer: click the icon to the left of the address, open the site settings and choose to delete the data.</li>
<li>Firefox: click the padlock to the left of the address and choose to clear cookies and site data.</li>
<li>Safari on a Mac: in Safari's settings, open the privacy section and manage website data, select sofiagleda.com and remove it.</li>
<li>iPhone and iPad: in Settings, open Safari, then Advanced and Website Data, and delete sofiagleda.com. If you added the site to your Home Screen, remove its icon too.</li>
<li>Every browser also lets you clear the data of all sites in its settings.</li>
</ul>
<p>This deletes the language, the mode, your favourite venues, the record of the cookie notice and the copy of the site. The site keeps working, and the cookie notice is shown again.</p>

<h2 id="complaint">10. Complaints</h2>
<p>If you believe your rights have been infringed, you can lodge a complaint with the Bulgarian Commission for Personal Data Protection (Комисия за защита на личните данни): 2 Prof. Tsvetan Lazarov Blvd, 1592 Sofia, Bulgaria, <a href="mailto:kzld@cpdp.bg">kzld@cpdp.bg</a>, <a href="https://www.cpdp.bg/">www.cpdp.bg</a>. Under Bulgarian law the complaint must be filed within 6 months of learning of the infringement and no later than two years after it occurred (Article 38 of the Personal Data Protection Act). You can also turn to the supervisory authority of the EU country where you live or work (Article 77 GDPR), or to the courts.</p>

<h2 id="changes">11. Changes</h2>
<p>When this policy changes, we publish the new version on this page with its new date. After a significant change, the cookie notice is shown again.</p>
"""

COOKIES_BG = """
<p class="lg-lead">Тази политика описва какво пази Sofia Gleda в браузъра ви и защо.</p>
<section class="lg-key" aria-labelledby="nakratko">
<h2 id="nakratko">Накратко</h2>
<ul>
<li>Sofia Gleda не поставя бисквитки.</li>
<li>Не използваме статистика за посещенията, проследяване или реклама.</li>
<li>Трейлърите на филмите се зареждат от YouTube само ако ги пуснете. Тогава YouTube може да запише свои данни в браузъра ви (вижте т. 3).</li>
<li>В браузъра ви пазим само това, без което сайтът не може да работи така, както сте поискали: езика, режима, любимите ви места, избора ви от съобщението за бисквитките и на sofiagleda.com копие на сайта за работа без интернет.</li>
<li>Затова съобщението за бисквитките само ви информира и няма какво да изключите.</li>
</ul>
</section>

<h2 id="saglasie">1. Защо не искаме съгласие</h2>
<p>Съгласие се изисква, за да се съхранява информация в устройството ви, освен когато съхраняването е строго необходимо за услуга, която сами сте поискали (чл. 5, пар. 3 от Директива 2002/58/ЕО). Такива са избраният от вас език и режим, запазените от вас любими места, записът на избора ви за бисквитките и копието на сайта, с което той се отваря бързо и работи без интернет. Друго не пазим.</p>

<h2 id="spisak">2. Какво пазим в браузъра ви</h2>
<div class="lg-item">
<h3><code>sofia-screen-v2</code></h3>
<dl>
<dt>Вид</dt><dd>Локално хранилище на браузъра (localStorage)</dd>
<dt>Съдържание</dt><dd>Езикът, режимът (кино или театър), любимите ви кина и театри, настройката „Само любимите“ и технически настройки на подредбата</dd>
<dt>За какво служи</dt><dd>Сайтът да се отваря така, както сте го оставили</dd>
<dt>Срок</dt><dd>Докато не изтриете данните на сайта от браузъра</dd>
</dl>
</div>
<div class="lg-item">
<h3><code>sofia-consent</code></h3>
<dl>
<dt>Вид</dt><dd>Локално хранилище на браузъра (localStorage)</dd>
<dt>Съдържание</dt><dd>Версията на тази политика, датата и часа, в които сте затворили съобщението за бисквитките, и включените незадължителни категории (засега няма такива)</dd>
<dt>За какво служи</dt><dd>Съобщението да не се показва при всяко посещение</dd>
<dt>Срок</dt><dd>Докато не изтриете данните на сайта; при промяна на политиката съобщението се показва отново</dd>
</dl>
</div>
<div class="lg-item">
<h3><code>sofia-gleda-v…-shell</code>, <code>sofia-gleda-v…-assets</code></h3>
<dl>
<dt>Вид</dt><dd>Кеш на service worker (Cache Storage); номерът след „v“ се сменя с всяка версия на сайта</dd>
<dt>Съдържание</dt><dd>Копие на страницата, иконите и плакатите, които сте разгледали</dd>
<dt>За какво служи</dt><dd>Сайтът да се отваря бързо и да работи без интернет; само на основния адрес на сайта (sofiagleda.com)</dd>
<dt>Срок</dt><dd>До следващата версия на сайта или докато не изтриете данните на сайта</dd>
</dl>
</div>

<h2 id="drugi">3. Бисквитки на други сайтове</h2>
<p>Част от плакатите се зареждат директно от сайтовете на кината и фестивалите. Тези сайтове могат да поставят свои бисквитки, които ние не управляваме и не можем да прочетем. Към датата на тази политика сървърът за изображения на cinemacity.bg (Cloudflare) поставя бисквитката <code>__cf_bm</code>, която служи за защита от автоматизиран трафик и изтича след 30 минути. Повече информация има в политиките на тези сайтове. Можете да блокирате бисквитките на други сайтове в настройките на браузъра си, а Sofia Gleda ще продължи да работи.</p>
<p>Трейлърите на филмите са от YouTube. Плейърът се зарежда от www.youtube-nocookie.com (режимът на YouTube с подобрена поверителност) само когато натиснете бутона за пускане, и се премахва, когато спрете видеото или затворите филма. Дотогава YouTube и Google не получават нищо и не поставят нищо в браузъра ви. След като пуснете трейлър, YouTube може да съхранява информация в браузъра ви (бисквитки или локално хранилище) по своите правила: <a href="https://www.youtube.com/t/terms">Условия за ползване на YouTube</a> и <a href="https://policies.google.com/privacy">Политика за поверителност на Google</a>. Ние не управляваме и не можем да прочетем тези данни. Не искаме отделно съгласие за това, защото плейърът се зарежда само по ваше изрично искане: натискането на бутона. Повече има в <a href="[[PRIVACY]]">Политиката за поверителност</a>.</p>

<h2 id="iztrivane">4. Как да изтриете данните</h2>
<p>Можете да изтриете данните на сайта от настройките на браузъра (например „Изчистване на данните за сайта“). Така ще изтриете и любимите си места и настройките, а съобщението за бисквитките ще се покаже отново.</p>

<h2 id="nastroyki">5. Настройки за бисквитки</h2>
<p>Връзката „Настройки за бисквитки“ в долната част на сайта отваря отново съобщението. Ако някога добавим незадължителни инструменти, например статистика за посещенията или вход с профил, те ще се включват само след изричното ви съгласие, а съобщението ще ви попита отново.</p>

<h2 id="lichni">6. Лични данни</h2>
<p>Повече за личните данни и за правата ви има в <a href="[[PRIVACY]]">Политиката за поверителност</a>.</p>
"""

COOKIES_EN = """
<p class="lg-lead">This policy describes what Sofia Gleda keeps in your browser, and why.</p>
<section class="lg-key" aria-labelledby="in-short">
<h2 id="in-short">In short</h2>
<ul>
<li>Sofia Gleda sets no cookies.</li>
<li>We use no visitor statistics, tracking or advertising.</li>
<li>Film trailers load from YouTube only if you play them. YouTube may then store its own data in your browser (see section 3).</li>
<li>Your browser keeps only what the site needs to work the way you asked: your language, mode, favourite venues, your answer to the cookie notice and, on sofiagleda.com, a copy of the site for offline use.</li>
<li>That is why the cookie notice only informs you and has nothing to switch off.</li>
</ul>
</section>

<h2 id="consent">1. Why we do not ask for consent</h2>
<p>Consent is required to store information on your device, unless the storage is strictly necessary for a service you have asked for (Article 5(3) of Directive 2002/58/EC). Your chosen language and mode, the venues you saved as favourites, the record of your cookie choice and the copy of the site that lets it open quickly and work offline are such cases. We keep nothing else.</p>

<h2 id="list">2. What we keep in your browser</h2>
<div class="lg-item">
<h3><code>sofia-screen-v2</code></h3>
<dl>
<dt>Type</dt><dd>Browser local storage (localStorage)</dd>
<dt>Contents</dt><dd>Your language, mode (cinema or theatre), favourite cinemas and theatres, the “Only my favourites” setting and technical ordering settings</dd>
<dt>Purpose</dt><dd>The site opens the way you left it</dd>
<dt>Kept</dt><dd>Until you delete the site's data from your browser</dd>
</dl>
</div>
<div class="lg-item">
<h3><code>sofia-consent</code></h3>
<dl>
<dt>Type</dt><dd>Browser local storage (localStorage)</dd>
<dt>Contents</dt><dd>The version of this policy, the date and time you closed the cookie notice, and the optional categories you switched on (there are none yet)</dd>
<dt>Purpose</dt><dd>The notice is not shown on every visit</dd>
<dt>Kept</dt><dd>Until you delete the site's data; when this policy changes, the notice is shown again</dd>
</dl>
</div>
<div class="lg-item">
<h3><code>sofia-gleda-v…-shell</code>, <code>sofia-gleda-v…-assets</code></h3>
<dl>
<dt>Type</dt><dd>Service worker cache (Cache Storage); the number after “v” changes with each version of the site</dd>
<dt>Contents</dt><dd>A copy of the page, the icons and the posters you have looked at</dd>
<dt>Purpose</dt><dd>The site opens quickly and works offline; only on the site's main address (sofiagleda.com)</dd>
<dt>Kept</dt><dd>Until the next version of the site, or until you delete the site's data</dd>
</dl>
</div>

<h2 id="third-party">3. Cookies set by other websites</h2>
<p>Some posters load directly from the websites of cinemas and festivals. Those websites may set their own cookies, which we do not control and cannot read. At the date of this policy, the image server of cinemacity.bg (Cloudflare) sets the <code>__cf_bm</code> cookie, which protects against automated traffic and expires after 30 minutes. Their own policies have more information. You can block cookies from other websites in your browser settings, and Sofia Gleda will keep working.</p>
<p>Film trailers come from YouTube. The player loads from www.youtube-nocookie.com (YouTube's privacy-enhanced mode) only when you press the play button, and it is removed when you stop the video or close the film. Until then YouTube and Google receive nothing and store nothing in your browser. Once you play a trailer, YouTube may store information in your browser (cookies or local storage) under its own rules: the <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a> and the <a href="https://policies.google.com/privacy">Google Privacy Policy</a>. We do not control and cannot read that data. We do not ask for separate consent for it, because the player loads only at your explicit request: pressing the button. The <a href="[[PRIVACY]]">Privacy policy</a> has more.</p>

<h2 id="delete">4. How to delete the data</h2>
<p>You can delete the site's data in your browser settings (for example “Clear site data”). This also deletes your favourite venues and settings, and the cookie notice will be shown again.</p>

<h2 id="settings">5. Cookie settings</h2>
<p>The “Cookie settings” link at the bottom of the site opens the notice again. If we ever add optional tools, such as visitor statistics or signing in with an account, they will only run after you explicitly agree, and the notice will ask you again.</p>

<h2 id="personal-data">6. Personal data</h2>
<p>More about personal data and your rights is in the <a href="[[PRIVACY]]">Privacy policy</a>.</p>
"""

FONTS_NOTE = {
    # while a page loads Google Fonts
    "google": {
        "bg": '<p>Шрифтовете на сайта се зареждат от Google Fonts, услуга на Google. При зареждането им '
              'браузърът ви изпраща IP адреса ви на сървърите на Google. Основание: легитимният интерес '
              'текстът да се показва с шрифтовете на сайта (чл. 6, пар. 1, буква „е“ от ОРЗД). Google LLC '
              'е сертифициран по Рамката за защита на личните данни в отношенията между ЕС и САЩ.</p>',
        "en": "<p>The site's fonts load from Google Fonts, a Google service. When they load, your browser "
              "sends your IP address to Google's servers. Legal basis: our legitimate interest in showing "
              "the text in the site's fonts (Article 6(1)(f) GDPR). Google LLC is certified under the "
              "EU-U.S. Data Privacy Framework.</p>",
    },
    # once the fonts are served from the site itself
    "self": {
        "bg": "<p>Шрифтовете също се зареждат от нашия сайт, без връзка с други сървъри.</p>",
        "en": "<p>The fonts are served from our own site too, with no request to any other server.</p>",
    },
}

TEXTS = {("terms", "bg"): TERMS_BG, ("terms", "en"): TERMS_EN,
         ("privacy", "bg"): PRIVACY_BG, ("privacy", "en"): PRIVACY_EN,
         ("cookies", "bg"): COOKIES_BG, ("cookies", "en"): COOKIES_EN}

CSS = """/* Sofia Gleda: the legal pages (scripts/gen_legal_pages.py inlines this in each page).
   The app's tokens: ink and fg, crimson accent, Onest for text, Oswald for the title. */
:root{
  --ink:#0C0A0F; --ink-2:#141018; --ink-3:#1C1622; --ink-4:#28202F;
  --line:rgba(255,255,255,.09); --line-2:rgba(255,255,255,.17);
  --fg:#F4EFF6; --fg-2:#B6ABBC; --fg-3:#7E7386;
  --crimson:#E0163A; --accent:var(--crimson);
  color-scheme:dark;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%; text-size-adjust:100%}
body{margin:0; background:var(--ink); color:var(--fg-2);
  font-family:"Onest","Segoe UI",system-ui,sans-serif; font-size:16px; line-height:1.65;
  -webkit-font-smoothing:antialiased}
a{color:var(--fg); text-decoration:underline; text-decoration-color:rgba(224,22,58,.65);
  text-underline-offset:3px; overflow-wrap:anywhere}
a:hover{text-decoration-color:var(--crimson)}
:focus-visible{outline:2px solid var(--accent); outline-offset:2px; border-radius:6px}
p,li,dd{overflow-wrap:break-word}
strong{color:var(--fg); font-weight:600}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.9em; color:var(--fg);
  background:var(--ink-3); border-radius:6px; padding:1px 6px; overflow-wrap:anywhere}

.lg-skip{position:absolute; left:12px; top:-60px; z-index:10; padding:10px 16px; border-radius:10px;
  background:var(--fg); color:var(--ink); text-decoration:none; font-weight:600}
.lg-skip:focus{top:calc(10px + env(safe-area-inset-top,0px))}
.lg-wrap{max-width:760px; margin:0 auto;
  padding-inline:max(18px,env(safe-area-inset-left,0px)) max(18px,env(safe-area-inset-right,0px))}

.lg-bar{position:sticky; top:0; z-index:5; padding-top:env(safe-area-inset-top,0px);
  background:color-mix(in srgb,var(--ink) 92%,transparent); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--line)}
.lg-bar-in{display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:60px}
.lg-brand{display:inline-flex; align-items:center; min-height:44px}
.lg-brand img{display:block; height:30px; width:auto}
.lg-lang{display:inline-flex; align-items:center; min-height:40px; padding:0 14px; border-radius:999px;
  border:1px solid var(--line-2); color:var(--fg); text-decoration:none; font-size:14px; font-weight:500}
.lg-lang:hover{background:rgba(255,255,255,.08)}

.lg-docs{display:flex; flex-wrap:wrap; gap:8px; margin:28px 0 22px}
.lg-docs a{display:inline-flex; align-items:center; min-height:38px; padding:0 15px; border-radius:999px;
  border:1px solid var(--line-2); color:var(--fg-2); text-decoration:none; font-size:14px}
.lg-docs a:hover{color:var(--fg); background:rgba(255,255,255,.06)}
.lg-docs a[aria-current="page"]{background:var(--fg); border-color:var(--fg); color:var(--ink); font-weight:600}

h1{display:flex; align-items:center; gap:12px; margin:0 0 6px; color:var(--fg);
  font-family:Oswald,"Arial Narrow",sans-serif; font-weight:600; text-transform:uppercase;
  letter-spacing:.05em; font-size:clamp(28px,6vw,40px); line-height:1.1; text-wrap:balance}
h1::before{content:""; flex:0 0 5px; width:5px; height:.9em; border-radius:2px; background:var(--accent)}
.lg-updated{margin:0 0 24px; color:var(--fg-3); font-size:14px}
.lg-lead{font-size:17px; color:var(--fg)}
h2{margin:40px 0 10px; color:var(--fg); font-size:20px; font-weight:600; line-height:1.3; text-wrap:balance;
  scroll-margin-top:84px}
h3{margin:0; color:var(--fg); font-size:15px; font-weight:600}
ul{padding-left:22px}
li{margin:6px 0}

.lg-key{margin:22px 0 8px; padding:18px 20px 12px; border-radius:18px; background:var(--ink-2);
  border:1px solid var(--line); border-left:3px solid var(--accent)}
.lg-key h2{margin:0 0 4px; font-family:Oswald,"Arial Narrow",sans-serif; text-transform:uppercase;
  letter-spacing:.06em; font-size:17px}
.lg-key li{color:var(--fg)}
.lg-warn{margin:12px 0; padding:12px 16px; border-radius:14px; color:var(--fg);
  background:rgba(217,178,60,.08); border:1px solid rgba(217,178,60,.3)}
.lg-attr{margin:14px 0; padding:14px 16px; border-radius:14px; background:var(--ink-2); border:1px solid var(--line)}
.lg-attr p{margin:6px 0}
.lg-tmdb{display:block; height:20px; width:auto; margin:2px 0 8px}
.lg-item{margin:12px 0; padding:16px 18px; border-radius:16px; background:var(--ink-2); border:1px solid var(--line)}
.lg-item dl{display:grid; grid-template-columns:minmax(120px,max-content) 1fr; gap:6px 16px; margin:12px 0 0}
.lg-item dt{color:var(--fg-3); font-size:14px}
.lg-item dd{margin:0}
@media (max-width:520px){
  .lg-item dl{grid-template-columns:1fr; gap:2px}
  .lg-item dt{margin-top:8px}
}

.lg-foot{margin-top:56px; border-top:1px solid var(--line); color:var(--fg-3); font-size:14px;
  padding-block:20px calc(36px + env(safe-area-inset-bottom,0px))}
.lg-foot-nav{display:flex; flex-wrap:wrap; gap:4px 18px}
.lg-foot-nav a{display:inline-flex; align-items:center; min-height:36px; color:var(--fg-2)}
/* the line and the links span the footer's column and reflow with it (owner, 22 Oct 2026) */
.lg-foot p{margin:10px 0 0}
"""


def tpl(s, **kw):
    """Fill [[KEY]] markers; a marker left over is a bug in this script."""
    for k, v in kw.items():
        s = s.replace(f"[[{k}]]", v)
    left = re.findall(r"\[\[[A-Z_]+\]\]", s)
    if left:
        sys.exit(f"gen_legal_pages: unfilled markers {sorted(set(left))}")
    return s


def consent_version():
    """The cookie and privacy policies' version is the app's CONSENT_VERSION: when it changes the
    banner asks again, and the policies show it as their date."""
    m = re.search(r'^const CONSENT_VERSION="(\d{4}-\d{2}-\d{2})";', UI_SRC.read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit(f"gen_legal_pages: const CONSENT_VERSION not found in {UI_SRC}")
    return m.group(1)


def uses_google_fonts():
    """Whether any page loads Google Fonts: this font block, or the app's own."""
    return any("fonts.googleapis.com" in s or "fonts.gstatic.com" in s
               for s in (FONTS, UI_SRC.read_text(encoding="utf-8")))


def path_of(doc, lang):
    return f"legal/{doc}/" if lang == "bg" else f"legal/en/{doc}/"


def human_date(iso, lang):
    y, m, d = (int(x) for x in iso.split("-"))
    month = UI[lang]["months"][m - 1]
    return f"{d} {month} {y} г." if lang == "bg" else f"{d} {month} {y}"


def tmdb_logo(root):
    if not TMDB_LOGO.exists():
        return ""
    return f'<img class="lg-tmdb" src="{root}icons/tmdb.svg" alt="TMDB" height="20">'


def page(doc, lang, updated, google):
    u = UI[lang]
    here = path_of(doc, lang)
    root = "../" * here.count("/")
    other = "en" if lang == "bg" else "bg"
    content = tpl(TEXTS[(doc, lang)],
                  TERMS="../terms/", PRIVACY="../privacy/", COOKIES="../cookies/",
                  TMDB_LOGO=tmdb_logo(root),
                  FONTS_NOTE=FONTS_NOTE["google" if google else "self"][lang])
    links = "\n".join(
        f'<a href="../{d}/"' + (' aria-current="page"' if d == doc else "") + f'>{html.escape(u["nav"][d])}</a>'
        for d in DOCS)
    head = tpl(HEAD, LANG=lang, TITLE=html.escape(u["titles"][doc]), DESC=html.escape(u["desc"][doc]),
               CANONICAL=f"{SITE_URL}/{here}", URL_BG=f"{SITE_URL}/{path_of(doc, 'bg')}",
               URL_EN=f"{SITE_URL}/{path_of(doc, 'en')}", SITE=SITE_URL, OG_LOCALE=u["og_locale"],
               FONTS=FONTS, ROOT=root, CSS=CSS)
    body = tpl(BODY, ROOT=root, SKIP=html.escape(u["skip"]), HOME=html.escape(u["home"]),
               OTHER_URL=root + path_of(doc, other), OTHER_LANG=other,
               OTHER_NAME=html.escape(u["other_name"]), DOCS_LABEL=html.escape(u["docs_label"]),
               DOC_LINKS=links, TITLE=html.escape(u["titles"][doc]),
               UPDATED_LABEL=html.escape(u["updated"]), UPDATED_ISO=updated,
               UPDATED_TEXT=human_date(updated, lang), CONTENT=content.strip(),
               FOOT_LABEL=html.escape(u["foot_label"]), BACK=html.escape(u["back"]),
               COOKIE_SETTINGS=html.escape(u["cookie_settings"]),
               FOOT_LINE=html.escape(u["foot_line"]))
    return here, head + body


def main():
    version = consent_version()
    google = uses_google_fonts()
    updated = {"terms": TERMS_UPDATED, "privacy": version, "cookies": version}
    for doc in DOCS:
        for lang in LANGS:
            here, text = page(doc, lang, updated[doc], google)
            target = ROOT / here / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"  wrote {here}index.html")
    print(f"gen_legal_pages: 6 pages (policies {version}, "
          f"fonts {'Google Fonts' if google else 'self-hosted'}, "
          f"TMDB logo {'shown' if TMDB_LOGO.exists() else 'MISSING: add icons/tmdb.svg'})")


if __name__ == "__main__":
    main()
