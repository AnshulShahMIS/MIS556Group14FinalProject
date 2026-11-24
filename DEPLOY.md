# Deploying Saguaro: GitHub + Streamlit Community Cloud

**Deployed:** https://saguaro.streamlit.app, from https://github.com/AnshulShahMIS/MIS556Group14FinalProject (branch `main`, `app.py`, Python 3.13).

**GitHub** holds the code and project files (what the professor browses). **Streamlit Community Cloud**
runs the app from that GitHub repository and gives it a permanent public link. Both are free.

Everything the app needs is already in the repository: the processed catalog, the MiniLM place and
review vectors, and the team workbook. Nothing has to be rebuilt on the server.

## 1. Put the project on GitHub (about 5 minutes)

1. Sign in at [github.com](https://github.com) (or create a free account).
2. Click **+** (top right) → **New repository**.
   - Name: `MIS556Group14FinalProject`
   - Visibility: **Public** (the professor can open it without an invite, and Streamlit deploys it for free)
   - Do **not** tick "Add a README" or ".gitignore" (the project already has them).
   - Click **Create repository** and copy its URL, e.g. `https://github.com/AnshulShahMIS/MIS556Group14FinalProject.git`.
3. In PowerShell, inside the project folder, run (one at a time):

```powershell
git remote add origin https://github.com/AnshulShahMIS/MIS556Group14FinalProject.git
```

```powershell
git push -u origin main
```

   Windows opens a GitHub sign-in window the first time; sign in there. The push uploads about 120 MB,
   so give it a minute.

4. Refresh the repository page: you should see `app.py`, `src/`, `data/`, `README.md` and the rest.
   Share this link with the professor - the README explains the project, the 12 agents and the results.

## 2. Deploy the app on Streamlit Community Cloud (about 10 minutes)

1. Go to [share.streamlit.io](https://share.streamlit.io) and choose **Continue with GitHub**, then allow access.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `AnshulShahMIS/MIS556Group14FinalProject`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** `saguaro` → `https://saguaro.streamlit.app` (the live app)
4. Open **Advanced settings**:
   - **Python version:** `3.13`
   - **Secrets:** paste only the keys you want (all optional - see below). Leave it empty to start.
5. Click **Deploy**. The first build installs the libraries (about 5-8 minutes). The first visit then
   takes about 2 minutes while the agents warm up; after that it's fast.

### Secrets (optional)

Paste in TOML format, for example:

```toml
GOOGLE_PLACES_API_KEY = "..."
TICKETMASTER_API_KEY = "..."
TAVILY_API_KEY = "..."
TOMTOM_API_KEY = "..."
AZ_REQUIRE_USER_KEY = "1"
```

- **Don't put your own `OPENAI_API_KEY` here** unless you're happy to pay for every visitor's GPT-4o use.
  Visitors can paste their own key with the **OpenAI key** button; without one the built-in planner runs.
  `AZ_REQUIRE_USER_KEY = "1"` makes sure the server's key is never used for visitors.
- Never commit keys to GitHub. The `.gitignore` already excludes `.env` and `.streamlit/secrets.toml`.

## 3. Updating the app later

Change the code, then:

```powershell
git add -A
```

```powershell
git commit -m "Describe the change"
```

```powershell
git push
```

Streamlit Cloud redeploys automatically within a minute or two.

## Good to know

- **Sleeping:** free apps sleep after a few days without visitors. The page then shows a
  "Yes, get this app back up" button; it wakes in about 2 minutes. Open it once before a presentation.
- **Saved data resets:** remembered profiles, saved trips, shared plans and "closed for good" reports are
  stored on the server's disk, which Streamlit resets when the app restarts or redeploys.
- **Rebuilding the data** (only if the workbook changes): put `arizona_places_extended.csv` (not in the
  repository - it's over GitHub's 100 MB limit) in `data/raw/`, then run
  `$env:PYTHONPATH="src"; py -m azmulti.build` and commit the new files in `data/processed/`.
