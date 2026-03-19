# BIT Durg Attendance Tracker

A Flask + Playwright web app that fetches and calculates attendance from the BIT Durg ERP portal.

---

## 🚀 Deploy on Railway (Free)

### Step 1 — Push to GitHub
1. Create a new GitHub repository (e.g. `bit-attendance`)
2. Upload all files from this folder to the repo (drag & drop on GitHub.com works fine)

### Step 2 — Deploy on Railway
1. Go to [railway.app](https://railway.app) and sign up with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your `bit-attendance` repository
4. Railway auto-detects Python and builds it
5. Wait ~2-3 minutes for the build to finish
6. Click **"Generate Domain"** under Settings → Networking to get your public URL

That's it! ✅

---

## 📁 Project Structure

```
bit-attendance/
├── app.py              # Flask backend
├── templates/
│   └── index.html      # Frontend UI
├── requirements.txt    # Python dependencies
├── Procfile            # Railway start command
├── railway.json        # Railway config
└── .gitignore
```

---

## ⚙️ Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium

# Run the app
python app.py
```

Then open http://localhost:8080

---

## 🔒 Security Notes
- Passwords are **not stored** anywhere — they are only used in-memory to log in and scrape attendance, then discarded.
- Do not commit any `.csv` files with credentials.
