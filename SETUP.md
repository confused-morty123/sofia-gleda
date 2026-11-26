# Publishing Sofia Gleda as a website — a step-by-step guide

This guide takes you from the folder on your Mac to a live website that anyone
can open, on a phone or a computer, that **updates its own listings every
Sunday** without you touching anything.

No coding. No software to install. Everything happens on a free service called
**GitHub**. Expect it to take about 20–30 minutes the first time.

At the end you will have:

- a public address like `https://yourname.github.io/sofia-gleda/`,
- a site that can be "installed" on a phone like a normal app,
- an automatic weekly refresh of films, plays, posters and prices.

---

## What each thing is (in one line)

- **GitHub** — a free website that stores your files and can host a site from them.
- **Repository ("repo")** — just a folder, living on GitHub instead of your Mac.
- **GitHub Pages** — the free feature that turns that folder into a real website.
- **GitHub Actions** — a free robot that runs your weekly refresh on a schedule.
- **Secret** — a password you give the robot privately, so it never appears in public.

You do not need to understand these deeply. Follow the steps.

---

## Step 1 — Create a free GitHub account

1. Go to **https://github.com/signup**.
2. Enter your email, a password and a username. Your username becomes part of
   your website address, so pick something clean — e.g. `georgi` gives
   `https://georgi.github.io/...`.
3. Confirm your email when GitHub sends you a code.

That's it — the free plan includes everything here.

---

## Step 2 — Create the repository

1. Once logged in, click the **+** at the top-right of GitHub → **New repository**.
2. **Repository name:** type `sofia-gleda`.
3. Leave it **Public** (required for free hosting).
4. Do **not** tick "Add a README" — your folder already has one.
5. Click **Create repository**.

You'll land on a page that says "Quick setup". Leave it open.

---

## Step 3 — Upload the files

1. On that page, click the link **"uploading an existing file"**
   (or go to the **Add file** button → **Upload files**).
2. Open the **`webapp`** folder on your Mac (the one this guide is in).
3. Select **everything inside it** and drag it all into the browser window.
   Make sure you include the hidden **`.github`** folder — it holds the weekly
   robot. If you can't see it in Finder, press **⌘ + Shift + .** (dot) to show
   hidden files, then drag it in too.
4. Wait for every file to finish uploading (you'll see them listed).
5. Scroll down and click **Commit changes**.

> If dragging the `.github` folder is fiddly, don't worry — you can also create
> it later. See "Troubleshooting" at the end.

---

## Step 4 — Turn on the website (GitHub Pages)

1. In your repository, click **Settings** (top menu).
2. In the left sidebar, click **Pages**.
3. Under **Build and deployment → Source**, choose **Deploy from a branch**.
4. Under **Branch**, pick **main** and **/ (root)**, then click **Save**.
5. Wait 1–2 minutes. Refresh the page. It will show:
   **"Your site is live at https://yourname.github.io/sofia-gleda/"**.

Open that address — Sofia Gleda is now on the internet. 🎬

---

## Step 5 — Get your TMDB token ready

The film posters and English titles come from **TMDB** (The Movie Database).
The weekly robot needs a free key called a **token** to fetch them.

- You already have one (it's the long string I gave you). If you ever need a new
  one: create a free account at **https://www.themoviedb.org/**, then go to
  **Settings → API → API Read Access Token** and copy it.

Keep that token handy for the next step. **Never paste it into a file** — only
into the private box in Step 6.

---

## Step 6 — Give the token to the robot (as a Secret)

1. In your repository, go to **Settings**.
2. Left sidebar: **Secrets and variables → Actions**.
3. Click **New repository secret**.
4. **Name:** type exactly `TMDB_TOKEN` (capital letters, with the underscore).
5. **Secret:** paste your TMDB token.
6. Click **Add secret**.

The token is now stored privately. It never appears on your public site.

---

## Step 7 — Run the first refresh yourself (optional but nice)

The robot runs automatically every Sunday, but you can trigger it once now to
see it work:

1. Go to the **Actions** tab of your repository.
2. If GitHub asks you to enable Actions, click **"I understand… enable them"**.
3. Click **Weekly refresh** on the left.
4. Click **Run workflow** (right-hand side) → **Run workflow** again.
5. Watch it run (a couple of minutes). A green tick means it worked.

After it finishes it will have updated the listings and posters, and your live
site will show the fresh data within a minute or two.

---

## Step 8 — Install it on your phone (optional)

Because Sofia Gleda is a **Progressive Web App**, it can live on your home
screen like a normal app and even work offline:

- **iPhone (Safari):** open the site → tap the **Share** button → **Add to Home
  Screen**.
- **Android (Chrome):** open the site → menu **⋮** → **Install app** / **Add to
  Home screen**.

---

## How the weekly update works (so you know it's really automatic)

Every **Sunday at ~06:00 Sofia time**, the robot:

1. re-reads the cinema and theatre programmes from their websites,
2. refreshes film posters, English titles and ticket prices,
3. rebuilds the page, and
4. saves it back to your repository — which makes the live site update itself.

If a source website is down or changes, the robot **keeps last week's data for
that venue** rather than emptying it, and records what happened in a file called
`build_report.json` in your repository. Nothing breaks; the site stays up.

> GitHub pauses the weekly robot if a repository has had **no activity for 60
> days**. If you ever get an email about that, just click the link it contains,
> or press **Run workflow** once (Step 7), and it resumes.

---

## Changing things later

- **Edit a file:** open it on GitHub, click the pencil ✏️, make your change,
  click **Commit changes**. The site updates in a minute.
- **Replace the whole app:** re-upload the files (Step 3) — GitHub keeps the
  history, so nothing is ever truly lost.

---

## Troubleshooting

**"My site shows a README or a file list, not the app."**
Pages is serving the wrong thing. Re-check Step 4: Source = *Deploy from a
branch*, Branch = *main*, folder = */ (root)*.

**"The Actions tab is empty / the weekly robot didn't appear."**
The hidden `.github` folder didn't upload. On GitHub: **Add file → Create new
file**, type `.github/workflows/refresh.yml` as the name (GitHub creates the
folders as you type the slashes), then paste in the contents of that file from
your Mac and commit.

**"The posters aren't updating."**
Check the secret in Step 6 is named exactly `TMDB_TOKEN`. Then re-run the
workflow (Step 7) and open **build_report.json** in your repo to see what the
robot reported.

**"I want a nicer address (my own domain)."**
That's optional and costs money (a domain is ~£10/year). In **Settings → Pages
→ Custom domain** you can point one you own at the site. Not needed to go live.

---

That's everything. Once Steps 1–6 are done, Sofia Gleda runs itself.
