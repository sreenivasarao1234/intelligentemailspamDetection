import os
import re
import sqlite3
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import joblib
import numpy as np

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(STATIC_DIR, "spam_history.db")

app = Flask(__name__)
CORS(app)

# ──────────────────────────────────────────────
# SQLite Initialisation
# ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_preview TEXT,
            full_message    TEXT,
            prediction      TEXT,
            confidence      REAL,
            spam_words      TEXT,
            urls_found      TEXT,
            explanation     TEXT,
            sender          TEXT DEFAULT '',
            feedback        TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


init_db()

# ──────────────────────────────────────────────
# Load ML Artefacts
# ──────────────────────────────────────────────
model = None
vectorizer = None
model_accuracy = None
feature_names = None

try:
    model = joblib.load(os.path.join(STATIC_DIR, "model.pkl"))
    vectorizer = joblib.load(os.path.join(STATIC_DIR, "vectorizer.pkl"))
    model_accuracy = joblib.load(os.path.join(STATIC_DIR, "accuracy.pkl"))
    fn_path = os.path.join(STATIC_DIR, "feature_names.pkl")
    if os.path.exists(fn_path):
        feature_names = joblib.load(fn_path)
    print(f"Model loaded. Accuracy: {model_accuracy:.4f}")
except Exception as e:
    print(f"Error loading model: {e}")

# ──────────────────────────────────────────────
# Suspicious URL / Phishing helpers
# ──────────────────────────────────────────────
URL_REGEX = re.compile(
    r'https?://[^\s<>"\']+|www\.[^\s<>"\']+', re.IGNORECASE
)

SUSPICIOUS_DOMAINS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd",
    "buff.ly", "adf.ly", "bit.do", "cutt.ly",
    "free-money.com", "prize-winner.com", "claim-now.net",
}

SUSPICIOUS_TLDS = {".xyz", ".top", ".buzz", ".club", ".work", ".click", ".loan", ".win"}


def analyse_urls(text):
    urls = URL_REGEX.findall(text)
    results = []
    for url in urls:
        domain = url.split("//")[-1].split("/")[0].split("?")[0].lower()
        flags = []
        if domain in SUSPICIOUS_DOMAINS:
            flags.append("Known URL shortener / suspicious domain")
        if any(domain.endswith(tld) for tld in SUSPICIOUS_TLDS):
            flags.append("Suspicious TLD")
        if re.search(r'\d{1,3}(\.\d{1,3}){3}', domain):
            flags.append("IP-based URL")
        if len(domain) > 40:
            flags.append("Unusually long domain")
        results.append({"url": url, "domain": domain, "flags": flags, "suspicious": len(flags) > 0})
    return results

# ──────────────────────────────────────────────
# Rule-based spam keywords (Hybrid Detection)
# ──────────────────────────────────────────────
SPAM_RULES = [
    (r'\bfree\s+money\b', "Contains 'free money'"),
    (r'\byou\s+have\s+won\b', "Contains 'you have won'"),
    (r'\bclaim\s+(your|now|prize)\b', "Contains 'claim your/now/prize'"),
    (r'\burgent\b', "Uses urgency language"),
    (r'\bact\s+now\b', "Contains 'act now'"),
    (r'\blimited\s+time\b', "Contains 'limited time'"),
    (r'\bclick\s+here\b', "Contains 'click here'"),
    (r'\bregistration\s+fee\b', "Contains 'registration fee'"),
    (r'\bbank\s+details\b', "Asks for bank details"),
    (r'\b(earn|make)\s+\$?\d', "Promises specific earnings"),
    (r'\bcongratulations\b', "Contains 'congratulations'"),
    (r'\b(bitcoin|btc|crypto)\b', "References cryptocurrency"),
    (r'\bno\s+investment\b', "Claims no investment needed"),
    (r'\b100%\s+free\b', "Claims 100% free"),
    (r'\bunsubscribe\b', "Contains 'unsubscribe'"),
]


def rule_based_check(text):
    lower = text.lower()
    triggered = []
    for pattern, desc in SPAM_RULES:
        if re.search(pattern, lower):
            triggered.append(desc)
    return triggered

# ──────────────────────────────────────────────
# Explainable AI — top contributing features
# ──────────────────────────────────────────────

def get_spam_words(text, top_n=10):
    """Return the top N words in the text that most strongly indicate spam."""
    if feature_names is None or model is None:
        return []

    vec = vectorizer.transform([text])
    # log-probability per feature for the 'spam' class
    classes = list(model.classes_)
    spam_idx = classes.index("spam") if "spam" in classes else 1
    log_probs = model.feature_log_prob_[spam_idx]

    # Features present in this message
    nonzero = vec.nonzero()[1]
    word_scores = []
    for idx in nonzero:
        word = feature_names[idx]
        # Only keep words that actually appear in the original text (case-insensitive)
        if word.lower() in text.lower():
            word_scores.append((word, float(log_probs[idx])))

    word_scores.sort(key=lambda x: x[1], reverse=True)
    return [w for w, _ in word_scores[:top_n]]


def build_explanation(prediction, confidence, spam_words, rule_hits, url_results):
    reasons = []
    if prediction == "spam":
        if confidence > 0.85:
            reasons.append(f"High spam probability ({confidence*100:.0f}%)")
        if spam_words:
            reasons.append(f"Contains spam-associated words: {', '.join(spam_words[:5])}")
        if rule_hits:
            for r in rule_hits[:3]:
                reasons.append(r)
        sus_urls = [u for u in url_results if u["suspicious"]]
        if sus_urls:
            reasons.append(f"Contains {len(sus_urls)} suspicious link(s)")
    else:
        reasons.append("No significant spam patterns detected")
        if confidence > 0.9:
            reasons.append(f"High confidence safe email ({confidence*100:.0f}%)")
    return reasons


