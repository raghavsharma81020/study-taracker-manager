import json
import os
import sqlite3
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.secret_key = "study-command-secret-key-2026"
DB_PATH = "study_database.db"


# ==============================================================================
# 1. SQLITE DATABASE INITIALIZATION (Standard Library)
# ==============================================================================
def init_db():
  with sqlite3.connect(DB_PATH) as conn:
    cursor = conn.cursor()
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_state (
                username TEXT PRIMARY KEY,
                payload TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()


init_db()


# ==============================================================================
# 2. CRASH-PROOF GEMINI AI ENGINE
# ==============================================================================
def generate_ai_answer(prompt: str) -> str:
  api_key = os.environ.get("GEMINI_API_KEY", "").strip()
  if not api_key:
    return ""

  # Method A: Try modern google-genai
  try:
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )
    if resp and resp.text:
      return resp.text.strip()
  except Exception:
    pass

  # Method B: Try legacy google-generativeai
  try:
    import google.generativeai as legacy_ai

    legacy_ai.configure(api_key=api_key)
    model = legacy_ai.GenerativeModel("gemini-2.5-flash")
    resp = model.generate_content(prompt)
    if resp and resp.text:
      return resp.text.strip()
  except Exception as err:
    print(f"Gemini API Exception: {err}")

  return ""


# ==============================================================================
# 3. ROUTES & API ENDPOINTS
# ==============================================================================
@app.route("/")
def index():
  return render_template("index.html")


# Cloud Sync: Save user state
@app.route("/api/sync/save", methods=["POST"])
def sync_save():
  try:
    data = request.get_json() or {}
    username = data.get("username", "default_student").strip() or "student"
    payload_str = json.dumps(data)

    with sqlite3.connect(DB_PATH) as conn:
      cur = conn.cursor()
      cur.execute(
          """
                INSERT INTO user_state (username, payload, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(username) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP
            """,
          (username, payload_str),
      )
      conn.commit()
    return jsonify({"status": "saved", "message": "Synced to SQLite Cloud"})
  except Exception as e:
    return jsonify({"status": "error", "message": str(e)}), 500


# Cloud Sync: Load user state
@app.route("/api/sync/load", methods=["GET"])
def sync_load():
  username = request.args.get("username", "default_student").strip()
  try:
    with sqlite3.connect(DB_PATH) as conn:
      cur = conn.cursor()
      cur.execute(
          "SELECT payload FROM user_state WHERE username = ?", (username,)
      )
      row = cur.fetchone()
      if row:
        return jsonify({"found": True, "data": json.loads(row[0])})
    return jsonify({"found": False})
  except Exception as e:
    return jsonify({"found": False, "error": str(e)})


# AI Doubt Solver Endpoint
@app.route("/api/ask-ai", methods=["POST"])
def ask_ai():
  data = request.get_json() or {}
  question = data.get("question", "").strip()
  exam = data.get("exam", "Competitive Exam")

  if not question:
    return jsonify({"answer": "Please ask a specific concept or doubt."})

  prompt = f"""
    You are an elite academic mentor for {exam}.
    Answer this student's doubt directly, concisely, and with precision:
    "{question}"
    Use clear bullet points, relevant formulas, or shortcuts. Keep it under 150 words.
    """
  answer = generate_ai_answer(prompt)
  if not answer:
    answer = (
        f"Concept Guide for {exam}: Focus on foundational principles, verify"
        " dimensional consistency, and confirm all boundary/sign conventions"
        " before proceeding."
    )

  return jsonify({"answer": answer})


# PWA Assets
@app.route("/manifest.json")
def serve_manifest():
  return send_from_directory(
      "static", "manifest.json", mimetype="application/manifest+json"
  )


@app.route("/sw.js")
def serve_sw():
  resp = send_from_directory(
      "static", "sw.js", mimetype="application/javascript"
  )
  resp.headers["Cache-Control"] = "no-cache"
  return resp


if __name__ == "__main__":
  app.run(debug=True)