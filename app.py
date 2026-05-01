"""Legacy / standalone Flask scaffold (NOT the production entry point).

This file predates :mod:`flask_app` and exposes a tiny login + admin-log
UI backed by Flask-Login and Flask-SQLAlchemy on its own ``users.db``
SQLite file.  The active production app is :mod:`flask_app`.

Kept in the tree because :file:`templates/admin_logs.html` is rendered
from here and the route is occasionally used for ad-hoc usage-log
inspection.  Run with ``python app.py`` (defaults to port 5000).
Delete only after confirming :mod:`flask_app` covers the admin needs.
"""
from flask import Flask, render_template, redirect, url_for, request, session, send_file
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import io, csv

app = Flask(__name__)
app.config['SECRET_KEY'] = 'replace-this-with-a-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True)
    password_hash = db.Column(db.String(128))

class UsageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    action = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    session_id = db.Column(db.String(128))
    extra = db.Column(db.Text)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user)
            log_action(user.username, 'login')
            session['login_time'] = datetime.utcnow()
            return redirect(url_for('home'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_action(current_user.username, 'logout')
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    log_action(current_user.username, 'access_home')
    return render_template('home.html', username=current_user.username)

@app.route('/admin/logs')
@login_required
def admin_logs():
    if current_user.username != 'admin':
        return "Access denied", 403
    logs = UsageLog.query.order_by(UsageLog.timestamp.desc()).all()
    return render_template('admin_logs.html', logs=logs)

@app.route('/admin/export_logs')
@login_required
def export_logs():
    if current_user.username != 'admin':
        return "Access denied", 403
    logs = UsageLog.query.order_by(UsageLog.timestamp.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['username', 'action', 'timestamp', 'session_id', 'extra'])
    for log in logs:
        writer.writerow([log.username, log.action, log.timestamp, log.session_id, log.extra])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='usage_logs.csv')

def log_action(username, action, extra=None):
    log = UsageLog(username=username, action=action, session_id=session.get('_id', ''), extra=str(extra) if extra else '')
    db.session.add(log)
    db.session.commit()

# --- Add create_note_page route to resolve BuildError ---
@app.route('/create', methods=['GET'])
@login_required
def create_note_page():
    # Render the actual create_note.html wizard template
    return render_template('create_note.html', username=current_user.username)

@app.before_request
def track_usage():
    if current_user.is_authenticated:
        log_action(current_user.username, f'visit_{request.endpoint}', {'path': request.path, 'method': request.method})

def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='user1').first():
            db.session.add(User(username='user1', password_hash=generate_password_hash('password1')))
        if not User.query.filter_by(username='user2').first():
            db.session.add(User(username='user2', password_hash=generate_password_hash('password2')))
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password_hash=generate_password_hash('adminpass')))
        db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
