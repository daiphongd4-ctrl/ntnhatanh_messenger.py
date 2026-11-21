import http.server
import socketserver
import json
import os
import uuid
import hashlib
import base64
import mimetypes
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# Cấu hình cổng & thư mục lưu trữ (tương thích máy cá nhân và host cloud)
PORT = int(os.environ.get("PORT", 8000))
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

DATA_FILE = os.path.join(DATA_DIR, "ntnhatanh_data.json")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_MEDIA_MB = 30
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


ADMIN_PASS_HASH = _hash(ADMIN_PASS)


# --- QUẢN LÝ DỮ LIỆU ---
def load_data():
    default_data = {"users": {}, "messages": [], "themes": {}, "activity_logs": []}
    if not os.path.exists(DATA_FILE):
        return default_data
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("users", {})
            data.setdefault("messages", [])
            data.setdefault("themes", {})
            data.setdefault("activity_logs", [])
            return data
    except Exception:
        return default_data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_activity(data, actor, action, detail=""):
    data.setdefault("activity_logs", []).append({
        "id": uuid.uuid4().hex[:8],
        "time": datetime.now().strftime("%d/%m %H:%M:%S"),
        "actor": actor or "Khách",
        "action": action,
        "detail": detail
    })
    if len(data["activity_logs"]) > 500:
        data["activity_logs"] = data["activity_logs"][-500:]


def generate_unique_code(existing_codes):
    while True:
        code = uuid.uuid4().hex[:6].upper()
        if code not in existing_codes:
            return code


def get_avatar_color(username):
    colors = ["#0084ff", "#a033ff", "#ff5ca1", "#00a400", "#fa3e3e", "#f5a623", "#00bfa5"]
    return colors[sum(ord(c) for c in username) % len(colors)]


def verify_user_password(user_info, input_password):
    if not user_info:
        return False
    if "password" in user_info:
        return user_info["password"] == input_password
    if "password_hash" in user_info:
        return user_info["password_hash"] == _hash(input_password)
    return False


# --- SERVICE WORKER & MANIFEST ---
SERVICE_WORKER_JS = """
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow('/ntnhatanh');
    })
  );
});
"""

MANIFEST_JSON = json.dumps({
    "name": "ntnhatanh Messenger",
    "short_name": "ntnhatanh",
    "start_url": "/ntnhatanh",
    "display": "standalone",
    "background_color": "#ffffff",
    "theme_color": "#0084ff",
    "icons": []
}, ensure_ascii=False)


