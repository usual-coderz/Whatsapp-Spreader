import os
import time
import json
import uuid
import random
import string
import base64
import threading
from datetime import datetime
from io import BytesIO

import qrcode
from flask import Flask, render_template, request, redirect, session, jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# ─── MongoDB ──────────────────────────────────────────────────
MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb+srv://nexacoders2_db_user:dxYh7QOdHvH6OVdd@cluster0.f4qxcbk.mongodb.net/?appName=Cluster0"
)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["whatsapp_spreader"]
users_col = db["users"]
keys_col = db["login_keys"]
broadcast_col = db["broadcasts"]
sessions_col = db["whatsapp_sessions"]

# ─── Browser Pool ─────────────────────────────────────────────
browsers = {}        # user_id -> { page, context, browser }
broadcast_status = {}  # user_id -> { running, done, fail, total, ... }

# ─── Helpers ──────────────────────────────────────────────────

def generate_login_key():
    return "WS-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

def generate_user_id():
    return str(uuid.uuid4())[:8]

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def load_numbers():
    path = "number.txt"
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return [line.strip() for line in f if line.strip()]

def init_playwright():
    """Lazy-import and return sync Playwright instance."""
    from playwright.sync_api import sync_playwright
    return sync_playwright()

def get_or_create_browser(user_id):
    """Get existing browser context or create a new one with session persistence."""
    if user_id in browsers and browsers[user_id].get("page"):
        try:
            # Check if page is still alive
            browsers[user_id]["page"].title()
            return browsers[user_id]
        except:
            pass  # Dead, recreate

    pw = init_playwright()
    pw_instance = pw.__enter__()
    browser = pw_instance.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--single-process",
        ]
    )

    # Try to load saved session
    saved = sessions_col.find_one({"user_id": user_id})
    context_options = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
    }

    if saved and saved.get("storage_state"):
        context = browser.new_context(
            storage_state=saved["storage_state"],
            **context_options
        )
    else:
        context = browser.new_context(**context_options)

    page = context.new_page()
    page.set_default_timeout(30000)

    data = {
        "pw": pw_instance,
        "browser": browser,
        "context": context,
        "page": page,
        "connected": False,
        "phone": None,
    }
    browsers[user_id] = data
    return data


def save_session(user_id):
    """Persist Playwright storage state to MongoDB."""
    data = browsers.get(user_id)
    if not data or not data.get("context"):
        return
    try:
        storage = data["context"].storage_state()
        sessions_col.update_one(
            {"user_id": user_id},
            {"$set": {
                "storage_state": storage,
                "updated_at": now_str(),
                "phone": data.get("phone", "unknown"),
            }},
            upsert=True
        )
    except Exception as e:
        print(f"[!] Session save error: {e}")


def close_browser(user_id):
    data = browsers.pop(user_id, None)
    if data:
        try:
            data["context"].close()
        except:
            pass
        try:
            data["browser"].close()
        except:
            pass
        try:
            data["pw"].__exit__(None, None, None)
        except:
            pass


# ─── Routes: Auth ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        admin = users_col.find_one({"username": username, "role": "admin"})
        if admin and check_password_hash(admin["password"], password):
            session["admin"] = True
            session["username"] = username
            return redirect("/admin/dashboard")
        return render_template("index.html", error="Invalid credentials")
    return render_template("index.html")


@app.route("/admin/register", methods=["POST"])
def admin_register():
    existing = users_col.find_one({"role": "admin"})
    if existing:
        secret = request.form.get("admin_secret", "")
        if secret != os.environ.get("ADMIN_SECRET", "changeme123"):
            return render_template("index.html", error="Registration disabled or invalid secret")
    username = request.form.get("username")
    password = request.form.get("password")
    if users_col.find_one({"username": username}):
        return render_template("index.html", error="Username already exists")
    users_col.insert_one({
        "username": username,
        "password": generate_password_hash(password),
        "role": "admin",
        "created_at": now_str()
    })
    return render_template("index.html", success="Admin registered. Please login.")


@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect("/")
    keys = list(keys_col.find().sort("created_at", -1))
    bcasts = list(broadcast_col.find().sort("started_at", -1).limit(50))
    return render_template("admin.html",
                         keys=keys,
                         broadcasts=bcasts,
                         total_keys=len(keys),
                         used_keys=len([k for k in keys if k.get("used")]),
                         total_broadcasts=len(bcasts),
                         session=session)


@app.route("/admin/generate-key", methods=["POST"])
def generate_key():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    key = generate_login_key()
    keys_col.insert_one({
        "key": key,
        "used": False,
        "created_at": now_str(),
        "assigned_to": None
    })
    return jsonify({"success": True, "key": key})


