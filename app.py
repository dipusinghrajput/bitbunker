"""
All Playwright work runs on a single dedicated background thread (_pw_thread).
Flask routes communicate with it via _cmd_queue (send command) and _res_queue
(receive result). This avoids the greenlet "cannot switch to a different thread"
error that happens when Flask's request threads try to touch Playwright objects
created on another thread.
"""

import queue
import threading
import os
from flask import Flask, request, jsonify, render_template
from playwright.sync_api import sync_playwright

app = Flask(__name__)

attendance_data = []

# ── Inter-thread communication ──────────────────────────────────────────────
_cmd_queue = queue.Queue()    # Flask  → PW thread
_res_queue = queue.Queue()    # PW thread → Flask
_pw_lock   = threading.Lock() # one Playwright request at a time


# ── Playwright worker thread ────────────────────────────────────────────────
def _playwright_worker():
    """Runs forever on its own thread; owns every Playwright object."""
    with sync_playwright() as pw:
        browser = None
        page    = None

        while True:
            msg = _cmd_queue.get()
            cmd = msg.get("cmd")

            # ── LOGIN ────────────────────────────────────────────────────
            if cmd == "login":
                try:
                    if browser:
                        try: browser.close()
                        except: pass
                    browser = pw.chromium.launch(headless=True)
                    page    = browser.new_page()

                    page.goto("https://erp.bitdurg.ac.in/Login.jsp")
                    page.fill('input[name="username"]', msg["username"])
                    page.fill('input[name="password"]', msg["password"])
                    page.click('button.btn-primary')
                    page.wait_for_load_state("networkidle")

                    if "verify_otp" in page.url:
                        _res_queue.put({"ok": True, "otp_required": True})

                    elif "Login.jsp" in page.url:
                        browser.close()
                        browser = page = None
                        _res_queue.put({"ok": False, "error": "Invalid Credentials"})

                    else:
                        result = _scrape_attendance(page)
                        browser.close()
                        browser = page = None
                        _res_queue.put({"ok": True, **result})

                except Exception as e:
                    try:
                        if browser: browser.close()
                    except: pass
                    browser = page = None
                    _res_queue.put({"ok": False, "error": str(e)})

            # ── SUBMIT OTP ───────────────────────────────────────────────
            elif cmd == "submit_otp":
                if page is None:
                    _res_queue.put({"ok": False, "error": "Session expired. Please login again."})
                    continue
                try:
                    page.wait_for_selector('#otp', timeout=5000)
                    page.fill('#otp', msg["otp"])
                    page.keyboard.press('Enter')
                    page.wait_for_load_state("networkidle")

                    if "verify_otp" in page.url:
                        # Still on OTP page — wrong code, keep session alive for retry
                        _res_queue.put({"ok": False, "error": "Invalid OTP. Please try again."})

                    elif "Login.jsp" in page.url:
                        browser.close()
                        browser = page = None
                        _res_queue.put({"ok": False, "error": "OTP verification failed. Please login again."})

                    elif "dashboard.jsp" in page.url or "erp.bitdurg.ac.in" in page.url:
                        # Successfully landed on dashboard — scrape attendance
                        result = _scrape_attendance(page)
                        browser.close()
                        browser = page = None
                        _res_queue.put({"ok": True, **result})

                    else:
                        # Unknown page after OTP — try scraping anyway
                        result = _scrape_attendance(page)
                        browser.close()
                        browser = page = None
                        _res_queue.put({"ok": True, **result})

                except Exception as e:
                    try:
                        if browser: browser.close()
                    except: pass
                    browser = page = None
                    _res_queue.put({"ok": False, "error": f"OTP error: {str(e)}"})

            elif cmd == "quit":
                try:
                    if browser: browser.close()
                except: pass
                break


