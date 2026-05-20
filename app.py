"""
WhatsApp Web Bot — Flask Backend
Run with: py app.py  →  http://localhost:5001
"""

import csv
import io
import json
import logging
import platform
import socket
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Files ──────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
CONTACTS_FILE  = BASE_DIR / "contacts.csv"
LOGS_FILE      = BASE_DIR / "logs.csv"
WA_PROFILE_DIR = BASE_DIR / "wa_profile"   # bot's own Chrome profile (login saved here)

CONTACT_FIELDS = ["name", "phone", "custom_message", "status", "sent_at"]
LOG_FIELDS     = ["timestamp", "name", "phone", "message", "status", "note"]

# ── In-memory state ────────────────────────────────────────────────────────────
contacts: list[dict] = []
bot_running  = False
bot_thread: threading.Thread | None = None
log_messages: list[str] = []

# ── Selenium driver ────────────────────────────────────────────────────────────
_driver: webdriver.Chrome | None = None


# ── Chrome helpers ─────────────────────────────────────────────────────────────

CHROME_DEBUG_PORT = 9222

# macOS / Windows Chrome binary paths
_CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_CHROME_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _is_debug_port_open() -> bool:
    """Return True if Chrome is already listening on the debug port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("localhost", CHROME_DEBUG_PORT)) == 0


def _connect_to_existing_chrome() -> webdriver.Chrome | None:
    """Attach Selenium to already-running Chrome via debug port. Returns None if unavailable."""
    if not _is_debug_port_open():
        return None
    try:
        opts = ChromeOptions()
        opts.add_experimental_option("debuggerAddress", f"localhost:{CHROME_DEBUG_PORT}")
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=opts)
        _ = driver.window_handles
        return driver
    except Exception:
        return None


def _build_own_driver() -> webdriver.Chrome:
    """Launch the bot's own Chrome using wa_profile (fallback when user's Chrome unavailable)."""
    WA_PROFILE_DIR.mkdir(exist_ok=True)
    opts = ChromeOptions()
    opts.add_argument(f"--user-data-dir={WA_PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=opts)
    driver.maximize_window()
    return driver


def _ensure_whatsapp_tab(driver: webdriver.Chrome):
    """
    Switch to an already-open WhatsApp Web tab if one exists.
    Only opens a new tab if WhatsApp isn't open anywhere.
    """
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if "web.whatsapp.com" in driver.current_url:
            push_log("✓ Found your existing WhatsApp tab — using it.")
            return
    # No WhatsApp tab found — open one without disturbing other tabs
    push_log("Opening WhatsApp Web in a new tab…")
    driver.execute_script("window.open('https://web.whatsapp.com', '_blank');")
    time.sleep(4)
    driver.switch_to.window(driver.window_handles[-1])


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    try:
        driver.find_element(By.CSS_SELECTOR, 'canvas[aria-label="Scan me!"]')
        return False
    except Exception:
        return True


def _wait_for_login(driver: webdriver.Chrome, timeout: int = 120) -> bool:
    push_log("⚠ Please scan the QR code in the Chrome window to log in to WhatsApp.")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_logged_in(driver):
            push_log("✓ WhatsApp logged in — session saved. Starting sends…")
            return True
        time.sleep(2)
    push_log("✗ QR scan timed out. Stop the bot and try again.")
    return False


def get_driver() -> webdriver.Chrome:
    global _driver
    if _driver is not None:
        try:
            _ = _driver.window_handles
            return _driver
        except Exception:
            close_driver()

    # ── 1. Try the user's own Chrome (debug port must be open) ───────────────
    push_log("Connecting to Chrome…")
    driver = _connect_to_existing_chrome()
    if driver:
        _driver = driver
        push_log("✓ Connected to YOUR Chrome — no new window opened.")
        _ensure_whatsapp_tab(_driver)
        if not _is_logged_in(_driver):
            _wait_for_login(_driver)
        else:
            push_log("✓ WhatsApp already logged in — ready to send!")
        return _driver

    # ── 2. Fallback: bot's own Chrome ────────────────────────────────────────
    push_log("⚠ Could not connect to your Chrome.")
    push_log("💡 Click 'Launch Chrome for Bot' on the Dashboard first, then start the bot.")
    push_log("Opening bot's own Chrome window as fallback…")
    _driver = _build_own_driver()
    _driver.get("https://web.whatsapp.com")
    time.sleep(5)
    if not _is_logged_in(_driver):
        _wait_for_login(_driver)
    else:
        push_log("✓ WhatsApp session restored — ready.")
    return _driver


def close_driver():
    global _driver
    if _driver:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


# WhatsApp Web selectors (multiple fallbacks in case WA changes their DOM)
_SEND_BTN_SELECTORS = [
    'button[data-testid="compose-btn-send"]',
    'button[aria-label="Send"]',
    'span[data-icon="send"]',
    '[data-testid="send"]',
]

_ERROR_SELECTORS = [
    'div[data-testid="confirm-popup"]',
    'div._2A8P4',        # "number not on WhatsApp" popup
]


def _do_send(driver: webdriver.Chrome, phone: str, message: str, wait_time: int) -> tuple[bool, str]:
    """Navigate to WhatsApp chat and click Send. Returns (ok, note)."""
    # Make sure we're on the WhatsApp tab before doing anything
    wa_handle = None
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if "web.whatsapp.com" in driver.current_url:
            wa_handle = handle
            break
    if wa_handle is None:
        # Open WhatsApp in a new tab if it got closed
        driver.execute_script("window.open('https://web.whatsapp.com', '_blank');")
        time.sleep(4)
        driver.switch_to.window(driver.window_handles[-1])

    if not _is_logged_in(driver):
        return False, "Not logged in — scan the QR code in Chrome first"

    url = (
        "https://web.whatsapp.com/send"
        f"?phone={phone}"
        f"&text={urllib.parse.quote(message)}"
    )
    driver.get(url)
    deadline = time.time() + wait_time

    while time.time() < deadline:
        # ── Check for "number not on WhatsApp" popup ──────────────────────────
        for sel in _ERROR_SELECTORS:
            try:
                popup = driver.find_element(By.CSS_SELECTOR, sel)
                if popup.is_displayed():
                    return False, "Number not on WhatsApp"
            except Exception:
                pass

        # ── Try each Send button selector ─────────────────────────────────────
        for sel in _SEND_BTN_SELECTORS:
            try:
                btn = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                btn.click()
                time.sleep(2)
                return True, ""
            except Exception:
                pass

        time.sleep(1)

    return False, f"Timed out after {wait_time}s — send button never appeared"


def _send_via_applescript(phone: str, message: str, wait_time: int) -> tuple[bool, str]:
    """
    macOS only — controls your EXISTING Chrome via AppleScript.
    Finds the WhatsApp tab that is already open, navigates it to the send URL,
    waits for the chat to load, then presses Enter to send.
    No debug port, no Chrome restart, no QR scan needed.
    """
    url = (
        "https://web.whatsapp.com/send"
        f"?phone={phone}"
        f"&text={urllib.parse.quote(message)}"
    )

    # ── Step 1: find WhatsApp tab and navigate it ──────────────────────────────
    nav_script = f'''
tell application "Google Chrome"
    set waFound to false
    repeat with w in every window
        repeat with t in every tab of w
            if URL of t contains "web.whatsapp.com" then
                set index of w to 1
                set active tab index of w to index of t
                set URL of t to "{url}"
                set waFound to true
                exit repeat
            end if
        end repeat
        if waFound then exit repeat
    end repeat
    if not waFound then
        tell front window
            make new tab with properties {{URL:"{url}"}}
        end tell
        set waFound to true
    end if
    activate
    return waFound as string
end tell
'''
    nav = subprocess.run(
        ["osascript", "-e", nav_script],
        capture_output=True, text=True, timeout=15
    )
    if nav.returncode != 0:
        err = nav.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            msg = (
                "PERMISSION DENIED (-1743) — macOS is blocking Python from controlling Chrome. "
                "Fix: System Settings → Privacy & Security → Automation → "
                "find Terminal (or Python) → enable Google Chrome. "
                "Then restart the bot."
            )
            push_log(f"⛔ {msg}")
            return False, msg
        return False, f"AppleScript error: {err[:200]}"

    # ── Step 2: wait for WhatsApp to load the chat ────────────────────────────
    push_log(f"  ↳ WhatsApp tab navigating… waiting {wait_time}s for chat to load")
    time.sleep(wait_time)

    # ── Step 3: press Enter to send (WhatsApp sends on Enter) ─────────────────
    send_script = '''
tell application "Google Chrome" to activate
delay 0.5
tell application "System Events"
    keystroke return
end tell
'''
    send = subprocess.run(
        ["osascript", "-e", send_script],
        capture_output=True, text=True, timeout=8
    )
    if send.returncode != 0:
        err = send.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            push_log("⛔ Permission denied for System Events. Fix: System Settings → Privacy & Security → Automation → Terminal → enable Google Chrome.")
            return False, "Permission denied — see instructions above"
        return False, f"Could not press Enter: {err[:200]}"

    time.sleep(1)
    return True, ""


def send_one(phone: str, message: str, wait_time: int) -> tuple[bool, str]:
    """
    Send a WhatsApp message.
    macOS → uses AppleScript to control your existing Chrome (no restart needed).
    Other OS → uses Selenium (requires 'Launch Chrome for Bot' one-time setup).
    """
    # ── macOS: AppleScript path — works with any Chrome, no debug port ─────────
    if platform.system() == "Darwin":
        return _send_via_applescript(phone, message, wait_time)

    # ── Windows / Linux: Selenium path ────────────────────────────────────────
    for attempt in range(2):
        try:
            driver = get_driver()
            return _do_send(driver, phone, message, wait_time)
        except Exception as e:
            err = str(e)
            if "target window already closed" in err or "web view not found" in err or "no such window" in err:
                push_log("Chrome window was closed — restarting browser...")
                close_driver()
                time.sleep(2)
                continue
            return False, f"Error: {err[:200]}"
    return False, "Chrome failed to recover after window was closed"


# ── General helpers ────────────────────────────────────────────────────────────

def push_log(msg: str):
    ts    = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    log_messages.append(entry)
    log.info(msg)


def save_contacts():
    with open(CONTACTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CONTACT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(contacts)


def load_contacts():
    global contacts
    contacts = []
    if not CONTACTS_FILE.exists():
        return
    with open(CONTACTS_FILE, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            row.setdefault("status", "")
            row.setdefault("sent_at", "")
            row.setdefault("custom_message", "")
            contacts.append(row)


def append_log_entry(name: str, phone: str, message: str, status: str, note: str = ""):
    exists = LOGS_FILE.exists()
    with open(LOGS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "name":      name,
            "phone":     phone,
            "message":   message[:200],
            "status":    status,
            "note":      note,
        })


def load_log_entries() -> list[dict]:
    if not LOGS_FILE.exists():
        return []
    with open(LOGS_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_previous_send(phone: str) -> dict | None:
    phone = phone.strip()
    result = None
    for row in load_log_entries():
        if row.get("phone", "").strip() == phone and row.get("status") == "sent":
            result = row
    return result


def validate_phone(phone: str) -> tuple[bool, str]:
    digits = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not digits.isdigit():
        return False, "Phone contains non-numeric characters"
    if len(digits) < 10:
        return False, f"Too short ({len(digits)} digits, min 10)"
    if len(digits) > 15:
        return False, f"Too long ({len(digits)} digits, max 15)"
    return True, ""


def format_message(template: str, contact: dict) -> str:
    try:
        return template.format(**contact)
    except KeyError:
        return template


# ── Bot worker ─────────────────────────────────────────────────────────────────

def bot_worker(template: str, delay: int, wait_time: int, mode: str = "template"):
    """
    mode='template' → use template for every contact (ignore custom_message).
    mode='csv'      → use contact's custom_message if set, else fall back to template.
    """
    global bot_running
    push_log(f"Bot started. Mode: {'📋 Template' if mode == 'template' else '📂 CSV (per-contact message)'}.")

    pending = [c for c in contacts if c.get("status", "").lower() not in ("sent", "skipped")]

    if not pending:
        push_log("No pending contacts — nothing to send.")
        bot_running = False
        return

    for i, contact in enumerate(pending, 1):
        if not bot_running:
            push_log("Bot stopped by user.")
            break

        phone = contact.get("phone", "").strip()
        name  = contact.get("name", phone)

        if not phone:
            push_log(f"SKIP [{i}/{len(pending)}]: '{name}' — no phone number.")
            contact["status"]  = "skipped"
            contact["sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_contacts()
            append_log_entry(name, phone, "", "skipped", "No phone number")
            continue

        if not phone.startswith("+"):
            phone = "+" + phone

        # Phone validation
        valid, reason = validate_phone(phone)
        if not valid:
            push_log(f"INVALID [{i}/{len(pending)}]: {name} ({phone}) — {reason}")
            contact["status"]  = "failed"
            contact["sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_contacts()
            append_log_entry(name, phone, "", "failed", f"Invalid number: {reason}")
            continue

        # Duplicate check
        prev = find_previous_send(phone)
        if prev:
            note = f"Already sent on {prev['timestamp']}"
            push_log(f"SKIP [{i}/{len(pending)}]: {name} ({phone}) — {note}")
            contact["status"]  = "skipped"
            contact["sent_at"] = prev["timestamp"]
            save_contacts()
            append_log_entry(name, phone, "", "skipped", note)
            continue

        # Template mode: always use the typed template, ignore custom_message column.
        # CSV mode: use the contact's own custom_message if present, else fall back to template.
        if mode == "template":
            msg = format_message(template, contact)
        else:
            msg = format_message(contact.get("custom_message") or template, contact)
        push_log(f"[{i}/{len(pending)}] Sending to {name} ({phone})...")

        ok, note = send_one(phone, msg, wait_time)
        status = "sent" if ok else "failed"
        contact["status"]  = status
        contact["sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_contacts()
        append_log_entry(name, phone, msg, status, note)

        if ok:
            push_log(f"✓ Sent: {name} ({phone})")
        else:
            push_log(f"✗ FAILED: {name} ({phone}) — {note}")

        if i < len(pending) and bot_running:
            push_log(f"Waiting {delay}s before next message...")
            time.sleep(delay)

    sent    = sum(1 for c in contacts if c.get("status") == "sent")
    failed  = sum(1 for c in contacts if c.get("status") == "failed")
    skipped = sum(1 for c in contacts if c.get("status") == "skipped")
    push_log(f"Done. ✓ Sent: {sent}  ✗ Failed: {failed}  — Skipped: {skipped}")
    bot_running = False


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    load_contacts()
    return render_template("index.html")


@app.route("/api/contacts", methods=["GET"])
def get_contacts():
    return jsonify(contacts)


@app.route("/api/contacts", methods=["POST"])
def add_contact():
    data  = request.json
    phone = data.get("phone", "").strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    valid, reason = validate_phone(phone)
    if not valid:
        return jsonify({"ok": False, "error": f"Invalid phone: {reason}"}), 400
    contact = {
        "name":           data.get("name", "").strip(),
        "phone":          phone,
        "custom_message": data.get("custom_message", "").strip(),
        "status":         "",
        "sent_at":        "",
    }
    contacts.append(contact)
    save_contacts()
    return jsonify({"ok": True, "contacts": contacts})


@app.route("/api/contacts/<int:idx>", methods=["DELETE"])
def delete_contact(idx):
    if 0 <= idx < len(contacts):
        contacts.pop(idx)
        save_contacts()
    return jsonify({"ok": True, "contacts": contacts})


@app.route("/api/contacts/<int:idx>/reset", methods=["POST"])
def reset_contact(idx):
    if 0 <= idx < len(contacts):
        contacts[idx]["status"]  = ""
        contacts[idx]["sent_at"] = ""
        save_contacts()
    return jsonify({"ok": True, "contacts": contacts})


@app.route("/api/contacts/upload", methods=["POST"])
def upload_csv():
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "No file"}), 400
    stream = io.StringIO(file.stream.read().decode("utf-8"))
    new_contacts = []
    for row in csv.DictReader(stream):
        row.setdefault("status", "")
        row.setdefault("sent_at", "")
        row.setdefault("custom_message", "")
        new_contacts.append(row)
    contacts.clear()
    contacts.extend(new_contacts)
    save_contacts()
    return jsonify({"ok": True, "contacts": contacts})


@app.route("/api/bot/start", methods=["POST"])
def start_bot():
    global bot_running, bot_thread
    if bot_running:
        return jsonify({"ok": False, "error": "Bot already running"})
    data      = request.json
    template  = data.get("template", "Hi {name}!")
    delay     = int(data.get("delay", 35))
    wait_time = int(data.get("wait_time", 20))
    mode      = data.get("mode", "template")   # "template" or "csv"
    bot_running = True
    bot_thread  = threading.Thread(
        target=bot_worker, args=(template, delay, wait_time, mode), daemon=True
    )
    bot_thread.start()
    return jsonify({"ok": True})


@app.route("/api/fix-permissions", methods=["POST"])
def fix_permissions():
    """Open macOS System Settings → Privacy → Automation so user can grant Chrome access."""
    try:
        subprocess.Popen([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
        ])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/chrome-mode", methods=["GET"])
def chrome_mode():
    """Tell the frontend which send method will be used."""
    system = platform.system()
    if system == "Darwin":
        return jsonify({"mode": "applescript", "label": "✓ Using your existing Chrome (macOS)"})
    if _is_debug_port_open():
        return jsonify({"mode": "selenium_connected", "label": "✓ Connected to your Chrome"})
    return jsonify({"mode": "selenium_own", "label": "⚠ Will open bot's own Chrome window"})


@app.route("/api/launch-chrome", methods=["POST"])
def launch_chrome():
    """
    Close Chrome gracefully and relaunch it with --remote-debugging-port=9222.
    Uses --restore-last-session so all existing tabs (incl. WhatsApp) come back.
    """
    # Already connected? Nothing to do.
    if _is_debug_port_open():
        push_log("✓ Chrome is already running in bot-connect mode!")
        return jsonify({"ok": True, "msg": "Already connected"})

    system = platform.system()
    try:
        if system == "Darwin":
            # Gracefully quit Chrome (preserves session for restore)
            subprocess.run(
                ["osascript", "-e", 'tell application "Google Chrome" to quit'],
                capture_output=True, timeout=8
            )
            time.sleep(2)
            # Relaunch via the actual binary — NOT `open -a` — so flags are applied
            subprocess.Popen([
                _CHROME_MAC,
                f"--remote-debugging-port={CHROME_DEBUG_PORT}",
                "--restore-last-session",       # brings back all your old tabs
            ])

        elif system == "Windows":
            subprocess.run("taskkill /F /IM chrome.exe", shell=True,
                           capture_output=True, timeout=8)
            time.sleep(2)
            chrome_exe = next((p for p in _CHROME_WIN if Path(p).exists()), None)
            if not chrome_exe:
                return jsonify({"ok": False,
                                "error": "Chrome not found. Is Google Chrome installed?"})
            subprocess.Popen([
                chrome_exe,
                f"--remote-debugging-port={CHROME_DEBUG_PORT}",
                "--restore-last-session",
            ])
        else:
            return jsonify({"ok": False, "error": f"Unsupported OS: {system}"})

        push_log("✓ Chrome is restarting with bot-connect mode enabled.")
        push_log("💡 All your tabs (including WhatsApp) will restore automatically.")
        push_log("Wait a few seconds, then click ▶ Start Bot.")
        return jsonify({"ok": True})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/bot/stop", methods=["POST"])
def stop_bot():
    global bot_running
    bot_running = False
    return jsonify({"ok": True})


@app.route("/api/bot/status", methods=["GET"])
def bot_status():
    sent    = sum(1 for c in contacts if c.get("status") == "sent")
    failed  = sum(1 for c in contacts if c.get("status") == "failed")
    skipped = sum(1 for c in contacts if c.get("status") == "skipped")
    pending = sum(1 for c in contacts if c.get("status", "") not in ("sent", "skipped", "failed"))
    return jsonify({
        "running": bot_running,
        "sent": sent, "failed": failed,
        "skipped": skipped, "pending": pending, "total": len(contacts),
    })


@app.route("/api/logs", methods=["GET"])
def get_logs():
    return jsonify(log_messages[-200:])


@app.route("/api/logs/stream")
def stream_logs():
    """SSE endpoint — pushes new log lines in real time."""
    def generate():
        idx = len(log_messages)          # start from current tail
        while True:
            if len(log_messages) > idx:
                for msg in log_messages[idx:]:
                    yield f"data: {json.dumps(msg)}\n\n"
                idx = len(log_messages)
            time.sleep(0.3)
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    log_messages.clear()
    return jsonify({"ok": True})


@app.route("/api/history", methods=["GET"])
def get_history():
    entries = load_log_entries()
    for i, e in enumerate(entries):
        e["_id"] = i          # original CSV row index, used for delete/edit
    entries.reverse()
    return jsonify(entries)


@app.route("/api/history/<int:idx>", methods=["DELETE"])
def delete_history_entry(idx):
    entries = load_log_entries()
    if 0 <= idx < len(entries):
        entries.pop(idx)
        with open(LOGS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            writer.writeheader()
            writer.writerows(entries)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Index out of range"}), 404


@app.route("/api/history/<int:idx>", methods=["PUT"])
def edit_history_entry(idx):
    data    = request.json or {}
    entries = load_log_entries()
    if 0 <= idx < len(entries):
        for field in LOG_FIELDS:
            if field in data:
                entries[idx][field] = data[field]
        with open(LOGS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            writer.writeheader()
            writer.writerows(entries)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Index out of range"}), 404


@app.route("/api/history/clear", methods=["POST"])
def clear_history():
    if LOGS_FILE.exists():
        LOGS_FILE.unlink()
    push_log("Message history cleared.")
    return jsonify({"ok": True})


@app.route("/api/history/export", methods=["GET"])
def export_history():
    entries = load_log_entries()
    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=LOG_FIELDS)
    writer.writeheader()
    writer.writerows(entries)
    return Response(
        si.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=message_history.csv"},
    )


if __name__ == "__main__":
    load_contacts()
    push_log("WhatsApp Bot server started. Open http://localhost:5001")
    try:
        app.run(debug=False, host="0.0.0.0", port=5001)
    finally:
        close_driver()
