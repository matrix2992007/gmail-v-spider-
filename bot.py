hereimport telebot
from telebot import types
import sqlite3
import os
import requests
import hmac
import hashlib
import time

# --- الإعدادات الأساسية والهويات ---
TOKEN = "8019972443:AAGzr9hbVWpcL6cg9seVze_SRg3I3vqxpOc"
OWNER_ID = 7253092491       # الآيدي الخاص بك (يوسف)
MANAGER_ID = 1234567890     # ضع هنا آيدي المدير الخاص بك (عمر)

# --- إعدادات Binance Pay (اختياري لتفعيل الدفع التلقائي بالفيزا/الكريبتو) ---
BINANCE_API_KEY = "YOUR_BINANCE_API_KEY"
BINANCE_SECRET_KEY = "YOUR_BINANCE_SECRET_KEY"

bot = telebot.TeleBot(TOKEN)

# --- إعداد قاعدة البيانات ---
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
            premium_expiry TEXT DEFAULT NULL
        )
    ''')
    
    # جدول المسوقين والبروموكود
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            marketer_id INTEGER,
            commission REAL,
            ratio_counter INTEGER DEFAULT 0,
            saved_balance REAL DEFAULT 0.0
        )
    ''')
    
    # جدول القنوات للاشتراك الإجباري
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_username TEXT
        )
    ''')

    # جدول خطط الاشتراكات المدفوعة
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
    
    # جدول فواتير بايننس المعلقة للدفع التلقائي
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS binance_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            plan_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'PENDING'
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# --- دالة مساعدة لقاعدة البيانات ---
def db_query(query, params=(), fetch=False, commit=False):
    conn = sqlite3.connect("barq_bot.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = None
    if fetch:
        result = cursor.fetchall()
    if commit:
        conn.commit()
    conn.close()
    return result

# --- التحقق من الاشتراك الإجباري في القنوات ---
def is_subscribed(user_id):
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

# --- توليد توقيع أمان لـ Binance Pay API ---
def generate_binance_signature(payload, timestamp, nonce):
    query_string = f"{timestamp}\n{nonce}\n{payload}\n"
    return hmac.new(
        BINANCE_SECRET_KEY.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest().upper()

# --- إنشاء طلب دفع بايننس (Binance Pay Order) ---
def create_binance_payment(user_id, plan_name, price_usdt, plan_id):
    # إذا لم تكن المفاتيح مهيأة، نعتمد الدفع اليدوي
    if "YOUR_" in BINANCE_API_KEY:
        return None, "manual"
        
    url = "https://bpay.binanceapi.com/binancepay/openapi/v2/order"
    timestamp = str(int(time.time() * 1000))
    nonce = os.urandom(16).hex()
    
    merchant_trade_no = f"BARQ_{int(time.time())}_{user_id}"
    
    body = {
        "env": {"terminalType": "APP"},
        "merchantTradeNo": merchant_trade_no,
        "orderAmount": price_usdt,
        "currency": "USDT",
        "goods": {
            "goodsType": "01",
            "goodsCategory": "6000",
            "referenceGoodsId": f"plan_{plan_id}",
            "goodsName": plan_name
        }
    }
    
    import json
    payload = json.dumps(body)
    signature = generate_binance_signature(payload, timestamp, nonce)
    
    headers = {
        "Content-Type": "application/json",
        "BinancePay-Timestamp": timestamp,
        "BinancePay-Nonce": nonce,
        "BinancePay-Certificate-SN": BINANCE_API_KEY,
        "BinancePay-Signature": signature
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload)
        data = response.json()
        if data.get("status") == "SUCCESS":
            prepay_id = data["data"]["prepayId"]
            checkout_url = data["data"]["checkoutUrl"]
            db_query("INSERT INTO binance_orders (order_id, user_id, plan_id, amount) VALUES (?, ?, ?, ?)",
                     (prepay_id, user_id, plan_id, price_usdt), commit=True)
            return checkout_url, prepay_id
    except Exception:
        pass
    return None, "manual"

# --- القوائم ولوحات التحكم ---
def main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📥 تسليم جيميلات", "💰 حسابي")
    
    user = db_query("SELECT has_entered_promo, is_premium FROM users WHERE user_id = ?", (user_id,), fetch=True)
    is_premium = user[0][1] if user else 0
    
    # زر الاشتراك يظهر إذا لم يكن مشتركاً ذهبياً
    if not is_premium:
        markup.row("👑 ترقية الحساب (Premium)")
        
    if user and user[0][0] == 0:
        markup.row("➕ إضافة بروموكود")
        
    markup.row("🤝 كن مسوق بالعمولة", "📞 الدعم الفني")
    
    if user_id == OWNER_ID:
        markup.row("👑 لوحة المالك")
    elif user_id == MANAGER_ID:
        markup.row("💼 لوحة المدير")
    return markup

def owner_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔑 تفعيل بروموكود جديد", callback_data="add_promo"),
        types.InlineKeyboardButton("📢 إضافة قناة اشتراك إجباري", callback_data="add_channel"),
        types.InlineKeyboardButton("❌ إزالة قناة اشتراك إجباري", callback_data="del_channel"),
        types.InlineKeyboardButton("⭐ إضافة باقة اشتراك مميز", callback_data="add_premium_plan"),
        types.InlineKeyboardButton("📊 إحصائيات المسوقين والتحليلات", callback_data="marketers_stats")
    )
    return markup

def manager_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📥 طلبات السحب المعلقة", callback_data="withdrawal_requests"),
        types.InlineKeyboardButton("✅ مراجعة الجيميلات المستلمة", callback_data="review_emails")
    )
    return markup

# --- رسالة الترحيب /start ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "مستخدم"
    
    user_exists = db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if not user_exists:
        db_query("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username), commit=True)
        
    if not is_subscribed(user_id):
        channels = db_query("SELECT channel_username FROM channels", fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for (ch_user,) in channels:
            markup.add(types.InlineKeyboardButton("اضغط هنا للاشتراك 📢", url=f"https://t.me/{ch_user.replace('@', '')}"))
        markup.add(types.InlineKeyboardButton("✅ تم الاشتراك (تأكيد)", callback_data="check_subscription"))
        
        bot.send_message(user_id, "⚠️ عذراً عزيزي، يجب عليك الاشتراك في قنوات الإثباتات أولاً لتتمكن من استخدام البوت:", reply_markup=markup)
        return

    user_data = db_query("SELECT has_entered_promo FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if user_data and user_data[0][0] == 0:
        msg = bot.send_message(user_id, "🔍 إذا كنت تملك بروموكود للمسوق الخاص بك يرجى كتابته الآن.\n\nإذا لم تكن تملك واحداً، أرسل كلمة **تخطي** للبدء مباشرة.")
        bot.register_next_step_handler(msg, process_promo_entry)
    else:
        bot.send_message(user_id, f"أهلاً بك مجدداً يا {message.from_user.first_name} في بوت برق! 👋", reply_markup=main_keyboard(user_id))

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
        bot.send_message(user_id, f"🎉 تم تطبيق البروموكود `{text}` بنجاح في حسابك!", parse_mode="Markdown", reply_markup=main_keyboard(user_id))
    else:
        msg = bot.send_message(user_id, "❌ الرمز الذي أدخلته غير صحيح أو غير متوفر حالياً!\n\nأعد المحاولة بكتابة كود صحيح، أو اكتب **تخطي** للمتابعة.")
        bot.register_next_step_handler(msg, process_promo_entry)

# --- معالجة الأزرار العادية ---
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
            markup.add(types.InlineKeyboardButton(f"اشترك في {name}", callback_data=f"buy_plan_{p_id}"))
            
        bot.send_message(user_id, text_plans, parse_mode="Markdown", reply_markup=markup)

    elif text == "➕ إضافة بروموكود":
        user_data = db_query("SELECT has_entered_promo FROM users WHERE user_id = ?", (user_id,), fetch=True)
        if user_data and user_data[0][0] == 0:
            msg = bot.send_message(user_id, "🔍 أرسل البروموكود الخاص بالمسوق الذي دعاك للبوت الآن:")
            bot.register_next_step_handler(msg, process_promo_entry)
        else:
            bot.send_message(user_id, "⚠️ لقد قمت بإدخال بروموكود مسبقاً في حسابك!")
            
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
        bot.send_message(user_id, f"👤 **معلومات حسابك:**\n\n💵 رصيدك الحالي: **{balance} ج.م**\n⭐ نوع الحساب: {status_text}", parse_mode="Markdown", reply_markup=markup)
        
    elif text == "📥 تسليم جيميلات":
        bot.send_message(user_id, "إجراءات تسليم الجيميلات تتم حالياً بالتنسيق المباشر مع الإدارة لتأمين حساباتك ومراجعتها يدوياً.")
        
    elif text == "👑 لوحة المالك" and user_id == OWNER_ID:
        bot.send_message(user_id, "👑 أهلاً بك يا يوسف في لوحة المالك الحصرية. اختر ما تريد التحكم به:", reply_markup=owner_keyboard())
        
    elif text == "💼 لوحة المدير" and (user_id == MANAGER_ID or user_id == OWNER_ID):
        bot.send_message(user_id, "💼 لوحة تحكم المدير لإدارة العمل اليومي والطلبات المفتوحة:", reply_markup=manager_keyboard())

# --- معالجة الضغط على أزرار لوحات التحكم ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    
    if call.data == "check_subscription":
        if is_subscribed(user_id):
            bot.delete_message(call.message.chat.id, call.message.message_id)
            start(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ لم تشترك في كافة القنوات المطلوبة بعد!", show_alert=True)
            
    elif call.data == "add_promo" and user_id == OWNER_ID:
        msg = bot.send_message(user_id, "✍️ أرسل اسم البروموكود الجديد الذي ترغب في إنشائه (مثال: `TEAM_OMAR`):")
        bot.register_next_step_handler(msg, admin_get_promo_name)
        
    elif call.data == "add_channel" and user_id == OWNER_ID:
        msg = bot.send_message(user_id, "✍️ أرسل معرف القناة مسبوقاً بـ @ (مثال: `@mychannel`):\n*تأكد من رفع البوت كأدمن في القناة أولاً.*")
        bot.register_next_step_handler(msg, admin_get_channel_user)
        
    elif call.data == "del_channel" and user_id == OWNER_ID:
        channels = db_query("SELECT channel_username FROM channels", fetch=True)
        if not channels:
            bot.send_message(user_id, "لا توجد قنوات مضافة حالياً!")
            return
        markup = types.InlineKeyboardMarkup()
        for (user,) in channels:
            markup.add(types.InlineKeyboardButton(f"❌ حذف {user}", callback_data=f"remove_ch_{user}"))
        bot.send_message(user_id, "اختر القناة التي تريد إزالتها من الاشتراك الإجباري:", reply_markup=markup)
        
    elif call.data.startswith("remove_ch_") and user_id == OWNER_ID:
        ch_user = call.data.replace("remove_ch_", "")
        db_query("DELETE FROM channels WHERE channel_username = ?", (ch_user,), commit=True)
        bot.answer_callback_query(call.id, f"تم حذف القناة {ch_user} بنجاح!")
        bot.delete_message(call.message.chat.id, call.message.message_id)

    elif call.data == "marketers_stats" and user_id == OWNER_ID:
        stats = db_query("SELECT code, marketer_id, commission, saved_balance FROM promo_codes", fetch=True)
        if not stats:
            bot.send_message(user_id, "لا يوجد مسوقين مسجلين حالياً.")
            return
        text = "📊 **إحصائيات المسوقين الفعالة:**\n\n"
        for code, m_id, comm, saved in stats:
            text += f"• الكود: `{code}`\n  آيدي المسوق: `{m_id}`\n  العمولة: {comm} ج.م\n  الحصالة المعلقة: {saved}/5 ج.م\n\n"
        bot.send_message(user_id, text, parse_mode="Markdown")

    # --- إضافة باقة اشتراك جديدة (المالك فقط) ---
    elif call.data == "add_premium_plan" and user_id == OWNER_ID:
        msg = bot.send_message(user_id, "✍️ أرسل اسم الباقة الجديدة (مثال: `الباقة الماسية 💎`):")
        bot.register_next_step_handler(msg, admin_get_plan_name)

    # --- شراء باقة مميزة ---
    elif call.data.startswith("buy_plan_"):
        plan_id = int(call.data.replace("buy_plan_", ""))
        plan = db_query("SELECT plan_name, price_egp, price_usdt FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
        if plan:
            name, price_egp, price_usdt = plan[0]
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("💳 دفع فوري تلقائي (Binance Pay)", callback_data=f"pay_binance_{plan_id}"),
                types.InlineKeyboardButton("👤 تحويل يدوي وتأكيد مع الإدارة", callback_data=f"pay_manual_{plan_id}")
            )
            bot.send_message(user_id, f"🛒 **تأكيد شراء {name}:**\n\nيرجى اختيار طريقة الدفع المناسبة لك:\n\n💰 بالجنيه المصري: {price_egp} ج.م\n🪙 بالدولار الرقمي: {price_usdt} USDT", reply_markup=markup, parse_mode="Markdown")

    # --- معالجة الدفع التلقائي عبر بايننس ---
    elif call.data.startswith("pay_binance_"):
        plan_id = int(call.data.replace("pay_binance_", ""))
        plan = db_query("SELECT plan_name, price_usdt FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
        if plan:
            name, price_usdt = plan[0]
            checkout_url, order_info = create_binance_payment(user_id, name, price_usdt, plan_id)
            
            if checkout_url:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🌐 اضغط هنا للدفع الفوري", url=checkout_url))
                markup.add(types.InlineKeyboardButton("🔄 تأكيد الدفع التلقائي", callback_data=f"verify_binance_{order_info}"))
                bot.send_message(user_id, f"💸 تم إنشاء فاتورة دفع بايننس بقيمة **{price_usdt} USDT** بنجاح!\nاضغط على الرابط بالأسفل لإتمام عملية الدفع الفوري ثم اضغط تأكيد.", reply_markup=markup, parse_mode="Markdown")
            else:
                # في حال عدم وجود مفاتيح API، يتم تحويله للدفع اليدوي فوراً
                bot.send_message(user_id, "⚠️ الدفع التلقائي عبر Binance Pay غير مهيأ حالياً، يرجى التواصل مع الإدارة لإتمام العملية يدوياً وإرسال لقطة شاشة التحويل.")

    # --- التحقق التلقائي يدويًا للعميل من نجاح طلب بايننس ---
    elif call.data.startswith("verify_binance_"):
        order_id = call.data.replace("verify_binance_", "")
        # هنا سنقوم بمحاكاة التحقق أو الاتصال بالـ API، ثم تفعيل الاشتراك للعميل
        order = db_query("SELECT user_id, plan_id FROM binance_orders WHERE order_id = ?", (order_id,), fetch=True)
        if order:
            u_id, p_id = order[0]
            plan = db_query("SELECT plan_name, duration_days FROM premium_plans WHERE plan_id = ?", (p_id,), fetch=True)
            name, duration = plan[0]
            
            # تحديث حالة المستخدم في قاعدة البيانات
            expiry_date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + (duration * 86400)))
            db_query("UPDATE users SET is_premium = 1, premium_expiry = ? WHERE user_id = ?", (expiry_date, u_id), commit=True)
            db_query("UPDATE binance_orders SET status = 'SUCCESS' WHERE order_id = ?", (order_id,), commit=True)
            
            bot.answer_callback_query(call.id, "🎉 تم التحقق من نجاح الدفع وتفعيل الاشتراك المميز بنجاح!", show_alert=True)
            bot.send_message(u_id, f"👑 مبروك! تم تفعيل اشتراكك الـ **{name}** بنجاح لمدة {duration} يوم.\nتمتع بكافة الصلاحيات المميزة الآن!", reply_markup=main_keyboard(u_id), parse_mode="Markdown")
            
    # --- الدفع اليدوي ---
    elif call.data.startswith("pay_manual_"):
        plan_id = int(call.data.replace("pay_manual_", ""))
        plan = db_query("SELECT plan_name, price_egp, price_usdt FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
        name, egp, usdt = plan[0]
        msg = bot.send_message(user_id, f"📝 يرجى تحويل مبلغ **{egp} جنيه** أو **{usdt} USDT** وإرسال لقطة شاشة (Screenshot) لعملية التحويل هنا في هذا الشات ليقوم المدير بتفعيل حسابك يدوياً فوراً:")
        bot.register_next_step_handler(msg, handle_manual_receipt_upload, plan_id)

# --- استقبال وتأكيد إثبات التحويل اليدوي من العميل ---
def handle_manual_receipt_upload(message, plan_id):
    user_id = message.from_user.id
    if not message.photo:
        bot.send_message(user_id, "❌ يجب إرسال إثبات التحويل كصورة! يرجى إعادة المحاولة والبدء من جديد.")
        return
        
    photo_id = message.photo[-1].file_id
    plan = db_query("SELECT plan_name FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
    plan_name = plan[0][0]
    
    # تحويل لقطة الشاشة للمدير والمالك فوراً للتفعيل اليدوي
    notify_text = (
        "👑 **طلب ترقية حساب جديد (يدوي):**\n\n"
        f"👤 المستخدم: `{user_id}` (@{message.from_user.username or 'بدون'})\n"
        f"⭐ الباقة المطلوبة: {plan_name}\n"
        "👇 إثبات التحويل بالأسفل:"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ قبول وتفعيل الاشتراك", callback_data=f"approve_manual_{user_id}_{plan_id}"),
        types.InlineKeyboardButton("❌ رفض الطلب", callback_data=f"reject_manual_{user_id}")
    )
    
    # إرسال الصورة والإثبات للمدير
    bot.send_photo(MANAGER_ID, photo_id, caption=notify_text, reply_markup=markup, parse_mode="Markdown")
    # نسخة للمالك
    bot.send_photo(OWNER_ID, photo_id, caption=notify_text, reply_markup=markup, parse_mode="Markdown")
    
    bot.send_message(user_id, "✅ تم إرسال إثبات التحويل الخاص بك للمراجعة، سيتم تفعيل باقتك بمجرد تحقق المدير منها.")

# --- معالجة قبول أو رفض تفعيل الاشتراكات يدوياً ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_manual_") or call.data.startswith("reject_manual_"))
def handle_admin_manual_actions(call):
    # صلاحية المدير أو المالك فقط
    if call.from_user.id not in [OWNER_ID, MANAGER_ID]:
        return
        
    if call.data.startswith("approve_manual_"):
        parts = call.data.split("_")
        target_id = int(parts[2])
        plan_id = int(parts[3])
        
        plan = db_query("SELECT plan_name, duration_days FROM premium_plans WHERE plan_id = ?", (plan_id,), fetch=True)
        name, duration = plan[0]
        
        expiry_date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + (duration * 86400)))
        db_query("UPDATE users SET is_premium = 1, premium_expiry = ? WHERE user_id = ?", (expiry_date, target_id), commit=True)
        
        bot.send_message(target_id, f"👑 تهانينا! تم مراجعة إثبات تحويلك بنجاح، وتم تفعيل الـ **{name}** لحسابك بنجاح!", reply_markup=main_keyboard(target_id), parse_mode="Markdown")
        bot.edit_message_caption("✅ تم قبول طلب الاشتراك وتفعيله بنجاح للمستخدم!", call.message.chat.id, call.message.message_id)
        
    elif call.data.startswith("reject_manual_"):
        target_id = int(call.data.replace("reject_manual_", ""))
        bot.send_message(target_id, "❌ عذراً، لقد تم رفض طلب ترقية حسابك لعدم صحة بيانات التحويل أو لعدم استلام الأموال بعد. تواصل مع الدعم للمزيد.")
        bot.edit_message_caption("❌ تم رفض الطلب وإلغاء المعاملة.", call.message.chat.id, call.message.message_id)

# --- خطوات المالك لإضافة خطة مميزة جديدة ---
def admin_get_plan_name(message):
    plan_name = message.text.strip()
    msg = bot.send_message(OWNER_ID, "أدخل سعر الباقة بالجنيه المصري (EGP):")
    bot.register_next_step_handler(msg, admin_get_plan_price_egp, plan_name)

def admin_get_plan_price_egp(message, plan_name):
    try:
        price_egp = float(message.text.strip())
        msg = bot.send_message(OWNER_ID, "أدخل سعر الباقة بالدولار الرقمي (USDT):")
        bot.register_next_step_handler(msg, admin_get_plan_price_usdt, plan_name, price_egp)
    except ValueError:
        msg = bot.send_message(OWNER_ID, "⚠️ أدخل قيمة رقمية صحيحة:")
        bot.register_next_step_handler(msg, admin_get_plan_price_egp, plan_name)

def admin_get_plan_price_usdt(message, plan_name, price_egp):
    try:
        price_usdt = float(message.text.strip())
        msg = bot.send_message(OWNER_ID, "أدخل مدة الباقة بالأيام (مثال: `30`):")
        bot.register_next_step_handler(msg, admin_get_plan_duration, plan_name, price_egp, price_usdt)
    except ValueError:
        msg = bot.send_message(OWNER_ID, "⚠️ أدخل قيمة رقمية صحيحة:")
        bot.register_next_step_handler(msg, admin_get_plan_price_usdt, plan_name, price_egp)

def admin_get_plan_duration(message, plan_name, price_egp, price_usdt):
    try:
        duration = int(message.text.strip())
        msg = bot.send_message(OWNER_ID, "أدخل وصف ومميزات الباقة بوضوح:")
        bot.register_next_step_handler(msg, admin_save_plan, plan_name, price_egp, price_usdt, duration)
    except ValueError:
        msg = bot.send_message(OWNER_ID, "⚠️ أدخل عدد أيام صحيح (أرقام):")
        bot.register_next_step_handler(msg, admin_get_plan_duration, plan_name, price_egp, price_usdt)

def admin_save_plan(message, plan_name, price_egp, price_usdt, duration):
    description = message.text.strip()
    db_query("INSERT INTO premium_plans (plan_name, price_egp, price_usdt, duration_days, description) VALUES (?, ?, ?, ?, ?)",
             (plan_name, price_egp, price_usdt, duration, description), commit=True)
    bot.send_message(OWNER_ID, f"✅ تم حفظ وإطلاق الباقة المميزة الجديدة `{plan_name}` بنجاح في البوت!", parse_mode="Markdown")

# --- خطوات المالك لإنشاء بروموكود جديد ---
def admin_get_promo_name(message):
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
        
        success_msg = f"✅ تم تفعيل البروموكود `{promo_name}` بنجاح!\n• عمولة الحساب المقبول: {commission} ج.م\n• حساب المسوق المربوط: `{marketer_id}`\n• نظام التصفية النشط: 3:1 (حصالة 5 ج.م)"
        bot.send_message(OWNER_ID, success_msg, parse_mode="Markdown")
        
        try:
            bot.send_message(marketer_id, f"🎉 تم اعتمادك كمسوق رسمي في البوت!\nكودك الفعال هو: `{promo_name}`\nتمنياتنا لك بالتوفيق! 🚀", parse_mode="Markdown")
        except Exception:
            pass
            
    except ValueError:
        msg = bot.send_message(OWNER_ID, "⚠️ الرجاء إدخال آيدي صحيح (أرقام فقط). أعد المحاولة:")
        bot.register_next_step_handler(msg, admin_get_promo_marketer, promo_name, commission)

# --- خطوات المالك لإضافة قناة اشتراك إجباري ---
def admin_get_channel_user(message):
    ch_user = message.text.strip()
    if not ch_user.startswith("@"):
        bot.send_message(OWNER_ID, "❌ المعرف يجب أن يبدأ بـ @. تم إلغاء العملية.")
        return
    try:
        chat = bot.get_chat(ch_user)
        db_query("INSERT OR REPLACE INTO channels (channel_id, channel_username) VALUES (?, ?)", 
                 (str(chat.id), ch_user), commit=True)
        bot.send_message(OWNER_ID, f"✅ تم إضافة القناة {ch_user} بنجاح إلى قائمة الاشتراك الإجباري!")
    except Exception as e:
        bot.send_message(OWNER_ID, f"❌ حدث خطأ! تأكد من أن البوت تم رفعه كأدمن في القناة أولاً.\nالخطأ: {e}")

# --- معالجة طلب سحب الأرباح وقوانين السحب (محافظ، بايننس ID، شبكة) ---
@bot.callback_query_handler(func=lambda call: call.data == "request_withdraw")
def process_withdrawal_request(call):
    user_id = call.from_user.id
    user = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    balance = user[0][0] if user else 0.0
    
    if balance < 5:  
        bot.answer_callback_query(call.id, "❌ رصيدك أقل من الحد الأدنى للسحب!", show_alert=True)
        return
        
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("💳 سحب كاش (فودافون كاش / اتصالات..)", callback_data="w_cash"),
        types.InlineKeyboardButton("🆔 سحب عبر Binance ID (داخلي بدون عمولة)", callback_data="w_binance_id"),
        types.InlineKeyboardButton("🌐 سحب عبر شبكة الكريبتو (USDT / TRC20)", callback_data="w_network"),
        types.InlineKeyboardButton("☎️ سحب رصيد صافي / كروت شحن", callback_data="w_phone_balance")
    )
    
    bot.send_message(user_id, "📋 **يرجى اختيار طريقة السحب المفضلة لديك:**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("w_"))
def handle_withdraw_type(call):
    user_id = call.from_user.id
    w_type = call.data
    
    if w_type == "w_cash":
        msg = bot.send_message(user_id, "✍️ أرسل الآن المبلغ المراد سحبه ورقمه بالتنسيق التالي:\n`المبلغ - رقم المحفظة الكاش`\n\n*(تنويه: الحد الأقصى 100 جنيه، وسيصلك 98 جنيه صافي بعد خصم الـ 2 جنيه عمولة التحويل تلقائياً)*", parse_mode="Markdown")
        bot.register_next_step_handler(msg, save_withdraw_request, "محفظة كاش")
        
    elif w_type == "w_binance_id":
        msg = bot.send_message(user_id, "✍️ أرسل الآن تفاصيل السحب بالتنسيق التالي:\n`المبلغ بالجنيه - Binance ID الخاص بك`\n\n*(سيتم احتساب القيمة بالدولار وسحبها بدون أي عمولات تحويل داخل بايننس)*", parse_mode="Markdown")
        bot.register_next_step_handler(msg, save_withdraw_request, "Binance ID")
        
    elif w_type == "w_network":
        msg = bot.send_message(user_id, "✍️ أرسل الآن تفاصيل السحب بالتنسيق التالي:\n`المبلغ بالجنيه - عنوان المحفظة (USDT-TRC20)`\n\n*(يرجى التأكد التام من صحة عنوان الشبكة المرسل لتفادي فقدان الأموال)*", parse_mode="Markdown")
        bot.register_next_step_handler(msg, save_withdraw_request, "شبكة USDT-TRC20")
        
    elif w_type == "w_phone_balance":
        msg = bot.send_message(user_id, "✍️ أرسل الآن تفاصيل السحب بالتنسيق التالي:\n`المبلغ - رقم الموبايل - نوع الخط (رصيد صافي / كروت شحن)`\n\n*(سيقوم المدير بتأكيد وشحن القيمة لخطك مباشرة)*", parse_mode="Markdown")
        bot.register_next_step_handler(msg, save_withdraw_request, "رصيد خط / كروت")

def save_withdraw_request(message, method_name):
    user_id = message.from_user.id
    details = message.text.strip()
    
    notify_text = (
        "⚠️ **طلب سحب معلق جديد للإدارة:**\n\n"
        f"👤 المستخدم: `{user_id}` (@{message.from_user.username or 'بدون'})\n"
        f"⚙️ طريقة السحب: *{method_name}*\n"
        f"📝 تفاصيل السحب والقيمة: {details}"
    )
    
    try:
        bot.send_message(MANAGER_ID, notify_text, parse_mode="Markdown")
    except Exception:
        pass
    try:
        bot.send_message(OWNER_ID, notify_text, parse_mode="Markdown")
    except Exception:
        pass
        
    bot.send_message(user_id, "✅ تم إرسال طلب السحب الخاص بك بنجاح إلى الإدارة. سيقوم المدير بمراجعته وتحويل الأموال لك فوراً.")

# --- منطق الـ 3:1 التلقائي المخفي وحصالة الـ 5 جنيه التلقائية للمسوقين ---
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
                bot.send_message(marketer_id, f"💰 تم إضافة **{new_saved_balance:.2f} ج.م** إلى رصيدك من عمولات فريقك بنجاح!", parse_mode="Markdown")
            except Exception:
                pass
            db_query("UPDATE promo_codes SET saved_balance = 0.0, ratio_counter = ? WHERE code = ?", (new_counter, promo_code), commit=True)
        else:
            db_query("UPDATE promo_codes SET saved_balance = ?, ratio_counter = ? WHERE code = ?", (new_saved_balance, new_counter, promo_code), commit=True)

# --- بدء تشغيل البوت ---
print("⚡ البوت المطور والذكي يعمل بكامل قوته الآن...")
bot.infinity_polling()
