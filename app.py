from flask import Flask, render_template, request, redirect, session, jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import uuid
import threading
import time
import os
import json
import random
import string
from datetime import datetime
from bson import ObjectId

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# ─── Configuration ──────────────────────────────────────────────
MONGO_URI = "mongodb://localhost:27017"
client = MongoClient(MONGO_URI)
db = client["whatsapp_spreader"]
users_col = db["users"]
clients_col = db["clients"]
keys_col = db["login_keys"]
broadcast_col = db["broadcasts"]

# ─── Global WebDriver pool (one per user) ──────────────────────
user_drivers = {}  # {user_id: driver_instance}

# ─── Helper Functions ───────────────────────────────────────────

def generate_login_key():
    return "WS-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

def generate_user_id():
    return str(uuid.uuid4())[:8]

def get_current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def create_driver(user_id):
    """Create a Chrome driver with a persistent user profile for WhatsApp Web"""
    profile_dir = os.path.join(os.getcwd(), "profiles", user_id)
    os.makedirs(profile_dir, exist_ok=True)
    
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    # Try headless first; if it fails due to QR scan needed, user will be prompted
    # chrome_options.add_argument("--headless=new")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.get("https://web.whatsapp.com")
    return driver

# ─── Routes ─────────────────────────────────────────────────────

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

@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if users_col.find_one({"username": username}):
            return render_template("index.html", error="Username already exists")
        
        hashed_pw = generate_password_hash(password)
        users_col.insert_one({
            "username": username,
            "password": hashed_pw,
            "role": "admin",
            "created_at": get_current_time()
        })
        return render_template("index.html", success="Admin registered. Please login.")
    
    return render_template("index.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect("/")
    
    keys = list(keys_col.find().sort("created_at", -1))
    clients = list(clients_col.find().sort("created_at", -1))
    broadcasts = list(broadcast_col.find().sort("started_at", -1).limit(50))
    
    total_keys = len(keys)
    used_keys = len([k for k in keys if k.get("used")])
    total_clients = len(clients)
    total_broadcasts = len(broadcasts)
    
    return render_template("admin.html", 
                         keys=keys, clients=clients, broadcasts=broadcasts,
                         total_keys=total_keys, used_keys=used_keys,
                         total_clients=total_clients, total_broadcasts=total_broadcasts)

@app.route("/admin/generate-key", methods=["POST"])
def generate_key():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    key = generate_login_key()
    keys_col.insert_one({
        "key": key,
        "used": False,
        "created_at": get_current_time(),
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
        {"$set": {"value": message, "updated_by": session["username"], "updated_at": get_current_time()}},
        upsert=True
    )
    return jsonify({"success": True, "message": "Broadcast message updated"})

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

# ─── Sub-User (Client) Routes ───────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def user_login():
    if request.method == "POST":
        login_key = request.form.get("login_key")
        
        key_doc = keys_col.find_one({"key": login_key})
        if not key_doc:
            return render_template("index.html", error="Invalid login key")
        
        if key_doc.get("used"):
            return render_template("index.html", error="This key has already been used")
        
        user_id = generate_user_id()
        keys_col.update_one({"_id": key_doc["_id"]}, {
            "$set": {"used": True, "assigned_to": user_id, "used_at": get_current_time()}
        })
        
        clients_col.insert_one({
            "user_id": user_id,
            "login_key": login_key,
            "whatsapp_connected": False,
            "whatsapp_number": None,
            "created_at": get_current_time(),
            "last_broadcast": None,
            "total_sent": 0,
            "total_failed": 0
        })
        
        session["user_id"] = user_id
        session["login_key"] = login_key
        return redirect("/dashboard")
    
    return render_template("index.html")

@app.route("/dashboard")
def user_dashboard():
    if not session.get("user_id"):
        return redirect("/")
    
    user_id = session["user_id"]
    client = clients_col.find_one({"user_id": user_id})
    
    if not client:
        return redirect("/")
    
    settings = db.settings.find_one({"key": "broadcast_message"})
    broadcast_msg = settings["value"] if settings else "No message set by admin yet"
    
    broadcasts = list(broadcast_col.find({"user_id": user_id}).sort("started_at", -1).limit(20))
    
    return render_template("dashboard.html", 
                         client=client, 
                         broadcast_msg=broadcast_msg,
                         broadcasts=broadcasts)

@app.route("/connect-whatsapp", methods=["POST"])
def connect_whatsapp():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    
    whatsapp_number = request.form.get("whatsapp_number")
    if not whatsapp_number:
        return jsonify({"error": "WhatsApp number required"}), 400
    
    user_id = session["user_id"]
    
    # Launch Chrome for this user and open WhatsApp Web (QR scan needed)
    try:
        driver = create_driver(user_id)
        # Wait up to 60 seconds for QR scan / login
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-testid='conversation-panel-wrapper']"))
        )
        user_drivers[user_id] = driver
        
        clients_col.update_one({"user_id": user_id}, {
            "$set": {
                "whatsapp_number": whatsapp_number,
                "whatsapp_connected": True,
                "connected_at": get_current_time()
            }
        })
        
        return jsonify({"success": True, "message": "WhatsApp connected successfully! QR scanned."})
    except TimeoutException:
        return jsonify({"success": False, "error": "QR scan timeout. Please try again."}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"Connection failed: {str(e)}"}), 400

