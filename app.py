from datetime import datetime
from flask import Flask, redirect, render_template_string, request, url_for, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
import os
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-2026')

# Handle Render Postgres URL (postgres:// → postgresql://)
db_url = os.getenv('DATABASE_URL', 'sqlite:///jaithari.db')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB max

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db = SQLAlchemy(app)

# BEST for Render free tier: threading + simple-websocket
# Avoids gevent-websocket configuration errors completely
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25
)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    insta = db.Column(db.String(50), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    
    messages = db.relationship('Message', backref='user', lazy=True, cascade='all, delete-orphan')
    posts = db.relationship('Post', backref='user', lazy=True, cascade='all, delete-orphan')

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    is_deleted = db.Column(db.Boolean, default=False)
    
    def to_dict(self):
        # Use relationship instead of extra query (faster)
        user = self.user
        return {
            'id': self.id,
            'msg_id': self.id,
            'sender': user.name if user else 'Deleted',
            'msg': self.content,
            'time': self.created_at.strftime("%I:%M %p"),
            'insta': user.insta if user else '@user',
            'wa': user.phone if user else '9876543210',
            'is_admin': user.is_admin if user else False,
            'is_deleted': self.is_deleted
        }

class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    post_type = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    def to_dict(self):
        user = self.user
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'uploader': user.name if user else 'Anonymous',
            'date': self.created_at.strftime("%d %b %Y")
        }

class AppSettings(db.Model):
    __tablename__ = 'app_settings'
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def get_setting(key, default=None):
    setting = AppSettings.query.filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def set_setting(key, value):
    setting = AppSettings.query.filter_by(setting_key=key).first()
    if setting:
        setting.setting_value = value
    else:
        setting = AppSettings(setting_key=key, setting_value=value)
        db.session.add(setting)
    db.session.commit()

