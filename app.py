import csv
from datetime import datetime, timedelta
from functools import wraps
import io
import json
import os
import sqlite3
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
try:
    from google import genai
except ImportError:
    try:
        import google.generativeai as genai
    except ImportError:
        genai = NoneS
from google.genai import types
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "raghav_sharma_studysync_ultra_secret_key_2026"
DB_NAME = "study_hub.db"

# Initialize Gemini Client with the current active model
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODEL_NAME = "gemini-3.6-flash"


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                xp INTEGER DEFAULT 100,
                daily_goal_mins INTEGER DEFAULT 60,
                is_premium INTEGER DEFAULT 0,
                subscription_plan TEXT DEFAULT 'Free Tier',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backlogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL,
                priority TEXT NOT NULL,
                estimated_hours REAL DEFAULT 1.0,
                due_date TEXT,
                status TEXT DEFAULT 'Backlog',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                mastery_level INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_type TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """
        )

        # Self-healing database migrations
        migrations = [
            "ALTER TABLE users ADD COLUMN xp INTEGER DEFAULT 100",
            "ALTER TABLE users ADD COLUMN daily_goal_mins INTEGER DEFAULT 60",
            "ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN subscription_plan TEXT DEFAULT 'Free Tier'",
            "ALTER TABLE flashcards ADD COLUMN mastery_level INTEGER DEFAULT 0",
        ]
        for query in migrations:
            try:
                conn.execute(query)
            except sqlite3.OperationalError:
                pass

        conn.execute("UPDATE users SET xp = 100 WHERE xp IS NULL")
        conn.execute(
            "UPDATE users SET daily_goal_mins = 60 WHERE daily_goal_mins IS NULL"
        )
        conn.execute(
            "UPDATE users SET is_premium = 0 WHERE is_premium IS NULL"
        )
        conn.execute(
            "UPDATE users SET subscription_plan = 'Free Tier' WHERE subscription_plan IS NULL"
        )
        conn.commit()


init_db()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access your study workstation.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def award_xp(user_id, amount):
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET xp = COALESCE(xp, 100) + ? WHERE id = ?",
            (amount, user_id),
        )
        conn.commit()

        user = conn.execute(
            "SELECT xp FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        current_xp = user["xp"] if user and user["xp"] is not None else 100

    new_level = (current_xp // 250) + 1
    xp_in_level = current_xp % 250
    level_progress = int((xp_in_level / 250) * 100)

    return {
        "xp": current_xp,
        "level": new_level,
        "xp_in_level": xp_in_level,
        "level_progress": level_progress,
        "gained": amount,
    }


def calculate_streak(user_id):
    with get_db_connection() as conn:
        distinct_dates = conn.execute(
            """
            SELECT DISTINCT date(completed_at, 'localtime') as s_date
            FROM study_sessions 
            WHERE user_id = ? AND session_type = 'Focus'
            ORDER BY s_date DESC
        """,
            (user_id,),
        ).fetchall()

    if not distinct_dates:
        return 0

    dates = [
        datetime.strptime(row["s_date"], "%Y-%m-%d").date()
        for row in distinct_dates
    ]
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if dates[0] not in [today, yesterday]:
        return 0

    streak = 1
    for i in range(len(dates) - 1):
        if dates[i] - dates[i + 1] == timedelta(days=1):
            streak += 1
        else:
            break
    return streak


# --- Auth Routes ---
@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        hashed_password = generate_password_hash(password)
        try:
            with get_db_connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, xp, daily_goal_mins, is_premium, subscription_plan) VALUES (?, ?, 100, 60, 0, 'Free Tier')",
                    (username, hashed_password),
                )
                conn.commit()
                session["user_id"] = cursor.lastrowid
                session["username"] = username
                flash(
                    f"Welcome to Study Tracker & Manager by Raghav Sharma, {username}! +100 Welcome XP unlocked.",
                    "success",
                )
                return redirect(url_for("home"))
        except sqlite3.IntegrityError:
            flash("Username already taken. Please pick another.", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash(
                f"Welcome back to Study Tracker & Manager, {user['username']}!",
                "success",
            )
            return redirect(url_for("home"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out safely. Keep conquering!", "info")
    return redirect(url_for("login"))


# --- In-App Checkout Upgrade ---
@app.route("/subscription/upgrade", methods=["POST"])
@login_required
def upgrade_subscription():
    user_id = session["user_id"]
    data = request.get_json() or {}
    plan = data.get("plan", "VIP Pro Scholar")

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET is_premium = 1, subscription_plan = ? WHERE id = ?",
            (plan, user_id),
        )
        conn.commit()

    xp_data = award_xp(user_id, 200)
    return jsonify(
        {
            "status": "success",
            "message": f"🎉 Congratulations! You have unlocked {plan}!",
            "is_premium": 1,
            "plan": plan,
            "xp_data": xp_data,
        }
    )


# --- Main Dashboard ---
@app.route("/")
@login_required
def home():
    user_id = session["user_id"]

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()

        tasks = conn.execute(
            """
            SELECT * FROM backlogs 
            WHERE user_id = ? 
            ORDER BY CASE status WHEN 'In Progress' THEN 1 WHEN 'Backlog' THEN 2 ELSE 3 END, id DESC
        """,
            (user_id,),
        ).fetchall()

        cards_data = conn.execute(
            "SELECT id, subject, question, answer, mastery_level FROM flashcards WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()

        subject_query = conn.execute(
            """
            SELECT subject, SUM(estimated_hours) as total_hours 
            FROM backlogs 
            WHERE user_id = ? 
            GROUP BY subject 
            ORDER BY total_hours DESC
        """,
            (user_id,),
        ).fetchall()

        subject_labels = [row["subject"] for row in subject_query]
        subject_hours = [row["total_hours"] for row in subject_query]

        total_tasks = len(tasks)
        completed_tasks = sum(1 for t in tasks if t["status"] == "Done")
        in_progress_tasks = sum(1 for t in tasks if t["status"] == "In Progress")
        backlog_tasks = sum(1 for t in tasks if t["status"] == "Backlog")
        completion_rate = (
            round((completed_tasks / total_tasks * 100), 1) if total_tasks else 0
        )
        total_hours = sum(t["estimated_hours"] for t in tasks)

        today_focus_row = conn.execute(
            """
            SELECT SUM(duration_minutes) as today_mins, COUNT(id) as session_count
            FROM study_sessions 
            WHERE user_id = ? 
              AND date(completed_at, 'localtime') = date('now', 'localtime') 
              AND session_type = 'Focus'
        """,
            (user_id,),
        ).fetchone()

        today_focus_mins = (
            today_focus_row["today_mins"]
            if today_focus_row["today_mins"]
            else 0
        )
        today_session_count = (
            today_focus_row["session_count"]
            if today_focus_row["session_count"]
            else 0
        )

        recent_sessions = conn.execute(
            """
            SELECT id, session_type, duration_minutes, 
                   strftime('%H:%M', datetime(completed_at, 'localtime')) as session_time,
                   strftime('%d %b', datetime(completed_at, 'localtime')) as session_date
            FROM study_sessions 
            WHERE user_id = ? 
            ORDER BY id DESC LIMIT 10
        """,
            (user_id,),
        ).fetchall()

        session_dates_query = conn.execute(
            """
            SELECT date(completed_at, 'localtime') as study_day, SUM(duration_minutes) as total_day_mins
            FROM study_sessions
            WHERE user_id = ? AND session_type = 'Focus'
              AND date(completed_at, 'localtime') >= date('now', '-29 days', 'localtime')
            GROUP BY study_day
        """,
            (user_id,),
        ).fetchall()

    daily_study_dict = {
        row["study_day"]: row["total_day_mins"] for row in session_dates_query
    }
    today_dt = datetime.now().date()
    heatmap_data = []

    for i in range(29, -1, -1):
        day_date = today_dt - timedelta(days=i)
        day_str = day_date.strftime("%Y-%m-%d")
        mins = daily_study_dict.get(day_str, 0)
        
        if mins >= 60:
            level = 4
        elif mins >= 40:
            level = 3
        elif mins >= 20:
            level = 2
        elif mins > 0:
            level = 1
        else:
            level = 0

        heatmap_data.append(
            {
                "date": day_str,
                "label": day_date.strftime("%b %d"),
                "minutes": mins,
                "level": level,
            }
        )

    user_xp = (
        user["xp"]
        if user and "xp" in user.keys() and user["xp"] is not None
        else 100
    )
    daily_goal = (
        user["daily_goal_mins"]
        if user
        and "daily_goal_mins" in user.keys()
        and user["daily_goal_mins"] is not None
        else 60
    )
    is_premium = user["is_premium"] if user and "is_premium" in user.keys() else 0
    subscription_plan = (
        user["subscription_plan"]
        if user and "subscription_plan" in user.keys()
        else "Free Tier"
    )

    user_level = (user_xp // 250) + 1
    xp_in_level = user_xp % 250
    level_progress = int((xp_in_level / 250) * 100)
    goal_percent = min(100, int((today_focus_mins / daily_goal) * 100)) if daily_goal else 0
    current_streak = calculate_streak(user_id)

    mastered_cards_count = sum(
        1 for c in cards_data if c["mastery_level"] and c["mastery_level"] >= 2
    )
    deck_mastery_rate = (
        round((mastered_cards_count / len(cards_data) * 100), 1)
        if cards_data
        else 0
    )

    readiness_score = int(
        (completion_rate * 0.40)
        + (deck_mastery_rate * 0.35)
        + (goal_percent * 0.25)
    )
    if readiness_score >= 85:
        exam_grade = "A+ (Elite Master)"
        grade_badge = "bg-success"
    elif readiness_score >= 70:
        exam_grade = "A (High Proficiency)"
        grade_badge = "bg-primary"
    elif readiness_score >= 50:
        exam_grade = "B (Solid Progress)"
        grade_badge = "bg-warning text-dark"
    else:
        exam_grade = "C (Ramp Up Study)"
        grade_badge = "bg-danger"

    today_date = datetime.now().date()
    upcoming_deadlines = []
    for t in tasks:
        if t["due_date"] and t["status"] != "Done":
            try:
                due_d = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
                days_left = (due_d - today_date).days
                upcoming_deadlines.append(
                    {
                        "topic": t["topic"],
                        "subject": t["subject"],
                        "days_left": days_left,
                        "due_date": t["due_date"],
                        "urgent": days_left <= 2,
                    }
                )
            except ValueError:
                pass
    upcoming_deadlines.sort(key=lambda x: x["days_left"])

    quests = [
        {
            "id": "q1",
            "title": "Complete 1 Pomodoro Focus Sprint",
            "icon": "⏱️",
            "reward_xp": 50,
            "completed": today_session_count >= 1,
        },
        {
            "id": "q2",
            "title": "Master or Review at least 2 Flashcards",
            "icon": "🧠",
            "reward_xp": 30,
            "completed": mastered_cards_count >= 2,
        },
        {
            "id": "q3",
            "title": "Move 1 Study Target to Completed",
            "icon": "🏆",
            "reward_xp": 35,
            "completed": completed_tasks >= 1,
        },
    ]

    badges = [
        {
            "name": "Novice Scholar",
            "icon": "🎓",
            "unlocked": user_level >= 1,
            "desc": "Join Raghav Sharma's Hub",
        },
        {
            "name": "VIP Pro Patron",
            "icon": "💎",
            "unlocked": bool(is_premium),
            "desc": "Unlock Pro VIP Membership",
        },
        {
            "name": "Focus Master",
            "icon": "⚡",
            "unlocked": today_focus_mins >= 50,
            "desc": "50+ mins focused today",
        },
        {
            "name": "Streak Warrior",
            "icon": "🔥",
            "unlocked": current_streak >= 3,
            "desc": "3-day study streak",
        },
        {
            "name": "Feynman Master",
            "icon": "🧪",
            "unlocked": user_xp >= 300,
            "desc": "Pass Feynman AI Review",
        },
        {
            "name": "Task Crusher",
            "icon": "🏆",
            "unlocked": completed_tasks >= 5,
            "desc": "Complete 5 study targets",
        },
    ]

    stats = {
        "total": total_tasks,
        "completed": completed_tasks,
        "in_progress": in_progress_tasks,
        "backlog": backlog_tasks,
        "rate": completion_rate,
        "hours": total_hours,
        "total_cards": len(cards_data),
        "mastered_cards": mastered_cards_count,
        "deck_mastery_rate": deck_mastery_rate,
        "today_focus_mins": today_focus_mins,
        "today_session_count": today_session_count,
        "username": session.get("username", "Scholar"),
        "ai_enabled": bool(GEMINI_API_KEY),
        "xp": user_xp,
        "level": user_level,
        "level_progress": level_progress,
        "daily_goal": daily_goal,
        "goal_percent": goal_percent,
        "streak": current_streak,
        "badges": badges,
        "readiness_score": readiness_score,
        "exam_grade": exam_grade,
        "grade_badge": grade_badge,
        "upcoming_deadlines": upcoming_deadlines[:3],
        "quests": quests,
        "heatmap": heatmap_data,
        "is_premium": is_premium,
        "subscription_plan": subscription_plan,
    }

    chart_data = {
        "subjectLabels": subject_labels,
        "subjectHours": subject_hours,
        "statusCounts": [backlog_tasks, in_progress_tasks, completed_tasks],
    }

    flashcards_list = [
        {
            "id": c["id"],
            "subject": c["subject"],
            "question": c["question"],
            "answer": c["answer"],
            "mastery_level": c["mastery_level"] if c["mastery_level"] else 0,
        }
        for c in cards_data
    ]

    return render_template(
        "index.html",
        tasks=tasks,
        stats=stats,
        chart_data=json.dumps(chart_data),
        flashcards_json=json.dumps(flashcards_list),
        flashcards=cards_data,
        recent_sessions=recent_sessions,
    )


# --- AI Mock Exam Generator ---
@app.route("/ai/generate-mock-exam", methods=["POST"])
@login_required
def ai_generate_mock_exam():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    difficulty = data.get("difficulty", "Intermediate").strip()

    if not subject:
        return jsonify({"error": "Course subject is required."}), 400

    prompt = f"""
    Create a formal University-Level Mock Exam Paper for:
    Course: {subject}
    Difficulty Tier: {difficulty}

    Return a JSON object containing:
    - "exam_title": "Official Mock Exam: {subject}",
    - "total_marks": 50,
    - "time_allowed": "45 Minutes",
    - "instructions": "Read all questions carefully. Answer concisely.",
    - "sections": [
        {{
          "section_name": "Section A: Conceptual & Analytical (3 Questions)",
          "questions": [
            {{"q_num": 1, "marks": 5, "question": "Question 1 text...", "solution_hint": "Key solution point..."}},
            {{"q_num": 2, "marks": 5, "question": "Question 2 text...", "solution_hint": "Key solution point..."}},
            {{"q_num": 3, "marks": 10, "question": "Question 3 scenario problem...", "solution_hint": "Key solution point..."}}
          ]
        }},
        {{
          "section_name": "Section B: Applied Problem Solving (2 Questions)",
          "questions": [
            {{"q_num": 4, "marks": 15, "question": "Deep calculation or architectural design question...", "solution_hint": "Full derivation/key steps..."}},
            {{"q_num": 5, "marks": 15, "question": "Comparative critical analysis question...", "solution_hint": "Expected criteria..."}}
          ]
        }}
      ]
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        exam_data = json.loads(response.text)
        xp_data = award_xp(user_id, 40)
        return jsonify(
            {"status": "success", "exam": exam_data, "xp_data": xp_data}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- AI Feynman Technique Evaluator ---
@app.route("/ai/feynman-evaluate", methods=["POST"])
@login_required
def ai_feynman_evaluate():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    topic = data.get("topic", "").strip()
    explanation = data.get("explanation", "").strip()

    if not topic or not explanation:
        return jsonify({"error": "Topic and explanation are required."}), 400

    prompt = f"""
    You are an expert cognitive learning scientist evaluating a student using the 'Feynman Technique' (explaining a complex idea simply in their own words).
    Course/Subject: {subject}
    Topic: {topic}
    Student's Explanation:
    ---
    {explanation}
    ---

    Evaluate the student's conceptual grasp and return a valid JSON object with:
    - "feynman_score": Integer 0-100 based on clarity, intuitive grasp, and absence of jargon.
    - "mastery_tier": "Mastery (Intuitive)", "Proficient (Minor Gaps)", or "Needs Simplicity (Jargon-Heavy)"
    - "strengths": Array of 2-3 specific points explained accurately.
    - "blind_spots": Array of 1-2 misconceptions or omitted nuances.
    - "simplified_analogy": A 2-sentence plain-English metaphor explaining this concept to a 10-year-old child.
    - "verdict_feedback": Encouraging 2-sentence summary feedback.
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        result = json.loads(response.text)
        xp_data = award_xp(user_id, 50)
        return jsonify({"status": "success", "result": result, "xp_data": xp_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- AI Visual Mind-Map Generator (Mermaid.js) ---
@app.route("/ai/generate-mindmap", methods=["POST"])
@login_required
def ai_generate_mindmap():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    topic = data.get("topic", "").strip()

    if not subject or not topic:
        return jsonify({"error": "Subject and topic are required."}), 400

    prompt = f"""
    Create an intuitive, structured Mermaid.js mind map diagram for Course '{subject}', Topic '{topic}'.
    Requirements:
    1. Start with 'graph TD'.
    2. Place the Main Topic at root.
    3. Branch into 3-4 Primary Concept pillars.
    4. Sub-branch each pillar into 2-3 key sub-concepts or formulas.
    5. Clean node labels without syntax brackets inside text.
    6. Return ONLY raw Mermaid.js code (NO markdown, NO backticks).
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
        raw_mermaid = response.text.strip()
        if raw_mermaid.startswith("```"):
            lines = raw_mermaid.split("\n")
            raw_mermaid = "\n".join(
                [line for line in lines if not line.startswith("```")]
            )

        xp_data = award_xp(user_id, 25)
        return jsonify(
            {
                "status": "success",
                "mermaid_code": raw_mermaid.strip(),
                "xp_data": xp_data,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/quest/claim/<int:reward_xp>", methods=["POST"])
@login_required
def claim_quest(reward_xp):
    user_id = session["user_id"]
    xp_data = award_xp(user_id, reward_xp)
    return jsonify(
        {
            "status": "success",
            "xp_data": xp_data,
            "message": f"Quest Claimed! +{reward_xp} XP Added!",
        }
    )


@app.route("/ai/motivate", methods=["POST"])
@login_required
def ai_motivate():
    user_id = session["user_id"]
    if not ai_client:
        return jsonify(
            {
                "status": "success",
                "quote": "“Discipline is choosing between what you want now and what you want most.”",
                "author": "Raghav Sharma's Wisdom Engine",
                "tactical_tip": "Focus on 1 deep work target without phone notifications for 25 minutes.",
            }
        )

    prompt = """
    You are Raghav Sharma's elite AI Study Performance Coach. Provide an inspiring quote and a concrete 1-sentence tactical study tip.
    Return JSON with keys:
    - "quote": "Powerful motivational quote"
    - "author": "Famous thinker or Raghav Sharma Coaching"
    - "tactical_tip": "1 actionable technique to immediately crush study procrastination"
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        data = json.loads(response.text)
        award_xp(user_id, 10)
        return jsonify({"status": "success", **data})
    except Exception:
        return jsonify(
            {
                "status": "success",
                "quote": "“Success is the sum of small efforts, repeated day in and day out.”",
                "author": "Robert Collier",
                "tactical_tip": "Complete just 5 minutes of your hardest task right now to break inertia.",
            }
        )


@app.route("/flashcard/review/<int:card_id>/<int:level>", methods=["POST"])
@login_required
def review_flashcard(card_id, level):
    user_id = session["user_id"]
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE flashcards SET mastery_level = ? WHERE id = ? AND user_id = ?",
            (level, card_id, user_id),
        )
        conn.commit()

    xp_bonus = 15 if level >= 2 else 5
    xp_data = award_xp(user_id, xp_bonus)

    return jsonify(
        {
            "status": "success",
            "new_level": level,
            "xp_data": xp_data,
            "message": f"+{xp_bonus} XP gained for active recall!",
        }
    )


@app.route("/status/<int:task_id>/<new_status>", methods=["GET", "POST"])
@login_required
def update_status(task_id, new_status):
    user_id = session["user_id"]
    if new_status in ["Backlog", "In Progress", "Done"]:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE backlogs SET status = ? WHERE id = ? AND user_id = ?",
                (new_status, task_id, user_id),
            )
            conn.commit()

    xp_data = None
    if new_status == "Done":
        xp_data = award_xp(user_id, 35)

    if (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
    ):
        return jsonify(
            {
                "status": "success",
                "task_id": task_id,
                "new_status": new_status,
                "xp_data": xp_data,
            }
        )

    return redirect(url_for("home"))


@app.route("/ai/parse-text", methods=["POST"])
@login_required
def ai_parse_notes_text():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    raw_text = data.get("text", "").strip()
    subject = data.get("subject", "").strip() or "General Study"

    if not raw_text:
        return (
            jsonify({"error": "Please provide study notes or syllabus text."}),
            400,
        )

    prompt = f"""
    Analyze the following study notes or syllabus text for Course '{subject}':
    ---
    {raw_text}
    ---
    Extract and return a JSON object with:
    1. "extracted_tasks": Array of 3-4 actionable study backlog items (topic, priority: High/Medium/Low, estimated_hours: float).
    2. "extracted_flashcards": Array of 3 high-yield question/answer pairs.
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        parsed_data = json.loads(response.text)

        today_str = datetime.now().strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            for t in parsed_data.get("extracted_tasks", []):
                conn.execute(
                    """
                    INSERT INTO backlogs (user_id, subject, topic, priority, estimated_hours, due_date, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'Backlog')
                """,
                    (
                        user_id,
                        subject,
                        t.get("topic", "Extracted Topic"),
                        t.get("priority", "Medium"),
                        float(t.get("estimated_hours") or 1.5),
                        today_str,
                    ),
                )

            for f in parsed_data.get("extracted_flashcards", []):
                conn.execute(
                    """
                    INSERT INTO flashcards (user_id, subject, question, answer, mastery_level)
                    VALUES (?, ?, ?, ?, 0)
                """,
                    (
                        user_id,
                        subject,
                        f.get("question", ""),
                        f.get("answer", ""),
                    ),
                )
            conn.commit()

        xp_data = award_xp(user_id, 45)
        return jsonify(
            {
                "status": "success",
                "tasks_count": len(parsed_data.get("extracted_tasks", [])),
                "cards_count": len(
                    parsed_data.get("extracted_flashcards", [])
                ),
                "xp_data": xp_data,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ai/tutor-chat", methods=["POST"])
@login_required
def ai_tutor_chat():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    active_subject = data.get("subject", "").strip() or "General Study"
    active_topic = data.get("topic", "").strip() or "Academic Concepts"

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    prompt = f"""
    You are 'Professor Sync', the AI Tutor inside Raghav Sharma's Study Tracker & Manager platform.
    Student Context:
    - Subject: {active_subject}
    - Topic: {active_topic}

    Student's question: "{user_message}"
    Provide an encouraging, clear, structured explanation with key points and a 1-sentence memorable summary.
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
        reply_text = response.text
        xp_data = award_xp(user_id, 10)
        return jsonify(
            {"status": "success", "reply": reply_text, "xp_data": xp_data}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ai/generate-notes", methods=["POST"])
@login_required
def ai_generate_notes():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    topic = data.get("topic", "").strip()

    if not subject or not topic:
        return jsonify({"error": "Subject and topic are required."}), 400

    prompt = f"""
    Create a high-yield Cheat Sheet for:
    Course: {subject}
    Topic: {topic}

    Return JSON with:
    - "topic_title": "{topic}",
    - "quick_summary": "3-4 concise bullet points",
    - "key_concepts": [{{"name": "Concept 1", "explanation": "Definition"}}],
    - "memory_hack": "A clever mnemonic or mental model",
    - "exam_traps": ["Common mistake 1", "Common mistake 2"]
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        notes_data = json.loads(response.text)
        xp_data = award_xp(user_id, 25)
        return jsonify(
            {"status": "success", "notes": notes_data, "xp_data": xp_data}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ai/generate-roadmap", methods=["POST"])
@login_required
def ai_generate_roadmap():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    target_goal = (
        data.get("target_goal", "").strip()
        or "Master fundamentals and core problem solving"
    )
    hours_per_day = float(data.get("hours_per_day") or 2.0)

    if not subject:
        return jsonify({"error": "Subject is required."}), 400

    prompt = f"""
    Create a 7-Day Study Syllabus for:
    Course: {subject}
    Target Goal: {target_goal}
    Daily Time Commitment: {hours_per_day} hours/day

    Return JSON with 'course_title', 'overview', and 'days' (array of 7 objects with day_number, title, tasks, estimated_hours, key_takeaway).
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        roadmap_data = json.loads(response.text)
        xp_data = award_xp(user_id, 30)
        return jsonify(
            {
                "status": "success",
                "roadmap": roadmap_data,
                "xp_data": xp_data,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ai/import-roadmap", methods=["POST"])
@login_required
def ai_import_roadmap():
    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "AI Syllabus")
    days = data.get("days", [])

    if not days:
        return jsonify({"error": "No roadmap days provided."}), 400

    today = datetime.now().date()
    with get_db_connection() as conn:
        for day in days:
            day_num = day.get("day_number", 1)
            topic = f"Day {day_num}: {day.get('title', 'Study Sprint')}"
            est_hours = float(day.get("estimated_hours") or 2.0)
            due_date = (today + timedelta(days=day_num - 1)).strftime(
                "%Y-%m-%d"
            )

            conn.execute(
                """
                INSERT INTO backlogs (user_id, subject, topic, priority, estimated_hours, due_date, status)
                VALUES (?, ?, ?, 'Medium', ?, ?, 'Backlog')
            """,
                (user_id, subject, topic, est_hours, due_date),
            )
        conn.commit()

    xp_data = award_xp(user_id, 40)
    return jsonify(
        {
            "status": "success",
            "imported_count": len(days),
            "xp_data": xp_data,
        }
    )


@app.route("/ai/generate-flashcards", methods=["POST"])
@login_required
def ai_generate_flashcards():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    user_id = session["user_id"]
    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    topic = data.get("topic", "").strip()

    if not subject or not topic:
        return jsonify({"error": "Subject and topic are required."}), 400

    prompt = f"""
    Generate exactly 3 high-yield study flashcards for Course '{subject}', Topic '{topic}'.
    Return JSON array of objects with 'question' and 'answer' keys.
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        generated_cards = json.loads(response.text)

        with get_db_connection() as conn:
            for card in generated_cards:
                conn.execute(
                    """
                    INSERT INTO flashcards (user_id, subject, question, answer, mastery_level)
                    VALUES (?, ?, ?, ?, 0)
                """,
                    (user_id, subject, card["question"], card["answer"]),
                )
            conn.commit()

        xp_data = award_xp(user_id, 20)
        return jsonify(
            {
                "status": "success",
                "count": len(generated_cards),
                "cards": generated_cards,
                "xp_data": xp_data,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ai/generate-quiz", methods=["POST"])
@login_required
def ai_generate_quiz():
    if not ai_client:
        return jsonify({"error": "Gemini API key is not configured."}), 400

    data = request.get_json() or {}
    subject = data.get("subject", "").strip()
    topic = data.get("topic", "").strip()

    if not subject or not topic:
        return jsonify({"error": "Subject and topic are required."}), 400

    prompt = f"""
    Create a 3-question Multiple Choice Quiz for Course '{subject}', Topic '{topic}'.
    Return JSON with 'question', 'options' (array of 4), 'correct_index' (0-3), and 'explanation'.
    """

    try:
        response = ai_client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        quiz_data = json.loads(response.text)
        return jsonify({"status": "success", "quiz": quiz_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/add", methods=["POST"])
@login_required
def add_task():
    user_id = session["user_id"]
    subject = request.form.get("subject", "").strip()
    topic = request.form.get("topic", "").strip()
    priority = request.form.get("priority", "Medium")
    estimated_hours = float(request.form.get("estimated_hours") or 1.0)
    due_date = request.form.get("due_date", "")

    if subject and topic:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO backlogs (user_id, subject, topic, priority, estimated_hours, due_date, status)
                VALUES (?, ?, ?, ?, ?, ?, 'Backlog')
            """,
                (user_id, subject, topic, priority, estimated_hours, due_date),
            )
            conn.commit()
        award_xp(user_id, 15)
    return redirect(url_for("home"))


@app.route("/edit/<int:task_id>", methods=["POST"])
@login_required
def edit_task(task_id):
    user_id = session["user_id"]
    subject = request.form.get("subject", "").strip()
    topic = request.form.get("topic", "").strip()
    priority = request.form.get("priority", "Medium")
    estimated_hours = float(request.form.get("estimated_hours") or 1.0)
    due_date = request.form.get("due_date", "")

    if subject and topic:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE backlogs 
                SET subject = ?, topic = ?, priority = ?, estimated_hours = ?, due_date = ?
                WHERE id = ? AND user_id = ?
            """,
                (
                    subject,
                    topic,
                    priority,
                    estimated_hours,
                    due_date,
                    task_id,
                    user_id,
                ),
            )
            conn.commit()
    return redirect(url_for("home"))


@app.route("/delete/<int:task_id>")
@login_required
def delete_task(task_id):
    user_id = session["user_id"]
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM backlogs WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        conn.commit()
    return redirect(url_for("home"))


@app.route("/flashcard/add", methods=["POST"])
@login_required
def add_flashcard():
    user_id = session["user_id"]
    subject = request.form.get("card_subject", "").strip()
    question = request.form.get("card_question", "").strip()
    answer = request.form.get("card_answer", "").strip()

    if subject and question and answer:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO flashcards (user_id, subject, question, answer, mastery_level)
                VALUES (?, ?, ?, ?, 0)
            """,
                (user_id, subject, question, answer),
            )
            conn.commit()
        award_xp(user_id, 10)
    return redirect(url_for("home"))


@app.route("/flashcard/delete/<int:card_id>")
@login_required
def delete_flashcard(card_id):
    user_id = session["user_id"]
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM flashcards WHERE id = ? AND user_id = ?",
            (card_id, user_id),
        )
        conn.commit()
    return redirect(url_for("home"))


@app.route("/session/log", methods=["POST"])
@login_required
def log_session():
    user_id = session["user_id"]
    data = request.get_json(silent=True) or request.form
    session_type = data.get("session_type", "Focus")
    duration_minutes = int(data.get("duration_minutes", 25))

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (user_id, session_type, duration_minutes)
            VALUES (?, ?, ?)
        """,
            (user_id, session_type, duration_minutes),
        )
        conn.commit()

    xp_data = None
    if session_type == "Focus":
        xp_data = award_xp(user_id, 50)

    return jsonify(
        {
            "status": "success",
            "logged_minutes": duration_minutes,
            "xp_data": xp_data,
        }
    )


@app.route("/session/clear", methods=["POST"])
@login_required
def clear_sessions():
    user_id = session["user_id"]
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM study_sessions WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    return redirect(url_for("home"))


@app.route("/export/csv")
@login_required
def export_csv():
    user_id = session["user_id"]
    with get_db_connection() as conn:
        tasks = conn.execute(
            "SELECT * FROM backlogs WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "# Study Tracker & Manager | Official Transcript - Made by Raghav Sharma"
        ]
    )
    writer.writerow(
        [
            "Task ID",
            "Subject",
            "Topic",
            "Priority",
            "Estimated Hours",
            "Due Date",
            "Status",
            "Created Date",
        ]
    )

    for t in tasks:
        writer.writerow(
            [
                t["id"],
                t["subject"],
                t["topic"],
                t["priority"],
                t["estimated_hours"],
                t["due_date"] if t["due_date"] else "N/A",
                t["status"],
                t["created_at"],
            ]
        )

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=RaghavSharma_StudyTracker_{session.get('username')}.csv"
        },
    )

@app.route("/robots.txt")
def robots():
  content = """User-agent: *
Allow: /
Allow: /login
Allow: /register
Sitemap: https://study-taracker-manager.onrender.com/sitemap.xml
"""
  return Response(content, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
  xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://study-taracker-manager.onrender.com/</loc>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://study-taracker-manager.onrender.com/login</loc>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://study-taracker-manager.onrender.com/register</loc>
    <priority>0.8</priority>
  </url>
</urlset>
"""
  return Response(xml_data, mimetype="application/xml")
  import json
import google.generativeai as genai
import os
import json
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory

# Safe AI import: Prevents server crashes if the library is missing or loading
try:
    import google.generativeai as genai
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
except Exception:
    genai = None
from flask import Flask, jsonify, render_template, request, session

# Configure Gemini if key is present
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_KEY:
  genai.configure(api_key=GEMINI_KEY)


@app.route("/api/generate-hud", methods=["POST"])
def generate_hud():
  data = request.get_json() or {}
  name = data.get("name", "Scholar")
  grade = data.get("grade", "Class 11")
  target_exam = data.get("target_exam", "JEE")
  daily_hours = data.get("daily_hours", "6")

  # High-reliability fallback blueprints in case Gemini is offline or rate-limited
  fallback_blueprints = {
      "JEE": {
          "theme": "engineer",
          "system_title": "IIT-JEE QUANTUM CORE // ENG-OS",
          "badge": "ENGINEERING ASPIRANT",
          "primary_color": "#00f0ff",
          "accent_color": "#8b5cf6",
          "subjects": [
              {
                  "name": "Physics",
                  "chapter": "Rotational Dynamics & Inertia",
                  "hours": 3.0,
                  "total_q": 45,
              },
              {
                  "name": "Physics",
                  "chapter": "Work, Power & Energy",
                  "hours": 2.5,
                  "total_q": 30,
              },
              {
                  "name": "Mathematics",
                  "chapter": "Coordinate Geometry & Conic Sections",
                  "hours": 4.0,
                  "total_q": 60,
              },
              {
                  "name": "Mathematics",
                  "chapter": "Calculus: Continuity & Limits",
                  "hours": 3.5,
                  "total_q": 50,
              },
              {
                  "name": "Chemistry",
                  "chapter": "Chemical Bonding & Molecular Structure",
                  "hours": 2.5,
                  "total_q": 40,
              },
              {
                  "name": "Chemistry",
                  "chapter": "Thermodynamics & Thermochemistry",
                  "hours": 3.0,
                  "total_q": 35,
              },
          ],
      },
      "NEET": {
          "theme": "medical",
          "system_title": "NEET-UG SURGICAL BIO-MATRIX // MED-OS",
          "badge": "FUTURE DOCTOR / MBBS ASPIRANT",
          "primary_color": "#10b981",
          "accent_color": "#ec4899",
          "subjects": [
              {
                  "name": "Biology (Botany)",
                  "chapter": "Plant Physiology & Photosynthesis",
                  "hours": 3.0,
                  "total_q": 70,
              },
              {
                  "name": "Biology (Zoology)",
                  "chapter": "Human Circulatory System & Cardiac Cycle",
                  "hours": 3.5,
                  "total_q": 85,
              },
              {
                  "name": "Biology (Genetics)",
                  "chapter": "Molecular Basis of Inheritance",
                  "hours": 4.0,
                  "total_q": 90,
              },
              {
                  "name": "Chemistry",
                  "chapter": "Organic Chemistry: Hydrocarbons & Mechanisms",
                  "hours": 3.0,
                  "total_q": 50,
              },
              {
                  "name": "Chemistry",
                  "chapter": "Equilibrium & Solutions",
                  "hours": 2.5,
                  "total_q": 40,
              },
              {
                  "name": "Physics",
                  "chapter": "Ray Optics & Optical Instruments",
                  "hours": 3.0,
                  "total_q": 40,
              },
          ],
      },
      "UPSC": {
          "theme": "civil",
          "system_title": "CIVIL SERVICES STRATEGIC COMMAND // UPSC-OS",
          "badge": "IAS / IPS ASPIRANT",
          "primary_color": "#f59e0b",
          "accent_color": "#3b82f6",
          "subjects": [
              {
                  "name": "Indian Polity",
                  "chapter": "Fundamental Rights & Constitutional Framework",
                  "hours": 4.0,
                  "total_q": 30,
              },
              {
                  "name": "Modern History",
                  "chapter": "Freedom Struggle 1857-1947",
                  "hours": 3.5,
                  "total_q": 25,
              },
              {
                  "name": "Geography",
                  "chapter": "Geomorphology & Monsoon Mechanisms",
                  "hours": 3.0,
                  "total_q": 30,
              },
              {
                  "name": "Economics",
                  "chapter": "Macroeconomic Indicators & Fiscal Policy",
                  "hours": 3.0,
                  "total_q": 25,
              },
          ],
      },
  }

  # Pick default archetype based on target
  selected_key = "JEE"
  if "NEET" in target_exam.upper() or "MED" in target_exam.upper():
    selected_key = "NEET"
  elif (
      "UPSC" in target_exam.upper()
      or "CIVIL" in target_exam.upper()
      or "GOVT" in target_exam.upper()
  ):
    selected_key = "UPSC"

  blueprint = fallback_blueprints.get(selected_key, fallback_blueprints["JEE"])

  # Attempt Gemini AI Real-time Generation
  if GEMINI_KEY:
    try:
      model = genai.GenerativeModel("gemini-2.5-flash")
      prompt = f"""
            You are an expert curriculum architect for competitive students.
            Create a custom study HUD profile for:
            - Student Name: {name}
            - Grade/Class: {grade}
            - Target Examination/Course: {target_exam}
            - Daily Target Hours: {daily_hours}
            
            Return ONLY raw JSON (no markdown formatting, no backticks, no explanatory text) with this exact schema:
            {{
                "theme": "engineer" or "medical" or "civil",
                "system_title": "Custom Futuristic HUD Title",
                "badge": "Personalized Role Badge",
                "primary_color": "#hex",
                "accent_color": "#hex",
                "subjects": [
                    {{"name": "Subject Name", "chapter": "Important High-Yield Chapter", "hours": 3.0, "total_q": 40}},
                    {{"name": "Subject Name", "chapter": "High-Yield Chapter 2", "hours": 2.5, "total_q": 50}},
                    {{"name": "Subject Name", "chapter": "High-Yield Chapter 3", "hours": 3.5, "total_q": 35}},
                    {{"name": "Subject Name", "chapter": "High-Yield Chapter 4", "hours": 2.0, "total_q": 30}},
                    {{"name": "Subject Name", "chapter": "High-Yield Chapter 5", "hours": 4.0, "total_q": 60}}
                ]
            }}
            """
      response = model.generate_content(prompt)
      cleaned_text = (
          response.text.strip()
          .replace("```json", "")
          .replace("```", "")
          .strip()
      )
      parsed_data = json.loads(cleaned_text)
      return jsonify({"status": "success", "profile": parsed_data})
    except Exception as e:
      print("Gemini API fallback engaged:", str(e))

  return jsonify({"status": "success", "profile": blueprint})


@app.route("/ai-tutor", methods=["POST"])
def ai_tutor():
  data = request.get_json() or {}
  question = data.get("question", "")
  user_exam = data.get("exam", "General")
  student_name = data.get("name", "Student")

  if not GEMINI_KEY:
    return jsonify({
        "reply": (
            f"Exam Coach for {user_exam}: Focus on fundamental definitions,"
            " past exam patterns, and high-yield problem solving steps."
        )
    })

  try:
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""
        You are an elite, highly encouraging AI Professor coaching {student_name} for the {user_exam} exam.
        Answer their doubt concisely in maximum 3 bullet points or short steps. Be rigorous, precise, and exam-focused.
        Question: {question}
        """
    response = model.generate_content(prompt)
    return jsonify({"reply": response.text.strip()})
  except Exception as e:
    return jsonify({
        "reply": (
            "Break down the problem by writing the known variables and applying"
            " standard first-principles formulas."
        )
    })
    # --- PWA Mobile App Support ---
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def serve_sw():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache'
    return response
if __name__ == "__main__":
    app.run(debug=True, port=5000)