@app.route("/disconnect-whatsapp", methods=["POST"])
def disconnect_whatsapp():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    
    # Close driver if exists
    if user_id in user_drivers:
        try:
            user_drivers[user_id].quit()
        except:
            pass
        del user_drivers[user_id]
    
    clients_col.update_one({"user_id": user_id}, {
        "$set": {"whatsapp_number": None, "whatsapp_connected": False}
    })
    
    return jsonify({"success": True, "message": "WhatsApp disconnected"})

# ─── Broadcasting Engine ────────────────────────────────────────

broadcast_status = {}  # {user_id: {"running": bool, "done": int, "fail": int, "total": int, "current": int}}

def whatsapp_broadcast_worker(user_id, numbers, message):
    """Background thread for broadcasting using Selenium"""
    global broadcast_status
    
    broadcast_status[user_id] = {
        "running": True,
        "done": 0,
        "fail": 0,
        "total": len(numbers),
        "current": 0,
        "status": "starting"
    }
    
    done_count = 0
    fail_count = 0
    
    driver = user_drivers.get(user_id)
    if not driver:
        broadcast_status[user_id]["running"] = False
        broadcast_status[user_id]["status"] = "error: no driver"
        return
    
    # Make sure we're on WhatsApp Web
    try:
        driver.get("https://web.whatsapp.com")
        time.sleep(5)
    except:
        pass
    
    for idx, number in enumerate(numbers):
        if not broadcast_status.get(user_id, {}).get("running", False):
            break
        
        number = number.strip()
        if not number:
            continue
        
        # Clean number
        clean_num = ''.join(c for c in number if c.isdigit() or c == '+')
        if not clean_num:
            fail_count += 1
            broadcast_status[user_id]["fail"] = fail_count
            continue
        
        broadcast_status[user_id]["current"] = idx + 1
        broadcast_status[user_id]["status"] = f"Sending to {clean_num}"
        
        try:
            # Open chat for this number
            driver.get(f"https://web.whatsapp.com/send?phone={clean_num}")
            
            # Wait for the message input box to load
            wait = WebDriverWait(driver, 30)
            
            # Wait for the message input div to be present
            msg_box = wait.until(
                EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true' and @data-tab='10']"))
            )
            
            # Additional wait to ensure the chat is fully loaded
            time.sleep(3)
            
            msg_box.click()
            time.sleep(1)
            
            # Type message character by character for realism
            for char in message:
                msg_box.send_keys(char)
                time.sleep(random.uniform(0.01, 0.05))
            
            time.sleep(1)
            
            # Send via Enter key
            msg_box.send_keys(Keys.ENTER)
            
            # Wait a bit to ensure message is sent
            time.sleep(random.uniform(2, 4))
            
            done_count += 1
            broadcast_status[user_id]["done"] = done_count
            
        except TimeoutException:
            fail_count += 1
            broadcast_status[user_id]["fail"] = fail_count
            print(f"[!] Timeout sending to {clean_num}")
        except Exception as e:
            fail_count += 1
            broadcast_status[user_id]["fail"] = fail_count
            print(f"[!] Error sending to {clean_num}: {str(e)}")
        
        # Random delay between sends (5-10 seconds) to avoid rate limiting
        if idx < len(numbers) - 1:  # Don't sleep after the last one
            delay = random.uniform(5, 10)
            time.sleep(delay)
    
    # Mark complete
    broadcast_status[user_id]["running"] = False
    broadcast_status[user_id]["status"] = "completed"
    
    # Save broadcast record
    broadcast_col.insert_one({
        "user_id": user_id,
        "login_key": session.get("login_key", "unknown"),
        "numbers_count": len(numbers),
        "done": done_count,
        "fail": fail_count,
        "message": message[:50] + "..." if len(message) > 50 else message,
        "started_at": get_current_time(),
        "completed_at": get_current_time(),
        "status": "completed"
    })
    
    # Update client stats
    clients_col.update_one({"user_id": user_id}, {
        "$set": {"last_broadcast": get_current_time()},
        "$inc": {"total_sent": done_count, "total_failed": fail_count}
    })

