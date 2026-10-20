# Toronto Rideshare Board — public website package

A complete, publish-ready static website for the **Toronto Rideshare Board**:
a daily-updated board of Kijiji Toronto rideshare & carpool ads, grouped into
**Toronto → Montréal**, **Toronto → Ottawa**, and other Toronto rides, with
route weather (Toronto, Kingston, Ottawa, Montréal), a pickup-spot map, and
each ride's departure day/time and phone number highlighted on its card.

Hosting is **free** (GitHub Pages) and the site **updates itself daily** via
GitHub Actions. SEO is switched on: the listings are baked into the static
HTML, and `robots.txt` explicitly welcomes all search engines **and** AI
crawlers.

## What's in the package

| Path | What it is |
|---|---|
| `index.html` | The public page, with the current listings, weather snapshot, meta/Open Graph/Twitter tags and JSON-LD structured data baked in. Generated — do not hand-edit (edit the template instead). |
| `templates/index.template.html` | The page template the generator fills in. |
| `data/listings.json` | Cleaned listings + counts + weather snapshot, refreshed daily. The page also fetches this on open and swaps in anything newer. |
| `scripts/update-listings.py` | The generator (Python 3, standard library only). Pulls Kijiji, filters, extracts, fetches weather, rewrites the JSON, `index.html` and `sitemap.xml`. |
| `.github/workflows/update-and-deploy.yml` | Daily GitHub Actions job: run the generator → commit the fresh data → deploy to GitHub Pages. |
| `robots.txt` | Allows every crawler, including AI crawlers (GPTBot, ChatGPT-User, ClaudeBot, Claude-Web, PerplexityBot, Google-Extended, CCBot, …). |
| `sitemap.xml` | Sitemap (lastmod refreshed by the generator). |

## How the daily update works

1. GitHub Actions runs at **11:15 UTC** daily (≈ 7:15 a.m. Toronto in summer,
   6:15 a.m. in winter) — or any time from the **Actions → Update listings &
   deploy → Run workflow** button.
2. `scripts/update-listings.py` fetches the Kijiji Toronto rideshare category
   and reads the listings out of the page's embedded data.
3. **Hard filters** are applied: rides serving **Brampton or Windsor** are
   excluded, and Kijiji paid promoted **“Top Ad”** listings are excluded. The
   page shows a small “N ads hidden by filters” note.
4. For each kept ad it extracts, from the ad's own text only, a **departure
   day/time** and a **phone number** (shown highlighted; omitted when the ad
   doesn't state one — never invented).
5. It fetches current weather for Toronto, Kingston, Ottawa and Montréal from
   Open-Meteo (no API key), then rewrites `data/listings.json`, regenerates
   `index.html` with the listings baked in, and refreshes the sitemap date.
6. The workflow commits the changes and deploys the site to GitHub Pages.

Run it locally any time with:

```bash
python3 scripts/update-listings.py
```

## Publish it on GitHub Pages (free, under your own account)

Publishing needs **your own free GitHub account** — the site, the daily
update job and the traffic all live under your account.

1. **Create the account** at <https://github.com/signup> if you don't have one.
2. **Create a new repository** named exactly **`toronto-rideshare-board`**
   (public, no README needed) at <https://github.com/new>.
3. **Upload this package**: either push the folder with git —
   ```bash
   cd toronto-rideshare-site
   git init && git add . && git commit -m "Toronto Rideshare Board"
   git branch -M main
   git remote add origin https://github.com/patpound2k/toronto-rideshare-board.git
   git push -u origin main
   ```
   — or use GitHub's web upload (“uploading an existing file”) and commit.
   > Note: the `.github/workflows/` folder must be included, or the daily
   > update and the Pages deployment won't run.
4. **Turn on Pages**: in the repo, go to **Settings → Pages → Build and
   deployment → Source** and choose **GitHub Actions**.
5. **Set the real canonical URL**: the package ships with the placeholder
   `patpound2k` in `index.html`, `templates/index.template.html`,
   `robots.txt` and `sitemap.xml`. Replace it with your actual GitHub
   username in all four files (find-and-replace `patpound2k`),
   then commit. Your site address will be
   `https://patpound2k.github.io/toronto-rideshare-board/`.
6. **Run the first build**: go to the **Actions** tab, open **Update listings
   & deploy**, and click **Run workflow**. When it finishes (a couple of
   minutes), the site is live at the address above.

## Get it indexed by search engines and AI

- `robots.txt` already allows all crawlers, including AI crawlers — nothing
  to configure.
- **Google**: open <https://search.google.com/search-console>, add a
  **URL-prefix property** with your site address, verify ownership (the HTML
  meta-tag or file method works with GitHub Pages), then go to **Sitemaps**
  and submit `sitemap.xml`.
- **Bing** (which also feeds ChatGPT search and DuckDuckGo): open
  <https://www.bing.com/webmasters>, add the site (you can import it from
  Google Search Console), and submit `sitemap.xml` there too.
- AI assistants that crawl the open web (ChatGPT, Claude, Perplexity, …) can
  read the page as soon as it's live and indexed — the listings are real HTML
  with JSON-LD structured data, so they don't need JavaScript to understand it.
- Indexing isn't instant: expect days for the first crawl, then the daily
  refresh keeps the content (and the sitemap date) current.

## Notes & limitations

- Data source: the public Kijiji Toronto rideshare category
  (`kijiji.ca/b-rideshare-carpool/city-of-toronto`). Listings are the first
  page of current ads (~40), matching the private board. If Kijiji changes
  its page structure, `scripts/update-listings.py` may need a tweak — the
  workflow run will fail loudly rather than publish a broken page.
- Prices in about half of Kijiji ads aren't published; those cards show
  “See ad”. Pickup coordinates come from the ads; an ad without coordinates
  appears in the list without a map pin.
- The map uses Leaflet + OpenStreetMap tiles (free, no key). If the map
  library or tiles can't load on a visitor's network, the page falls back to
  the numbered pickup index, which is synced with the ride cards.
- Accident/incident reports from the private Muse board are **not** part of
  this public package (they were gathered from news/official posts at each
  check, which doesn't translate to an unattended static site).
- This board is not affiliated with Kijiji. Always confirm price, time and
  pickup with the driver before travelling.
