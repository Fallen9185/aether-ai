import os
import io
import base64
import sqlite3
from flask import Flask, render_template, request, jsonify
from gigachat import GigaChat

app = Flask(__name__)

# Ваш ключ авторизации GigaChat
GIGACHAT_KEY = "MDFhMjBlM2EtMTA5My03NDZmLWJhMDEtZDc1YTFlNzE4ZTczOmZjZDMzYjM2LTAyNTQtNDU4OS05ZTY0LTFjMGExZDEyYTdlOA=="

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "chat_history.db")

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS chats (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            title TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS messages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            chat_id INTEGER NOT NULL,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
                        )''')

init_db()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/favicon.ico")
def favicon():
    svg = '''<svg xmlns="http://w3.org" viewBox="0 0 64 64">
        <rect width="64" height="64" rx="16" fill="#0f172a"/>
        <polygon points="32,8 39,25 57,26 43,38 47,56 32,46 17,56 21,38 7,26 25,25" fill="#38bdf8"/>
    </svg>'''
    return svg, 200, {"Content-Type": "image/svg+xml"}

@app.route("/chats", methods=["GET"])
def get_chats():
    with get_db() as conn:
        rows = conn.execute("SELECT id, title FROM chats ORDER BY id DESC").fetchall()
        chats = [{"id": r["id"], "title": r["title"]} for r in rows]
    return jsonify(chats)

@app.route("/chats/<int:chat_id>", methods=["DELETE"])
def delete_chat(chat_id):
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return jsonify({"status": "deleted"})

@app.route("/messages/<int:msg_id>", methods=["DELETE"])
def delete_message(msg_id):
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    return jsonify({"status": "deleted"})

@app.route("/history/clear", methods=["DELETE"])
def clear_all_history():
    with get_db() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM chats")
    return jsonify({"status": "cleared"})

@app.route("/chats/<int:chat_id>/messages", methods=["GET"])
def get_messages(chat_id):
    with get_db() as conn:
        rows = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,)).fetchall()
        msgs = [{"role": r["role"], "content": r["content"]} for r in rows]
    return jsonify(msgs)

@app.route("/history", methods=["GET"])
def get_history():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, chat_id, content, datetime(created_at, 'localtime') as time
            FROM messages
            WHERE role = 'user'
            ORDER BY id DESC
        """).fetchall()
        items = [{"id": r["id"], "chat_id": r["chat_id"], "question": r["content"], "time": r["time"]} for r in rows]
    return jsonify(items)

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    chat_id = data.get("chat_id")
    user_message = data.get("message", "").strip()
    image_data = data.get("image")
    role_prompt = data.get("role", "Ты полезный ИИ-помощник.")

    if image_data and not user_message:
        user_message = "Реши задание с фото пошагово, объясни ход решения и проверь, есть ли ошибки (если на фото есть моё решение)."

    saved_user_text = ("📷 [Фото ДЗ] " + user_message) if image_data else user_message

    with get_db() as conn:
        if not chat_id:
            short_title = saved_user_text[:28] + ("..." if len(saved_user_text) > 28 else "")
            cursor = conn.execute("INSERT INTO chats (title) VALUES (?)", (short_title,))
            chat_id = cursor.lastrowid

        conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", saved_user_text))
        rows = conn.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 10", (chat_id,)).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    models_to_try = ["GigaChat-2-Max", "GigaChat-2-Pro", "GigaChat-2", "GigaChat-Pro"] if image_data else ["GigaChat-2", "GigaChat-3-Ultra", "GigaChat-Pro"]
    last_error = ""

    for model_name in models_to_try:
        try:
            with GigaChat(credentials=GIGACHAT_KEY, verify_ssl_certs=False, model=model_name, scope="GIGACHAT_API_PERS", timeout=25.0) as giga:
                api_messages = [{"role": "system", "content": role_prompt}] + history
                if image_data:
                    _, _, raw_b64 = image_data.partition(",") if "," in image_data else ("", "", image_data)
                    file_stream = io.BytesIO(base64.b64decode(raw_b64))
                    file_stream.name = "homework.jpg"
                    uploaded = giga.upload_file(file_stream)
                    api_messages.pop()
                    api_messages.append({
                        "role": "user",
                        "content": user_message,
                        "attachments": [uploaded.id_]
                    })

                response = giga.chat({"messages": api_messages})
                first_choice = next(iter(response.choices))
                answer = first_choice.message.content

            with get_db() as conn:
                conn.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", answer))

            return jsonify({"reply": answer, "chat_id": chat_id})
        except Exception as e:
            last_error = str(e)
            if "No such model" in last_error or "404" in last_error or "model" in last_error.lower():
                continue
            break

    return jsonify({"reply": f"Ошибка GigaChat: {last_error}", "chat_id": chat_id}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", debug=False, port=port)