@app.route("/start-broadcast", methods=["POST"])
def start_broadcast():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    client = clients_col.find_one({"user_id": user_id})
    
    if not client or not client.get("whatsapp_connected"):
        return jsonify({"error": "Please connect your WhatsApp number first"}), 400
    
    if user_id not in user_drivers:
        return jsonify({"error": "WhatsApp Web session not active. Please reconnect."}), 400
    
    if broadcast_status.get(user_id, {}).get("running", False):
        return jsonify({"error": "Broadcast already running"}), 400
    
    # Read numbers from file
    if not os.path.exists("number.txt"):
        return jsonify({"error": "number.txt not found. Create it with one number per line."}), 400
    
    with open("number.txt", "r") as f:
        numbers = [line.strip() for line in f if line.strip()]
    
    if not numbers:
        return jsonify({"error": "number.txt is empty"}), 400
    
    # Get broadcast message from admin
    settings = db.settings.find_one({"key": "broadcast_message"})
    message = settings["value"] if settings else None
    
    if not message:
        return jsonify({"error": "Admin has not set a broadcast message yet"}), 400
    
    # Start broadcasting in background thread
    thread = threading.Thread(
        target=whatsapp_broadcast_worker,
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
        return jsonify({"success": True, "message": "Broadcast stopped"})
    
    return jsonify({"error": "No active broadcast"}), 400

@app.route("/broadcast-status", methods=["GET"])
def get_broadcast_status():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    status = broadcast_status.get(user_id, {
        "running": False,
        "done": 0,
        "fail": 0,
        "total": 0,
        "current": 0,
        "status": "idle"
    })
    
    return jsonify(status)

@app.route("/logout")
def user_logout():
    user_id = session.get("user_id")
    if user_id and user_id in user_drivers:
        try:
            user_drivers[user_id].quit()
        except:
            pass
        del user_drivers[user_id]
    
    session.pop("user_id", None)
    session.pop("login_key", None)
    return redirect("/")

# ─── Setup Default Admin ────────────────────────────────────────

def setup_default_admin():
    if not users_col.find_one({"role": "admin"}):
        hashed_pw = generate_password_hash("admin123")
        users_col.insert_one({
            "username": "admin",
            "password": hashed_pw,
            "role": "admin",
            "created_at": get_current_time()
        })
        print("[+] Default admin created: admin / admin123")

if __name__ == "__main__":
    setup_default_admin()
    print("[+] WhatsApp Spreader Panel starting...")
    print("[+] Default Admin: http://127.0.0.1:5000/admin/login")
    print("[+] User Login: http://127.0.0.1:5000/")
    print("[!] On first run, Chrome will open for WhatsApp Web QR scan.")
    app.run(debug=True, host="0.0.0.0", port=5000)