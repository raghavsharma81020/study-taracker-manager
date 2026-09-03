from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory

app = Flask(__name__)
app.secret_key = "super-secret-study-key-change-anytime"

# --- Main Dashboard Route ---
@app.route('/')
def index():
    return render_template('index.html')

# --- Logout / Reset Route ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- PWA Offline Support Routes ---
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def serve_sw():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache'
    return response

if __name__ == '__main__':
    app.run(debug=True)