# WhatsApp Bot Dashboard

A professional browser-based dashboard to send WhatsApp messages in bulk using Selenium automation.

## Setup

```bash
pip install -r requirements.txt
```

### requirements.txt
```
flask
selenium
webdriver-manager
```

## Run

```bash
python app.py
```

Then open **http://localhost:5001** in your browser.

## Features

- **Dashboard** — stats overview, manage contacts, add/delete/reset contacts
- **Compose** — two send modes:
  - 📋 **Send by Template** — type a message template, send to all contacts (ignores custom_message)
  - 📂 **Send by CSV** — upload a CSV with per-contact custom messages
- **Template Editor** — formatting toolbar (Bold/Italic/Strike/Mono), variable chips (`{name}`, `{phone}`), live WhatsApp bubble preview
- **Live Logs** — real-time SSE terminal with color-coded output, filter tabs, auto-scroll
- **History** — full message log in `logs.csv` with per-row **Edit** and **Delete** actions
- **Duplicate detection** — already-sent contacts are automatically skipped
- **Phone validation** — invalid numbers are caught before sending
- **Session persistence** — WhatsApp login saved to `wa_profile/` (scan QR once)

## First Run

On first launch, a Chrome window will open with WhatsApp Web.  
Scan the QR code with your phone — the session is saved and you won't need to scan again.

## CSV Format

```
name,phone,custom_message
Moiz,+923158442355,
Alice,+14155552671,"Hey {name}, special offer just for you!"
```

- `custom_message` is optional — leave blank to use the template
- Phone numbers must be in international format with `+` country code

## Project Structure

```
app.py              — Flask backend (Selenium automation, REST API)
templates/
  index.html        — Full dashboard UI
requirements.txt    — Python dependencies
wa_profile/         — Chrome profile for WhatsApp session (auto-created, git-ignored)
logs.csv            — Message history (auto-created, git-ignored)
contacts.csv        — Current contact batch (auto-created, git-ignored)
```
