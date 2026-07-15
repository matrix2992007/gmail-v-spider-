import telebot
from telebot import types
import sqlite3
import os
import requests
import hmac
import hashlib
import time
import re
import threading

# --- الإعدادات الأساسية والهويات ---
TOKEN = "8019972443:AAEUjxmmdd88uBm90ar1Xpu19q6qxAVEUiA"
OWNER_ID = 7253092491       # الآيدي الخاص بك (يوسف)
MANAGER_ID = 1234567890     # ضع هنا آيدي المدير الخاص بك (عمر)

# --- إعدادات Binance Pay (اختياري) ---
BINANCE_API_KEY = "YOUR_BINANCE_API_KEY"
BINANCE_SECRET_KEY = "YOUR_BINANCE_SECRET_KEY"

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
            status TEXT DEFAULT 'PENDING'
        )
    ''')

    # 6. جدول الأسماء المرفوعة من الإدارة لتسمية الجيميلات (النظام الجديد)
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
            if member.status in ['left', 'kicked']:
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
        types.InlineKeyboardButton("⭐ إضافة باقة Premium", callback_data="add_premium_plan")
    )
    return markup

# --- كيبورد لوحة المدير (متاحة لعمر ويوسف ويشمل إدارة الأسماء والجيميلات) ---
def manager_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📥 مراجعة الجيميلات المعلقة", callback_data="review_emails"),
        types.InlineKeyboardButton("💸 طلبات السحب المعلقة", callback_data="withdrawal_requests")
    )
    # إضافة أزرار التحكم في الأسماء هنا ليعمل عليها المدير (عمر) أيضاً
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
#      نظام تسليم الجيميلات بناءً على الأسماء
# ==========================================

@bot.message_handler(func=lambda message: message.text == "📥 تسليم جيميلات")
def handle_gmail_submission_flow(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id):
        start(message)
        return

    # أولاً: التحقق مما إذا كان لدى المستخدم اسم محجوز مسبقاً ولم يسلمه بعد
    existing_reservation = db_query(
        "SELECT name_id, name_text FROM gmail_names WHERE reserved_by = ? AND status = 'RESERVED'", 
        (user_id,), fetch=True
    )

    if existing_reservation:
        name_id, name_text = existing_reservation[0]
        send_reservation_instructions(user_id, name_id, name_text)
    else:
        # ثانياً: البحث عن اسم متاح وحجزه للمستخدم فوراً
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
        # حجز الاسم في الداتابيز لمدة معينة (أو لحين الإرسال)
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
        "2. يجب أن يكون الحساب مؤمن ببريد بديل.\n\n"
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

    # التحقق من أن المستخدم لم يضغط على إلغاء الحجز من أزرار إنلاين قبل كتابة الرسالة
    check_status = db_query("SELECT status FROM gmail_names WHERE name_id = ?", (name_id,), fetch=True)
    if not check_status or check_status[0][0] != 'RESERVED':
        return # الحجز تم إلغاؤه بالفعل

    if not text:
        msg = bot.send_message(user_id, "❌ الرجاء إدخال نص صحيح يحتوي على بيانات الجيميل:")
        bot.register_next_step_handler(msg, process_submitted_gmail, name_id, name_text)
        return

    # فحص Regex ذكي للتأكد من الهيكل العام (email | pass | recovery)
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

    # فرز البيانات بنجاح
    parts = [p.strip() for p in text.split("|")]
    email_address, email_password, recovery_email = parts[0], parts[1], parts[2]

    # تخزين الجيميل المقدم في قاعدة البيانات بحالة معلقة
    submitted_at = time.strftime('%Y-%m-%d %H:%M:%S')
    db_query(
        "INSERT INTO submitted_emails (user_id, name_id, email_address, email_password, recovery_email, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name_id, email_address, email_password, recovery_email, submitted_at), commit=True
    )

    # تحديث حالة الاسم المحجوز إلى مستخدم بانتظار المراجعة
    db_query("UPDATE gmail_names SET status = 'USED' WHERE name_id = ?", (name_id,), commit=True)

    # إرسال تأكيد للمستخدم
    bot.send_message(
        user_id, 
        "✅ **تم استلام الجيميل بنجاح وجاري إرساله للمدير للفحص والمراجعة!**\n"
        "سيتم إشعارك فوراً بالنتيجة وإضافة القيمة المالية لحسابك بمجرد قبوله.",
        reply_markup=main_keyboard(user_id)
    )

    # إرسال إشعار فوري للمدير والمالك
    notify_text = (
        "📥 **جيميل جديد قيد الانتظار لمراجعته:**\n\n"
        f"👤 مرسل بواسطة: `{user_id}`\n"
        f"🏷️ الاسم المحجوز: `{name_text}`\n"
        f"📧 البريد: `{email_address}`\n"
        f"🔑 كلمة المرور: `{email_password}`\n"
        f"🔄 البريد البديل: `{recovery_email}`"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    # الحصول على الآيدي التلقائي للجيميل المخزن لتسهيل المعالجة
    last_id_row = db_query("SELECT last_insert_rowid()", fetch=True)
    gmail_db_id = last_id_row[0][0] if last_id_row else 1
    
    markup.add(
        types.InlineKeyboardButton("✅ قبول وتحديد السعر", callback_data=f"approve_gmail_{gmail_db_id}"),
        types.InlineKeyboardButton("❌ رفض وتحديد السبب", callback_data=f"reject_gmail_{gmail_db_id}")
    )
    try:
        bot.send_message(MANAGER_ID, notify_text, reply_markup=markup)
    except Exception:
        pass
    try:
        bot.send_message(OWNER_ID, notify_text, reply_markup=markup)
    except Exception:
        pass


# ==========================================
#     معالجة الضغط على أزرار لوحات التحكم (Inline)
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    
    # 1. إلغاء حجز الاسم من المستخدم
    if call.data.startswith("cancel_reserve_"):
        name_id = int(call.data.replace("cancel_reserve_", ""))
        # إلغاء الحجز في الداتابيز وإرجاعه متاحاً للآخرين
        db_query("UPDATE gmail_names SET status = 'AVAILABLE', reserved_by = NULL, reserved_at = 0 WHERE name_id = ?", (name_id,), commit=True)
        bot.answer_callback_query(call.id, "❌ تم إلغاء حجز الاسم بنجاح وصار متاحاً لغيرك.", show_alert=True)
        bot.edit_message_text("❌ تم إلغاء هذه العملية بنجاح.", call.message.chat.id, call.message.message_id)

    # 2. تأكيد الاشتراك الإجباري
    elif call.data == "check_subscription":
        if is_subscribed(user_id):
            bot.delete_message(call.message.chat.id, call.message.message_id)
            start(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ لم تشترك في كافة القنوات المطلوبة بعد!", show_alert=True)

    # 3. قبول الجيميل المسلم (المدراء فقط)
    elif call.data.startswith("approve_gmail_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        gmail_id = int(call.data.replace("approve_gmail_", ""))
        # نطلب من المدير إدخل سعر مقبول للجيميل لإضافته لرصيد العضو
        msg = bot.send_message(user_id, "✍️ أرسل الآن القيمة المالية بالجنيه المصري لإضافتها لحساب المستخدم (مثال: `2.5`):")
        bot.register_next_step_handler(msg, manager_approve_gmail_step, gmail_id, call.message.message_id)

    # 4. رفض الجيميل المسلم (المدراء فقط)
    elif call.data.startswith("reject_gmail_"):
        if user_id not in [OWNER_ID, MANAGER_ID]:
            return
        gmail_id = int(call.data.replace("reject_gmail_", ""))
        msg = bot.send_message(user_id, "✍️ أرسل الآن سبب الرفض بوضوح ليصل للمستخدم (مثال: `كلمة المرور غير صحيحة`):")
        bot.register_next_step_handler(msg, manager_reject_gmail_step, gmail_id, call.message.message_id)

    # 5. طلب سحب الأرباح للمسوقين والأعضاء
    elif call.data == "request_withdraw":
        user_balance = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
        balance = user_balance[0][0] if user_balance else 0.0
        
        if balance < 5:  
            bot.answer_callback_query(call.id, "❌ رصيدك أقل من الحد الأدنى للسحب وهو 5 جنيه!", show_alert=True)
            return
            
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("💳 سحب كاش (فودافون كاش / اتصالات..)", callback_data="w_cash"),
            types.InlineKeyboardButton("🆔 سحب عبر Binance ID (بدون عمولة)", callback_data="w_binance_id"),
            types.InlineKeyboardButton("🌐 سحب عبر شبكة الكريبتو (USDT / TRC20)", callback_data="w_network"),
            types.InlineKeyboardButton("☎️ سحب رصيد صافي / كروت شحن", callback_data="w_phone_balance")
        )
        bot.edit_message_text("📋 **يرجى اختيار طريقة السحب المفضلة لديك:**", call.message.chat.id, call.message.message_id, reply_markup=markup)

    # 6. تفريغ إدخال السحب
    elif call.data.startswith("w_"):
        w_type = call.data
        if w_type == "w_cash":
            msg = bot.send_message(user_id, "✍️ أرسل الآن المبلغ المراد سحبه ورقمه بالتنسيق التالي:\n`المبلغ - رقم المحفظة الكاش`\n\n*(تنويه: خصم الـ 2 جنيه عمولة التحويل تلقائياً)*")
            bot.register_next_step_handler(msg, save_withdraw_request, "محفظة كاش")
        elif w_type == "w_binance_id":
            msg = bot.send_message(user_id, "✍️ أرسل تفاصيل السحب بالتنسيق التالي:\n`المبلغ بالجنيه - Binance ID` *(سحب بدون عمولة)*")
            bot.register_next_step_handler(msg, save_withdraw_request, "Binance ID")
        elif w_type == "w_network":
            msg = bot.send_message(user_id, "✍️ أرسل تفاصيل السحب بالتنسيق التالي:\n`المبلغ بالجنيه - عنوان المحفظة (USDT-TRC20)`")
            bot.register_next_step_handler(msg, save_withdraw_request, "USDT-TRC20 Network")
        elif w_type == "w_phone_balance":
            msg = bot.send_message(user_id, "✍️ أرسل تفاصيل السحب بالتنسيق التالي:\n`المبلغ - رقم الموبايل - نوع خطك (رصيد/كروت)`")
            bot.register_next_step_handler(msg, save_withdraw_request, "شحن رصيد/كروت")

    # 7. إدارة الأسماء (متاحة للمالك عمر والمدير يوسف)
    elif call.data == "admin_add_names" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "✍️ **يرجى إرسال الأسماء المطلوبة** (كل اسم في سطر منفصل):\nمثال:\n`Youssef Ahmed`\n`Mostafa Salem`\n`Omar Kamel`")
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

    # 8. إدارة المالك الحصرية للميزات الإضافية (يوسف فقط)
    elif call.data == "admin_broadcast" and user_id == OWNER_ID:
        msg = bot.send_message(OWNER_ID, "📢 **أرسل نص الإذاعة التي تريد توجيهها للـ 6,600 مستخدم:**\n*(يمكنك استخدام الماركدوان)*")
        bot.register_next_step_handler(msg, admin_process_broadcast)

    elif call.data == "add_promo" and user_id == OWNER_ID:
        msg = bot.send_message(OWNER_ID, "✍️ أرسل اسم البروموكود الجديد الذي ترغب في إنشائه (مثال: `TEAM_OMAR`):")
        bot.register_next_step_handler(msg, admin_get_promo_name)

    elif call.data == "marketers_stats" and user_id == OWNER_ID:
        stats = db_query("SELECT code, marketer_id, commission, saved_balance FROM promo_codes", fetch=True)
        if not stats:
            bot.send_message(OWNER_ID, "لا يوجد مسوقين مسجلين حالياً.")
            return
        text = "📊 **إحصائيات المسوقين الفعالة:**\n\n"
        for code, m_id, comm, saved in stats:
            text += f"• الكود: `{code}`\n  آيدي المسوق: `{m_id}`\n  العمولة: {comm} ج.م\n  الحصالة المعلقة: {saved}/5 ج.م\n\n"
        bot.send_message(OWNER_ID, text)


# ==========================================
#     تأكيد وقبول ورفض الجيميلات من المدراء
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

    # جلب تفاصيل الجيميل والاسم
    gmail_data = db_query(
        "SELECT user_id, name_id, email_address FROM submitted_emails WHERE email_id = ?", 
        (gmail_id,), fetch=True
    )
    if not gmail_data:
        bot.send_message(message.from_user.id, "❌ خطأ: لم يتم العثور على هذا الجيميل.")
        return

    user_id, name_id, email_address = gmail_data[0]

    # تحديث البيانات وقبول الجيميل
    db_query(
        "UPDATE submitted_emails SET status = 'APPROVED', price_assigned = ? WHERE email_id = ?", 
        (price, gmail_id), commit=True
    )
    # تحديث رصيد المستخدم
    db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id), commit=True)
    # مسح الاسم نهائياً من قائمة الأسماء المتاحة
    db_query("DELETE FROM gmail_names WHERE name_id = ?", (name_id,), commit=True)

    # تشغيل نظام عمولة المسوقين 3:1 المخفي تلقائياً
    apply_referral_commission_on_approval(user_id)

    # إخطار العضو بنجاح عملية القبول
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
    try:
        bot.edit_message_caption("✅ تم قبول هذا الحساب بنجاح وإضافة الرصيد للعضو.", message.chat.id, original_msg_id)
    except Exception:
        pass

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

    # تحديث حالة الجيميل وتوثيق الرفض
    db_query(
        "UPDATE submitted_emails SET status = 'REJECTED', rejection_reason = ? WHERE email_id = ?", 
        (reason, gmail_id), commit=True
    )
    # إعادة الاسم المحجوز ليكون متاحاً للبقية لأنه لم يستغل بشكل صحيح
    db_query(
        "UPDATE gmail_names SET status = 'AVAILABLE', reserved_by = NULL, reserved_at = 0 WHERE name_id = ?", 
        (name_id,), commit=True
    )

    # إخطار العضو بالرفض والسبب للتعلم من الخطأ
    try:
        bot.send_message(
            user_id, 
            f"❌ **عذراً، تم رفض الجيميل المقدم من قبلك:**\n"
            f"📧 البريد: `{email_address}`\n"
            f"⚠️ سبب الرفض: *{reason}*\n\n"
            f"💡 لقد قمنا بإعادة إتاحة الاسم المخصص لك مجدداً، يرجى إنشاء الحساب بشكل صحيح وإعادة تسليمه."
        )
    except Exception:
        pass

    bot.send_message(message.from_user.id, "❌ تم رفض الحساب بنجاح وإرسال الإشعار والسبب للمستخدم.")
    try:
        bot.edit_message_caption(f"❌ تم رفض هذا الحساب بسبب: {reason}", message.chat.id, original_msg_id)
    except Exception:
        pass


# ==========================================
#          تسيير شؤون المالك والمدراء
# ==========================================

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
    bot.send_message(OWNER_ID, "🚀 **بدأت عملية الإرسال الذكية في الخلفية الآن!**\nسأعلمك فور اكتمال الإرسال لكافة الأعضاء النشطين.")

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
            time.sleep(0.05) 
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
            bot.send_message(user_id, "⚠️ لا تتوفر خطط اشتراك مميزة مضافة حالياً من قبل الإدارة.")
            return
            
        text_plans = "👑 **باقات الاشتراك المميز المتاحة لك:**\n\n"
        markup = types.InlineKeyboardMarkup()
        for p_id, name, price_egp, price_usdt, duration, desc in plans:
            text_plans += f"⭐ **{name}** ({duration} يوم)\n• السعر: {price_egp} جنيه / {price_usdt} USDT\n• المميزات: {desc}\n\n"
            markup.add(types.InlineKeyboardButton(f"اشترك in {name}", callback_data=f"buy_plan_{p_id}"))
        bot.send_message(user_id, text_plans, reply_markup=markup)

    elif text == "➕ إضافة بروموكود":
        user_data = db_query("SELECT has_entered_promo FROM users WHERE user_id = ?", (user_id,), fetch=True)
        if user_data and user_data[0][0] == 0:
            msg = bot.send_message(user_id, "🔍 أرسل البروموكود الخاص بالمسوق الذي دعاك للبوت الآن:")
            bot.register_next_step_handler(msg, process_promo_entry)
        else:
            bot.send_message(user_id, "⚠️ لقد قمت بإدخل بروموكود مسبقاً في حسابك!")
            
    elif text == "🤝 كن مسوق بالعمولة":
        bot.send_message(user_id, "للانضمام إلى فريق المسوقين والحصول على كود إحالة مخصص لك، يرجى التواصل مباشرة مع المالك:\n\n👤 مطور البوت والمالك: @VIR_XT")
        
    elif text == "📞 الدعم الفني":
        bot.send_message(user_id, "لأية استفسارات أو مشاكل تخص العمل أو التحويلات، تواصل معنا هنا:\n\n👤 الدعم الفني: @VIR_XT")
        
    elif text == "💰 حسابي":
        user = db_query("SELECT balance, is_premium, premium_expiry FROM users WHERE user_id = ?", (user_id,), fetch=True)
        balance, is_prem, expiry = user[0] if user else (0.0, 0, None)
        status_text = "حساب عادي 🔘" if not is_prem else f"حساب مميز 👑 (ينتهي: {expiry})"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 سحب أرباحي", callback_data="request_withdraw"))
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
    
    notify_text = (
        "⚠️ **طلب سحب معلق جديد للإدارة:**\n\n"
        f"👤 المستخدم: `{user_id}`\n"
        f"⚙️ طريقة السحب: *{method_name}*\n"
        f"📝 تفاصيل السحب والقيمة: {details}"
    )
    
    try:
        bot.send_message(MANAGER_ID, notify_text)
    except Exception:
        pass
    try:
        bot.send_message(OWNER_ID, notify_text)
    except Exception:
        pass
        
    bot.send_message(user_id, "✅ تم إرسال طلب السحب الخاص بك بنجاح إلى الإدارة. سيتم تحويل الأموال لك فوراً.")

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

# --- تشغيل البوت ---
print("⚡ البوت الفورتيكة المطور يعمل الآن بكفاءة مطلقة...")
bot.infinity_polling()
