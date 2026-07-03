from flask import Flask, render_template, request, redirect, session, jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import threading
import time
import pywhatkit as kit
import pyautogui
import keyboard
import os
import json
from datetime import datetime
from bson import ObjectId
import random
import string

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# ─── MongoDB Connection ─────────────────────────────────────────
MONGO_URI = "mongodb+srv://nexacoders2_db_user:dxYh7QOdHvH6OVdd@cluster0.f4qxcbk.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["whatsapp_spreader"]
users_col = db["users"]          # Panel admin
clients_col = db["clients"]      # Sub-users (connected accounts)
keys_col = db["login_keys"]      # Generated login keys
broadcast_col = db["broadcasts"] # Broadcast history

# ─── Helper Functions ───────────────────────────────────────────

def generate_login_key():
    """Generate a unique login key for sub-users"""
    return "WS-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

def generate_user_id():
    """Generate a unique user ID"""
    return str(uuid.uuid4())[:8]

def get_current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    
    # Stats
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
    """Panel admin sets the target broadcast message"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    message = request.form.get("message")
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
    
    # Save as global broadcast message
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
        
        # Mark key as used and create client session
        user_id = generate_user_id()
        keys_col.update_one({"_id": key_doc["_id"]}, {
            "$set": {"used": True, "assigned_to": user_id, "used_at": get_current_time()}
        })
        
        # Create client record
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
    
    # Get broadcast message from admin
    settings = db.settings.find_one({"key": "broadcast_message"})
    broadcast_msg = settings["value"] if settings else "No message set by admin yet"
    
    # Get user's broadcast history
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
    clients_col.update_one({"user_id": user_id}, {
        "$set": {
            "whatsapp_number": whatsapp_number,
            "whatsapp_connected": True,
            "connected_at": get_current_time()
        }
    })
    
    return jsonify({"success": True, "message": "WhatsApp connected successfully"})

@app.route("/disconnect-whatsapp", methods=["POST"])
def disconnect_whatsapp():
    if not session.get("user_id"):
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    clients_col.update_one({"user_id": user_id}, {
        "$set": {
            "whatsapp_number": None,
            "whatsapp_connected": False
        }
    })
    
    return jsonify({"success": True, "message": "WhatsApp disconnected"})

# ─── Broadcasting Engine ────────────────────────────────────────

broadcast_status = {}  # {user_id: {"running": bool, "done": int, "fail": int, "total": int, "current": int}}

def whatsapp_broadcast_worker(user_id, numbers, message):
    """Background thread for broadcasting"""
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
    
    for idx, number in enumerate(numbers):
        if not broadcast_status.get(user_id, {}).get("running", False):
            # Broadcast was stopped
            break
        
        number = number.strip()
        if not number:
            continue
        
        # Remove any non-digit characters except +
        clean_num = ''.join(c for c in number if c.isdigit() or c == '+')
        if not clean_num:
            fail_count += 1
            continue
        
        broadcast_status[user_id]["current"] = idx + 1
        broadcast_status[user_id]["status"] = f"Sending to {clean_num}"
        
        try:
            # Send via pywhatkit – this opens WhatsApp Web
            kit.sendwhatmsg_instantly(
                phone_no=clean_num,
                message=message,
                wait_time=15,  # seconds to wait for WhatsApp Web to load
                tab_close=True  # Close tab after sending
            )
            
            # Small delay to avoid detection
            time.sleep(random.uniform(3, 6))
            
            done_count += 1
            broadcast_status[user_id]["done"] = done_count
            
        except Exception as e:
            print(f"Failed to send to {clean_num}: {str(e)}")
            fail_count += 1
            broadcast_status[user_id]["fail"] = fail_count
    
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
        "message": message,
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
    
    if broadcast_status.get(user_id, {}).get("running", False):
        return jsonify({"error": "Broadcast already running"}), 400
    
    # Read numbers from file
    if not os.path.exists("number.txt"):
        return jsonify({"error": "number.txt not found. Create it with one number per line."}), 400
    
    with open("number.txt", "r") as f:
        numbers = [line.strip() for line in f if line.strip()]
    
    if not numbers:
        return jsonify({"error": "number.txt is empty"}), 400
    
    # Get broadcast message
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
    app.run(debug=True, host="0.0.0.0", port=5000)