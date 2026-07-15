import telebot
from telebot import types
import sqlite3
import os
import time
import re
import threading
from datetime import datetime, timedelta

# --- التوكين وإعدادات الآيدي ---
TOKEN = os.getenv("BOT_TOKEN", "8019972443:AAHkHWE_7cFrgdYe8iRDCBHm2Doh9_zfPkg")
OWNER_ID = 7253092491       
MANAGER_ID = 6525167572     

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# --- تهيئة قاعدة البيانات الشاملة ---
def init_db():
    conn = sqlite3.connect("barq_bot.db")
    cursor = conn.cursor()
    
    # جدول المستخدمين
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
    
    # جدول البروموكود (تمت إضافة حقل اسم المسوق المعين يدوياً marketer_name)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            marketer_id INTEGER,
            commission REAL,
            ratio_counter INTEGER DEFAULT 0,
            saved_balance REAL DEFAULT 0.0,
            marketer_name TEXT DEFAULT NULL
        )
    ''')
    
    # جدول القنوات الإجبارية
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_username TEXT
        )
    ''')

    # جدول الجيميلات المستلمة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS submitted_emails (
            email_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            email_address TEXT,
            status TEXT DEFAULT 'PENDING',
            submitted_at TEXT
        )
    ''')

    # جدول طلبات السحب
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            withdraw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            details TEXT,
            status TEXT DEFAULT 'PENDING',
            requested_at TEXT
        )
    ''')
    
    # جدول الإعدادات العامة للبوت
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # قيم افتراضية للإعدادات
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('global_password', 'Barq1234')")
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('gmail_format', 'الاسم المطلوب رقمين @gmail.com')")
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('point_price', '2.5')")
    # الجملة الخاصة بفتح لوحة المسوق (الافتراضية)
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('marketer_secret_phrase', 'افتح لوحة المسوق')")

    # تحديث تلقائي لقاعدة البيانات لإضافة عمود اسم المسوق في حال لم يكن موجوداً
    try:
        cursor.execute("ALTER TABLE promo_codes ADD COLUMN marketer_name TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass  # العمود موجود بالفعل

    conn.commit()
    conn.close()

init_db()

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

# الحصول على الإعدادات الحالية
def get_setting(key):
    res = db_query("SELECT value FROM system_settings WHERE key = ?", (key,), fetch=True)
    return res[0][0] if res else ""

# --- التحقق من الاشتراك الإجبارى ---
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
            return False
    return True

# --- الكيبورد الرئيسي للمستخدم ---
def main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📥 تسليم جيميلات", "💰 حسابي")
    markup.row("➕ إضافة بروموكود")
    markup.row("🤝 كن مسوق بالعمولة", "📞 الدعم الفني")
    if user_id in [OWNER_ID, MANAGER_ID]:
        markup.row("👑 لوحة التحكم والإدارة")
    return markup

# --- لوحة التحكم المتقدمة للادمن ---
def admin_combined_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📥 مراجعة الجيميلات المعلقة", callback_data="review_emails_page_0"),
        types.InlineKeyboardButton("💸 طلبات السحب المعلقة", callback_data="review_withdraws_page_0"),
        types.InlineKeyboardButton("🔑 تعيين كلمة سر موحدة", callback_data="set_global_pass"),
        types.InlineKeyboardButton("📝 تعيين صيغة الجيميل", callback_data="set_gmail_format"),
        types.InlineKeyboardButton("💵 تحديد سعر النقاط/الحساب", callback_data="set_point_price"),
        types.InlineKeyboardButton("✍️ تعيين جملة لوحة المسوق", callback_data="set_secret_phrase"),
        types.InlineKeyboardButton("👤 تعيين اسم للمسوق (يدوي)", callback_data="set_marketer_name"),
        types.InlineKeyboardButton("🔑 إضافة بروموكود جديد", callback_data="add_promo"),
        types.InlineKeyboardButton("📈 إحصائيات المسوقين عمومی", callback_data="marketers_stats"),
        types.InlineKeyboardButton("📢 إذاعة ذكية للأعضاء", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("➕ إضافة قناة إجبارية", callback_data="add_channel"),
        types.InlineKeyboardButton("❌ إزالة قناة إجبارية", callback_data="del_channel")
    )
    return markup

# --- بداية البوت /start ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "مستخدم"
    joined_date = datetime.now().strftime('%Y-%m-%d')
    
    user_exists = db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if not user_exists:
        db_query("INSERT INTO users (user_id, username, joined_date) VALUES (?, ?, ?)", (user_id, username, joined_date), commit=True)
        
    if not is_subscribed(user_id):
        channels = db_query("SELECT channel_username, channel_id FROM channels", fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for ch_user, ch_id in channels:
            markup.add(types.InlineKeyboardButton("اضغط هنا للاشتراك 📢", url=f"https://t.me/{ch_user.replace('@', '')}"))
        markup.add(types.InlineKeyboardButton("✅ تم الاشتراك (تأكيد)", callback_data="check_subscription"))
        bot.send_message(user_id, "⚠️ **يجب الاشتراك في قنوات البوت أولاً للتشغيل:**", reply_markup=markup)
        return

    welcome_text = (
        f"⚡ **أهلاً بك في بوت برق المطور لتسليم الجيميلات!** 🚀\n\n"
        f"⚙️ **الإعدادات الحالية المطلوبة للعمل:**\n"
        f"🔑 كلمة السر الموحدة المطلوبة: `{get_setting('global_password')}`\n"
        f"📝 صيغة الجيميل المطلوب: `{get_setting('gmail_format')}`\n"
        f"💵 سعر الحساب المقبول: **{get_setting('point_price')} ج.م**\n\n"
        "🟢 إستعمل القائمة بالأسفل لبدء العمل وسحب الأرباح!"
    )
    bot.send_message(user_id, welcome_text, reply_markup=main_keyboard(user_id))

# --- نظام تسليم الجيميلات ---
@bot.message_handler(func=lambda message: message.text == "📥 تسليم جيميلات")
def handle_gmail_submission_flow(message):
    user_id = message.from_user.id
    if not is_subscribed(user_id): return
    
    text = (
        "📥 **قسم تسليم الحسابات:**\n\n"
        f"📝 الصيغة المطلوبة: `{get_setting('gmail_format')}`\n"
        f"🔑 كلمة السر الموحدة التي يجب إنشاؤها: `{get_setting('global_password')}`\n\n"
        "✍️ أرسل الحساب الآن مباشرة (البريد فقط):"
    )
    msg = bot.send_message(user_id, text)
    bot.register_next_step_handler(msg, process_submitted_gmail)

def process_submitted_gmail(message):
    user_id = message.from_user.id
    email_address = message.text.strip() if message.text else ""

    if not email_address or "@" not in email_address:
        bot.send_message(user_id, "❌ يرجى إرسال بريد إلكتروني صحيح.")
        return

    submitted_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_query("INSERT INTO submitted_emails (user_id, email_address, submitted_at) VALUES (?, ?, ?)",
             (user_id, email_address, submitted_at), commit=True)

    bot.send_message(user_id, "✅ تم استلام الجيميل وبانتظار مراجعة الإدارة.")
    
    # إشعار الإدارة
    for m_id in [MANAGER_ID, OWNER_ID]:
        try: bot.send_message(m_id, f"📥 **جيميل جديد للمراجعة:**\n👤 مرسل: `{user_id}`\n📧 البريد: `{email_address}`")
        except: pass

# --- أزرار التفاعل Callbacks ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    
    if call.data == "check_subscription":
        if is_subscribed(user_id):
            bot.delete_message(call.message.chat.id, call.message.message_id)
            start(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ لم تشترك في كافة القنوات بعد!", show_alert=True)

    elif call.data.startswith("review_emails_page_"):
        if user_id not in [OWNER_ID, MANAGER_ID]: return
        page = int(call.data.replace("review_emails_page_", ""))
        show_review_emails_page(user_id, call.message.message_id, page)

    elif call.data.startswith("approve_email_"):
        if user_id not in [OWNER_ID, MANAGER_ID]: return
        email_id = int(call.data.replace("approve_email_", ""))
        price = float(get_setting('point_price'))
        
        email_data = db_query("SELECT user_id, email_address FROM submitted_emails WHERE email_id = ?", (email_id,), fetch=True)
        if email_data:
            u_id, email = email_data[0]
            db_query("UPDATE submitted_emails SET status = 'APPROVED' WHERE email_id = ?", (email_id,), commit=True)
            db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, u_id), commit=True)
            apply_referral_commission_on_approval(u_id)
            try: bot.send_message(u_id, f"🎉 تم قبول حسابك `{email}` وإضافة {price} ج.م لرصيدك!")
            except: pass
        bot.answer_callback_query(call.id, "✅ تم القبول بنجاح")
        show_review_emails_page(user_id, call.message.message_id, 0)

    elif call.data.startswith("reject_email_"):
        if user_id not in [OWNER_ID, MANAGER_ID]: return
        email_id = int(call.data.replace("reject_email_", ""))
        db_query("UPDATE submitted_emails SET status = 'REJECTED' WHERE email_id = ?", (email_id,), commit=True)
        bot.answer_callback_query(call.id, "❌ تم الرفض")
        show_review_emails_page(user_id, call.message.message_id, 0)

    # --- لوحة إعدادات المشرفين ---
    elif call.data == "set_global_pass" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "🔑 أرسل كلمة السر الموحدة الجديدة:")
        bot.register_next_step_handler(msg, lambda m: update_setting(m, 'global_password', "تم تحديث كلمة السر الموحدة!"))
        
    elif call.data == "set_gmail_format" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "📝 أرسل صيغة الجيميل المطلوبة الجديدة:")
        bot.register_next_step_handler(msg, lambda m: update_setting(m, 'gmail_format', "تم تحديث صيغة الجيميل بنجاح!"))

    elif call.data == "set_point_price" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "💵 أرسل سعر النقاط الجديد للحساب الواحد:")
        bot.register_next_step_handler(msg, lambda m: update_setting(m, 'point_price', "تم تحديث السعر بنجاح!"))

    elif call.data == "set_secret_phrase" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "✍️ أرسل الجملة السرية الجديدة التي تفتح لوحة المسوق (مثال: `اللوحة السرية`):")
        bot.register_next_step_handler(msg, lambda m: update_setting(m, 'marketer_secret_phrase', "تم تحديث الجملة السرية لفتح لوحة المسوق بنجاح!"))

    # --- ميزة تعيين اسم المسوق يدوياً من الآدمن ---
    elif call.data == "set_marketer_name" and user_id in [OWNER_ID, MANAGER_ID]:
        msg = bot.send_message(user_id, "✍️ أرسل أولاً **آيدي المسوق** (الرقم التعريفي له) لتعيين اسم له:")
        bot.register_next_step_handler(msg, admin_get_marketer_id_for_name)

    # --- معالجة طلبات السحب المتعددة ---
    elif call.data == "request_withdraw":
        user_balance = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
        balance = user_balance[0][0] if user_balance else 0.0
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("💳 سحب كاش (فودافون/اتصالات...)", callback_data="w_cash"))
        
        if balance >= 10:
            markup.add(types.InlineKeyboardButton("☎️ سحب رصيد صافي (فوق 10 ج.م)", callback_data="w_roseed"))
        if balance >= 20:
            markup.add(types.InlineKeyboardButton("🃏 سحب كروت فكة (فوق 20 ج.م)", callback_data="w_fakka"))
        if balance >= 50:
            markup.add(types.InlineKeyboardButton("🎮 سحب شدات ببجي PUBG (فوق 50 ج.م)", callback_data="w_pubg"))
            
        markup.add(types.InlineKeyboardButton("🪙 سحب بايننس Binance", callback_data="w_binance_root"))
        
        bot.edit_message_text(f"📋 **خيارات السحب المتاحة لرصيدك ({balance:.2f} ج.م):**", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data == "w_cash":
        msg = bot.send_message(user_id, "✍️ أرسل: `المبلغ - رقم الكاش - اسم صاحب الرقم`:")
        bot.register_next_step_handler(msg, process_withdraw_save, "سحب كاش")

    elif call.data == "w_roseed":
        msg = bot.send_message(user_id, "✍️ أرسل: `المبلغ - رقم الهاتف والشبكة` لطلب رصيد صافي:")
        bot.register_next_step_handler(msg, process_withdraw_save, "رصيد صافي")

    elif call.data == "w_fakka":
        msg = bot.send_message(user_id, "✍️ أرسل: `المبلغ - تفاصيل كارت الفكة المطلوب`:")
        bot.register_next_step_handler(msg, process_withdraw_save, "كروت فكة")

    elif call.data == "w_pubg":
        msg = bot.send_message(user_id, "✍️ أرسل تفاصيل الشدات بالتنسيق: `الـ ID الخاص بك - الكمية المطلوبة`:")
        bot.register_next_step_handler(msg, process_withdraw_save, "شدات ببجي")

    elif call.data == "w_binance_root":
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🌐 سحب عن طريق شبكة (Network)", callback_data="w_binance_net"),
            types.InlineKeyboardButton("🆔 سحب عن طريق ID البايننس", callback_data="w_binance_id")
        )
        bot.edit_message_text("⚙️ اختر نوع سحب بايننس المفضل:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data == "w_binance_net":
        msg = bot.send_message(user_id, "✍️ أرسل: `المبلغ - عنوان المحفظة - نوع الشبكة`:")
        bot.register_next_step_handler(msg, process_withdraw_save, "بايننس (شبكة)")

    elif call.data == "w_binance_id":
        msg = bot.send_message(user_id, "✍️ أرسل: `المبلغ - Binance ID` الخاص بك:")
        bot.register_next_step_handler(msg, process_withdraw_save, "بايننس (ID)")

    elif call.data.startswith("review_withdraws_page_"):
        if user_id not in [OWNER_ID, MANAGER_ID]: return
        page = int(call.data.replace("review_withdraws_page_", ""))
        show_review_withdraws_page(user_id, call.message.message_id, page)

    elif call.data.startswith("complete_w_"):
        if user_id not in [OWNER_ID, MANAGER_ID]: return
        w_id = int(call.data.replace("complete_w_", ""))
        db_query("UPDATE withdrawal_requests SET status = 'COMPLETED' WHERE withdraw_id = ?", (w_id,), commit=True)
        bot.answer_callback_query(call.id, "✅ تم إكمال السحب")
        show_review_withdraws_page(user_id, call.message.message_id, 0)

    elif call.data == "add_promo" and user_id == OWNER_ID:
        msg = bot.send_message(OWNER_ID, "✍️ أرسل اسم كود البروموكود الجديد:")
        bot.register_next_step_handler(msg, admin_get_promo_name)

def update_setting(message, key, success_msg):
    if message.from_user.id not in [OWNER_ID, MANAGER_ID]: return
    val = message.text.strip()
    db_query("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, val), commit=True)
    bot.send_message(message.from_user.id, f"✅ {success_msg}")

# --- خطوات تعيين اسم المسوق يدوياً ---
def admin_get_marketer_id_for_name(message):
    if message.from_user.id not in [OWNER_ID, MANAGER_ID]: return
    m_id_str = message.text.strip()
    if not m_id_str.isdigit():
        bot.send_message(message.from_user.id, "❌ يرجى كتابة آيدي صحيح (أرقام فقط).")
        return
    
    m_id = int(m_id_str)
    # التحقق من وجوده كمسوق في قاعدة البيانات أولاً
    promo = db_query("SELECT code FROM promo_codes WHERE marketer_id = ?", (m_id,), fetch=True)
    if not promo:
        bot.send_message(message.from_user.id, f"⚠️ لا يوجد كود بروموكود مسجل لهذا الآيدي `{m_id}` في البوت!")
        return
        
    msg = bot.send_message(message.from_user.id, f"👤 المسوق لديه كود `{promo[0][0]}`.\n✍️ أرسل الآن **الاسم المطلوب تعيينه له**:")
    bot.register_next_step_handler(msg, save_marketer_name_db, m_id)

def save_marketer_name_db(message, m_id):
    if message.from_user.id not in [OWNER_ID, MANAGER_ID]: return
    m_name = message.text.strip()
    if not m_name:
        bot.send_message(message.from_user.id, "❌ الاسم لا يمكن أن يكون فارغاً.")
        return
        
    db_query("UPDATE promo_codes SET marketer_name = ? WHERE marketer_id = ?", (m_name, m_id), commit=True)
    bot.send_message(message.from_user.id, f"✅ تم تعيين اسم المسوق بنجاح!\n👤 الآيدي: `{m_id}`\n🏷️ الاسم: `{m_name}`")

# --- معالجة وحفظ طلب السحب المالي ---
def process_withdraw_save(message, method):
    user_id = message.from_user.id
    details = message.text.strip() if message.text else ""
    
    if not details:
        bot.send_message(user_id, "❌ إدخال فارغ، تم الإلغاء.")
        return
        
    user_balance = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    balance = user_balance[0][0] if user_balance else 0.0
    
    try: req_amt = float(details.split("-")[0].strip())
    except: req_amt = balance

    if req_amt > balance or balance <= 0:
        bot.send_message(user_id, "❌ رصيدك الحالي لا يكفي لإتمام هذه العملية.")
        return

    db_query("UPDATE users SET balance = balance - ? WHERE user_id = ?", (req_amt, user_id), commit=True)
    requested_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    db_query("INSERT INTO withdrawal_requests (user_id, amount, method, details, requested_at) VALUES (?, ?, ?, ?, ?)",
             (user_id, req_amt, method, details, requested_at), commit=True)
             
    bot.send_message(user_id, f"✅ تم تقديم طلب السحب الخاص بك ({method}) بنجاح للمراجعة الإدارية.")

# --- لوحة مراجعة الجيميلات ---
def show_review_emails_page(admin_id, message_id, page=0):
    pending = db_query("SELECT email_id, user_id, email_address, submitted_at FROM submitted_emails WHERE status = 'PENDING' LIMIT 1 OFFSET ?", (page,), fetch=True)
    total = db_query("SELECT COUNT(*) FROM submitted_emails WHERE status = 'PENDING'", fetch=True)[0][0]
    
    if not pending:
        bot.edit_message_text("📥 لا توجد حسابات معلقة حالياً.", admin_id, message_id)
        return
        
    email_id, u_id, email, s_time = pending[0]
    text = f"📥 **مراجعة حساب (صفحة {page+1} من {total}):**\n\n👤 مرسل: `{u_id}`\n📧 البريد: `{email}`\n📅 الوقت: `{s_time}`"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ قبول", callback_data=f"approve_email_{email_id}"),
        types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_email_{email_id}")
    )
    bot.edit_message_text(text, admin_id, message_id, reply_markup=markup)

# --- لوحة مراجعة السحوبات ---
def show_review_withdraws_page(admin_id, message_id, page=0):
    pending = db_query("SELECT withdraw_id, user_id, amount, method, details FROM withdrawal_requests WHERE status = 'PENDING' LIMIT 1 OFFSET ?", (page,), fetch=True)
    total = db_query("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'PENDING'", fetch=True)[0][0]
    
    if not pending:
        bot.edit_message_text("💸 لا توجد طلبات سحب معلقة.", admin_id, message_id)
        return
        
    w_id, u_id, amount, method, details = pending[0]
    text = f"💸 **طلب سحب معلق (صفحة {page+1} من {total}):**\n\n👤 المستخدم: `{u_id}`\n💵 المبلغ: **{amount} ج.م**\n⚙️ النوع: {method}\n📝 التفاصيل: `{details}`"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ تم التحويل بنجاح", callback_data=f"complete_w_{w_id}"))
    bot.edit_message_text(text, admin_id, message_id, reply_markup=markup)

# --- معالجة جميع الرسائل النصية الواردة والتحقق من الجملة السرية واسم المسوق ---
@bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if not is_subscribed(user_id): return

    secret_phrase = get_setting('marketer_secret_phrase')

    # الخطوة الأولى: إرسال الجملة السرية في الشات
    if text == secret_phrase:
        # التحقق إذا كان مسوقاً مسجلاً في النظام أم لا
        promo_data = db_query("SELECT code, marketer_name FROM promo_codes WHERE marketer_id = ?", (user_id,), fetch=True)
        if not promo_data:
            bot.send_message(user_id, "⚠️ عذراً، أنت لست مسجلاً كمسوق بالعمولة لدينا لتتمكن من فتح اللوحة.")
            return
            
        # طلب إدخال اسم صاحب البروموكود المخصص له من الإدارة
        msg = bot.send_message(user_id, "🔐 **الجملة السرية صحيحة!**\n\n✍️ يرجى كتابة **اسم صاحب البروموكود** لتأكيد هويتك وفتح اللوحة:")
        bot.register_next_step_handler(msg, verify_marketer_name_step, promo_data[0][0], promo_data[0][1])
        return

    # باقي الأزرار والقوائم التقليدية
    if text == "➕ إضافة بروموكود":
        count_approved = db_query("SELECT COUNT(*) FROM submitted_emails WHERE user_id = ? AND status = 'APPROVED'", (user_id,), fetch=True)[0][0]
        
        if count_approved >= 5:
            msg = bot.send_message(user_id, "🔍 أرسل البروموكود المتاح لديك الآن لتفعيله:")
            bot.register_next_step_handler(msg, process_promo_entry)
        else:
            bot.send_message(
                user_id,
                f"⚠️ **عذراً، هذا الخيار مقفل حالياً!**\n\n"
                f"📊 عدد حساباتك المقبولة حالياً: ({count_approved}/5 حسابات).\n"
                f"يجب تسليم 5 جيميلات على الأقل ليفتح الزر تلقائياً، أو تواصل مع الإدارة مباشرة للتفعيل: @VIR_XT"
            )

    elif text == "💰 حسابي":
        user = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
        balance = user[0][0] if user else 0.0
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 طلب سحب أرباحي", callback_data="request_withdraw"))
        bot.send_message(user_id, f"👤 **حسابك المالي الحالي:**\n\n💵 الرصيد المتاح: **{balance:.2f} ج.م**", reply_markup=markup)

    elif text == "🤝 كن مسوق بالعمولة":
        bot.send_message(user_id, "🤝 لتصبح مسوقاً وتملك بروموكود خاص بك، يرجى مراسلة المالك لإنشاء كود لك: @VIR_XT")

    elif text == "📞 الدعم الفني":
        bot.send_message(user_id, "📞 للتواصل مع الدعم:\n👑 المالك: @VIR_XT\n⚙️ المشرف: @Omar_7874")

    elif text == "👑 لوحة التحكم والإدارة" and user_id in [OWNER_ID, MANAGER_ID]:
        bot.send_message(user_id, "👑 أهلاً بك في لوحة الإدارة المحدثة:", reply_markup=admin_combined_keyboard())

# الخطوة الثانية: التحقق من اسم المسوق المكتوب
def verify_marketer_name_step(message, promo_code, registered_name):
    user_id = message.from_user.id
    input_name = message.text.strip()
    
    # التحقق من تطابق الاسم المدخل مع الاسم المسجل (تجاهل الفروقات البسيطة في المسافات)
    if not registered_name or input_name.lower() != registered_name.lower():
        bot.send_message(user_id, "❌ الاسم غير صحيح! لا يمكن فتح لوحة المسوق.")
        return

    # إذا تطابق الاسم تماماً، تفتح لوحة المسوق الخاص به
    referred_users = db_query("SELECT user_id, username, balance FROM users WHERE referred_by = ?", (promo_code,), fetch=True)
    
    panel_text = (
        f"🔓 **تم التحقق بنجاح!**\n"
        f"📊 **لوحة التحكم الخاصة بالمسوق: `{registered_name}`**\n"
        f"🔑 الكود الخاص بك: `{promo_code}`\n\n"
        f"👥 إجمالي عدد المسجلين من خلالك: **{len(referred_users)} مستخدم**\n\n"
        "📋 **تفاصيل ورصيد المستخدمين المسجلين عن طريقك:**\n"
    )
    
    if referred_users:
        for u_id, uname, bal in referred_users:
            panel_text += f"• الآيدي: `{u_id}` | اليوزر: @{uname} | الرصيد الحالي: **{bal:.2f} ج.م**\n"
    else:
        panel_text += "_لا يوجد مستخدمين مسجلين حتى الآن._"
        
    bot.send_message(user_id, panel_text)

def process_promo_entry(message):
    user_id = message.from_user.id
    text = message.text.strip()
    promo = db_query("SELECT code FROM promo_codes WHERE code = ?", (text,), fetch=True)
    if promo:
        db_query("UPDATE users SET referred_by = ?, has_entered_promo = 1 WHERE user_id = ?", (text, user_id), commit=True)
        bot.send_message(user_id, f"🎉 تم تفعيل البروموكود `{text}` لحسابك!")
    else:
        bot.send_message(user_id, "❌ الرمز غير صحيح.")

def admin_get_promo_name(message):
    if message.from_user.id != OWNER_ID: return
    p_name = message.text.strip()
    msg = bot.send_message(OWNER_ID, "أدخل آيدي المسوق صاحب هذا الكود:")
    bot.register_next_step_handler(msg, lambda m: save_promo_db(m, p_name))

def save_promo_db(message, p_name):
    m_id = int(message.text.strip())
    # حفظ الكود مع إعطاء اسم مبدئي "غير محدد" للمسوق لكي يقوم الأدمن بتعيينه لاحقاً بشكل كامل
    db_query("INSERT INTO promo_codes (code, marketer_id, commission, marketer_name) VALUES (?, ?, 1.0, 'غير محدد')", (p_name, m_id), commit=True)
    bot.send_message(OWNER_ID, f"✅ تم تفعيل الكود `{p_name}` للمسوق `{m_id}`.\n💡 يرجى استخدام زر تعيين اسم المسوق الآن لتعيين اسمه اليدوي.")

def apply_referral_commission_on_approval(user_id):
    user_info = db_query("SELECT referred_by FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if user_info and user_info[0][0]:
        code = user_info[0][0]
        promo = db_query("SELECT marketer_id, commission FROM promo_codes WHERE code = ?", (code,), fetch=True)
        if promo:
            m_id, comm = promo[0]
            db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (comm, m_id), commit=True)

if __name__ == '__main__':
    print("⚡ البوت يعمل بكافة التحديثات والشروط الجديدة بنجاح...")
    bot.polling(none_stop=True)
