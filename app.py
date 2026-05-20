"""
WhatsApp Web Bot — Flask Backend
Run with: py app.py  →  http://localhost:5001
"""

import csv
import io
import json
import logging
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

def _build_driver() -> webdriver.Chrome:
    WA_PROFILE_DIR.mkdir(exist_ok=True)
    opts = ChromeOptions()
    opts.add_argument(f"--user-data-dir={WA_PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.maximize_window()
    return driver


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    """Return True if WhatsApp Web is authenticated."""
    try:
        driver.find_element(By.CSS_SELECTOR, 'canvas[aria-label="Scan me!"]')
        return False   # QR visible → not logged in
    except Exception:
        return True


def _wait_for_login(driver: webdriver.Chrome, timeout: int = 120):
    """Block until the user scans the QR code or timeout expires."""
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
            _ = _driver.window_handles   # raises if session dead
            return _driver
        except Exception:
            close_driver()

    push_log("Opening WhatsApp Web in Chrome…")
    _driver = _build_driver()
    _driver.get("https://web.whatsapp.com")
    time.sleep(5)   # let page load

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
    # Check login before navigating
    if not _is_logged_in(driver):
        return False, "Not logged in — scan the QR code in Chrome first"

    # Keep + unencoded so WhatsApp Web recognises the number correctly
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


def send_one(phone: str, message: str, wait_time: int) -> tuple[bool, str]:
    """Send a WhatsApp message, auto-recovering from a dead Chrome window."""
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
                continue   # retry with fresh driver
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