# --- GIAO DIỆN CHAT CHÍNH ---
HTML_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#0084ff">
    <link rel="manifest" href="/manifest.json">
    <title>ntnhatanh • Messenger</title>
    <style>
        :root {
            --bg-body: #ffffff;
            --bg-secondary: #f0f2f5;
            --bg-hover: #f2f2f2;
            --bg-active: #ebf5ff;
            --text-primary: #050505;
            --text-secondary: #65676b;
            --border-color: #e4e6eb;
            --accent-color: #0084ff;
            --bubble-sent: linear-gradient(135deg, #0084ff 0%, #006aff 100%);
            --bubble-received: #f0f0f0;
            --quick-emoji: "👍";
        }
        [data-theme="gradient"] {
            --accent-color: #a033ff;
            --bubble-sent: linear-gradient(180deg, #ff5ca1 0%, #a033ff 50%, #0084ff 100%);
            --quick-emoji: "❤️";
        }
        [data-theme="love"] {
            --accent-color: #ff2e63;
            --bubble-sent: linear-gradient(135deg, #ff5ca1 0%, #ff2e63 100%);
            --quick-emoji: "😍";
        }
        [data-theme="emerald"] {
            --accent-color: #00a884;
            --bubble-sent: linear-gradient(135deg, #00c896 0%, #008f6b 100%);
            --quick-emoji: "🍀";
        }
        [data-theme="sunset"] {
            --accent-color: #fa3e3e;
            --bubble-sent: linear-gradient(135deg, #ff9a44 0%, #fc6076 100%);
            --quick-emoji: "🔥";
        }
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; }
        body { background: var(--bg-body); color: var(--text-primary); height: 100vh; overflow: hidden; }
        .auth-wrapper { height: 100vh; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 20px; background: #fff; }
        .mess-logo {
            width: 72px; height: 72px; margin-bottom: 18px;
            background: radial-gradient(circle at 30% 107%, #0099ff 0%, #0099ff 5%, #a033ff 60%, #ff5280 90%, #ff7061 100%);
            border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 24px rgba(0,132,255,0.25);
        }
        .mess-logo svg { width: 40px; height: 40px; fill: white; }
        .brand-tag { font-size: 13px; font-weight: 800; color: #0084ff; letter-spacing: 1.2px; margin-bottom: 6px; text-transform: uppercase; }
        .auth-title { font-size: 28px; font-weight: 700; margin-bottom: 8px; text-align: center; }
        .auth-subtitle { font-size: 15px; color: var(--text-secondary); margin-bottom: 24px; text-align: center; max-width: 360px; }
        .auth-form { width: 100%; max-width: 340px; display: flex; flex-direction: column; gap: 12px; }
        .auth-form input { width: 100%; padding: 13px 16px; border: 1px solid #dddfe2; border-radius: 12px; font-size: 15px; outline: none; }
        .auth-form input:focus { border-color: #0084ff; }
        .auth-btn { width: 100%; padding: 13px; background: #0084ff; color: white; border: none; border-radius: 24px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 4px; }
        .auth-switch { margin-top: 18px; font-size: 14px; color: #0084ff; cursor: pointer; font-weight: 500; }
        .auth-error { color: #fa3e3e; font-size: 13px; text-align: center; min-height: 18px; }
        .app-layout { display: flex; height: 100vh; width: 100vw; }
        .hidden { display: none !important; }
        .left-sidebar { width: 360px; border-right: 1px solid var(--border-color); display: flex; flex-direction: column; background: #fff; flex-shrink: 0; }
        .sidebar-header { padding: 14px 16px 8px; display: flex; justify-content: space-between; align-items: center; }
        .sidebar-header h1 { font-size: 22px; font-weight: 800; }
        .brand-pill { font-size: 11px; background: #e7f3ff; color: #0084ff; padding: 3px 8px; border-radius: 999px; font-weight: 700; margin-left: 6px; }
        .header-actions { display: flex; gap: 6px; align-items: center; }
        .icon-btn { width: 34px; height: 34px; border-radius: 50%; background: var(--bg-secondary); border: none; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 15px; text-decoration: none; color: inherit; }
        .icon-btn:hover { background: #e4e6eb; }
        .notify-bar { margin: 4px 16px 6px; padding: 8px 12px; background: #fff8e6; border: 1px solid #ffe58f; border-radius: 10px; display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #614700; gap: 8px; }
        .notify-btn { background: #fa8c16; color: #fff; border: none; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; cursor: pointer; flex-shrink: 0; }
        .my-code-bar { margin: 6px 16px 8px; padding: 10px 14px; background: #f0f7ff; border-radius: 12px; display: flex; justify-content: space-between; align-items: center; border: 1px solid #d0e5ff; }
        .my-code-info { font-size: 13px; }
        .my-code-value { font-weight: 800; color: #0084ff; font-size: 15px; letter-spacing: 1px; margin-left: 4px; }
        .copy-btn { background: #0084ff; color: white; border: none; padding: 5px 10px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; }
        .search-connect { padding: 4px 16px 10px; display: flex; gap: 8px; }
        .search-input-wrap { flex: 1; background: var(--bg-secondary); border-radius: 20px; padding: 0 12px; display: flex; align-items: center; }
        .search-input-wrap input { width: 100%; border: none; background: transparent; padding: 9px 6px; font-size: 14px; outline: none; text-transform: uppercase; }
        .connect-btn { background: var(--bg-secondary); color: var(--text-primary); border: none; padding: 0 14px; border-radius: 20px; font-weight: 600; font-size: 13px; cursor: pointer; }
        .chat-list { flex: 1; overflow-y: auto; padding: 0 8px; }
        .chat-item { display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: 12px; cursor: pointer; }
        .chat-item:hover { background: var(--bg-hover); }
        .chat-item.active { background: var(--bg-active); }
        .avatar-wrap { position: relative; flex-shrink: 0; }
        .avatar { width: 50px; height: 50px; border-radius: 50%; color: white; font-weight: 700; font-size: 19px; display: flex; align-items: center; justify-content: center; text-transform: uppercase; }
        .online-dot { width: 13px; height: 13px; background: #31a24c; border: 2px solid #fff; border-radius: 50%; position: absolute; bottom: 1px; right: 1px; }
        .chat-item-info { flex: 1; min-width: 0; }
        .chat-item-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 2px; }
        .chat-item-name { font-weight: 600; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .chat-item-code { font-size: 11px; color: var(--text-secondary); background: #f0f2f5; padding: 1px 6px; border-radius: 6px; }
        .chat-item-preview { font-size: 13px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .chat-item.unread .chat-item-name, .chat-item.unread .chat-item-preview { font-weight: 700; color: #050505; }
        .unread-dot { width: 10px; height: 10px; background: #0084ff; border-radius: 50%; flex-shrink: 0; }
        .chat-center { flex: 1; display: flex; flex-direction: column; background: #fff; min-width: 0; }
        .chat-header { height: 62px; padding: 0 16px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; }
        .chat-header-left { display: flex; align-items: center; gap: 12px; }
        .header-avatar { width: 40px; height: 40px; font-size: 16px; }
        .header-user-meta h2 { font-size: 16px; font-weight: 700; }
        .header-user-meta span { font-size: 12px; color: var(--text-secondary); }
        .chat-header-right { display: flex; gap: 6px; }
        .header-action-icon { width: 36px; height: 36px; border-radius: 50%; border: none; background: transparent; color: var(--accent-color); font-size: 18px; cursor: pointer; }
        .messages-area { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 4px; }
        .empty-state { margin: auto; text-align: center; color: var(--text-secondary); max-width: 360px; line-height: 1.5; }
        .empty-state h3 { color: var(--text-primary); margin-bottom: 8px; font-size: 18px; }
        .msg-row { display: flex; align-items: flex-end; gap: 8px; position: relative; }
        .msg-row.sent { justify-content: flex-end; }
        .msg-row.received { justify-content: flex-start; }
        .msg-row.group-gap { margin-top: 10px; }
        .msg-mini-avatar { width: 28px; height: 28px; border-radius: 50%; color: white; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .msg-mini-avatar.invisible { visibility: hidden; }
        .msg-bubble-wrap { position: relative; max-width: 72%; display: flex; align-items: center; gap: 6px; }
        .msg-row.sent .msg-bubble-wrap { flex-direction: row-reverse; }
        .msg-bubble { padding: 9px 14px; font-size: 15px; line-height: 1.4; word-wrap: break-word; position: relative; border-radius: 18px; }
        .msg-row.sent .msg-bubble { background: var(--bubble-sent); color: #fff; border-bottom-right-radius: 4px; }
        .msg-row.received .msg-bubble { background: var(--bubble-received); color: #050505; border-bottom-left-radius: 4px; }
        .msg-bubble.emoji-only { background: transparent !important; font-size: 36px; padding: 2px 4px; }
        .msg-bubble.media-only { background: transparent !important; padding: 0; }
        .msg-bubble.has-media-and-text { padding: 6px; }
        .msg-bubble.has-media-and-text .msg-caption { padding: 6px 8px 3px; }
        .chat-media-img { max-width: 260px; max-height: 320px; border-radius: 16px; display: block; cursor: zoom-in; object-fit: cover; border: 1px solid rgba(0,0,0,0.08); }
        .chat-media-video { max-width: 280px; max-height: 340px; border-radius: 16px; display: block; background: #000; border: 1px solid rgba(0,0,0,0.08); }
        .msg-reaction { position: absolute; bottom: -10px; right: 6px; background: #fff; border: 1px solid var(--border-color); border-radius: 12px; padding: 1px 5px; font-size: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.12); z-index: 2; }
        .msg-row.received .msg-reaction { right: auto; left: 6px; }
        .react-trigger { opacity: 0; transition: 0.15s; cursor: pointer; font-size: 15px; color: #8a8d91; padding: 4px; position: relative; }
        .msg-row:hover .react-trigger { opacity: 1; }
        .react-picker { display: none; position: absolute; bottom: 26px; left: 50%; transform: translateX(-50%); background: #fff; border-radius: 24px; padding: 4px 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.18); border: 1px solid var(--border-color); gap: 6px; z-index: 10; white-space: nowrap; }
        .react-trigger:hover .react-picker { display: flex; }
        .react-picker span { font-size: 18px; cursor: pointer; }
        .media-preview-bar { padding: 10px 16px; border-top: 1px solid var(--border-color); background: #f8f9fa; display: flex; align-items: center; gap: 12px; }
        .preview-thumb-box { position: relative; display: inline-block; }
        .preview-thumb-box img, .preview-thumb-box video { height: 68px; max-width: 120px; border-radius: 10px; object-fit: cover; border: 1px solid #ddd; display: block; }
        .remove-media-btn { position: absolute; top: -6px; right: -6px; width: 22px; height: 22px; border-radius: 50%; background: #050505; color: #fff; border: none; font-size: 12px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
        .preview-meta { font-size: 13px; color: var(--text-secondary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .chat-footer { padding: 10px 16px; display: flex; align-items: center; gap: 8px; background: #fff; }
        .media-upload-btn { width: 36px; height: 36px; border-radius: 50%; border: none; background: transparent; color: var(--accent-color); font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .input-pill { flex: 1; background: var(--bg-secondary); border-radius: 22px; padding: 4px 14px; display: flex; align-items: center; gap: 8px; }
        .input-pill input { flex: 1; border: none; background: transparent; padding: 8px 4px; font-size: 15px; outline: none; }
        .emoji-btn { background: none; border: none; font-size: 20px; cursor: pointer; }
        .send-quick-btn { background: none; border: none; color: var(--accent-color); font-size: 22px; cursor: pointer; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .lightbox { position: fixed; inset: 0; background: rgba(0,0,0,0.88); z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 20px; cursor: zoom-out; }
        .lightbox img { max-width: 95vw; max-height: 92vh; border-radius: 8px; }
        .right-sidebar { width: 290px; border-left: 1px solid var(--border-color); padding: 24px 16px; display: flex; flex-direction: column; align-items: center; background: #fff; flex-shrink: 0; }
        .right-avatar { width: 76px; height: 76px; font-size: 30px; margin-bottom: 10px; }
        .right-name { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
        .right-status { font-size: 13px; color: var(--text-secondary); margin-bottom: 18px; }
        .info-card { width: 100%; background: var(--bg-secondary); border-radius: 12px; padding: 14px; margin-bottom: 12px; }
        .info-card-title { font-size: 12px; font-weight: 700; color: var(--text-secondary); margin-bottom: 10px; text-transform: uppercase; }
        .theme-dots { display: flex; gap: 10px; justify-content: space-between; }
        .theme-dot { width: 32px; height: 32px; border-radius: 50%; cursor: pointer; border: 2px solid transparent; }
        .mobile-back-btn { display: none; }
        @media (max-width: 980px) { .right-sidebar { display: none; } }
        @media (max-width: 680px) {
            .left-sidebar { width: 100%; }
            .left-sidebar.mobile-hidden { display: none; }
            .chat-center.mobile-hidden { display: none; }
            .mobile-back-btn { display: inline-flex !important; }
        }
    </style>
</head>
<body>
<div id="authScreen" class="auth-wrapper">
    <div class="mess-logo">
        <svg viewBox="0 0 36 36"><path d="M18 2C9.163 2 2 8.636 2 16.82c0 4.66 2.323 8.817 5.955 11.53V34l5.442-2.987c1.453.402 2.996.627 4.603.627 8.837 0 16-6.636 16-14.82S26.837 2 18 2zm1.592 19.95l-4.075-4.346-7.952 4.346 8.747-9.286 4.175 4.346 7.852-4.346-8.747 9.286z"/></svg>
    </div>
    <div class="brand-tag">ntnhatanh</div>
    <h1 class="auth-title" id="authHeading">Đăng nhập Messenger</h1>
    <p class="auth-subtitle" id="authSub">Dùng mã riêng 6 ký tự để nhắn tin, gửi ảnh & video với bạn bè</p>
    <div class="auth-form">
        <input type="text" id="usernameInput" placeholder="Tên người dùng" autocomplete="off">
        <input type="password" id="passwordInput" placeholder="Mật khẩu" onkeypress="if(event.key==='Enter') submitAuth()">
        <div class="auth-error" id="authError"></div>
        <button class="auth-btn" id="authBtn" onclick="submitAuth()">Đăng nhập</button>
    </div>
    <div class="auth-switch" id="authSwitch" onclick="toggleAuthMode()">Chưa có tài khoản? Tạo tài khoản mới</div>
</div>

<div id="mainApp" class="app-layout hidden">
    <div class="left-sidebar" id="leftSidebar">
        <div class="sidebar-header">
            <div style="display:flex;align-items:center;">
                <h1>Đoạn chat</h1>
                <span class="brand-pill">ntnhatanh</span>
            </div>
            <div class="header-actions">
                <a class="icon-btn" href="/admin" title="Trang quản trị Admin">🛡️</a>
                <button class="icon-btn" title="Đăng xuất" onclick="logout()">🚪</button>
            </div>
        </div>
        <div class="notify-bar" id="notifyBar">
            <span id="notifyText">Bật thông báo để nhận tin nhắn trên màn hình</span>
            <button class="notify-btn" id="enableNotifyBtn" onclick="requestNotificationPermission()">Bật ngay</button>
        </div>
        <div class="my-code-bar">
            <div class="my-code-info">
                <span id="myUsernameDisplay" style="font-weight:700;">User</span> • Mã:
                <span class="my-code-value" id="myCodeDisplay">------</span>
            </div>
            <button class="copy-btn" id="copyCodeBtn" onclick="copyMyCode()">Sao chép</button>
        </div>
        <div class="search-connect">
            <div class="search-input-wrap">
                <span>🔍</span>
                <input type="text" id="partnerCodeInput" placeholder="Nhập mã 6 ký tự..." maxlength="6" onkeypress="if(event.key==='Enter') connectByCode()">
            </div>
            <button class="connect-btn" onclick="connectByCode()">Nhắn</button>
        </div>
        <div class="chat-list" id="chatList"></div>
    </div>

    <div class="chat-center mobile-hidden" id="chatCenter">
        <div class="chat-header" id="chatHeader">
            <div class="chat-header-left">
                <button class="icon-btn mobile-back-btn" onclick="backToList()">←</button>
                <div class="avatar-wrap hidden" id="headerAvatarWrap">
                    <div class="avatar header-avatar" id="headerAvatar">U</div>
                    <div class="online-dot"></div>
                </div>
                <div class="header-user-meta">
                    <h2 id="headerName">ntnhatanh Messenger</h2>
                    <span id="headerSub">Nhập mã bạn bè ở cột bên trái để bắt đầu</span>
                </div>
            </div>
            <div class="chat-header-right hidden" id="headerIcons">
                <button class="header-action-icon" title="Đổi màu chủ đề" onclick="cycleTheme()">🎨</button>
            </div>
        </div>
        <div class="messages-area" id="messagesArea">
            <div class="empty-state">
                <h3>ntnhatanh Messenger</h3>
                <p>Sao chép <b>Mã riêng</b> của bạn ở góc trên bên trái gửi cho bạn bè, hoặc nhập mã của họ để nhắn tin, gửi hình ảnh và video.</p>
            </div>
        </div>
        <div class="media-preview-bar hidden" id="mediaPreviewBar">
            <div class="preview-thumb-box" id="previewThumbBox"></div>
            <div class="preview-meta" id="previewFileName">Đang chuẩn bị tệp...</div>
        </div>
        <div class="chat-footer hidden" id="chatFooter">
            <input type="file" id="mediaFileInput" accept="image/*,video/*" class="hidden" onchange="handleMediaSelected(event)">
            <button class="media-upload-btn" title="Gửi hình ảnh hoặc video từ thiết bị" onclick="document.getElementById('mediaFileInput').click()">🖼️</button>
            <div class="input-pill">
                <input type="text" id="msgInput" placeholder="Aa" oninput="updateSendIcon()" onkeypress="if(event.key==='Enter') sendMessage()">
                <button class="emoji-btn" onclick="insertEmoji('😊')">😊</button>
            </div>
            <button class="send-quick-btn" id="sendOrLikeBtn" onclick="handleSendOrLike()">👍</button>
        </div>
    </div>

    <div class="right-sidebar hidden" id="rightSidebar">
        <div class="avatar right-avatar" id="rightAvatar">U</div>
        <div class="right-name" id="rightName">Người dùng</div>
        <div class="right-status" id="rightCodeText">Mã: ------</div>
        <div class="info-card">
            <div class="info-card-title">Chủ đề đoạn chat</div>
            <div class="theme-dots">
                <div class="theme-dot" style="background: #0084ff;" onclick="setTheme('default')"></div>
                <div class="theme-dot" style="background: linear-gradient(135deg, #ff5ca1, #a033ff);" onclick="setTheme('gradient')"></div>
                <div class="theme-dot" style="background: #ff2e63;" onclick="setTheme('love')"></div>
                <div class="theme-dot" style="background: #00a884;" onclick="setTheme('emerald')"></div>
                <div class="theme-dot" style="background: linear-gradient(135deg, #ff9a44, #fc6076);" onclick="setTheme('sunset')"></div>
            </div>
        </div>
    </div>
</div>

<div id="lightboxModal" class="lightbox hidden" onclick="this.classList.add('hidden')">
    <img id="lightboxImg" src="" alt="Preview">
</div>

<script>
    let isLogin = true;
    let currentUser = JSON.parse(localStorage.getItem("ntnhatanh_user") || "null");
    let activePartner = null;
    let poller = null;
    let currentTheme = "default";
    let pendingMedia = null;
    let lastRenderedSignature = "";
    let seenMessageIds = new Set(JSON.parse(localStorage.getItem("ntnhatanh_seen_msgs") || "[]"));
    const themeOrder = ["default", "gradient", "love", "emerald", "sunset"];
    const themeQuickEmoji = { default: "👍", gradient: "❤️", love: "😍", emerald: "🍀", sunset: "🔥" };

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    updateNotificationStatusUI();
    if (currentUser) enterMessenger();

    function updateNotificationStatusUI() {
        if (!("Notification" in window)) {
            document.getElementById("notifyText").innerText = "Trình duyệt không hỗ trợ thông báo màn hình";
            document.getElementById("enableNotifyBtn").classList.add("hidden");
            return;
        }
        if (Notification.permission === "granted") {
            document.getElementById("notifyText").innerText = "Thông báo màn hình thiết bị đã bật ✓";
            document.getElementById("enableNotifyBtn").classList.add("hidden");
        } else if (Notification.permission === "denied") {
            document.getElementById("notifyText").innerText = "Thông báo đang bị chặn trong cài đặt trình duyệt";
            document.getElementById("enableNotifyBtn").classList.add("hidden");
        }
    }

    async function requestNotificationPermission() {
        if (!("Notification" in window)) return;
        const perm = await Notification.requestPermission();
        updateNotificationStatusUI();
        if (perm === "granted") {
            triggerDeviceNotification("ntnhatanh Messenger", "Thông báo màn hình đã sẵn sàng!");
        }
    }

    async function triggerDeviceNotification(title, body) {
        if (!("Notification" in window) || Notification.permission !== "granted") return;
        try {
            if ("serviceWorker" in navigator) {
                const reg = await navigator.serviceWorker.ready;
                await reg.showNotification(title, { body, vibrate: [200, 100, 200], tag: "ntnhatanh-chat" });
                return;
            }
        } catch (e) {}
        new Notification(title, { body });
    }

    function toggleAuthMode() {
        isLogin = !isLogin;
        document.getElementById("authHeading").innerText = isLogin ? "Đăng nhập Messenger" : "Tạo tài khoản ntnhatanh";
        document.getElementById("authSub").innerText = isLogin ? "Dùng mã riêng 6 ký tự để nhắn tin, gửi ảnh & video" : "Mỗi tài khoản sẽ có một mã riêng 6 ký tự";
        document.getElementById("authBtn").innerText = isLogin ? "Đăng nhập" : "Đăng ký";
        document.getElementById("authSwitch").innerText = isLogin ? "Chưa có tài khoản? Tạo tài khoản mới" : "Đã có tài khoản? Đăng nhập";
        document.getElementById("authError").innerText = "";
    }

    async function submitAuth() {
        const username = document.getElementById("usernameInput").value.trim();
        const password = document.getElementById("passwordInput").value.trim();
        const err = document.getElementById("authError");
        if (!username || !password) {
            err.innerText = "Vui lòng nhập đầy đủ tên và mật khẩu.";
            return;
        }
        const res = await fetch(isLogin ? "/api/login" : "/api/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (data.success) {
            currentUser = data.user;
            localStorage.setItem("ntnhatanh_user", JSON.stringify(currentUser));
            enterMessenger();
        } else {
            err.innerText = data.message;
        }
    }

    function logout() {
        localStorage.removeItem("ntnhatanh_user");
        currentUser = null;
        activePartner = null;
        clearInterval(poller);
        document.getElementById("mainApp").classList.add("hidden");
        document.getElementById("authScreen").classList.remove("hidden");
    }

    function enterMessenger() {
        document.getElementById("authScreen").classList.add("hidden");
        document.getElementById("mainApp").classList.remove("hidden");
        document.getElementById("myUsernameDisplay").innerText = currentUser.username;
        document.getElementById("myCodeDisplay").innerText = currentUser.code;
        refreshSidebar(false);
        poller = setInterval(() => {
            refreshSidebar(true);
            if (activePartner) refreshMessages();
        }, 1800);
    }

    function copyMyCode() {
        navigator.clipboard?.writeText(currentUser.code);
        const btn = document.getElementById("copyCodeBtn");
        btn.innerText = "Đã chép ✓";
        setTimeout(() => btn.innerText = "Sao chép", 1500);
    }

    async function connectByCode() {
        const input = document.getElementById("partnerCodeInput");
        const code = input.value.trim().toUpperCase();
        if (!code) return;
        if (code === currentUser.code) {
            alert("Đây là mã của chính bạn!");
            return;
        }
        const res = await fetch(`/api/find_user?code=${code}`);
        const data = await res.json();
        if (data.success) {
            input.value = "";
            openConversation(data.user);
        } else {
            alert(data.message);
        }
    }

    function openConversation(partner) {
        activePartner = partner;
        lastRenderedSignature = "";
        clearPendingMedia();
        document.getElementById("leftSidebar").classList.add("mobile-hidden");
        document.getElementById("chatCenter").classList.remove("mobile-hidden");
        document.getElementById("headerAvatarWrap").classList.remove("hidden");
        const hAv = document.getElementById("headerAvatar");
        hAv.innerText = partner.username[0];
        hAv.style.background = partner.color;
        document.getElementById("headerName").innerText = partner.username;
        document.getElementById("headerSub").innerText = `Đang hoạt động • Mã: ${partner.code}`;
        document.getElementById("headerIcons").classList.remove("hidden");
        document.getElementById("chatFooter").classList.remove("hidden");
        document.getElementById("rightSidebar").classList.remove("hidden");
        const rAv = document.getElementById("rightAvatar");
        rAv.innerText = partner.username[0];
        rAv.style.background = partner.color;
        document.getElementById("rightName").innerText = partner.username;
        document.getElementById("rightCodeText").innerText = `Mã riêng: ${partner.code}`;
        refreshMessages();
        refreshSidebar(false);
    }

    function backToList() {
        document.getElementById("leftSidebar").classList.remove("mobile-hidden");
        document.getElementById("chatCenter").classList.add("mobile-hidden");
    }

    function handleMediaSelected(event) {
        const file = event.target.files[0];
        if (!file) return;
        if (file.size > 30 * 1024 * 1024) {
            alert("Tệp vượt quá 30MB!");
            event.target.value = "";
            return;
        }
        const isVideo = file.type.startsWith("video/");
        const isImage = file.type.startsWith("image/");
        if (!isVideo && !isImage) {
            alert("Chỉ hỗ trợ ảnh hoặc video!");
            event.target.value = "";
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            pendingMedia = { dataUrl: reader.result, fileName: file.name, mediaType: isVideo ? "video" : "image" };
            const bar = document.getElementById("mediaPreviewBar");
            const thumbBox = document.getElementById("previewThumbBox");
            const meta = document.getElementById("previewFileName");
            thumbBox.innerHTML = isVideo
                ? `<video src="${reader.result}" muted></video><button class="remove-media-btn" onclick="clearPendingMedia()">✕</button>`
                : `<img src="${reader.result}" alt="Preview"><button class="remove-media-btn" onclick="clearPendingMedia()">✕</button>`;
            meta.innerText = `${isVideo ? "🎬 Video" : "🖼️ Hình ảnh"}: ${file.name}`;
            bar.classList.remove("hidden");
            updateSendIcon();
            document.getElementById("msgInput").focus();
        };
        reader.readAsDataURL(file);
    }

    function clearPendingMedia() {
        pendingMedia = null;
        document.getElementById("mediaFileInput").value = "";
        document.getElementById("mediaPreviewBar").classList.add("hidden");
        document.getElementById("previewThumbBox").innerHTML = "";
        updateSendIcon();
    }

    function openLightbox(src) {
        document.getElementById("lightboxImg").src = src;
        document.getElementById("lightboxModal").classList.remove("hidden");
    }

    function updateSendIcon() {
        const val = document.getElementById("msgInput").value.trim();
        document.getElementById("sendOrLikeBtn").innerText = (val.length > 0 || pendingMedia) ? "➤" : (themeQuickEmoji[currentTheme] || "👍");
    }

    function insertEmoji(em) {
        const inp = document.getElementById("msgInput");
        inp.value += em;
        inp.focus();
        updateSendIcon();
    }

    function handleSendOrLike() {
        const val = document.getElementById("msgInput").value.trim();
        if (!val && !pendingMedia) sendMessagePayload(themeQuickEmoji[currentTheme] || "👍", null);
        else sendMessage();
    }

    async function sendMessage() {
        if (!activePartner) return;
        const inp = document.getElementById("msgInput");
        const text = inp.value.trim();
        const mediaToSend = pendingMedia;
        if (!text && !mediaToSend) return;
        inp.value = "";
        clearPendingMedia();
        await sendMessagePayload(text, mediaToSend);
    }

    async function sendMessagePayload(text, media) {
        await fetch("/api/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                sender_code: currentUser.code,
                receiver_code: activePartner.code,
                text,
                media
            })
        });
        lastRenderedSignature = "";
        refreshMessages();
        refreshSidebar(false);
    }

    async function reactMessage(msgId, emoji) {
        await fetch("/api/react", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ msg_id: msgId, emoji })
        });
        lastRenderedSignature = "";
        refreshMessages();
    }

    async function setTheme(themeName) {
        if (!activePartner) return;
        await fetch("/api/theme", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ u1: currentUser.code, u2: activePartner.code, theme: themeName })
        });
        applyThemeUI(themeName);
    }

    function cycleTheme() {
        setTheme(themeOrder[(themeOrder.indexOf(currentTheme) + 1) % themeOrder.length]);
    }

    function applyThemeUI(themeName) {
        currentTheme = themeName || "default";
        document.documentElement.setAttribute("data-theme", currentTheme);
        updateSendIcon();
    }

    async function refreshMessages() {
        if (!activePartner) return;
        const res = await fetch(`/api/messages?u1=${currentUser.code}&u2=${activePartner.code}`);
        const data = await res.json();
        applyThemeUI(data.theme);
        for (const m of data.messages) seenMessageIds.add(m.id);
        localStorage.setItem("ntnhatanh_seen_msgs", JSON.stringify([...seenMessageIds].slice(-300)));

        const signature = JSON.stringify(data.messages.map(m => [m.id, m.reaction]));
        if (signature === lastRenderedSignature) return;
        lastRenderedSignature = signature;

        const area = document.getElementById("messagesArea");
        const nearBottom = area.scrollHeight - area.scrollTop <= area.clientHeight + 100;
        const emojisOnlyRegex = /^(\p{Emoji_Presentation}|\p{Extended_Pictographic}){1,3}$/u;

        if (data.messages.length === 0) {
            area.innerHTML = `<div class="empty-state">
                <div class="avatar" style="background:${activePartner.color}; width:64px; height:64px; font-size:26px; margin:0 auto 12px;">${activePartner.username[0]}</div>
                <h3>${escapeHTML(activePartner.username)}</h3>
                <p>Đã kết nối qua mã <b>${activePartner.code}</b>. Hãy gửi tin nhắn, hình ảnh hoặc video đầu tiên!</p>
            </div>`;
            return;
        }

        area.innerHTML = data.messages.map((m, idx) => {
            const isSent = m.sender === currentUser.code;
            const prevMsg = data.messages[idx - 1];
            const nextMsg = data.messages[idx + 1];
            const isFirstInGroup = !prevMsg || prevMsg.sender !== m.sender;
            const isLastInGroup = !nextMsg || nextMsg.sender !== m.sender;
            const hasMedia = Boolean(m.media_url);
            const hasText = Boolean(m.text && m.text.trim().length > 0);
            const isEmoji = !hasMedia && hasText && emojisOnlyRegex.test(m.text.trim());

            let mediaHTML = "";
            if (m.media_type === "image" && m.media_url) {
                mediaHTML = `<img class="chat-media-img" src="${m.media_url}" alt="Hình ảnh" onclick="openLightbox('${m.media_url}')">`;
            } else if (m.media_type === "video" && m.media_url) {
                mediaHTML = `<video class="chat-media-video" src="${m.media_url}" controls playsinline preload="metadata"></video>`;
            }

            let bubbleClass = "msg-bubble";
            if (isEmoji) bubbleClass += " emoji-only";
            else if (hasMedia && !hasText) bubbleClass += " media-only";
            else if (hasMedia && hasText) bubbleClass += " has-media-and-text";

            return `
            <div class="msg-row ${isSent ? 'sent' : 'received'} ${isFirstInGroup ? 'group-gap' : ''}">
                ${!isSent ? `<div class="msg-mini-avatar ${isLastInGroup ? '' : 'invisible'}" style="background:${activePartner.color}">${activePartner.username[0]}</div>` : ''}
                <div class="msg-bubble-wrap">
                    <div class="${bubbleClass}" title="${m.time}">
                        ${mediaHTML}
                        ${hasText ? `<div class="${hasMedia ? 'msg-caption' : ''}">${escapeHTML(m.text)}</div>` : ''}
                        ${m.reaction ? `<span class="msg-reaction">${m.reaction}</span>` : ''}
                    </div>
                    <div class="react-trigger">☺
                        <div class="react-picker">
                            <span onclick="reactMessage('${m.id}', '❤️')">❤️</span>
                            <span onclick="reactMessage('${m.id}', '😂')">😂</span>
                            <span onclick="reactMessage('${m.id}', '😮')">😮</span>
                            <span onclick="reactMessage('${m.id}', '👍')">👍</span>
                        </div>
                    </div>
                </div>
            </div>`;
        }).join("");
        if (nearBottom) area.scrollTop = area.scrollHeight;
    }

    async function refreshSidebar(shouldNotify) {
        const res = await fetch(`/api/contacts?code=${currentUser.code}`);
        const data = await res.json();
        if (shouldNotify && data.incoming_messages) {
            for (const m of data.incoming_messages) {
                if (!seenMessageIds.has(m.id)) {
                    seenMessageIds.add(m.id);
                    const isViewingThisChat = activePartner && activePartner.code === m.sender && document.hasFocus();
                    if (!isViewingThisChat) {
                        const previewBody = m.text || (m.media_type === "video" ? "Đã gửi một video" : "Đã gửi một hình ảnh");
                        triggerDeviceNotification(`Tin nhắn mới từ ${m.sender_name}`, previewBody);
                    }
                }
            }
            localStorage.setItem("ntnhatanh_seen_msgs", JSON.stringify([...seenMessageIds].slice(-300)));
        }

        const listEl = document.getElementById("chatList");
        let contacts = data.contacts;
        if (activePartner && !contacts.some(c => c.code === activePartner.code)) {
            contacts.unshift({ ...activePartner, last_msg: "Bắt đầu trò chuyện", last_time: "", unread: false });
        }
        listEl.innerHTML = contacts.map(c => {
            const isActive = activePartner && activePartner.code === c.code;
            return `
            <div class="chat-item ${isActive ? 'active' : ''} ${c.unread && !isActive ? 'unread' : ''}"
                 onclick='openConversation(${JSON.stringify({username: c.username, code: c.code, color: c.color})})'>
                <div class="avatar-wrap">
                    <div class="avatar" style="background:${c.color}">${c.username[0]}</div>
                    <div class="online-dot"></div>
                </div>
                <div class="chat-item-info">
                    <div class="chat-item-top">
                        <span class="chat-item-name">${escapeHTML(c.username)}</span>
                        <span class="chat-item-code">${c.code}</span>
                    </div>
                    <div class="chat-item-preview">${escapeHTML(c.last_msg || 'Chưa có tin nhắn')} ${c.last_time ? '· ' + c.last_time : ''}</div>
                </div>
                ${c.unread && !isActive ? '<div class="unread-dot"></div>' : ''}
            </div>`;
        }).join("");
    }

    function escapeHTML(str) {
        return String(str).replace(/[&<>'"]/g, t => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[t]));
    }
</script>
</body>
</html>
"""


# --- GIAO DIỆN TRANG ADMIN THEO DÕI HOẠT ĐỘNG & MẬT KHẨU (/admin) ---
ADMIN_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ntnhatanh Admin • Theo Dõi Hoạt Động</title>
    <style>
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0; }
        body { background: #f0f2f5; color: #050505; min-height: 100vh; }
        .login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .login-card { width: 100%; max-width: 380px; background: #fff; padding: 28px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
        .login-card h2 { font-size: 22px; margin-bottom: 6px; color: #0084ff; }
        .login-card p { font-size: 13px; color: #65676b; margin-bottom: 18px; }
        .login-card input { width: 100%; padding: 12px 14px; margin-bottom: 12px; border: 1px solid #dddfe2; border-radius: 10px; font-size: 14px; outline: none; }
        .btn { padding: 8px 14px; border: none; border-radius: 10px; font-weight: 600; font-size: 13px; cursor: pointer; }
        .btn-primary { width: 100%; background: #0084ff; color: #fff; font-size: 15px; padding: 12px; }
        .btn-danger { background: #ffebe9; color: #cf222e; padding: 6px 10px; font-size: 12px; }
        .btn-ghost { background: #eef6ff; color: #0084ff; padding: 5px 9px; font-size: 12px; }
        .btn-outline { background: #f0f2f5; color: #050505; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
        .err { color: #cf222e; font-size: 13px; margin-bottom: 10px; min-height: 18px; }

        .topbar { background: #fff; border-bottom: 1px solid #e4e6eb; padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
        .topbar-left { display: flex; align-items: center; gap: 10px; }
        .badge { background: #e7f3ff; color: #0084ff; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 999px; }
        .main-wrap { max-width: 1280px; margin: 20px auto; padding: 0 20px; }

        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 20px; }
        .stat-card { background: #fff; padding: 18px; border-radius: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        .stat-label { font-size: 13px; color: #65676b; margin-bottom: 6px; }
        .stat-value { font-size: 26px; font-weight: 800; color: #050505; }

        .panel-grid { display: grid; grid-template-columns: 1.45fr 1fr; gap: 18px; margin-bottom: 20px; }
        @media (max-width: 980px) { .panel-grid { grid-template-columns: 1fr; } }
        .panel { background: #fff; border-radius: 14px; padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        .panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 10px; flex-wrap: wrap; }
        .panel-header h3 { font-size: 16px; font-weight: 700; }
        .table-wrap { overflow-x: auto; max-height: 400px; overflow-y: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #f0f2f5; vertical-align: middle; }
        th { color: #65676b; font-weight: 600; position: sticky; top: 0; background: #fff; }
        .code-pill { background: #f0f7ff; color: #0084ff; font-weight: 700; padding: 2px 8px; border-radius: 6px; font-size: 12px; }
        .pass-cell { display: inline-flex; align-items: center; gap: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
        .log-item { padding: 9px 0; border-bottom: 1px solid #f0f2f5; font-size: 13px; display: flex; gap: 10px; align-items: flex-start; }
        .log-time { color: #65676b; font-size: 12px; white-space: nowrap; }
        .msg-feed-item { padding: 10px 0; border-bottom: 1px solid #f0f2f5; font-size: 13px; }
        .msg-meta { color: #65676b; font-size: 12px; margin-bottom: 3px; }
        .media-chip { display: inline-block; background: #eef6ff; color: #0084ff; padding: 2px 8px; border-radius: 6px; font-size: 12px; text-decoration: none; margin-top: 4px; }
        .hidden { display: none !important; }
    </style>
</head>
<body>
<div id="adminLoginBox" class="login-wrap">
    <div class="login-card">
        <h2>ntnhatanh Admin</h2>
        <p>Đăng nhập quản trị để theo dõi tài khoản, mật khẩu, tin nhắn và nhật ký hoạt động</p>
        <input type="text" id="adminUserInput" placeholder="Tài khoản admin" value="admin">
        <input type="password" id="adminPassInput" placeholder="Mật khẩu admin" onkeypress="if(event.key==='Enter') adminLogin()">
        <div class="err" id="adminLoginErr"></div>
        <button class="btn btn-primary" onclick="adminLogin()">Vào trang Admin</button>
        <div style="margin-top:14px;text-align:center;">
            <a href="/ntnhatanh" style="font-size:13px;color:#0084ff;text-decoration:none;">← Quay lại trang nhắn tin</a>
        </div>
    </div>
</div>

<div id="adminDashboard" class="hidden">
    <div class="topbar">
        <div class="topbar-left">
            <h2 style="font-size:20px;font-weight:800;">ntnhatanh Admin</h2>
            <span class="badge">Giám sát hoạt động</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
            <a class="btn btn-outline" href="/ntnhatanh">💬 Mở trang chat</a>
            <button class="btn btn-outline" onclick="loadDashboard()">🔄 Làm mới</button>
            <button class="btn btn-danger" onclick="adminLogout()">Đăng xuất</button>
        </div>
    </div>

    <div class="main-wrap">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Tổng tài khoản</div>
                <div class="stat-value" id="statUsers">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Tổng tin nhắn</div>
                <div class="stat-value" id="statMessages">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Ảnh / Video đã gửi</div>
                <div class="stat-value" id="statMedia">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Sự kiện ghi nhận</div>
                <div class="stat-value" id="statLogs">0</div>
            </div>
        </div>

        <div class="panel-grid">
            <div class="panel">
                <div class="panel-header">
                    <h3>Danh sách tài khoản, mã riêng & mật khẩu</h3>
                    <button class="btn btn-ghost" id="toggleAllPassBtn" onclick="toggleAllPasswords()">Hiện tất cả mật khẩu</button>
                </div>
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Tên người dùng</th>
                                <th>Mã riêng</th>
                                <th>Mật khẩu</th>
                                <th>Tin gửi / nhận</th>
                                <th>Thao tác</th>
                            </tr>
                        </thead>
                        <tbody id="usersTableBody"></tbody>
                    </table>
                </div>
            </div>

            <div class="panel">
                <div class="panel-header">
                    <h3>Nhật ký hoạt động gần đây</h3>
                </div>
                <div class="table-wrap" id="activityLogsBox"></div>
            </div>
        </div>

        <div class="panel">
            <div class="panel-header">
                <h3>Luồng tin nhắn & tệp media gần nhất</h3>
            </div>
            <div class="table-wrap" id="recentMessagesBox"></div>
        </div>
    </div>
</div>

<script>
    let adminToken = sessionStorage.getItem("ntnhatanh_admin_token") || "";
    let dashPoller = null;
    let showAllPasswords = false;
    let revealedUsers = new Set();

    if (adminToken) {
        showDashboard();
    }

    async function adminLogin() {
        const username = document.getElementById("adminUserInput").value.trim();
        const password = document.getElementById("adminPassInput").value.trim();
        const errEl = document.getElementById("adminLoginErr");
        const res = await fetch("/api/admin/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (data.success) {
            adminToken = data.token;
            sessionStorage.setItem("ntnhatanh_admin_token", adminToken);
            showDashboard();
        } else {
            errEl.innerText = data.message;
        }
    }

    function adminLogout() {
        sessionStorage.removeItem("ntnhatanh_admin_token");
        adminToken = "";
        clearInterval(dashPoller);
        document.getElementById("adminDashboard").classList.add("hidden");
        document.getElementById("adminLoginBox").classList.remove("hidden");
    }

    function showDashboard() {
        document.getElementById("adminLoginBox").classList.add("hidden");
        document.getElementById("adminDashboard").classList.remove("hidden");
        loadDashboard();
        clearInterval(dashPoller);
        dashPoller = setInterval(loadDashboard, 3000);
    }

    function toggleAllPasswords() {
        showAllPasswords = !showAllPasswords;
        document.getElementById("toggleAllPassBtn").innerText = showAllPasswords ? "Ẩn tất cả mật khẩu" : "Hiện tất cả mật khẩu";
        loadDashboard();
    }

    function toggleSinglePassword(username) {
        if (revealedUsers.has(username)) {
            revealedUsers.delete(username);
        } else {
            revealedUsers.add(username);
        }
        loadDashboard();
    }

    async function loadDashboard() {
        const res = await fetch(`/api/admin/overview?token=${encodeURIComponent(adminToken)}`);
        if (res.status === 401) {
            adminLogout();
            return;
        }
        const data = await res.json();
        document.getElementById("statUsers").innerText = data.stats.total_users;
        document.getElementById("statMessages").innerText = data.stats.total_messages;
        document.getElementById("statMedia").innerText = data.stats.total_media;
        document.getElementById("statLogs").innerText = data.stats.total_logs;

        document.getElementById("usersTableBody").innerHTML = data.users.map(u => {
            const visible = showAllPasswords || revealedUsers.has(u.username);
            const passText = u.password
                ? (visible ? u.password : "••••••••")
                : "(Tài khoản cũ chỉ có mã băm)";
            return `
            <tr>
                <td><b>${escapeHTML(u.username)}</b></td>
                <td><span class="code-pill">${escapeHTML(u.code)}</span></td>
                <td>
                    <span class="pass-cell">
                        <span>${escapeHTML(passText)}</span>
                        ${u.password ? `<button class="btn btn-ghost" onclick="toggleSinglePassword('${escapeHTML(u.username)}')">${visible ? 'Ẩn' : 'Hiện'}</button>` : ''}
                    </span>
                </td>
                <td>${u.sent_count} gửi / ${u.received_count} nhận</td>
                <td><button class="btn btn-danger" onclick="deleteUser('${escapeHTML(u.username)}')">Xóa</button></td>
            </tr>
        `;
        }).join("") || '<tr><td colspan="5" style="color:#65676b;">Chưa có tài khoản nào.</td></tr>';

        document.getElementById("activityLogsBox").innerHTML = data.logs.map(l => `
            <div class="log-item">
                <span class="log-time">${escapeHTML(l.time)}</span>
                <div><b>${escapeHTML(l.actor)}</b> — ${escapeHTML(l.action)} ${l.detail ? `<span style="color:#65676b;">(${escapeHTML(l.detail)})</span>` : ''}</div>
            </div>
        `).join("") || '<div style="color:#65676b;font-size:13px;">Chưa có nhật ký hoạt động.</div>';

        document.getElementById("recentMessagesBox").innerHTML = data.recent_messages.map(m => `
            <div class="msg-feed-item">
                <div class="msg-meta"><b>${escapeHTML(m.sender_name)}</b> (${m.sender}) → <b>${escapeHTML(m.receiver_name)}</b> (${m.receiver}) • ${m.time}</div>
                ${m.text ? `<div>${escapeHTML(m.text)}</div>` : ''}
                ${m.media_url ? `<a class="media-chip" href="${m.media_url}" target="_blank">${m.media_type === 'video' ? '🎬 Xem video' : '🖼️ Xem ảnh'} (${m.media_url})</a>` : ''}
            </div>
        `).join("") || '<div style="color:#65676b;font-size:13px;">Chưa có tin nhắn nào.</div>';
    }

    async function deleteUser(username) {
        if (!confirm(`Xóa tài khoản "${username}"?`)) return;
        await fetch("/api/admin/delete_user", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: adminToken, username })
        });
        loadDashboard();
    }

    function escapeHTML(str) {
        return String(str).replace(/[&<>'"]/g, t => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[t]));
    }
</script>
</body>
</html>
"""


# --- SERVER HTTP & API ---
class MessengerHandler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _pair_key(self, u1, u2):
        return "_".join(sorted([u1, u2]))

    def _check_admin_token(self, token):
        return bool(token) and token == _hash(f"{ADMIN_USER}:{ADMIN_PASS_HASH}")

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/ntnhatanh", "/ntnhatanh/", "/admin", "/admin/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path in ("/", "/ntnhatanh", "/ntnhatanh/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif path in ("/admin", "/admin/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ADMIN_PAGE.encode("utf-8"))

        elif path == "/sw.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.end_headers()
            self.wfile.write(SERVICE_WORKER_JS.encode("utf-8"))

        elif path == "/manifest.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(MANIFEST_JSON.encode("utf-8"))

        elif path.startswith("/uploads/"):
            filename = os.path.basename(path)
            filepath = os.path.join(UPLOAD_DIR, filename)
            if not os.path.isfile(filepath):
                return self.send_error(404)

            mime_type, _ = mimetypes.guess_type(filepath)
            mime_type = mime_type or "application/octet-stream"
            file_size = os.path.getsize(filepath)
            range_header = self.headers.get("Range")

            if range_header:
                byte_range = range_header.replace("bytes=", "").split("-")
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else file_size - 1
                end = min(end, file_size - 1)
                chunk_len = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(chunk_len))
                self.end_headers()
                with open(filepath, "rb") as f:
                    f.seek(start)
                    self.wfile.write(f.read(chunk_len))
            else:
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(file_size))
                self.end_headers()
                with open(filepath, "rb") as f:
                    self.wfile.write(f.read())

        elif path == "/api/admin/overview":
            token = params.get("token", [""])[0]
            if not self._check_admin_token(token):
                return self._send_json({"success": False, "message": "Chưa xác thực Admin"}, 401)

            data = load_data()
            code_to_name = {info["code"]: uname for uname, info in data["users"].items()}
            sent_map = {}
            recv_map = {}
            media_count = 0

            for m in data["messages"]:
                s_code = m.get("sender", "")
                r_code = m.get("receiver", "")
                sent_map[s_code] = sent_map.get(s_code, 0) + 1
                recv_map[r_code] = recv_map.get(r_code, 0) + 1
                if m.get("media_url"):
                    media_count += 1

            users_list = []
            for uname, info in data["users"].items():
                c_code = info["code"]
                users_list.append({
                    "username": uname,
                    "code": c_code,
                    "password": info.get("password", ""),
                    "sent_count": sent_map.get(c_code, 0),
                    "received_count": recv_map.get(c_code, 0)
                })

            recent_msgs = []
            for m in reversed(data["messages"][-40:]):
                recent_msgs.append({
                    **m,
                    "sender_name": code_to_name.get(m.get("sender"), m.get("sender", "")),
                    "receiver_name": code_to_name.get(m.get("receiver"), m.get("receiver", ""))
                })

            self._send_json({
                "success": True,
                "stats": {
                    "total_users": len(users_list),
                    "total_messages": len(data["messages"]),
                    "total_media": media_count,
                    "total_logs": len(data.get("activity_logs", []))
                },
                "users": users_list,
                "logs": list(reversed(data.get("activity_logs", [])[-80:])),
                "recent_messages": recent_msgs
            })

        elif path == "/api/find_user":
            code = params.get("code", [""])[0].upper()
            data = load_data()
            for uname, info in data["users"].items():
                if info["code"] == code:
                    return self._send_json({
                        "success": True,
                        "user": {"username": uname, "code": code, "color": info.get("color", get_avatar_color(uname))}
                    })
            self._send_json({"success": False, "message": "Không tìm thấy người dùng có mã này!"})

        elif path == "/api/messages":
            u1 = params.get("u1", [""])[0]
            u2 = params.get("u2", [""])[0]
            data = load_data()
            changed = False
            msgs = []
            for m in data["messages"]:
                if (m["sender"] == u1 and m["receiver"] == u2) or (m["sender"] == u2 and m["receiver"] == u1):
                    if m["receiver"] == u1 and not m.get("read", False):
                        m["read"] = True
                        changed = True
                    msgs.append(m)
            if changed:
                save_data(data)
            theme = data.get("themes", {}).get(self._pair_key(u1, u2), "default")
            self._send_json({"messages": msgs, "theme": theme})

        elif path == "/api/contacts":
            my_code = params.get("code", [""])[0]
            data = load_data()
            code_to_name = {info["code"]: uname for uname, info in data["users"].items()}
            partners_map = {}
            incoming_messages = []

            for m in data["messages"]:
                partner_code = None
                if m["sender"] == my_code:
                    partner_code = m["receiver"]
                    prefix = "Bạn: "
                elif m["receiver"] == my_code:
                    partner_code = m["sender"]
                    prefix = ""
                    incoming_messages.append({
                        **m,
                        "sender_name": code_to_name.get(m["sender"], m["sender"])
                    })

                if partner_code:
                    is_unread = (m["receiver"] == my_code and not m.get("read", False))
                    summary_text = m.get("text", "")
                    if not summary_text:
                        summary_text = "🎬 Đã gửi một video" if m.get("media_type") == "video" else "🖼️ Đã gửi một hình ảnh"
                    partners_map[partner_code] = {
                        "last_msg": prefix + summary_text,
                        "last_time": m["time"],
                        "unread": is_unread or partners_map.get(partner_code, {}).get("unread", False)
                    }

            contacts = []
            for uname, info in data["users"].items():
                c_code = info["code"]
                if c_code in partners_map:
                    meta = partners_map[c_code]
                    contacts.append({
                        "username": uname,
                        "code": c_code,
                        "color": info.get("color", get_avatar_color(uname)),
                        "last_msg": meta["last_msg"],
                        "last_time": meta["last_time"],
                        "unread": meta["unread"]
                    })
            self._send_json({"contacts": contacts, "incoming_messages": incoming_messages[-10:]})

        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > (MAX_MEDIA_MB + 12) * 1024 * 1024:
            return self._send_json({"success": False, "message": f"Tệp vượt quá giới hạn {MAX_MEDIA_MB}MB!"}, 413)

        body = json.loads(self.rfile.read(length).decode("utf-8"))
        data = load_data()

        if self.path == "/api/admin/login":
            u = body.get("username", "").strip()
            p = body.get("password", "").strip()
            if u == ADMIN_USER and _hash(p) == ADMIN_PASS_HASH:
                token = _hash(f"{ADMIN_USER}:{ADMIN_PASS_HASH}")
                record_activity(data, f"Admin ({u})", "Đăng nhập trang quản trị")
                save_data(data)
                return self._send_json({"success": True, "token": token})
            return self._send_json({"success": False, "message": "Sai tài khoản hoặc mật khẩu Admin!"})

        elif self.path == "/api/admin/delete_user":
            if not self._check_admin_token(body.get("token", "")):
                return self._send_json({"success": False, "message": "Chưa xác thực Admin"}, 401)
            target_user = body.get("username", "").strip()
            if target_user in data["users"]:
                del data["users"][target_user]
                record_activity(data, "Admin", "Xóa tài khoản", target_user)
                save_data(data)
            return self._send_json({"success": True})

        elif self.path == "/api/register":
            username = body.get("username", "").strip()
            password = body.get("password", "").strip()
            if username in data["users"]:
                return self._send_json({"success": False, "message": "Tên người dùng này đã tồn tại!"})
            existing_codes = {u["code"] for u in data["users"].values()}
            new_code = generate_unique_code(existing_codes)
            color = get_avatar_color(username)
            data["users"][username] = {"password": password, "code": new_code, "color": color}
            record_activity(data, username, "Đăng ký tài khoản mới", f"Mã: {new_code}")
            save_data(data)
            self._send_json({"success": True, "user": {"username": username, "code": new_code, "color": color}})

        elif self.path == "/api/login":
            username = body.get("username", "").strip()
            password = body.get("password", "").strip()
            user = data["users"].get(username)
            if not verify_user_password(user, password):
                return self._send_json({"success": False, "message": "Sai tên đăng nhập hoặc mật khẩu!"})
            # Nếu tài khoản cũ chỉ có password_hash thì cập nhật thêm password khi đăng nhập thành công
            if "password" not in user:
                user["password"] = password
            record_activity(data, username, "Đăng nhập hệ thống", f"Mã: {user['code']}")
            save_data(data)
            self._send_json({
                "success": True,
                "user": {"username": username, "code": user["code"], "color": user.get("color", get_avatar_color(username))}
            })

        elif self.path == "/api/send":
            sender_code = body.get("sender_code")
            receiver_code = body.get("receiver_code")
            media_payload = body.get("media")
            media_url = ""
            media_type = ""

            if media_payload and media_payload.get("dataUrl"):
                data_url = media_payload["dataUrl"]
                orig_name = media_payload.get("fileName", "file")
                media_type = media_payload.get("mediaType", "image")
                if "," in data_url:
                    _, b64_data = data_url.split(",", 1)
                    raw_bytes = base64.b64decode(b64_data)
                    ext = os.path.splitext(orig_name)[1].lower()
                    if not ext:
                        ext = ".mp4" if media_type == "video" else ".png"
                    saved_name = f"{uuid.uuid4().hex[:12]}{ext}"
                    with open(os.path.join(UPLOAD_DIR, saved_name), "wb") as f:
                        f.write(raw_bytes)
                    media_url = f"/uploads/{saved_name}"

            msg = {
                "id": uuid.uuid4().hex[:8],
                "sender": sender_code,
                "receiver": receiver_code,
                "text": body.get("text", ""),
                "media_url": media_url,
                "media_type": media_type,
                "time": datetime.now().strftime("%H:%M"),
                "read": False,
                "reaction": ""
            }
            data["messages"].append(msg)
            sender_name = next((u for u, info in data["users"].items() if info["code"] == sender_code), sender_code)
            act_detail = f"tới {receiver_code}" + (f" ({media_type})" if media_type else "")
            record_activity(data, sender_name, "Gửi tin nhắn", act_detail)
            save_data(data)
            self._send_json({"success": True})

        elif self.path == "/api/react":
            msg_id = body.get("msg_id")
            emoji = body.get("emoji")
            for m in data["messages"]:
                if m.get("id") == msg_id:
                    m["reaction"] = "" if m.get("reaction") == emoji else emoji
                    break
            save_data(data)
            self._send_json({"success": True})

        elif self.path == "/api/theme":
            u1 = body.get("u1")
            u2 = body.get("u2")
            theme_name = body.get("theme", "default")
            data["themes"][self._pair_key(u1, u2)] = theme_name
            record_activity(data, u1, "Đổi chủ đề chat", f"{u1} ↔ {u2}: {theme_name}")
            save_data(data)
            self._send_json({"success": True})

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), MessengerHandler) as httpd:
        print(f"ntnhatanh Messenger đang chạy tại: http://localhost:{PORT}/ntnhatanh")
        print(f"Trang quản trị Admin: http://localhost:{PORT}/admin (Mặc định: {ADMIN_USER} / {ADMIN_PASS})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nĐã dừng server.")