THEMES = {
    "rose": {
        "primary": "from-rose-600 to-pink-600", "accent": "rose-300", "dark": "#0b020c",
        "glass": "rgba(25, 8, 30, 0.75)", "border": "rgba(244, 63, 94, 0.25)", "text": "#ffe4e6",
        "gradient": "from-rose-300 via-pink-300 to-purple-300", "name": "Rose Love", "emoji": "🌹"
    },
    "lavender": {
        "primary": "from-purple-600 to-violet-600", "accent": "purple-300", "dark": "#0a0610",
        "glass": "rgba(20, 10, 35, 0.75)", "border": "rgba(168, 85, 247, 0.25)", "text": "#e9d5ff",
        "gradient": "from-purple-300 via-violet-300 to-pink-300", "name": "Lavender Dreams", "emoji": "💜"
    },
    "sunset": {
        "primary": "from-orange-600 to-red-600", "accent": "orange-300", "dark": "#0f0703",
        "glass": "rgba(30, 15, 8, 0.75)", "border": "rgba(251, 146, 60, 0.25)", "text": "#ffedd5",
        "gradient": "from-orange-300 via-red-300 to-pink-300", "name": "Sunset Bliss", "emoji": "🌅"
    },
    "ocean": {
        "primary": "from-cyan-600 to-blue-600", "accent": "cyan-300", "dark": "#020817",
        "glass": "rgba(8, 20, 40, 0.75)", "border": "rgba(34, 211, 238, 0.25)", "text": "#cffafe",
        "gradient": "from-cyan-300 via-blue-300 to-purple-300", "name": "Ocean Vibes", "emoji": "🌊"
    }
}

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'jaithari_admin_secure')
online_users = {}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Jaithari Love Hub</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        html, body { margin:0; padding:0; min-height:100vh; width:100%; background-color: {{ theme_dark }}; font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto; color:{{ theme_text }}; overflow-x:hidden; }
        .bg-insta { position:fixed; top:0; left:0; width:100vw; height:100vh; background:linear-gradient(135deg, rgba(30,5,35,0.85), rgba(70,10,50,0.82)), url('{{ bg_image }}') no-repeat center center fixed; background-size:cover; z-index:-3; }
        .floating-container { position:fixed; top:0; left:0; width:100%; height:100%; pointer-events:none; z-index:-2; overflow:hidden; }
        .heart-particle { position:absolute; bottom:-50px; font-size:20px; animation:floatUp 8s ease-in infinite; opacity:0; }
        @keyframes floatUp { 0% { transform:translateY(0) scale(0.5) rotate(0deg); opacity:0; } 10% { opacity:0.9; } 90% { opacity:0.9; } 100% { transform:translateY(-110vh) scale(1.3) rotate(720deg); opacity:0; } }
        .glass-card { background:{{ theme_glass }}; backdrop-filter:blur(30px); -webkit-backdrop-filter:blur(30px); border:1px solid {{ theme_border }}; box-shadow:0 20px 50px rgba(0,0,0,0.9); }
        .glass-input { background:rgba(12,3,15,0.95); border:1px solid {{ theme_border }}; color:{{ theme_text }}; font-size:13px; }
        .glass-input:focus { border-color:{{ theme_border_bright }}; outline:none; box-shadow:0 0 20px {{ theme_glow }}; }
        .btn-primary { transition:all 0.4s ease; cursor:pointer; font-weight:700; }
        .insta-bottom-nav { background:rgba(18,5,22,0.95); backdrop-filter:blur(25px); border-top:1px solid {{ theme_border }}; }
        .admin-badge { background:linear-gradient(135deg, #fbbf24, #f59e0b); color:#000; font-weight:700; padding:2px 8px; border-radius:4px; font-size:10px; margin-left:6px; }
        .user-chip { background:rgba(0,0,0,0.3); border:1px solid {{ theme_border }}; padding:4px 10px; border-radius:16px; font-size:11px; display:flex; align-items:center; gap:4px; }
        .online-indicator { width:6px; height:6px; border-radius:50%; background:#10b981; }
    </style>
</head>
<body class="flex flex-col items-center justify-start pb-24 pt-3 px-3 sm:px-5">
    <div class="bg-insta"></div>
    <div class="floating-container" id="particleContainer"></div>
    <script>
        const symbols = ['💖', '🌹', '✨', '💕', '❤️', '🌸', '💗', '💝'];
        const container = document.getElementById('particleContainer');
        function createParticle() {
            let span = document.createElement('div');
            span.className = 'heart-particle';
            span.innerText = symbols[Math.floor(Math.random() * symbols.length)];
            span.style.left = Math.random() * 100 + 'vw';
            const duration = 6 + Math.random() * 6;
            span.style.animationDuration = duration + 's';
            container.appendChild(span);
            setTimeout(() => span.remove(), duration * 1000);
        }
        for (let i = 0; i < 6; i++) { setTimeout(createParticle, i * 500); }
        setInterval(createParticle, 5000);
    </script>
    <div id="authModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/95 backdrop-blur-md p-4 {% if registered %}hidden{% endif %}">
        <div class="max-w-md w-full glass-card p-6 sm:p-8 rounded-3xl text-center space-y-5">
            <div class="text-4xl animate-bounce">💖🌹</div>
            <h2 class="text-xl font-black text-white uppercase tracking-wider">Jaithari Love Hub</h2>
            <form action="/register" method="POST" class="space-y-4 text-left">
                <input type="text" name="reg_name" required placeholder="Your Name" class="glass-input w-full rounded-xl px-4 py-2.5">
                <input type="tel" name="reg_phone" required placeholder="WhatsApp Number" class="glass-input w-full rounded-xl px-4 py-2.5">
                <input type="text" name="reg_insta" required placeholder="Instagram ID" class="glass-input w-full rounded-xl px-4 py-2.5">
                <div class="flex items-center space-x-3">
                    <input type="checkbox" id="age" required class="w-5 h-5 rounded">
                    <label for="age" class="text-sm">I am 18+ years old</label>
                </div>
                <button type="submit" class="btn-primary w-full bg-gradient-to-r {{ theme_primary }} py-3 rounded-xl text-white text-sm shadow-xl">Enter Hub ✨</button>
            </form>
        </div>
    </div>
    <div class="max-w-md w-full glass-card rounded-3xl p-4 sm:p-6 shadow-2xl mb-6">
        <div class="text-center mb-4 pb-4 border-b border-{{ theme_accent }}/20 flex justify-between items-center px-1">
            <h1 class="text-base sm:text-lg font-black text-transparent bg-clip-text bg-gradient-to-r {{ theme_gradient }}">✨ JAITHARI ✨</h1>
            <div class="flex space-x-2">
                <a href="/?tab=theme" class="bg-{{ theme_accent }}/20 text-{{ theme_accent }} px-3 py-1.5 rounded-xl text-xs font-bold border border-{{ theme_accent }}/30">🎨 Theme</a>
                <a href="/?tab=upload" class="bg-{{ theme_accent }}/20 text-{{ theme_accent }} px-3 py-1.5 rounded-xl text-xs font-bold border border-{{ theme_accent }}/30">+ Post</a>
            </div>
        </div>
        {% if msg %}
        <div class="mb-4 border border-{{ theme_accent }}/30 text-{{ theme_accent }} px-4 py-3 rounded-xl text-xs text-center font-medium bg-black/40">{{ msg }}</div>
        {% endif %}
        {% if tab == 'chat' %}
        <div class="tab-content space-y-4 text-xs">
            <div class="flex justify-between items-center px-1">
                <span class="font-bold text-{{ theme_accent }}">💬 Live Chat</span>
                <span class="text-[10px] text-emerald-400 bg-emerald-950/50 px-2.5 py-1 rounded-full border border-emerald-500/30">● Live</span>
            </div>
            <div class="glass-card p-3 rounded-2xl">
                <p class="text-[11px] font-bold text-{{ theme_accent }} mb-2">👥 Online (<span id="onlineCount">1</span>)</p>
                <div class="flex flex-wrap gap-2" id="onlineUsersList"><div class="user-chip"><div class="online-indicator"></div><span>{{ user_name }}</span></div></div>
            </div>
            <div class="bg-black/40 p-3 rounded-2xl border border-{{ theme_accent }}/10 h-72 overflow-y-auto space-y-3" id="chatContainer">
                <p class="text-center text-{{ theme_accent }}/50 text-[10px] py-4">Loading...</p>
            </div>
            <form id="chatForm" class="space-y-3">
                <div class="grid grid-cols-3 gap-2">
                    <input type="text" id="senderInput" value="{{ user_name }}" class="glass-input rounded-xl px-3 py-2.5 text-xs" readonly>
                    <input type="text" id="msgInput" placeholder="Say something..." class="col-span-2 glass-input rounded-xl px-3 py-2.5 text-xs">
                </div>
                <button type="submit" class="btn-primary w-full bg-gradient-to-r {{ theme_primary }} py-2.5 rounded-xl text-white text-xs shadow-lg">Send 💖</button>
            </form>
        </div>
        {% elif tab == 'theme' %}
        <div class="tab-content space-y-4 text-xs">
            <span class="font-bold text-{{ theme_accent }} px-1">🎨 Choose Your Vibe</span>
            <div class="grid grid-cols-2 gap-3">
                {% for theme_key, theme_data in themes.items() %}
                <form method="POST" action="/set_theme" class="contents">
                    <input type="hidden" name="theme" value="{{ theme_key }}">
                    <button type="submit" class="glass-card p-4 rounded-2xl text-center hover:scale-105 transition cursor-pointer">
                        <div class="text-2xl mb-2">{{ theme_data.emoji }}</div>
                        <div class="text-xs font-bold text-white">{{ theme_data.name }}</div>
                    </button>
                </form>
                {% endfor %}
            </div>
            <div class="glass-card p-4 rounded-2xl space-y-3">
                <span class="font-bold text-{{ theme_accent }}">🖼️ Upload Background</span>
                <form id="bgUploadForm" enctype="multipart/form-data" class="space-y-2">
                    <input type="file" id="bgFile" accept="image/*" class="glass-input w-full text-xs" required>
                    <button type="submit" class="btn-primary w-full bg-gradient-to-r {{ theme_primary }} py-2.5 rounded-xl text-white text-xs">Upload 🌟</button>
                </form>
            </div>
        </div>
        {% elif tab == 'upload' %}
        <div class="tab-content space-y-4 text-xs">
            <span class="font-bold text-{{ theme_accent }} px-1">📤 Share Your Vibe</span>
            <form method="POST" action="/submit_anything" class="glass-card p-4 rounded-2xl space-y-3">
                <select name="post_type" class="glass-input w-full rounded-xl p-3 text-xs">
                    <option value="confession">💌 Anonymous Secret</option>
                    <option value="study">📚 Study Notes</option>
                </select>
                <input type="text" name="title" required placeholder="Title" class="glass-input w-full rounded-xl p-3 text-xs">
                <textarea name="description" rows="3" required placeholder="Description..." class="glass-input w-full rounded-xl p-3 text-xs resize-none"></textarea>
                <button type="submit" class="btn-primary w-full bg-gradient-to-r {{ theme_primary }} py-2.5 rounded-xl text-white text-xs shadow-lg">Publish ✨</button>
            </form>
        </div>
        {% elif tab == 'confessions' %}
        <div class="tab-content space-y-4 text-xs">
            <span class="font-bold text-{{ theme_accent }}">💌 Anonymous Secrets</span>
            {% for post in confessions %}
            <div class="glass-card p-3 rounded-2xl space-y-2">
                <p class="text-{{ theme_accent }}/90 italic">"{{ post.title }}"</p>
                <div class="flex justify-between text-[10px] text-{{ theme_accent }}/50 pt-2 border-t border-{{ theme_accent }}/10"><span>🤫 Anonymous</span><span>{{ post.date }}</span></div>
            </div>
            {% endfor %}
        </div>
        {% elif tab == 'study' %}
        <div class="tab-content space-y-4 text-xs">
            <span class="font-bold text-{{ theme_accent }}">📚 Study Materials</span>
            {% for post in study_posts %}
            <div class="glass-card p-3 rounded-2xl space-y-2">
                <h3 class="font-bold text-white text-sm">{{ post.title }}</h3>
                <p class="text-{{ theme_accent }}/80 text-xs">{{ post.description }}</p>
                <p class="text-[10px] text-{{ theme_accent }}/60">By: {{ post.uploader }} • {{ post.date }}</p>
            </div>
            {% endfor %}
        </div>
        {% elif tab == 'admin' %}
        <div class="tab-content space-y-4 text-xs">
            <span class="font-bold text-amber-300 px-1">🛡️ Admin Panel</span>
            {% if not is_admin %}
            <form method="POST" action="/admin_login" class="glass-card p-4 rounded-2xl space-y-3">
                <input type="password" name="admin_pass" required placeholder="Password..." class="glass-input w-full rounded-xl p-3 text-xs">
                <button type="submit" class="btn-primary w-full bg-amber-600 py-2.5 rounded-xl text-white text-xs">Login 🔐</button>
            </form>
            {% else %}
            <div class="glass-card p-4 rounded-2xl space-y-3">
                <p class="text-emerald-400 font-bold text-center">✅ Admin Active</p>
                <button id="clearChatBtn" class="btn-primary w-full bg-red-600 py-2 rounded-lg text-white text-xs">🗑️ Clear Chat</button>
                <a href="/admin_logout" class="btn-primary w-full bg-gray-600 py-2.5 rounded-xl text-white text-xs text-center block">Logout</a>
            </div>
            {% endif %}
        </div>
        {% endif %}
    </div>
    <div class="insta-bottom-nav fixed bottom-0 left-0 w-full h-16 flex justify-around items-center px-2 z-40 text-xs">
        <a href="/?tab=chat" class="flex flex-col items-center {% if tab == 'chat' %}text-{{ theme_accent }} font-bold{% else %}text-{{ theme_accent }}/50{% endif %}"><span>🏠</span><span>Home</span></a>
        <a href="/?tab=study" class="flex flex-col items-center {% if tab == 'study' %}text-{{ theme_accent }} font-bold{% else %}text-{{ theme_accent }}/50{% endif %}"><span>📚</span><span>Notes</span></a>
        <a href="/?tab=upload" class="flex flex-col items-center"><span class="text-xl bg-gradient-to-r {{ theme_primary }} text-white rounded-full px-2 py-0.5">+</span><span>Post</span></a>
        <a href="/?tab=confessions" class="flex flex-col items-center {% if tab == 'confessions' %}text-{{ theme_accent }} font-bold{% else %}text-{{ theme_accent }}/50{% endif %}"><span>💌</span><span>Secrets</span></a>
        <a href="/?tab=admin" class="flex flex-col items-center {% if tab == 'admin' %}text-amber-400 font-bold{% else %}text-amber-400/50{% endif %}"><span>⚙️</span><span>Admin</span></a>
    </div>
    <script>
        const socket = io();
        const chatForm = document.getElementById('chatForm');
        const msgInput = document.getElementById('msgInput');
        const chatContainer = document.getElementById('chatContainer');
        const onlineUsersList = document.getElementById('onlineUsersList');
        const onlineCount = document.getElementById('onlineCount');
        const bgUploadForm = document.getElementById('bgUploadForm');
        const bgFile = document.getElementById('bgFile');
        const clearChatBtn = document.getElementById('clearChatBtn');
        let isAdmin = {{ 'true' if is_admin else 'false' }};

        socket.on('connect', () => { socket.emit('user_joined', { name: '{{ user_name }}' }); });
        socket.on('new_message', (data) => { addMessage(data); chatContainer.scrollTop = chatContainer.scrollHeight; });
        socket.on('update_online_users', (data) => {
            onlineCount.textContent = data.count;
            onlineUsersList.innerHTML = '';
            data.users.forEach(user => {
                const chip = document.createElement('div');
                chip.className = 'user-chip';
                chip.innerHTML = `<div class="online-indicator"></div><span>${user}</span>`;
                onlineUsersList.appendChild(chip);
            });
        });
        socket.on('message_deleted', (data) => { const el = document.getElementById(`msg-${data.msg_id}`); if (el) el.style.opacity = '0.5'; });
        socket.on('chat_cleared', () => { chatContainer.innerHTML = '<p class="text-center text-{{ theme_accent }}/50 text-[10px] py-4">Chat cleared</p>'; });

        if (chatForm) {
            chatForm.addEventListener('submit', (e) => {
                e.preventDefault();
                const msg = msgInput.value.trim();
                if (msg) {
                    socket.emit('send_message', { sender: '{{ user_name }}', msg: msg, insta: '{{ user_insta }}', wa: '{{ user_wa }}' });
                    msgInput.value = '';
                    msgInput.focus();
                }
            });
        }

        if(bgUploadForm) {
            bgUploadForm.addEventListener('submit', (e) => {
                e.preventDefault();
                const file = bgFile.files[0];
                if (file) {
                    const form = new FormData();
                    form.append('bg_file', file);
                    fetch('/upload_bg', { method: 'POST', body: form }).then(r => r.json()).then(d => { if (d.success) location.reload(); });
                }
            });
        }
        if (clearChatBtn) { clearChatBtn.addEventListener('click', () => { if (confirm('Clear all messages?')) socket.emit('clear_chat'); }); }

        function addMessage(data) {
            const el = document.createElement('div');
            el.id = `msg-${data.msg_id}`;
            el.className = 'glass-card p-3 rounded-xl space-y-2';
            const admin = data.is_admin ? '<span class="admin-badge">👑 ADMIN</span>' : '';
            const controls = isAdmin ? `<button class="text-[10px] bg-red-600/30 px-2 py-1 rounded" onclick="deleteMsg('${data.msg_id}')">Delete</button>` : '';
            el.innerHTML = `
                <div class="flex justify-between text-[11px] text-{{ theme_accent }}/80">
                    <span><strong>${data.sender}</strong> (${data.insta}) ${admin}</span>
                    <span class="text-[10px] text-{{ theme_accent }}/50">${data.time}</span>
                </div>
                <p class="text-{{ theme_accent }}/90">${data.msg}</p>
                <div class="flex gap-2">${controls}<a href="https://api.whatsapp.com/send?phone=${data.wa}" target="_blank" class="text-[10px] bg-emerald-600/30 text-emerald-300 px-2 py-1 rounded">Chat</a></div>
            `;
            chatContainer.appendChild(el);
        }
        function deleteMsg(id) { if (confirm('Delete?')) socket.emit('delete_message', { msg_id: id }); }
        
        // Only load messages when on chat tab
        if (chatContainer) {
            fetch('/get_messages').then(r => r.json()).then(d => {
                chatContainer.innerHTML = '';
                d.messages.forEach(m => addMessage(m));
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }).catch(() => {});
        }
    </script>
</body>
</html>
"""

@app.route("/")
def home():
    tab = request.args.get("tab", "chat")
    msg = request.args.get("msg", "")
    user_ip = request.remote_addr
    user = User.query.filter_by(ip_address=user_ip).first()
    registered = user is not None
    user_name = user.name if user else "Student"
    user_insta = user.insta if user else "@user"
    user_wa = user.phone if user else "9876543210"
    is_admin = user.is_admin if user else False
    
    theme = get_setting('theme', 'lavender')
    theme_config = THEMES.get(theme, THEMES["lavender"])
    bg_image = get_setting('bg_image', 'https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=1920&q=80')
    
    # Only load posts when needed (faster page loads)
    confessions = []
    study_posts = []
    if tab == 'confessions':
        confessions = Post.query.filter_by(post_type='confession').order_by(Post.created_at.desc()).limit(30).all()
    elif tab == 'study':
        study_posts = Post.query.filter_by(post_type='study').order_by(Post.created_at.desc()).limit(30).all()
    
    return render_template_string(HTML_TEMPLATE,
        tab=tab, registered=registered, user_name=user_name, user_insta=user_insta, user_wa=user_wa, is_admin=is_admin,
        confessions=confessions, study_posts=study_posts, bg_image=bg_image, msg=msg,
        theme_primary=theme_config["primary"], theme_accent=theme_config["accent"], theme_dark=theme_config["dark"],
        theme_text=theme_config["text"], theme_glass=theme_config["glass"], theme_border=theme_config.get("border", "rgba(168, 85, 247, 0.25)"),
        theme_border_bright="rgba(168, 85, 247, 0.9)", theme_glow="rgba(168, 85, 247, 0.4)", theme_gradient=theme_config["gradient"],
        themes=THEMES
    )

@app.route("/register", methods=["POST"])
def register():
    user_ip = request.remote_addr
    name = request.form.get("reg_name", "").strip()
    phone = request.form.get("reg_phone", "").strip()
    insta = request.form.get("reg_insta", "").strip()
    if name and phone and insta:
        user = User.query.filter_by(ip_address=user_ip).first()
        if not user:
            user = User(ip_address=user_ip, name=name, phone=phone, insta=insta)
            db.session.add(user)
        else:
            user.name, user.phone, user.insta = name, phone, insta
        db.session.commit()
    return redirect(url_for("home", tab="chat", msg="💖 Welcome!"))

@app.route("/upload_bg", methods=["POST"])
def upload_bg():
    if 'bg_file' not in request.files: return jsonify({"success": False})
    file = request.files['bg_file']
    try:
        img = Image.open(file)
        img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
        fn = f"bg_{request.remote_addr.replace('.', '_')}.webp"
        fp = os.path.join(app.config['UPLOAD_FOLDER'], fn)
        img.save(fp, 'WEBP', quality=85)
        set_setting('bg_image', f"/uploads/{fn}")
        return jsonify({"success": True})
    except: return jsonify({"success": False})

@app.route("/uploads/<filename>")
def download_file(filename):
    try: return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    except: return "Not found", 404

@app.route("/set_theme", methods=["POST"])
def set_theme():
    theme = request.form.get("theme", "rose").strip()
    if theme in THEMES: set_setting('theme', theme)
    return redirect(url_for("home", tab="theme", msg=f"✨ Theme updated!"))

@app.route("/submit_anything", methods=["POST"])
def submit_anything():
    user_ip = request.remote_addr
    user = User.query.filter_by(ip_address=user_ip).first()
    if not user: return redirect(url_for("home", tab="upload", msg="❌ Register first!"))
    post = Post(user_id=user.id, post_type=request.form.get("post_type"), title=request.form.get("title", "").strip(), description=request.form.get("description", "").strip())
    db.session.add(post)
    db.session.commit()
    return redirect(url_for("home", tab="study" if post.post_type=='study' else 'confessions', msg="✨ Published!"))

@app.route("/admin_login", methods=["POST"])
def admin_login():
    user_ip = request.remote_addr
    if request.form.get("admin_pass", "").strip() == ADMIN_PASSWORD:
        user = User.query.filter_by(ip_address=user_ip).first()
        if user: user.is_admin = True; db.session.commit()
        return redirect(url_for("home", tab="admin", msg="🔐 Admin access granted!"))
    return redirect(url_for("home", tab="admin", msg="❌ Wrong password!"))

@app.route("/admin_logout")
def admin_logout():
    user = User.query.filter_by(ip_address=request.remote_addr).first()
    if user: user.is_admin = False; db.session.commit()
    return redirect(url_for("home", msg="👋 Logged out!"))

@app.route("/get_messages")
def get_messages():
    from sqlalchemy.orm import joinedload
    msgs = (Message.query
            .options(joinedload(Message.user))
            .filter_by(is_deleted=False)
            .order_by(Message.created_at.desc())
            .limit(40)
            .all())
    return jsonify({"messages": [m.to_dict() for m in reversed(msgs)]})

@socketio.on('user_joined')
def user_joined(data):
    try:
        ip = request.remote_addr
        user = User.query.filter_by(ip_address=ip).first()
        if user:
            online_users[ip] = user.name
        emit('update_online_users', {'count': len(online_users), 'users': list(online_users.values())}, broadcast=True)
    except Exception:
        pass

@socketio.on('send_message')
def handle_message(data):
    try:
        user = User.query.filter_by(ip_address=request.remote_addr).first()
        if not user:
            return
        content = (data.get('msg') or '').strip()[:500]  # limit length
        if not content:
            return
        msg = Message(user_id=user.id, content=content)
        db.session.add(msg)
        db.session.commit()
        emit('new_message', msg.to_dict(), broadcast=True)
    except Exception:
        db.session.rollback()

@socketio.on('delete_message')
def delete_message(data):
    try:
        user = User.query.filter_by(ip_address=request.remote_addr).first()
        if not user or not user.is_admin:
            return
        msg = Message.query.get(data.get('msg_id'))
        if msg:
            msg.is_deleted = True
            db.session.commit()
            emit('message_deleted', {'msg_id': msg.id}, broadcast=True)
    except Exception:
        db.session.rollback()

@socketio.on('clear_chat')
def clear_chat():
    try:
        user = User.query.filter_by(ip_address=request.remote_addr).first()
        if not user or not user.is_admin:
            return
        Message.query.update({Message.is_deleted: True})
        db.session.commit()
        emit('chat_cleared', broadcast=True)
    except Exception:
        db.session.rollback()

@socketio.on('disconnect')
def user_disconnected():
    try:
        ip = request.remote_addr
        if ip in online_users:
            del online_users[ip]
        emit('update_online_users', {'count': len(online_users), 'users': list(online_users.values())}, broadcast=True)
    except Exception:
        pass

with app.app_context(): db.create_all()

if __name__ == "__main__":
    socketio.run(app, debug=False, port=5000, host='0.0.0.0')