def _scrape_attendance(page):
    """Navigate to Attendance Reports and extract table. Runs on PW thread."""
    try:
        # Wait for dashboard to fully load, then click Attendance Reports
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_selector('a:has-text("Attendance Reports")', timeout=10000)
        page.click('a:has-text("Attendance Reports")')
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_selector("table", timeout=10000)
    except Exception as e:
        return {"error": f"Failed to navigate to Attendance Reports: {e}"}

    raw_data = []
    for row in page.query_selector_all("tr"):
        s = row.query_selector("td:nth-child(2)")
        t = row.query_selector("td:nth-child(3)")
        a = row.query_selector("td:nth-child(4)")
        if s and t and a:
            try:
                raw_data.append({
                    "subject":  s.inner_text().strip(),
                    "total":    int(t.inner_text().strip()),
                    "attended": int(a.inner_text().strip()),
                })
            except ValueError:
                continue
    return {"raw_data": raw_data}


def _send(cmd_dict, timeout=60):
    """Send a command to the PW thread and wait for the result (thread-safe)."""
    with _pw_lock:
        _cmd_queue.put(cmd_dict)
        return _res_queue.get(timeout=timeout)


# Start the single Playwright worker thread at import time
_pw_thread = threading.Thread(target=_playwright_worker, daemon=True)
_pw_thread.start()


# ── Attendance calculation ────────────────────────────────────────────────────
def calculate_attendance(raw_data, required_percentage):
    calculated_data = {}
    for subject in raw_data:
        name     = subject["subject"]
        attended = subject["attended"]
        total    = subject["total"]

        if total == 0:
            calculated_data[name] = {
                "attended": attended, "total": total,
                "allowed_bunks": 0, "status": "No classes conducted",
                "current_percentage": None
            }
            continue

        pct = (attended / total) * 100

        if pct >= required_percentage:
            bunks = int(((100 * attended) - (required_percentage * total)) / required_percentage)
            calculated_data[name] = {
                "attended": attended, "total": total,
                "allowed_bunks": bunks, "status": "Enough attendance",
                "current_percentage": round(pct, 2)
            }
        else:
            ta, tt, needed = attended, total, 0
            while (ta / tt) * 100 < required_percentage:
                ta += 1; tt += 1; needed += 1
            calculated_data[name] = {
                "attended": attended, "total": total,
                "required_classes": needed, "status": "Not enough attendance",
                "current_percentage": round(pct, 2)
            }
    return calculated_data


# ── Flask routes ──────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['POST'])
def login():
    data     = request.json
    username = data.get("username", "")
    password = data.get("password", "")

    try:
        result = _send({"cmd": "login", "username": username, "password": password})
    except queue.Empty:
        return jsonify({"error": "Request timed out. Please try again."}), 504

    if not result["ok"]:
        return jsonify({"error": result["error"]}), 400

    if result.get("otp_required"):
        return jsonify({"otp_required": True}), 200

    global attendance_data
    attendance_data = result["raw_data"]
    return jsonify({"message": "Login successful.", "raw_data": attendance_data}), 200


@app.route('/submit_otp', methods=['POST'])
def submit_otp():
    data      = request.json
    otp_value = data.get("otp", "").strip()

    if not otp_value:
        return jsonify({"error": "OTP cannot be empty"}), 400

    try:
        result = _send({"cmd": "submit_otp", "otp": otp_value})
    except queue.Empty:
        return jsonify({"error": "OTP request timed out. Please try again."}), 504

    if not result["ok"]:
        return jsonify({"error": result["error"]}), 400

    global attendance_data
    attendance_data = result["raw_data"]
    return jsonify({"message": "OTP verified.", "raw_data": attendance_data}), 200


@app.route('/set_percentage', methods=['POST'])
def set_percentage():
    global attendance_data
    if not attendance_data:
        return jsonify({"error": "No attendance data available"}), 400

    data = request.json
    try:
        required_percentage = float(data.get("required_percentage", 75))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid percentage value"}), 400

    if not (0 <= required_percentage <= 100):
        return jsonify({"error": "Percentage must be between 0 and 100"}), 400

    calculated_data = calculate_attendance(attendance_data, required_percentage)
    return jsonify({"attendance": calculated_data, "required_percentage": required_percentage}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