# ──────────────────────────────────────────────
# Extract sender from email text
# ──────────────────────────────────────────────

def extract_sender(text):
    match = re.search(r'(?:from|sender)[:\s]+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    # fallback: any email address
    match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
    return match.group(0).lower() if match else ""


# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

# ─── Predict ──────────────────────────────────

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.json
        if not data or 'message' not in data:
            return jsonify({'error': 'No message provided'}), 400

        message = data['message']
        sender = data.get('sender', '') or extract_sender(message)

        if len(message) > 5000:
            return jsonify({'error': 'Message too long (max 5000 characters)'}), 400
        if not hasattr(model, 'predict'):
            return jsonify({'error': 'Model not loaded'}), 500

        # ML prediction
        transformed = vectorizer.transform([message])
        prediction = model.predict(transformed)[0]
        proba = model.predict_proba(transformed)[0]
        confidence = float(max(proba))

        # Hybrid: rule-based
        rule_hits = rule_based_check(message)

        # URL analysis
        url_results = analyse_urls(message)

        # Spam words (Explainable AI)
        spam_words = get_spam_words(message)

        # Hybrid override: if ML says ham but rules fired heavily, bump prediction
        if prediction == "ham" and len(rule_hits) >= 3:
            prediction = "spam"
            confidence = max(confidence, 0.72)

        # Build explanation
        explanation = build_explanation(prediction, confidence, spam_words, rule_hits, url_results)

        # Store in DB
        preview = message[:120] + ("..." if len(message) > 120 else "")
        conn = get_db()
        cursor = conn.execute(
            """INSERT INTO scan_history
               (message_preview, full_message, prediction, confidence, spam_words, urls_found, explanation, sender)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (preview, message, prediction, round(confidence, 4),
             json.dumps(spam_words), json.dumps(url_results),
             json.dumps(explanation), sender)
        )
        scan_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return jsonify({
            'id': scan_id,
            'prediction': prediction,
            'confidence': round(confidence, 3),
            'model_accuracy': round(model_accuracy, 4) if model_accuracy else None,
            'spam_words': spam_words,
            'urls': url_results,
            'rule_hits': rule_hits,
            'explanation': explanation,
            'sender': sender
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── File Upload ──────────────────────────────

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        allowed_ext = {'.txt', '.eml', '.msg'}
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in allowed_ext:
            return jsonify({'error': f'Unsupported file type. Use: {", ".join(allowed_ext)}'}), 400

        text = file.read().decode('utf-8', errors='ignore')
        if len(text.strip()) == 0:
            return jsonify({'error': 'File is empty'}), 400

        return jsonify({'text': text[:5000], 'filename': file.filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Feedback ─────────────────────────────────

@app.route('/feedback', methods=['POST'])
def feedback():
    try:
        data = request.json
        scan_id = data.get('id')
        fb = data.get('feedback')  # "spam" or "ham"
        if not scan_id or fb not in ("spam", "ham"):
            return jsonify({'error': 'Invalid feedback'}), 400

        conn = get_db()
        conn.execute("UPDATE scan_history SET feedback = ? WHERE id = ?", (fb, scan_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── History / Dashboard ─────────────────────

@app.route('/history', methods=['GET'])
def history():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, message_preview, prediction, confidence, sender, feedback, created_at FROM scan_history ORDER BY id DESC LIMIT 50"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM scan_history").fetchone()[0]
        spam_count = conn.execute("SELECT COUNT(*) FROM scan_history WHERE prediction='spam'").fetchone()[0]
        ham_count = conn.execute("SELECT COUNT(*) FROM scan_history WHERE prediction='ham'").fetchone()[0]
        conn.close()

        return jsonify({
            'history': [dict(r) for r in rows],
            'stats': {'total': total, 'spam': spam_count, 'ham': ham_count}
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Analytics ────────────────────────────────

@app.route('/analytics', methods=['GET'])
def analytics():
    try:
        conn = get_db()
        # Daily scan counts for last 30 days
        daily = conn.execute("""
            SELECT date(created_at) as day,
                   COUNT(*) as total,
                   SUM(CASE WHEN prediction='spam' THEN 1 ELSE 0 END) as spam,
                   SUM(CASE WHEN prediction='ham' THEN 1 ELSE 0 END) as ham
            FROM scan_history
            WHERE created_at >= datetime('now', '-30 days')
            GROUP BY date(created_at)
            ORDER BY day
        """).fetchall()
        conn.close()

        return jsonify({
            'daily': [dict(r) for r in daily]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Sender Reputation ───────────────────────

@app.route('/reputation', methods=['GET'])
def reputation():
    try:
        sender = request.args.get('sender', '').strip().lower()
        if not sender:
            return jsonify({'error': 'No sender provided'}), 400

        conn = get_db()
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN prediction='spam' THEN 1 ELSE 0 END) as spam,
                   SUM(CASE WHEN prediction='ham' THEN 1 ELSE 0 END) as ham
            FROM scan_history WHERE sender = ?
        """, (sender,)).fetchone()
        conn.close()

        total = row['total'] or 0
        spam = row['spam'] or 0
        spam_ratio = (spam / total * 100) if total > 0 else 0

        if total == 0:
            level = "unknown"
        elif spam_ratio > 60:
            level = "dangerous"
        elif spam_ratio > 30:
            level = "suspicious"
        else:
            level = "trusted"

        return jsonify({
            'sender': sender,
            'total_scans': total,
            'spam_count': spam,
            'ham_count': row['ham'] or 0,
            'spam_ratio': round(spam_ratio, 1),
            'level': level
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("Starting SpamShield AI on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)