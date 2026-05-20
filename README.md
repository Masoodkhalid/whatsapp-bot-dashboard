# WhatsApp Bot Dashboard

A professional browser-based dashboard to send WhatsApp messages in bulk using Selenium automation.

---

## 📋 Table of Contents
- [Requirements](#requirements)
- [Installation on macOS](#installation-on-macos)
- [Installation on Windows](#installation-on-windows)
- [Running the Bot](#running-the-bot)
- [First Time Login](#first-time-login)
- [How to Use](#how-to-use)
- [CSV Format](#csv-format)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- **Python 3.9+**
- **Google Chrome** (latest version)
- Internet connection
- A WhatsApp account linked to your phone

---

## Installation on macOS

### Step 1 — Install Python
Check if Python is already installed:
```bash
python3 --version
```
If not installed, download from **https://www.python.org/downloads/** and install.  
Or install via Homebrew:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python
```

### Step 2 — Install Google Chrome
Download from **https://www.google.com/chrome/** and install normally.

### Step 3 — Download the Project
Clone via Git:
```bash
git clone https://github.com/YOUR_USERNAME/whatsapp-bot.git
cd whatsapp-bot
```
Or download the ZIP from GitHub → Extract → open Terminal in that folder.

### Step 4 — Create a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```
You should see `(venv)` in your terminal prompt.

### Step 5 — Install Dependencies
```bash
pip install -r requirements.txt
```
This installs: `flask`, `selenium`, `webdriver-manager`

### Step 6 — Run the App
```bash
python3 app.py
```

### Step 7 — Open the Dashboard
Open your browser and go to:
```
http://localhost:5001
```

---

## Installation on Windows

### Step 1 — Install Python
1. Go to **https://www.python.org/downloads/**
2. Download the latest Python 3.x installer
3. Run the installer — **✅ check "Add Python to PATH"** before clicking Install
4. Verify installation — open **Command Prompt** and run:
```cmd
python --version
```

### Step 2 — Install Google Chrome
Download from **https://www.google.com/chrome/** and install normally.

### Step 3 — Download the Project
Clone via Git (install Git from **https://git-scm.com** if needed):
```cmd
git clone https://github.com/YOUR_USERNAME/whatsapp-bot.git
cd whatsapp-bot
```
Or download the ZIP from GitHub → Extract → open Command Prompt in that folder:
```cmd
cd C:\Users\YourName\Downloads\whatsapp-bot
```

### Step 4 — Create a Virtual Environment
```cmd
python -m venv venv
venv\Scripts\activate
```
You should see `(venv)` in your prompt.

> ⚠️ If you get a script execution error, run this first:
> ```cmd
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Step 5 — Install Dependencies
```cmd
pip install -r requirements.txt
```

### Step 6 — Run the App
```cmd
python app.py
```

### Step 7 — Open the Dashboard
Open your browser and go to:
```
http://localhost:5001
```

---

## Running the Bot

Every time you want to use the bot:

**macOS:**
```bash
cd whatsapp-bot
source venv/bin/activate
python3 app.py
```

**Windows:**
```cmd
cd whatsapp-bot
venv\Scripts\activate
python app.py
```

Then open **http://localhost:5001**

---

## First Time Login

1. On the Dashboard, click **▶ Start Bot**
2. A Chrome window will open automatically with WhatsApp Web
3. Scan the **QR code** with your phone:
   - Open WhatsApp on your phone
   - Tap **Menu (⋮)** → **Linked Devices** → **Link a Device**
   - Scan the QR code on screen
4. Once logged in, the session is saved to `wa_profile/`
5. **You only need to scan once** — future runs restore the session automatically

---

## How to Use

### Dashboard
- View stats (sent / failed / skipped / pending)
- Add contacts manually (Name + Phone in international format)
- Import contacts from a CSV file
- Delete or reset individual contacts

### Compose — Send by Template
1. Go to **Compose** page
2. Select the **📋 Send by Template** tab
3. Type your message in the template editor
   - Use `{name}` to personalise with the contact's name
   - Use formatting buttons for **Bold**, _Italic_, ~~Strike~~, `Mono`
4. Set **Delay** (seconds between messages, min 30 recommended)
5. Click **▶ Send by Template**

### Compose — Send by CSV
1. Go to **Compose** page
2. Select the **📂 Send by CSV** tab
3. Upload your CSV file (see format below)
4. Preview the contacts in the table
5. Click **▶ Send by CSV**
   - Each contact uses their own `custom_message` column
   - If `custom_message` is empty, the template is used as fallback

### Live Logs
- Real-time terminal showing every action
- Color-coded: ✓ green = sent, ✗ red = failed, ⚠ yellow = warning
- Filter by type, export logs

### History
- Full log of every message attempt saved to `logs.csv`
- **✏️ Edit** any row (fix name, status, note)
- **🗑 Delete** any individual row
- **Clear All** or **Export CSV**
- Already-sent numbers are automatically skipped on re-run

---

## CSV Format

```csv
name,phone,custom_message
Moiz,+923158442355,
Alice,+14155552671,"Hey {name}, special offer just for you!"
Bob,+923001234567,"Hi {name}, your order is ready!"
```

**Rules:**
- `phone` must include the country code with `+` (e.g. `+92`, `+1`, `+44`)
- `custom_message` is optional — leave blank to use the template
- Use `{name}` inside messages as a placeholder
- Save the file as UTF-8 encoded CSV

---

## Project Structure

```
whatsapp-bot/
├── app.py                  Flask backend + Selenium automation
├── templates/
│   └── index.html          Full dashboard UI (single-page app)
├── requirements.txt        Python dependencies
├── README.md               This file
├── .gitignore              Excludes personal data from git
│
│   (auto-created at runtime, git-ignored)
├── wa_profile/             Chrome profile with WhatsApp session
├── contacts.csv            Current contact batch
└── logs.csv                Permanent message history
```

---

## Troubleshooting

### Port already in use
```bash
# macOS / Linux
lsof -ti :5001 | xargs kill -9

# Windows
netstat -ano | findstr :5001
taskkill /PID <PID> /F
```

### Chrome doesn't open / ChromeDriver error
- Make sure **Google Chrome** is installed
- `webdriver-manager` downloads the correct ChromeDriver automatically
- If it fails, update it: `pip install --upgrade webdriver-manager`

### QR code not showing
- Delete the `wa_profile/` folder and restart the app — it will show a fresh QR

### Messages not sending
- Make sure you're logged into WhatsApp Web in the bot's Chrome window
- Increase the **Wait Time** setting on the Compose page (default 20s)
- Check the **Live Logs** page for specific error messages

### "venv\Scripts\activate" not working on Windows
Run PowerShell as Administrator and execute:
```cmd
Set-ExecutionPolicy RemoteSigned
```

### Phone number invalid error
- Always include the country code: `+923001234567` not `03001234567`
- No spaces or dashes — or the bot will clean them automatically

---

## ⚠️ Important Notes

- **Do not close** the Chrome window that opens — the bot uses it to send messages
- Keep a **minimum 30-second delay** between messages to avoid WhatsApp rate limits
- This tool is for **personal/business use only** — do not use it for spam
- WhatsApp may temporarily restrict accounts that send too many messages too quickly
