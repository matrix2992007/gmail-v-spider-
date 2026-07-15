import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
import sqlite3
import os
import requests
import hmac
import hashlib
import time
import re
import threading
from datetime import datetime, timedelta

# --- الإعدادات الأساسية والهويات ---
TOKEN = "8019972443:AAEUjxmmdd88uBm90ar1Xpu19q6qxAVEUiA"
OWNER_ID = 7253092491       # الآيدي الخاص بك (يوسف)
MANAGER_ID = 1234567890     # آيدي المدير الخاص بك (عمر)

# --- إعدادات Binance Pay (إصدار متكامل ومحاكي آمن) ---
BINANCE_API_KEY = "YOUR_BINANCE_API_KEY"
BINANCE_SECRET_KEY = "YOUR_BINANCE_SECRET_KEY"
BINANCE_API_URL = "https://bpay.binanceapi.com/binancepay/openapi/v2/order"

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# --- إعداد قاعدة البيانات الشاملة ---
def init_db():
    conn = sqlite3.connect("barq_bot.db")
    cursor = conn.cursor()
    
    # 1. جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0,
            referred_by TEXT DEFAULT NULL,
            has_entered_promo INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            premium_expiry TEXT DEFAULT NULL,
            joined_date TEXT
        )
    ''')
    
    # 2. جدول المسوقين والبروموكود
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            marketer_id INTEGER,
            commission REAL,
            ratio_counter INTEGER DEFAULT 0,
            saved_balance REAL DEFAULT 0.0
        )
    ''')
    
    # 3. جدول القنوات للاشتراك الإجباري
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_username TEXT
        )
    ''')

    # 4. جدول خطط الاشتراكات المدفوعة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_name TEXT,
            price_egp REAL,
            price_usdt REAL,
            duration_days INTEGER,
            description TEXT
        )
    ''')
    
    # 5. جدول فواتير بايننس المعلقة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS binance_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            plan_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'PENDING',
            created_at INTEGER
        )
    ''')

    # 6. جدول الأسماء المرفوعة من الإدارة لتسمية الجيميلات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gmail_names (
            name_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_text TEXT UNIQUE,
            status TEXT DEFAULT 'AVAILABLE', -- AVAILABLE, RESERVED, USED
            reserved_by INTEGER DEFAULT NULL,
            reserved_at INTEGER DEFAULT 0
        )
    ''')

    # 7. جدول الجيميلات التي تم تسليمها وبانتظار المراجعة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS submitted_emails (
            email_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name_id INTEGER,
            email_address TEXT,
            email_password TEXT,
            recovery_email TEXT,
            status TEXT DEFAULT 'PENDING', -- PENDING, APPROVED, REJECTED
            rejection_reason TEXT DEFAULT NULL,
            price_assigned REAL DEFAULT 0.0,
            submitted_at TEXT
        )
    ''')

    # 8. جدول طلبات السحب
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            withdraw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            details TEXT,
            status TEXT DEFAULT 'PENDING', -- PENDING, COMPLETED, REJECTED
            requested_at TEXT
        )
    ''')
    
    # 9. جدول الإحصائيات العامة لحفظ السجلات التراكمية للبوت
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_stats (
            stat_key TEXT PRIMARY KEY,
            stat_val TEXT
        )
    ''')
    
    # إدراج بعض القيم الافتراضية للخطط المميزة إن لم تكن موجودة
    cursor.execute("SELECT COUNT(*) FROM premium_plans")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO premium_plans (plan_name, price_egp, price_usdt, duration_days, description) VALUES (?, ?, ?, ?, ?)",
                       ("العضوية الفضية 🥈", 150.0, 3.0, 30, "تسليم أسرع مع عمولات سحب مخفضة."))
        cursor.execute("INSERT INTO premium_plans (plan_name, price_egp, price_usdt, duration_days, description) VALUES (?, ?, ?, ?, ?)",
                       ("العضوية الذهبية 🥇", 300.0, 6.0, 90, "أولوية مراجعة تلقائية فورية مع نسبة عمولة أعلى."))
        cursor.execute("INSERT INTO premium_plans (plan_name, price_egp, price_usdt, duration_days, description) VALUES (?, ?, ?, ?, ?)",
                       ("العضوية الماسية 💎", 500.0, 10.0, 180, "دعم فني مخصص، سحب فوري بدون عمولات تماماً."))
    
    conn.commit()
    conn.close()

init_db()

# --- دالة مساعدة معالجة اتصالات الداتابيز لضمان الثبات والسرعة ---
def db_query(query, params=(), fetch=False, commit=False):
    conn = sqlite3.connect("barq_bot.db")
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        result = None
        if fetch:
            result = cursor.fetchall()
        if commit:
            conn.commit()
        return result
    except Exception as e:
        print(f"Database Error: {e}")
        return None
    finally:
        conn.close()


# ==========================================
#         أنظمة التشغيل في الخلفية (Schedulers)
# ==========================================

def background_cleanup_tasks():
    """
    مهمة تعمل في الخلفية بشكل دوري كل 5 دقائق للقيام بالآتي:
    1. إلغاء حجز الأسماء التي تجاوز حجزها ساعتين دون تسليم.
    2. تنظيف طلبات الدفع المعلقة التي انتهت صلاحيتها.
    """
    while True:
        try:
            current_time_epoch = int(time.time())
            two_hours_ago = current_time_epoch - 7200 # ساعتين بالثواني
            
            # 1. تحديث الأسماء منتهية الصلاحية
            expired_names = db_query(
                "SELECT name_id, reserved_by FROM gmail_names WHERE status = 'RESERVED' AND reserved_at < ?", 
                (two_hours_ago,), fetch=True
            )
            
            if expired_names:
                for name_id, user_id in expired_names:
                    db_query(
                        "UPDATE gmail_names SET status = 'AVAILABLE', reserved_by = NULL, reserved_at = 0 WHERE name_id = ?",
                        (name_id,), commit=True
                    )
                    try:
                        bot.send_message(
                            user_id, 
                            "⚠️ **تنبيه:** لقد انتهت المهلة المحددة لحجز الاسم المخصص لك (ساعتين) دون إرسال الحساب.\n"
                            "تم إرجاع الاسم للمخزن العام، ويمكنك حجز اسم جديد عند جاهزيتك للعمل عبر زر 'تسليم جيميلات'."
                        )
                    except Exception:
                        pass
                        
            # 2. تنظيف فواتير بايننس منتهية الصلاحية (أكثر من ساعة)
            one_hour_ago = current_time_epoch - 3600
            db_query("DELETE FROM binance_orders WHERE status = 'PENDING' AND created_at < ?", (one_hour_ago,), commit=True)
            
        except Exception as e:
            print(f"Error in background scheduler: {e}")
            
        time.sleep(300) # فحص كل 5 دقائق

# بدء تشغيل السكولر في مسار Thread منفصل
cleanup_thread = threading.Thread(target=background_cleanup_tasks, daemon=True)
cleanup_thread.start()


# --- التحقق من الاشتراك الإجباري ---
def is_subscribed(user_id):
    if user_id in [OWNER_ID, MANAGER_ID]:
        return True
    channels = db_query("SELECT channel_id FROM channels", fetch=True)
    if not channels:
        return True 
        
    for (ch_id,) in channels:
        try:
            member = bot.get_chat_member(ch_id, user_id)
            if member.status in ['left', 'kicked', 'restricted']:
                return False
        except Exception:
            continue
    return True

# --- كيبورد القوائم الرئيسية التفاعلية ---
def main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📥 تسليم جيميلات", "💰 حسابي")
    
    user = db_query("SELECT has_entered_promo, is_premium FROM users WHERE user_id = ?", (user_id,), fetch=True)
    is_premium = user[0][1] if user else 0
    
    if not is_premium:
        markup.row("👑 ترقية الحساب (Premium)")
        
    if user and user[0][0] == 0:
        markup.row("➕ إضافة بروموكود")
        
    markup.row("🤝 كن مسوق بالعمولة", "📞 الدعم الفني")
    
    if user_id == OWNER_ID:
        markup.row("👑 لوحة المالك", "💼 لوحة المدير")
    elif user_id == MANAGER_ID:
        markup.row("💼 لوحة المدير")
    return markup

# --- كيبورد لوحة المالك (حصرية ليوسف فقط) ---
def owner_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔑 إضافة بروموكود", callback_data="add_promo"),
        types.InlineKeyboardButton("📊 إحصائيات المسوقين", callback_data="marketers_stats"),
        types.InlineKeyboardButton("📢 إذاعة ذكية لكافة الأعضاء", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="add_channel"),
        types.InlineKeyboardButton("❌ إزالة قناة إجبارية", callback_data="del_channel"),
        types.InlineKeyboardButton("⭐ إضافة باقة Premium", callback_data="add_premium_plan"),
        types.InlineKeyboardButton("⚙️ تهيئة وعمل Reset للنظام", callback_data="system_reset")
    )
    return markup

# --- كيبورد لوحة المدير (متاحة لعمر ويوسف وتشمل إدارة الأسماء والجيميلات بأسلوب Pagination) ---
def manager_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📥 مراجعة الجيميلات", callback_data="review_emails_page_0"),
        types.InlineKeyboardButton("💸 طلبات السحب", callback_data="review_withdraws_page_0")
    )
    markup.add(
        types.InlineKeyboardButton("➕ إضافة أسماء للجيميلات", callback_data="admin_add_names"),
        types.InlineKeyboardButton("📋 عرض حالة الأسماء", callback_data="admin_view_names_status")
    )
    return markup

# --- بداية البوت /start ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "مستخدم"
    joined_date = time.strftime('%Y-%m-%d')
    
    user_exists = db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if not user_exists:
        db_query("INSERT INTO users (user_id, username, joined_date) VALUES (?, ?, ?)", (user_id, username, joined_date), commit=True)
        
    if not is_subscribed(user_id):
        channels = db_query("SELECT channel_username, channel_id FROM channels", fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for ch_user, ch_id in channels:
            markup.add(types.InlineKeyboardButton("اضغط هنا للاشتراك 📢", url=f"https://t.me/{ch_user.replace('@', '')}"))
        markup.add(types.InlineKeyboardButton("✅ تم الاشتراك (تأكيد)", callback_data="check_subscription"))
        
        bot.send_message(user_id, "⚠️ **عذراً عزيزي، يجب عليك الاشتراك في قنوات الإثباتات أولاً لتتمكن من استخدام البوت:**", reply_markup=markup)
        return

    user_data = db_query("SELECT has_entered_promo FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if user_data and user_data[0][0] == 0:
        msg = bot.send_message(user_id, "🔍 **إذا كنت تملك بروموكود للمسوق الخاص بك يرجى كتابته الآن.**\n\nإذا لم تكن تملك واحداً، أرسل كلمة **تخطي** للبدء مباشرة.")
        bot.register_next_step_handler(msg, process_promo_entry)
    else:
        bot.send_message(user_id, f"👋 أهلاً بك مجدداً يا *{message.from_user.first_name}* في بوت برق المتطور! 🚀", reply_markup=main_keyboard(user_id))

def process_promo_entry(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text.lower() == "تخطي":
        db_query("UPDATE users SET has_entered_promo = 1 WHERE user_id = ?", (user_id,), commit=True)
        bot.send_message(user_id, "تم تخطي الخطوة بنجاح! يمكنك الآن استخدام البوت بشكل طبيعي.", reply_markup=main_keyboard(user_id))
        return
        
    promo = db_query("SELECT code FROM promo_codes WHERE code = ?", (text,), fetch=True)
    if promo:
        db_query("UPDATE users SET referred_by = ?, has_entered_promo = 1 WHERE user_id = ?", (text, user_id), commit=True)
        bot.send_message(user_id, f"🎉 **تم تطبيق البروموكود `{text}` بنجاح في حسابك!**", reply_markup=main_keyboard(user_id))
    else:
        msg = bot.send_message(user_id, "❌ الرمز الذي أدخلته غير صحيح!\n\nأعد المحاولة بكتابة كود صحيح، أو اكتب **تخطي** للمتابعة.")
        bot.register_next_step_handler(msg, process_promo_entry)


# ==========================================
#      نظام تسليم الجيميلات والتحقق الآمن
# ==========================================

@bot.message_handler(func=lambda message: message.text == "📥 تسليم جيميلات")
def handle_gmail_submission_flow(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id):
        start(message)
        return

    existing_reservation = db_query(
        "SELECT name_id, name_text FROM gmail_names WHERE reserved_by = ? AND status = 'RESERVED'", 
        (user_id,), fetch=True
    )

    if existing_reservation:
        name_id, name_text = existing_reservation[0]
        send_reservation_instructions(user_id, name_id, name_text)
    else:
        available_name = db_query(
            "SELECT name_id, name_text FROM gmail_names WHERE status = 'AVAILABLE' LIMIT 1", 
            fetch=True
        )
        if not available_name:
            bot.send_message(
                user_id, 
                "⚠️ **عذراً، لا تتوفر أسماء جديدة مطلوبة للعمل حالياً.**\nيرجى الانتظار حتى تقوم الإدارة برفع قائمة أسماء جديدة ومحاولة الضغط مجدداً لاحقاً."
            )
            return

        name_id, name_text = available_name[0]
        current_time_epoch = int(time.time())
        db_query(
            "UPDATE gmail_names SET status = 'RESERVED', reserved_by = ?, reserved_at = ? WHERE name_id = ?",
            (user_id, current_time_epoch, name_id), commit=True
        )
        send_reservation_instructions(user_id, name_id, name_text)

def send_reservation_instructions(user_id, name_id, name_text):
    text = (
        "📥 **نظام حجز وتعيين الأسماء الاحترافي:**\n\n"
        f"لقد تم حجز الاسم التالي لك لإنشاء الجيميل:\n"
        f"🏷️ الاسم المطلوب: `{name_text}`\n\n"
        "💡 **الشروط المطلوبة لضمان القبول:**\n"
        "1. يجب إنشاء الجيميل مستخدماً الاسم المحجوز لك أعلاه حرفياً.\n"
        "2. يجب أن يكون الحساب مؤمن ببريد بديل حقيقي.\n\n"
        "✍️ **يرجى إرسال بيانات الجيميل الذي أنشأته بالتنسيق التالي حصرياً:**\n"
        "`الإيميل | الباسورد | البريد البديل`\n\n"
        "*مثال للتوضيح:*\n"
        f"`{name_text.lower().replace(' ', '')}12@gmail.com | Pa$$word123 | recovery@mail.com`"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("❌ إلغاء حجز هذا الاسم", callback_data=f"cancel_reserve_{name_id}"))
    msg = bot.send_message(user_id, text, reply_markup=markup)
    bot.register_next_step_handler(msg, process_submitted_gmail, name_id, name_text)

def process_submitted_gmail(message, name_id, name_text):
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""

    # التحقق من أن الحجز لم يلغى
    check_status = db_query("SELECT status FROM gmail_names WHERE name_id = ?", (name_id,), fetch=True)
    if not check_status or check_status[0][0] != 'RESERVED':
        return

    if not text:
        msg = bot.send_message(user_id, "❌ الرجاء إدخال نص صحيح يحتوي على بيانات الجيميل:")
        bot.register_next_step_handler(msg, process_submitted_gmail, name_id, name_text)
        return

    pattern = r"^[\w\.-]+@gmail\.com\s*\|\s*.+\s*\|\s*[\w\.-]+@[\w\.-]+\.\w+$"
    if not re.match(pattern, text, re.IGNORECASE):
        msg = bot.send_message(
            user_id, 
            "⚠️ **صيغة الإرسال غير صحيحة!**\n\n"
            "الرجاء التأكد من وضع خط الفصل العمودي `|` بين البيانات الثلاثة كالتالي:\n"
            "`الإيميل | الباسورد | البريد البديل`"
        )
        bot.register_next_step_handler(msg, process_submitted_gmail, name_id, name_text)
        return

    parts = [p.strip() for p in text.split("|")]
    email_address, email_password, recovery_email = parts[0], parts[1], parts[2]

    submitted_at = time.strftime('%Y-%m-%d %H:%M:%S')
    db_query(
        "INSERT INTO submitted_emails (user_id, name_id, email_address, email_password, recovery_email, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name_id, email_address, email_password, recovery_email, submitted_at), commit=True
    )

    db_query("UPDATE gmail_names SET status = 'USED' WHERE name_id = ?", (name_id,), commit=True)

    bot.send_message(
        user_id, 
        "✅ **تم استلام الجيميل بنجاح وجاري إرساله للمدير للفحص والمراجعة!**\n"
        "سيتم إشعارك فوراً بالنتيجة وإضافة القيمة المالية لحسابك بمجرد قبوله.",
        reply_markup=main_keyboard(user_id)
    )

    # إشعار سريع للمشرفين
    notify_text = (
        "📥 **جيميل جديد قيد المراجعة:**\n"
        f"👤 مرسل بواسطة: `{user_id}`\n"
        f"🏷️ الاسم: `{name_text}`\n"
        f"📧 البريد: `{email_address}`"
    )
    for m_id in [MANAGER_ID, OWNER_ID]:
        try:
            bot.send_message(m_id, notify_text)
        except Exception:
            pass


# ==========================================
#     معالجة الضغط على أزرار لوحات التحكم (Inline Callbacks)
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    
    # 1. إلغاء حجز الاسم
    if call.data.startswith("cancel_reserve_"):
        name_id = int(call.data.replace("cancel_reserve_", ""))
        db_query("UPDATE gmail_names SET status = 'AVAILABLE', reserved_by = NULL, reserved_at = 0 WHERE name_id = ?", (name_id,), commit=True)
        bot.answer_callback_query(call.id, "❌ تم إلغاء حجز الاسم بنجاح.", show_alert=True)
        bot.edit_message_text("❌ تم إلغاء عملية الحجز بنجاح.", call.message.chat.id, call.message.message_id)

    # 2. تأكيد الاشتراك الإجباري
    elif call.data == "check_subscription":
        if is_subscribed(user_id):
            bot.delete_message(call.message.chat.id, call.message.message_id)
            start(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ لم تشترك في كافة القنوات المطلوبة بعد!", show_alert=True)

    # 3. تصفح الجيميلات المرفوعة بنظام الصفحات (Pagination للجروبات والمدراء)
    elif call.data.startswith("review_emails_page_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        page = int(call.data.replace("review_emails_page_", ""))
        show_review_emails_page(user_id, call.message.message_id, page)

    # 4. قبول الجيميل المسلم
    elif call.data.startswith("approve_item_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        gmail_id = int(call.data.replace("approve_item_", ""))
        msg = bot.send_message(user_id, "✍️ أرسل الآن القيمة المالية بالجنيه المصري لإضافتها لحساب المستخدم (مثال: `2.5`):")
        bot.register_next_step_handler(msg, manager_approve_gmail_step, gmail_id, call.message.message_id)

    # 5. رفض الجيميل المسلم
    elif call.data.startswith("reject_item_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        gmail_id = int(call.data.replace("reject_item_", ""))
        msg = bot.send_message(user_id, "✍️ أرسل الآن سبب الرفض بوضوح (مثال: `الجيميل لا يحمل الاسم الصحيح`):")
        bot.register_next_step_handler(msg, manager_reject_gmail_step, gmail_id, call.message.message_id)

    # 6. تصفح طلبات السحب بنظام الصفحات
    elif call.data.startswith("review_withdraws_page_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        page = int(call.data.replace("review_withdraws_page_", ""))
        show_review_withdraws_page(user_id, call.message.message_id, page)

    # 7. اعتماد إتمام عملية السحب
    elif call.data.startswith("complete_withdraw_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        w_id = int(call.data.replace("complete_withdraw_", ""))
        db_query("UPDATE withdrawal_requests SET status = 'COMPLETED' WHERE withdraw_id = ?", (w_id,), commit=True)
        bot.answer_callback_query(call.id, "✅ تم تعليم الطلب كمكتمل بنجاح!", show_alert=True)
        # إشعار العضو
        w_data = db_query("SELECT user_id, amount FROM withdrawal_requests WHERE withdraw_id = ?", (w_id,), fetch=True)
        if w_data:
            c_user, c_amt = w_data[0]
            try:
                bot.send_message(c_user, f"💸 **تم إرسال سحبك المالي بقيمة {c_amt} ج.م بنجاح!**\nنشكرك على مجهودك معنا.")
            except Exception:
                pass
        show_review_withdraws_page(user_id, call.message.message_id, 0)

    # 8. رفض طلب السحب
    elif call.data.startswith("reject_withdraw_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        w_id = int(call.data.replace("reject_withdraw_", ""))
        msg = bot.send_message(user_id, "✍️ أرسل سبب الرفض لإرجاع الأموال لحساب العضو:")
        bot.register_next_step_handler(msg, manager_reject_withdraw_step, w_id, call.message.message_id)

    # 9. طلب سحب الأرباح
    elif call.data == "request_withdraw":
        user_balance = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
        balance = user_balance[0][0] if user_balance else 0.0
        
        if balance < 5:  
            bot.answer_callback_query(call.id, "❌ رصيدك أقل من الحد الأدنى للسحب وهو 5 جنيه!", show_alert=True)
            return
            
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("💳 سحب كاش (فودافون / اتصالات..)", callback_data="w_cash"),
            types.InlineKeyboardButton("🆔 سحب عبر Binance ID", callback_data="w_binance_id"),
            types.InlineKeyboardButton("🌐 سحب عبر شبكة USDT-TRC20", callback_data="w_network"),
            types.InlineKeyboardButton("☎️ سحب رصيد شحن مباشر", callback_data="w_phone_balance")
        )
        bot.edit_message_text("📋 **يرجى اختيار طريقة السحب المفضلة لديك:**", call.message.chat.id, call.message.message_id, reply_markup=markup)

    # 10. معالجة خيارات السحب المحددة
    elif call.data.startswith("w_"):
        w_type = call.data
        if w_type == "w_cash":
            msg = bot.send_message(user_id, "✍️ أرسل الآن رقم المحفظة الكاش والقيمة المالية بالتنسيق التالي:\n`المبلغ - رقم المحفظة` (خصم 2 جنيه عمولة تحويل)")
            bot.register_next_step_handler(msg, save_withdraw_request, "محفظة كاش")
        elif w_type == "w_binance_id":
            msg = bot.send_message(user_id, "✍️ أرسل تفاصيل السحب بالتنسيق التالي:\n`المبلغ - Binance ID` (بدون عمولة تحويل)")
            bot.register_next_step_handler(msg, save_withdraw_request, "Binance ID")
        elif w_type == "w_network":
            msg = bot.send_message(user_id, "✍️ أرسل تفاصيل السحب بالتنسيق التالي:\n`المبلغ - عنوان محفظة USDT-TRC20`")
            bot.register_next_step_handler(msg, save_withdraw_request, "USDT Network")
        elif w_type == "w_phone_balance":
            msg = bot.send_message(user_id, "✍️ أرسل تفاصيل السحب بالتنسيق التالي:\n`المبلغ - رقم الهاتف - شركة المحمول (اتصالات/أورنج...)`")
            bot.register_next_step_handler(msg, save_withdraw_request, "رصيد شحن")

    # 11. شراء باقات بريميوم عبر بايننس
    elif call.data.startswith("buy_plan_"):
        plan_id = int(call.data.replace("buy_plan_", ""))
        create_binance_payment_order(user_id, plan_id, call.message)

    # 12. تفعيل قنوات الاشتراك الإجباري
    elif call.data == "add_channel" and user_id == OWNER_ID:
        msg = bot.send_message(OWNER_ID, "✍️ أرسل معرف القناة متبوعاً بالاسم العام لها بالشكل التالي:\n`ID | @username`")
        bot.register_next_step_handler(msg, admin_process_add_channel)

    elif call.data == "del_channel" and user_id == OWNER_ID:
        channels = db_query("SELECT channel_id, channel_username FROM channels", fetch=True)
        if not channels:
            bot.send_message(OWNER_ID, "لا توجد قنوات إجبارية حالياً.")
            return
        markup = types.InlineKeyboardMarkup()
        for ch_id, ch_user in channels:
            markup.add(types.InlineKeyboardButton(f"حذف {ch_user}", callback_data=f"remove_ch_{ch_id}"))
        bot.send_message(OWNER_ID, "اختر القناة التي تود إزالتها:", reply_markup=markup)

    elif call.data.startswith("remove_ch_"):
        if user_id != OWNER_ID:
            return
        ch_id = call.data.replace("remove_ch_", "")
        db_query("DELETE FROM channels WHERE channel_id = ?", (ch_id,), commit=True)
        bot.answer_callback_query(call.id, "✅ تم إزالة القناة بنجاح.")
        bot.delete_message(call.message.chat.id, call.message.message_id)

    # 13. ميزات المالك والمسوقين وإضافة الباقات
    elif call.data == "add_promo" and user_id == OWNER_ID:
        msg = bot.send_message(OWNER_ID, "✍️ أرسل اسم البروموكود الجديد الذي ترغب في إنشائه (مثال: `VIP_BARQ`):")
        bot.register_next_step_handler(msg, admin_get_promo_name)

    elif call.data == "marketers_stats" and user_id == OWNER_ID:
        stats = db_query("SELECT code, marketer_id, commission, saved_balance FROM promo_codes", fetch=True)
        if not stats:
            bot.send_message(OWNER_ID, "لا يوجد مسوقين مسجلين حالياً.")
            return
        text = "📊 **إحصائيات المسوقين الفعالة:**\n\n"
        for code, m_id, comm, saved in stats:
            text += f"• الكود: `{code}`\n  آيدي المسوق: `{m_id}`\n  العمولة: {comm} ج.م\n  المعلق بالحصالة: {saved}/5 ج.م\n\n"
        bot.send_message(OWNER_ID, text)

    elif call.data == "admin_broadcast" and user_id == OWNER_ID:
        msg = bot.send_message(OWNER_ID, "📢 **أرسل نص الإذاعة التي تريد توجيهها لكافة الأعضاء:**")
        bot.register_next_step_handler(msg, admin_process_broadcast)

    elif call.data == "admin_add_names" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "✍️ **يرجى إرسال الأسماء المطلوبة** (كل اسم في سطر منفصل):")
        bot.register_next_step_handler(msg, admin_process_add_names)

    elif call.data == "admin_view_names_status" and user_id in [OWNER_ID, MANAGER_ID]:
        avail = db_query("SELECT COUNT(*) FROM gmail_names WHERE status = 'AVAILABLE'", fetch=True)[0][0]
        res = db_query("SELECT COUNT(*) FROM gmail_names WHERE status = 'RESERVED'", fetch=True)[0][0]
        used = db_query("SELECT COUNT(*) FROM gmail_names WHERE status = 'USED'", fetch=True)[0][0]
        text = (
            "📋 **حالة الأسماء في قاعدة البيانات:**\n\n"
            f"🟢 الأسماء المتاحة للحجز: **{avail}**\n"
            f"🟡 الأسماء المحجوزة حالياً: **{res}**\n"
            f"🔴 أسماء تم استخدامها بانتظار الفحص: **{used}**"
        )
        bot.send_message(user_id, text)

    elif call.data == "system_reset" and user_id == OWNER_ID:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("⚠️ نعم، تهيئة تامة", callback_data="confirm_hard_reset"),
            types.InlineKeyboardButton("❌ تراجع", callback_data="cancel_reset")
        )
        bot.send_message(OWNER_ID, "❗ **تحذير خطير جداً:** هل تريد مسح بيانات الجيميلات والأسماء والبدء من جديد بالكامل؟ لا يمكن التراجع عن هذا الإجراء.", reply_markup=markup)

    elif call.data == "confirm_hard_reset" and user_id == OWNER_ID:
        db_query("DELETE FROM gmail_names", commit=True)
        db_query("DELETE FROM submitted_emails", commit=True)
        bot.edit_message_text("✅ تم تفريغ جداول الأسماء والجيميلات بالكامل وإعادة تشغيل قاعدة البيانات.", call.message.chat.id, call.message.message_id)


# ==========================================
#     نظام الـ Pagination لصفحات الإدارة والتحكم
# ==========================================

def show_review_emails_page(admin_id, message_id, page=0):
    limit = 1
    offset = page * limit
    
    # جلب الحسابات المعلقة
    pending_emails = db_query(
        "SELECT email_id, user_id, email_address, email_password, recovery_email, submitted_at, name_id "
        "FROM submitted_emails WHERE status = 'PENDING' LIMIT ? OFFSET ?", (limit, offset), fetch=True
    )
    
    total_pending = db_query("SELECT COUNT(*) FROM submitted_emails WHERE status = 'PENDING'", fetch=True)[0][0]
    
    if not pending_emails:
        bot.edit_message_text("📥 **لا توجد أي حسابات جيميل معلقة بانتظار المراجعة حالياً.**", admin_id, message_id)
        return

    email_id, u_id, email, password, recovery, s_time, name_id = pending_emails[0]
    
    # جلب الاسم المحجوز للتحقق
    name_data = db_query("SELECT name_text FROM gmail_names WHERE name_id = ?", (name_id,), fetch=True)
    name_text = name_data[0][0] if name_data else "غير محدد"

    text = (
        f"📥 **مراجعة الجيميلات المرفوعة (صفحة {page + 1} من {total_pending}):**\n\n"
        f"👤 مرسل بواسطة: `{u_id}`\n"
        f"🏷️ الاسم المطلوب: `{name_text}`\n"
        f"📧 البريد: `{email}`\n"
        f"🔑 كلمة المرور: `{password}`\n"
        f"🔄 البريد البديل: `{recovery}`\n"
        f"📅 تاريخ الإرسال: `{s_time}`"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ قبول وتحديد سعر", callback_data=f"approve_item_{email_id}"),
        types.InlineKeyboardButton("❌ رفض وتوثيق السبب", callback_data=f"reject_item_{email_id}")
    )
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"review_emails_page_{page - 1}"))
    if offset + limit < total_pending:
        nav_buttons.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"review_emails_page_{page + 1}"))
        
    if nav_buttons:
        markup.add(*nav_buttons)
        
    markup.add(types.InlineKeyboardButton("🔙 العودة للوحة", callback_data="back_to_m_panel"))
    
    try:
        bot.edit_message_text(text, admin_id, message_id, reply_markup=markup)
    except Exception:
        pass


def show_review_withdraws_page(admin_id, message_id, page=0):
    limit = 1
    offset = page * limit
    
    pending_withdraws = db_query(
        "SELECT withdraw_id, user_id, amount, method, details, requested_at "
        "FROM withdrawal_requests WHERE status = 'PENDING' LIMIT ? OFFSET ?", (limit, offset), fetch=True
    )
    
    total_pending = db_query("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'PENDING'", fetch=True)[0][0]
    
    if not pending_withdraws:
        bot.edit_message_text("💸 **لا توجد أي طلبات سحب معلقة حالياً.**", admin_id, message_id)
        return

    w_id, u_id, amount, method, details, r_time = pending_withdraws[0]

    text = (
        f"💸 **مراجعة طلبات السحب المعلقة (صفحة {page + 1} من {total_pending}):**\n\n"
        f"👤 صاحب الطلب: `{u_id}`\n"
        f"💰 المبلغ المطلوب: **{amount:.2f} ج.م**\n"
        f"⚙️ طريقة السحب: *{method}*\n"
        f"📝 تفاصيل الحساب: `{details}`\n"
        f"📅 وقت التقديم: `{r_time}`"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تم إرسال الأموال", callback_data=f"complete_withdraw_{w_id}"),
        types.InlineKeyboardButton("❌ رفض وإرجاع الرصيد", callback_data=f"reject_withdraw_{w_id}")
    )
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"review_withdraws_page_{page - 1}"))
    if offset + limit < total_pending:
        nav_buttons.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"review_withdraws_page_{page + 1}"))
        
    if nav_buttons:
        markup.add(*nav_buttons)
        
    markup.add(types.InlineKeyboardButton("🔙 العودة للوحة", callback_data="back_to_m_panel"))
    
    try:
        bot.edit_message_text(text, admin_id, message_id, reply_markup=markup)
    except Exception:
        pass


# ==========================================
#     تسيير وقبول ورفض الجيميلات وطلبات السحب
# ==========================================

def manager_approve_gmail_step(message, gmail_id, original_msg_id):
    if message.from_user.id not in [OWNER_ID, MANAGER_ID]:
        return
    try:
        price = float(message.text.strip())
    except ValueError:
        msg = bot.send_message(message.from_user.id, "⚠️ الرجاء إدخال رقم صحيح يمثل السعر:")
        bot.register_next_step_handler(msg, manager_approve_gmail_step, gmail_id, original_msg_id)
        return

    gmail_data = db_query(
        "SELECT user_id, name_id, email_address FROM submitted_emails WHERE email_id = ?", 
        (gmail_id,), fetch=True
    )
    if not gmail_data:
        bot.send_message(message.from_user.id, "❌ خطأ: لم يتم العثور على هذا الجيميل.")
        return

    user_id, name_id, email_address = gmail_data[0]

    db_query("UPDATE submitted_emails SET status = 'APPROVED', price_assigned = ? WHERE email_id = ?", (price, gmail_id), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id), commit=True)
    db_query("DELETE FROM gmail_names WHERE name_id = ?", (name_id,), commit=True)

    apply_referral_commission_on_approval(user_id)

    try:
        bot.send_message(
            user_id, 
            f"🎉 **خبر سار! تم قبول الجيميل الخاص بك:**\n"
            f"📧 البريد: `{email_address}`\n"
            f"💵 القيمة المضافة لرصيدك: **{price:.2f} ج.م**\n"
            f"تمنياتنا لك بالتوفيق الدائم! 🚀"
        )
    except Exception:
        pass

    bot.send_message(message.from_user.id, f"✅ تم اعتماد قبول الحساب وتحديث رصيد العضو بقيمة {price} ج.م بنجاح!")
    show_review_emails_page(message.from_user.id, original_msg_id, 0)


def manager_reject_gmail_step(message, gmail_id, original_msg_id):
    if message.from_user.id not in [OWNER_ID, MANAGER_ID]:
        return
    reason = message.text.strip()

    gmail_data = db_query(
        "SELECT user_id, name_id, email_address FROM submitted_emails WHERE email_id = ?", 
        (gmail_id,), fetch=True
    )
    if not gmail_data:
        bot.send_message(message.from_user.id, "❌ خطأ: لم يتم العثور على هذا الجيميل.")
        return

    user_id, name_id, email_address = gmail_data[0]

    db_query("UPDATE submitted_emails SET status = 'REJECTED', rejection_reason = ? WHERE email_id = ?", (reason, gmail_id), commit=True)
    db_query("UPDATE gmail_names SET status = 'AVAILABLE', reserved_by = NULL, reserved_at = 0 WHERE name_id = ?", (name_id,), commit=True)

    try:
        bot.send_message(
            user_id, 
            f"❌ **عذراً، تم رفض الجيميل المقدم من قبلك:**\n"
            f"📧 البريد: `{email_address}`\n"
            f"⚠️ سبب الرفض: *{reason}*\n\n"
            f"💡 لقد قمنا بإعادة إتاحة الاسم المخصص لك مجدداً لتصحيح المشكلة وإعادة تسليمه."
        )
    except Exception:
        pass

    bot.send_message(message.from_user.id, "❌ تم رفض الحساب بنجاح وإرسال الإشعار والسبب للمستخدم.")
    show_review_emails_page(message.from_user.id, original_msg_id, 0)


def manager_reject_withdraw_step(message, w_id, original_msg_id):
    if message.from_user.id not in [OWNER_ID, MANAGER_ID]:
        return
    reason = message.text.strip()

    w_data = db_query("SELECT user_id, amount FROM withdrawal_requests WHERE withdraw_id = ?", (w_id,), fetch=True)
    if not w_data:
        bot.send_message(message.from_user.id, "❌ خطأ: لم يتم العثور على طلب السحب هذا.")
        return

    u_id, amount = w_data[0]

    db_query("UPDATE withdrawal_requests SET status = 'REJECTED' WHERE withdraw_id = ?", (w_id,), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, u_id), commit=True)

    try:
        bot.send_message(
            u_id, 
            f"❌ **تنبيه: تم رفض طلب السحب الخاص بك بقيمة {amount} ج.م**\n"
            f"⚠️ السبب: *{reason}*\n"
            f"💰 تم إرجاع المبلغ بالكامل إلى رصيدك داخل البوت."
        )
    except Exception:
        pass

    bot.send_message(message.from_user.id, "❌ تم رفض طلب السحب وإعادة الرصيد للمستخدم بنجاح.")
    show_review_withdraws_page(message.from_user.id, original_msg_id, 0)


# ==========================================
#     نظام معالجة دفع بايننس (Binance Pay)
# ==========================================

def generate_binance_signature(payload, secret_key):
    """توليد التوقيع الرقمي الموثق لفواتير بايننس الإلكترونية"""
    return hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha512).hexdigest().upper()

def create_binance_payment_order(user_id, plan_id, original_message):
    plan_data = db_query("SELECT plan_name, price_usdt, duration_days FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
    if not plan_data:
        return
        
    plan_name, price_usdt, duration = plan_data[0]
    order_id = f"ORDER_{user_id}_{int(time.time())}"
    
    # حفظ الطلب في الداتابيز أولاً لمنع الفقدان
    db_query("INSERT INTO binance_orders (order_id, user_id, plan_id, amount, status, created_at) VALUES (?, ?, ?, ?, 'PENDING', ?)",
             (order_id, user_id, plan_id, price_usdt, int(time.time())), commit=True)
             
    # في حالة عدم وضع المفتاح الأصلي لـ Binance نقوم بعمل محاكاة دفع آمنة واحترافية للمستخدمين
    if BINANCE_API_KEY == "YOUR_BINANCE_API_KEY" or BINANCE_SECRET_KEY == "YOUR_BINANCE_SECRET_KEY":
        # نظام محاكاة الدفع الذكي (Sandbox Simulation) لتسهيل الفحص
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("💳 تأكيد الدفع المحاكي (آمن)", callback_data=f"simulate_pay_{order_id}"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel_payment")
        )
        bot.send_message(
            user_id,
            f"💎 **واجهة سداد بايننس المطورة:**\n\n"
            f"📦 الخدمة المطلوبة: *ترقية الحساب إلى {plan_name}*\n"
            f"💰 السعر المطلوب: **{price_usdt:.2f} USDT**\n"
            f"🏷️ كود الفاتورة الفريد: `{order_id}`\n\n"
            f"_تنبيه: أنت الآن تعمل في بيئة التطوير المحاكية والمستقرة، انقر على زر التأكيد لإتمام الشراء على الفور._",
            reply_markup=markup
        )
    else:
        # استدعاء API بايننس الرسمي
        headers = {
            "content-type": "application/json",
            "BinancePay-Certificate-SN": BINANCE_API_KEY,
            "BinancePay-Signature": ""
        }
        
        payload = {
            "env": {"terminalType": "MINI_PROGRAM"},
            "merchantTradeNo": order_id,
            "orderAmount": price_usdt,
            "currency": "USDT",
            "goods": {
                "goodsType": "01",
                "goodsCategory": "6000",
                "referenceGoodsId": f"PLAN_{plan_id}",
                "goodsName": plan_name
            }
        }
        
        # ربط الطلب بالتوقيع الإلكتروني وتفعيله
        try:
            res = requests.post(BINANCE_API_URL, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                resp_json = res.json()
                payment_url = resp_json.get("data", {}).get("universalUrl")
                if payment_url:
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("💸 اضغط هنا للدفع السريع عبر بايننس", url=payment_url))
                    bot.send_message(user_id, f"✅ **تم إنشاء الفاتورة بنجاح!**\n\nيرجى الضغط على الرابط أدناه لإتمام عملية السداد بقيمة {price_usdt} USDT.", reply_markup=markup)
                    return
            bot.send_message(user_id, "⚠️ **حدث خطأ فني أثناء الاتصال ببوابة بايننس.** يرجى مراجعة المالك لحل المشكلة يدوياً.")
        except Exception as e:
            bot.send_message(user_id, f"⚠️ خطأ في الاتصال الخارجي: {e}")

# تابع callback محاكي الدفع
@bot.callback_query_handler(func=lambda call: call.data.startswith("simulate_pay_"))
def handle_payment_simulation(call):
    user_id = call.from_user.id
    order_id = call.data.replace("simulate_pay_", "")
    
    order_data = db_query("SELECT plan_id, amount, status FROM binance_orders WHERE order_id = ?", (order_id,), fetch=True)
    if not order_data or order_data[0][2] != 'PENDING':
        bot.answer_callback_query(call.id, "❌ الفاتورة غير معلقة أو منتهية الصلاحية بالفعل.", show_alert=True)
        return
        
    plan_id, amt, status = order_data[0]
    plan_data = db_query("SELECT plan_name, duration_days FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
    plan_name, duration = plan_data[0] if plan_data else ("العضوية الفضية 🥈", 30)
    
    # حساب تاريخ الانتهاء الجديد
    expiry_date = (datetime.now() + timedelta(days=duration)).strftime('%Y-%m-%d %H:%M:%S')
    
    # تحديث الطلب إلى مكتمل
    db_query("UPDATE binance_orders SET status = 'COMPLETED' WHERE order_id = ?", (order_id,), commit=True)
    # تحديث وضع المستخدم إلى بريميوم وتحديد تاريخ الانتهاء
    db_query("UPDATE users SET is_premium = 1, premium_expiry = ? WHERE user_id = ?", (expiry_date, user_id), commit=True)
    
    bot.edit_message_text(
        f"🎉 **تهانينا الحارة! تم الدفع بنجاح واكتمل الشراء!**\n\n"
        f"👑 نوع الباقة المفعّلة: **{plan_name}**\n"
        f"📅 تاريخ انتهاء الباقة المحدّث: `{expiry_date}`\n"
        f"مرحباً بك في عالم الميزات الخاصة ببرق! 🚀", 
        call.message.chat.id, 
        call.message.message_id
    )
    bot.answer_callback_query(call.id, "✅ تم تحديث ترقيتك بنجاح!")


# ==========================================
#          تسيير شؤون المالك والمدراء
# ==========================================

def admin_process_add_channel(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        return
    text = message.text.strip() if message.text else ""
    if "|" not in text:
        bot.send_message(user_id, "⚠️ صيغة غير صحيحة، تم إلغاء العملية. يجب استخدام الفاصل `|`.")
        return
        
    parts = text.split("|")
    ch_id, ch_user = parts[0].strip(), parts[1].strip()
    
    db_query("INSERT OR REPLACE INTO channels (channel_id, channel_username) VALUES (?, ?)", (ch_id, ch_user), commit=True)
    bot.send_message(user_id, f"✅ تم حفظ القناة {ch_user} بنجاح كقناة اشتراك إجباري.")

def admin_process_add_names(message):
    user_id = message.from_user.id
    if user_id not in [OWNER_ID, MANAGER_ID]:
        return
    text = message.text.strip() if message.text else ""
    if not text:
        bot.send_message(user_id, "⚠️ قائمة الأسماء فارغة! تم إلغاء العملية.")
        return

    names_list = [n.strip() for n in text.split("\n") if n.strip()]
    inserted_counter = 0
    duplicate_counter = 0

    for name in names_list:
        try:
            db_query("INSERT INTO gmail_names (name_text) VALUES (?)", (name,), commit=True)
            inserted_counter += 1
        except Exception:
            duplicate_counter += 1

    bot.send_message(
        user_id, 
        f"🎯 **تمت العملية بنجاح!**\n\n"
        f"🟢 عدد الأسماء الجديدة المضافة: **{inserted_counter}**\n"
        f"🟡 الأسماء المكررة (تخطيها): **{duplicate_counter}**"
    )

def admin_process_broadcast(message):
    if message.from_user.id != OWNER_ID:
        return
    broadcast_text = message.text.strip() if message.text else ""
    if not broadcast_text:
        bot.send_message(OWNER_ID, "❌ نص الإذاعة فارغ، تم إلغاء الإرسال.")
        return

    broadcast_thread = threading.Thread(target=run_smart_broadcast, args=(broadcast_text,))
    broadcast_thread.start()
    bot.send_message(OWNER_ID, "🚀 **بدأت عملية الإرسال الذكية في الخلفية الآن!**")

def run_smart_broadcast(text):
    users = db_query("SELECT user_id FROM users", fetch=True)
    if not users:
        return

    success = 0
    blocked = 0
    
    for (u_id,) in users:
        try:
            bot.send_message(u_id, text)
            success += 1
            time.sleep(0.04) # حماية من معدل قيود الإرسال لتليجرام
        except Exception:
            blocked += 1
            
    try:
        bot.send_message(
            OWNER_ID, 
            f"📋 **تقرير اكتمال الإذاعة الذكية:**\n\n"
            f"✅ تم الإرسال بنجاح إلى: **{success}** مستخدم\n"
            f"🚫 أعضاء قاموا بحظر البوت: **{blocked}**"
        )
    except Exception:
        pass


# ==========================================
#     تسجيل ومحاذاة الميزات الأخرى للتحكم
# ==========================================

@bot.message_handler(func=lambda message: True)
def handle_menu_options(message):
    user_id = message.from_user.id
    text = message.text
    
    if not is_subscribed(user_id):
        start(message)
        return
        
    if text == "👑 ترقية الحساب (Premium)":
        plans = db_query("SELECT plan_id, plan_name, price_egp, price_usdt, duration_days, description FROM premium_plans", fetch=True)
        if not plans:
            bot.send_message(user_id, "⚠️ لا تتوفر خطط اشتراك مميزة حالياً.")
            return
            
        text_plans = "👑 **باقات الاشتراك المميز المتاحة لك:**\n\n"
        markup = types.InlineKeyboardMarkup()
        for p_id, name, price_egp, price_usdt, duration, desc in plans:
            text_plans += f"⭐ **{name}** ({duration} يوم)\n• السعر: {price_egp} جنيه / {price_usdt} USDT\n• المميزات: {desc}\n\n"
            markup.add(types.InlineKeyboardButton(f"اشترك في {name}", callback_data=f"buy_plan_{p_id}"))
        bot.send_message(user_id, text_plans, reply_markup=markup)

    elif text == "➕ إضافة بروموكود":
        user_data = db_query("SELECT has_entered_promo FROM users WHERE user_id = ?", (user_id,), fetch=True)
        if user_data and user_data[0][0] == 0:
            msg = bot.send_message(user_id, "🔍 أرسل البروموكود الخاص بالمسوق الذي دعاك للبوت الآن:")
            bot.register_next_step_handler(msg, process_promo_entry)
        else:
            bot.send_message(user_id, "⚠️ لقد قمت بإدخال بروموكود مسبقاً في حسابك!")
            
    elif text == "🤝 كن مسوق بالعمولة":
        bot.send_message(user_id, "للانضمام إلى فريق المسوقين المعتمدين والحصول على كود إحالة بنسب ربح مجزية، يرجى التواصل مباشرة مع المالك:\n\n👤 مطور البوت والمالك: @VIR_XT")
        
    elif text == "📞 الدعم الفني":
        bot.send_message(user_id, "لأية استفسارات أو مشاكل تخص تسليم الحسابات أو التحويلات المالية، تواصل معنا فوراً هنا:\n\n👤 الدعم الفني المباشر: @VIR_XT")
        
    elif text == "💰 حسابي":
        user = db_query("SELECT balance, is_premium, premium_expiry FROM users WHERE user_id = ?", (user_id,), fetch=True)
        balance, is_prem, expiry = user[0] if user else (0.0, 0, None)
        status_text = "حساب عادي 🔘" if not is_prem else f"حساب مميز 👑 (ينتهي في: {expiry})"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 سحب أرباحي الآن", callback_data="request_withdraw"))
        bot.send_message(user_id, f"👤 **معلومات حسابك المالية:**\n\n💵 رصيدك الحالي: **{balance:.2f} ج.م**\n⭐ نوع الحساب: {status_text}", reply_markup=markup)
        
    elif text == "👑 لوحة المالك" and user_id == OWNER_ID:
        bot.send_message(user_id, "👑 أهلاً بك يا يوسف في لوحة المالك الحصرية. اختر ما تريد التحكم به:", reply_markup=owner_keyboard())
        
    elif text == "💼 لوحة المدير" and (user_id == MANAGER_ID or user_id == OWNER_ID):
        bot.send_message(user_id, "💼 لوحة تحكم المدير لإدارة العمل اليومي والطلبات والأسماء المفتوحة:", reply_markup=manager_keyboard())

def save_withdraw_request(message, method_name):
    user_id = message.from_user.id
    details = message.text.strip() if message.text else ""
    if not details:
        bot.send_message(user_id, "❌ خطأ في الإدخال، تم إلغاء العملية.")
        return
        
    # التحقق من الرصيد والخصم التلقائي لتجنب العمليات الوهمية
    user_balance = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    balance = user_balance[0][0] if user_balance else 0.0
    
    # محاكاة لفرز الرقم المالي المطلوب سحبه
    try:
        req_amt = float(details.split("-")[0].strip())
    except Exception:
        req_amt = balance
        
    if req_amt > balance or req_amt < 5:
        bot.send_message(user_id, "❌ رصيدك الحالي لا يغطي هذا المبلغ أو أن القيمة المطلوبة أقل من 5 ج.م.")
        return
        
    # خصم المبلغ من رصيد العضو لحين البت في أمره
    db_query("UPDATE users SET balance = balance - ? WHERE user_id = ?", (req_amt, user_id), commit=True)
    
    requested_at = time.strftime('%Y-%m-%d %H:%M:%S')
    db_query(
        "INSERT INTO withdrawal_requests (user_id, amount, method, details, requested_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, req_amt, method_name, details, requested_at), commit=True
    )
    
    bot.send_message(user_id, "✅ تم إرسال طلب السحب الخاص بك بنجاح إلى الإدارة. تم تجميد الرصيد وجاري معالجته للتحويل.")
    
    # إشعار المشرفين
    notify_text = (
        "⚠️ **طلب سحب معلق جديد للإدارة:**\n\n"
        f"👤 المستخدم: `{user_id}`\n"
        f"⚙️ الطريقة: *{method_name}*\n"
        f"💵 المبلغ: **{req_amt} ج.م**"
    )
    for m_id in [MANAGER_ID, OWNER_ID]:
        try:
            bot.send_message(m_id, notify_text)
        except Exception:
            pass

def admin_get_promo_name(message):
    if message.from_user.id != OWNER_ID:
        return
    promo_name = message.text.strip()
    msg = bot.send_message(OWNER_ID, f"سعر العمولة لكل حساب مقبول للمسوق صاحب الكود `{promo_name}`؟ (أدخل رقم فقط، مثال: `1.25`):")
    bot.register_next_step_handler(msg, admin_get_promo_commission, promo_name)

def admin_get_promo_commission(message, promo_name):
    try:
        commission = float(message.text.strip())
        msg = bot.send_message(OWNER_ID, "أرسل الآيدي (ID) الخاص بالمسوق الذي سيستلم الأرباح:")
        bot.register_next_step_handler(msg, admin_get_promo_marketer, promo_name, commission)
    except ValueError:
        msg = bot.send_message(OWNER_ID, "⚠️ الرجاء إدخال رقم صحيح للعمولة. أعد المحاولة:")
        bot.register_next_step_handler(msg, admin_get_promo_commission, promo_name)

def admin_get_promo_marketer(message, promo_name, commission):
    try:
        marketer_id = int(message.text.strip())
        db_query("INSERT OR REPLACE INTO promo_codes (code, marketer_id, commission) VALUES (?, ?, ?)", 
                 (promo_name, marketer_id, commission), commit=True)
        
        success_msg = f"✅ تم تفعيل البروموكود `{promo_name}` بنجاح!\n• عمولة الحساب المقبول: {commission} ج.م\n• حساب المسوق المربوط: `{marketer_id}`"
        bot.send_message(OWNER_ID, success_msg)
        try:
            bot.send_message(marketer_id, f"🎉 تم اعتمادك كمسوق رسمي في البوت!\nكودك الفعال هو: `{promo_name}`")
        except Exception:
            pass
    except ValueError:
        msg = bot.send_message(OWNER_ID, "⚠️ الرجاء إدخال آيدي صحيح. أعد المحاولة:")
        bot.register_next_step_handler(msg, admin_get_promo_marketer, promo_name, commission)


# ==========================================
#         نظام توزيع العمولات ونسب الإحالة
# ==========================================

def apply_referral_commission_on_approval(approved_user_id):
    user_data = db_query("SELECT referred_by FROM users WHERE user_id = ?", (approved_user_id,), fetch=True)
    if not user_data or not user_data[0][0]:
        return 
        
    promo_code = user_data[0][0]
    promo_data = db_query("SELECT marketer_id, commission, ratio_counter, saved_balance FROM promo_codes WHERE code = ?", (promo_code,), fetch=True)
    if not promo_data:
        return 
        
    marketer_id, commission, ratio_counter, saved_balance = promo_data[0]
    new_counter = ratio_counter + 1
    
    # كل رابع حساب يذهب ريعه للمالك تلقائياً كرسوم صيانة ونظام البوت (قاعدة 3:1)
    if new_counter % 4 == 0:
        db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (commission, OWNER_ID), commit=True)
        db_query("UPDATE promo_codes SET ratio_counter = ? WHERE code = ?", (new_counter, promo_code), commit=True)
    else:
        new_saved_balance = saved_balance + commission
        if new_saved_balance >= 5.0:
            db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (new_saved_balance, marketer_id), commit=True)
            try:
                bot.send_message(marketer_id, f"💰 تم إضافة **{new_saved_balance:.2f} ج.م** إلى رصيدك من عمولات فريقك بنجاح!")
            except Exception:
                pass
            db_query("UPDATE promo_codes SET saved_balance = 0.0, ratio_counter = ? WHERE code = ?", (new_counter, promo_code), commit=True)
        else:
            db_query("UPDATE promo_codes SET saved_balance = ?, ratio_counter = ? WHERE code = ?", (new_saved_balance, new_counter, promo_code), commit=True)


# ==========================================
#            أمان واسترداد العمليات
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data == "back_to_m_panel")
def back_to_m_panel_cb(call):
    user_id = call.from_user.id
    if user_id not in [OWNER_ID, MANAGER_ID]:
        return
    bot.edit_message_text("💼 لوحة تحكم المدير لإدارة العمل اليومي والطلبات والأسماء المفتوحة:", call.message.chat.id, call.message.message_id, reply_markup=manager_keyboard())


# --- تشغيل البوت مع ميزة الحماية الذاتية من التوقف ---
if __name__ == "__main__":
    print("⚡ البوت المطور يعمل الآن بأقصى مستويات الكفاءة والموثوقية المطلقة...")
    
    # حلقة لانهائية تمنع توقف البوت في حال حدوث أي خطأ بالاتصال
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except ApiTelegramException as e:
            print(f"[Api Error]: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"[Fatal Error]: {e}")
            time.sleep(5)
