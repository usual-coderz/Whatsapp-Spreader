import os
from flask import Flask, render_template, request, redirect, session, jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from datetime import datetime
import uuid
import random
import string

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

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


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate_login_key():
    return "WS-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))


def generate_user_id():
    return str(uuid.uuid4())[:8]


# ─── Routes ────────────────────────────────────────────────────

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
    """Placeholder - will be implemented with Playwright"""
    return jsonify({"success": False, "error": "WhatsApp connection coming soon"})


@app.route("/check-whatsapp-status", methods=["GET"])
def check_whatsapp_status():
    return jsonify({"connected": False})


@app.route("/disconnect-whatsapp", methods=["POST"])
def disconnect_whatsapp():
    return jsonify({"success": True})


@app.route("/start-broadcast", methods=["POST"])
def start_broadcast():
    return jsonify({"error": "Broadcast engine coming soon"}), 400


@app.route("/stop-broadcast", methods=["POST"])
def stop_broadcast():
    return jsonify({"success": True})


@app.route("/broadcast-status", methods=["GET"])
def get_broadcast_status():
    return jsonify({"running": False, "done": 0, "fail": 0, "total": 0, "current": 0, "status": "idle"})


@app.route("/logout")
def user_logout():
    session.clear()
    return redirect("/")


# ─── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)