@app.route("/admin/set-message", methods=["POST"])
def set_message():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    message = request.form.get("message")
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
    db.settings.update_one(
        {"key": "broadcast_message"},
        {"$set": {"value": message, "updated_by": session["username"], "updated_at": now_str()}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route("/admin/delete-key/<key_id>", methods=["POST"])
def delete_key(key_id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    keys_col.delete_one({"_id": ObjectId(key_id)})
    return jsonify({"success": True})


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    session.pop("username", None)
    return redirect("/")


# ─── Routes: Sub-User ─────────────────────────────────────────

@app.route("/login", methods=["POST"])
def user_login():
    login_key = request.form.get("login_key")
    key_doc = keys_col.find_one({"key": login_key})
    if not key_doc:
        return render_template("index.html", error="Invalid login key")
    if key_doc.get("used"):
        return render_template("index.html", error="This key has already been used")

    user_id = generate_user_id()
    keys_col.update_one({"_id": key_doc["_id"]}, {
        "$set": {"used": True, "assigned_to": user_id, "used_at": now_str()}
    })

    session["user_id"] = user_id
    session["login_key"] = login_key
    return redirect("/dashboard")


@app.route("/dashboard")
def user_dashboard():
    if not session.get("user_id"):
        return redirect("/")
    user_id = session["user_id"]
    settings = db.settings.find_one({"key": "broadcast_message"})
    broadcast_msg = settings["value"] if settings else "No message set by admin yet"
    bcasts = list(broadcast_col.find({"user_id": user_id}).sort("started_at", -1).limit(20))
    saved = sessions_col.find_one({"user_id": user_id})
    connected = saved is not None and saved.get("storage_state") is not None
    return render_template("dashboard.html",
                         user_id=user_id,
                         connected=connected,
                         phone=saved.get("phone", "Unknown") if saved else None,
                         broadcast_msg=broadcast_msg,
                         broadcasts=bcasts)


@app.route("/connect-whatsapp", methods=["POST"])
def connect_whatsapp():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    data = get_or_create_browser(user_id)
    page = data["page"]

    # Navigate to WhatsApp Web
    page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

    # Check if already logged in (session restored)
    try:
        page.wait_for_selector("div[data-testid='conversation-panel-wrapper']", timeout=10000)
        # Already logged in!
        data["connected"] = True

        # Try to get phone number
        try:
            indicator = page.query_selector("header div[data-testid='conversation-info-header'] span")
            phone = indicator.inner_text() if indicator else "Restored Session"
        except:
            phone = "Restored Session"

        data["phone"] = phone
        save_session(user_id)

        return jsonify({
            "success": True,
            "connected": True,
            "phone": phone,
            "message": "WhatsApp session restored! No QR scan needed."
        })
    except:
        pass  # Need QR code

    # Wait for QR canvas element
    try:
        page.wait_for_selector("canvas", timeout=15000)
    except:
        return jsonify({"error": "Failed to load WhatsApp Web QR page"}), 500

    time.sleep(2)

    # Capture QR as base64
    qr_encoded = page.evaluate("""
        () => {
            const canvas = document.querySelector('canvas');
            if (!canvas) return null;
            return canvas.toDataURL('image/png').split(',')[1];
        }
    """)

    if not qr_encoded:
        return jsonify({"error": "Could not find QR code element"}), 500

    return jsonify({
        "success": True,
        "connected": False,
        "qr_code": qr_encoded,
        "message": "Scan the QR code with your phone's WhatsApp. The page will auto-detect connection."
    })


@app.route("/check-whatsapp-status", methods=["GET"])
def check_whatsapp_status():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    data = browsers.get(user_id)

    if not data or not data.get("page"):
        saved = sessions_col.find_one({"user_id": user_id})
        if saved and saved.get("storage_state"):
            return jsonify({"connected": True, "phone": saved.get("phone", "Session Saved")})
        return jsonify({"connected": False})

    page = data["page"]

    # Check if WhatsApp main panel is visible
    try:
        panel = page.query_selector("div[data-testid='conversation-panel-wrapper']")
        if panel:
            if not data["connected"]:
                data["connected"] = True
                try:
                    span = page.query_selector("header div[data-testid='conversation-info-header'] span")
                    phone = span.inner_text() if span else "Connected"
                except:
                    phone = "Connected"
                data["phone"] = phone
                save_session(user_id)
            return jsonify({"connected": True, "phone": data.get("phone", "Connected")})
    except:
        pass

    # Check if QR is still showing or if we're on a loading screen
    try:
        qr = page.query_selector("canvas")
        if qr:
            return jsonify({"connected": False, "message": "QR code still showing, scan it"})
    except:
        pass

    # Maybe loading between QR and connected
    return jsonify({"connected": False, "message": "Awaiting connection..."})


@app.route("/disconnect-whatsapp", methods=["POST"])
def disconnect_whatsapp():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    user_id = session["user_id"]
    close_browser(user_id)
    sessions_col.delete_one({"user_id": user_id})
    return jsonify({"success": True})


# ─── Broadcast Engine ──────────────────────────────────────────

def broadcast_worker(user_id, numbers, message):
    global broadcast_status

    broadcast_status[user_id] = {
        "running": True, "done": 0, "fail": 0,
        "total": len(numbers), "current": 0, "status": "starting"
    }

    done = 0
    fail = 0

    try:
        data = get_or_create_browser(user_id)
        page = data["page"]

        # Ensure we're on WhatsApp Web
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

        # Wait for main panel (ensure session is valid)
        try:
            page.wait_for_selector("div[data-testid='conversation-panel-wrapper']", timeout=20000)
        except:
            broadcast_status[user_id] = {
                "running": False, "done": 0, "fail": len(numbers),
                "total": len(numbers), "current": 0, "status": "session_expired"
            }
            return

        for idx, number in enumerate(numbers):
            if not broadcast_status.get(user_id, {}).get("running", False):
                break

            number = number.strip()
            if not number:
                continue

            clean = ''.join(c for c in number if c.isdigit() or c == '+')
            if not clean:
                fail += 1
                broadcast_status[user_id]["fail"] = fail
                continue

            broadcast_status[user_id]["current"] = idx + 1
            broadcast_status[user_id]["status"] = f"Sending to {clean}"

            try:
                # Open chat
                page.goto(f"https://web.whatsapp.com/send?phone={clean}",
                          wait_until="domcontentloaded")

                # Wait for message box
                page.wait_for_selector("div[contenteditable='true'][data-tab='10']", timeout=25)
                time.sleep(2)

                # Type the message character by character (evades detection)
                msg_box = page.query_selector("div[contenteditable='true'][data-tab='10']")
                msg_box.click()
                time.sleep(0.5)
                page.fill("div[contenteditable='true'][data-tab='10']", message)
                time.sleep(1)

                # Send
                page.keyboard.press("Enter")
                time.sleep(random.uniform(2, 4))

                done += 1
                broadcast_status[user_id]["done"] = done

            except Exception as e:
                fail += 1
                broadcast_status[user_id]["fail"] = fail
                print(f"[!] Failed {clean}: {e}")

            # Random delay between sends
            if idx < len(numbers) - 1:
                time.sleep(random.uniform(5, 12))

    except Exception as e:
        print(f"[!] Broadcast worker error: {e}")

    broadcast_status[user_id]["running"] = False
    broadcast_status[user_id]["status"] = "completed"

    # Save record
    broadcast_col.insert_one({
        "user_id": user_id,
        "login_key": session.get("login_key", "unknown"),
        "numbers_count": len(numbers),
        "done": done,
        "fail": fail,
        "message": (message[:80] + "...") if len(message) > 80 else message,
        "started_at": now_str(),
        "completed_at": now_str(),
        "status": "completed"
    })


@app.route("/start-broadcast", methods=["POST"])
def start_broadcast():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]

    # Check if connected
    saved = sessions_col.find_one({"user_id": user_id})
    if not saved or not saved.get("storage_state"):
        return jsonify({"error": "WhatsApp not connected. Connect first."}), 400

    if broadcast_status.get(user_id, {}).get("running", False):
        return jsonify({"error": "Broadcast already running"}), 400

    numbers = load_numbers()
    if not numbers:
        return jsonify({"error": "number.txt not found or empty"}), 400

    settings = db.settings.find_one({"key": "broadcast_message"})
    message = settings["value"] if settings else None
    if not message:
        return jsonify({"error": "No broadcast message set by admin"}), 400

    thread = threading.Thread(
        target=broadcast_worker,
        args=(user_id, numbers, message),
        daemon=True
    )
    thread.start()

    return jsonify({
        "success": True,
        "message": f"Broadcast started. Sending to {len(numbers)} numbers.",
        "total_numbers": len(numbers)
    })


@app.route("/stop-broadcast", methods=["POST"])
def stop_broadcast():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    user_id = session["user_id"]
    if broadcast_status.get(user_id, {}).get("running", False):
        broadcast_status[user_id]["running"] = False
        broadcast_status[user_id]["status"] = "stopped"
        return jsonify({"success": True})
    return jsonify({"error": "No active broadcast"}), 400


@app.route("/broadcast-status", methods=["GET"])
def get_broadcast_status():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    user_id = session["user_id"]
    status = broadcast_status.get(user_id, {
        "running": False, "done": 0, "fail": 0,
        "total": 0, "current": 0, "status": "idle"
    })
    return jsonify(status)


@app.route("/logout")
def user_logout():
    user_id = session.get("user_id")
    if user_id:
        close_browser(user_id)
    session.clear()
    return redirect("/")


# ─── Default Admin ─────────────────────────────────────────────

def setup_default_admin():
    if not users_col.find_one({"role": "admin"}):
        users_col.insert_one({
            "username": "admin",
            "password": generate_password_hash("admin123"),
            "role": "admin",
            "created_at": now_str()
        })
        print("[+] Default admin: admin / admin123")


# ─── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_default_admin()
    port = int(os.environ.get("PORT", 5000))
    print(f"[+] Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)