import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
import sqlite3
from datetime import datetime, timedelta
import threading
import schedule
import time
import re

API_TOKEN = '7772685279:AAFycEQ2N1TFn_ypYrwbGNYD-ai3K3hTTXA'
ADMIN_IDS = [5968988297]  # Replace with your Telegram user ID(s)

bot = telebot.TeleBot(API_TOKEN)

# SQLite DB Setup
conn = sqlite3.connect('promo_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    join_time TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS promos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    channel_link TEXT,
    deep_link TEXT UNIQUE,
    join_count INTEGER DEFAULT 0,
    banner_file_id TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS user_promos (
    user_id INTEGER,
    promo_id INTEGER,
    join_time TEXT,
    PRIMARY KEY(user_id, promo_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    user_id INTEGER,
    action TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT,
    send_time TEXT,
    sent INTEGER DEFAULT 0
)
''')

conn.commit()

# -- Helpers --

def is_admin(user_id):
    return user_id in ADMIN_IDS

def log_action(user_id, action):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO logs (timestamp, user_id, action) VALUES (?, ?, ?)",
                   (timestamp, user_id, action))
    conn.commit()

def register_user(user):
    cursor.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, join_time)
                      VALUES (?, ?, ?, ?, ?)''',
                   (user.id, user.username, user.first_name, user.last_name,
                    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()

def generate_deep_link(promo_id):
    # Use your bot username without '@'
    bot_username = bot.get_me().username
    # Deep link format: https://t.me/YourBot?start=promo123
    return f"https://t.me/{bot_username}?start=promo{promo_id}"

def parse_start_param(text):
    # Expected format: "/start promo<ID>"
    parts = text.strip().split()
    if len(parts) == 2 and parts[0] == "/start" and parts[1].startswith("promo"):
        promo_id_str = parts[1][5:]
        if promo_id_str.isdigit():
            return int(promo_id_str)
    return None

# --- Admin Menu Keyboard ---
def admin_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Add Promo", callback_data="add_promo"),
        InlineKeyboardButton("📊 Stats", callback_data="stats"),
        InlineKeyboardButton("📢 Broadcast Now", callback_data="broadcast_now"),
        InlineKeyboardButton("⏰ Schedule Broadcast", callback_data="schedule_broadcast"),
        InlineKeyboardButton("📝 Logs", callback_data="logs")
    )
    return kb

# --- Bot Handlers ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    register_user(message.from_user)
    promo_id = None

    # Check deep linking param
    param = None
    if message.text and len(message.text.split()) > 1:
        param = message.text.split()[1]
    if param and param.startswith("promo"):
        try:
            promo_id = int(param[5:])
        except:
            promo_id = None

    if promo_id:
        cursor.execute("SELECT id, title, channel_link, join_count FROM promos WHERE id=?", (promo_id,))
        promo = cursor.fetchone()
        if promo:
            # Track user join if not tracked before
            user_id = message.from_user.id
            cursor.execute("SELECT 1 FROM user_promos WHERE user_id=? AND promo_id=?", (user_id, promo_id))
            already_joined = cursor.fetchone()
            if not already_joined:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("INSERT INTO user_promos (user_id, promo_id, join_time) VALUES (?, ?, ?)",
                               (user_id, promo_id, now_str))
                # Increment join count
                cursor.execute("UPDATE promos SET join_count = join_count + 1 WHERE id=?", (promo_id,))
                conn.commit()
                log_action(user_id, f"Joined promo '{promo[1]}' ({promo[2]})")

            # Send promo banner if available
            cursor.execute("SELECT banner_file_id FROM promos WHERE id=?", (promo_id,))
            banner = cursor.fetchone()[0]
            if banner:
                bot.send_photo(message.chat.id, banner, caption=f"Thanks for joining promo: {promo[1]}!\nVisit: {promo[2]}")
            else:
                bot.send_message(message.chat.id,
                                 f"Thanks for joining promo: {promo[1]}!\nVisit: {promo[2]}")
        else:
            bot.send_message(message.chat.id, "Promo not found or expired.")
    else:
        # Normal start
        if is_admin(message.from_user.id):
            bot.send_message(message.chat.id,
                             "👋 Admin Panel - Manage your promotions using the buttons below.",
                             reply_markup=admin_menu_keyboard())
        else:
            bot.send_message(message.chat.id,
                             "Welcome! Use the deep links provided to join promotions.")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "⛔ You are not authorized.")
        return

    if call.data == "add_promo":
        msg = bot.send_message(call.message.chat.id, "Send the promo title:")
        bot.register_next_step_handler(msg, get_promo_title)
    elif call.data == "stats":
        send_stats(call.message)
    elif call.data == "broadcast_now":
        msg = bot.send_message(call.message.chat.id, "Send the broadcast message (text only):")
        bot.register_next_step_handler(msg, process_broadcast)
    elif call.data == "schedule_broadcast":
        msg = bot.send_message(call.message.chat.id, "Send scheduled broadcast in format:\nYYYY-MM-DD HH:MM\nThen send the message text:")
        bot.register_next_step_handler(msg, schedule_broadcast_step1)
    elif call.data == "logs":
        send_logs(call.message)

    bot.answer_callback_query(call.id)

# --- Add Promo Workflow ---

def get_promo_title(message):
    title = message.text.strip()
    if not title:
        return bot.reply_to(message, "Title can't be empty. Send the promo title:")
    message.chat_data = {}
    message.chat_data['promo_title'] = title
    msg = bot.send_message(message.chat.id, "Send the Telegram channel/group link (e.g. https://t.me/yourchannel):")
    bot.register_next_step_handler(msg, get_promo_link, message.chat_data)

def get_promo_link(message, chat_data):
    link = message.text.strip()
    if not re.match(r'https?://t\.me/[\w\d_]+', link):
        return bot.reply_to(message, "Invalid link format. Send the Telegram channel/group link:")
    chat_data['promo_link'] = link
    msg = bot.send_message(message.chat.id, "Send the promo banner image (or send /skip to skip):")
    bot.register_next_step_handler(msg, get_promo_banner, chat_data)

def get_promo_banner(message, chat_data):
    if message.content_type == 'photo':
        file_id = message.photo[-1].file_id
        chat_data['banner_file_id'] = file_id
    elif message.text and message.text.lower() == '/skip':
        chat_data['banner_file_id'] = None
    else:
        return bot.reply_to(message, "Please send a photo or /skip to skip.")

    # Insert promo into DB
    cursor.execute("INSERT INTO promos (title, channel_link, join_count, banner_file_id) VALUES (?, ?, 0, ?)",
                   (chat_data['promo_title'], chat_data['promo_link'], chat_data['banner_file_id']))
    conn.commit()
    promo_id = cursor.lastrowid
    deep_link = generate_deep_link(promo_id)
    cursor.execute("UPDATE promos SET deep_link=? WHERE id=?", (deep_link, promo_id))
    conn.commit()

    log_action(message.from_user.id, f"Added promo '{chat_data['promo_title']}' with link {chat_data['promo_link']}")

    bot.send_message(message.chat.id,
                     f"✅ Promo added!\nTitle: {chat_data['promo_title']}\nLink: {chat_data['promo_link']}\nDeep Link:\n{deep_link}",
                     reply_markup=admin_menu_keyboard())

# --- Stats ---

def send_stats(message):
    cursor.execute("SELECT id, title, join_count FROM promos ORDER BY join_count DESC")
    promos = cursor.fetchall()
    if not promos:
        bot.send_message(message.chat.id, "No promotions found.", reply_markup=admin_menu_keyboard())
        return

    text = (
    f"📢 Promo ID: {promo[0]}\n"
    f"Title: {promo[1]}\n"
    f"Total Joins: {promo[2]}\n"
    "Joined Users:\n"
    )
        cursor.execute("""
            SELECT users.username, users.first_name, users.last_name, user_promos.join_time
            FROM user_promos
            JOIN users ON users.user_id = user_promos.user_id
            WHERE user_promos.promo_id = ?
        """, (promo[0],))
        joins = cursor.fetchall()
        if joins:
            for user in joins:
                name = f"{user[1]} {user[2]}" if user[1] or user[2] else "N/A"
                username = f"@{user[0]}" if user[0] else "N/A"
                text += f" - {name} ({username}) at {user[3]}
"
        else:
            text += "No users joined yet.
"

        bot.send_message(message.chat.id, text[:4096])  # Avoid Telegram message limit

def process_broadcast(message):
    text = message.text.strip()
    if not text:
        return bot.reply_to(message, "Message cannot be empty. Send the broadcast message:")
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    sent_count = 0
    for (uid,) in users:
        try:
            bot.send_message(uid, text)
            sent_count += 1
        except Exception:
            pass
    log_action(message.from_user.id, f"Broadcast sent to {sent_count} users")
    bot.send_message(message.chat.id, f"✅ Broadcast sent to {sent_count} users.", reply_markup=admin_menu_keyboard())

# --- Schedule Broadcast Workflow ---
schedule_data = {}

def schedule_broadcast_step1(message):
    try:
        schedule_time = datetime.strptime(message.text.strip(), '%Y-%m-%d %H:%M')
        if schedule_time < datetime.utcnow():
            return bot.reply_to(message, "Time must be in the future. Send the time again:")
        schedule_data[message.chat.id] = {'time': schedule_time}
        msg = bot.send_message(message.chat.id, "Now send the message to broadcast:")
        bot.register_next_step_handler(msg, schedule_broadcast_step2)
    except ValueError:
        bot.reply_to(message, "Invalid datetime format. Use YYYY-MM-DD HH:MM")

def schedule_broadcast_step2(message):
    text = message.text.strip()
    if not text:
        return bot.reply_to(message, "Message cannot be empty. Send the message:")
    sch_time = schedule_data[message.chat.id]['time']
    cursor.execute("INSERT INTO broadcasts (message, send_time, sent) VALUES (?, ?, 0)",
                   (text, sch_time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    log_action(message.from_user.id, f"Scheduled broadcast for {sch_time}")
    bot.send_message(message.chat.id, f"✅ Broadcast scheduled for {sch_time}", reply_markup=admin_menu_keyboard())
    schedule_data.pop(message.chat.id, None)

# --- Logs ---
def send_logs(message):
    cursor.execute("SELECT timestamp, user_id, action FROM logs ORDER BY id DESC LIMIT 10")
    logs = cursor.fetchall()
    if not logs:
        bot.send_message(message.chat.id, "No logs found.", reply_markup=admin_menu_keyboard())
        return
    text = "📝 Recent Logs:\n"
    for log in logs:
        text += f"[{log[0]}] User {log[1]}: {log[2]}\n"
    bot.send_message(message.chat.id, text, reply_markup=admin_menu_keyboard())

# --- Scheduled Broadcast Checker ---

def scheduled_broadcast_worker():
    while True:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("SELECT id, message FROM broadcasts WHERE send_time <= ? AND sent = 0", (now,))
        rows = cursor.fetchall()
        for bid, msg_text in rows:
            cursor.execute("SELECT user_id FROM users")
            users = cursor.fetchall()
            sent_count = 0
            for (uid,) in users:
                try:
                    bot.send_message(uid, msg_text)
                    sent_count += 1
                except Exception:
                    pass
            cursor.execute("UPDATE broadcasts SET sent=1 WHERE id=?", (bid,))
            conn.commit()
            log_action(0, f"Auto Broadcast sent to {sent_count} users at {now}")
        time.sleep(30)

# Start scheduled broadcast worker in separate thread
threading.Thread(target=scheduled_broadcast_worker, daemon=True).start()

# --- Run the bot ---
print("Promotion bot running...")
bot.infinity_polling()
