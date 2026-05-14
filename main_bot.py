import telebot
from telebot import types
import json, os, requests, time, threading
from datetime import datetime
import math

# ========== ДИАГНОСТИКА ОКРУЖЕНИЯ ==========
print("=" * 50)
print("ЗАПУСК ОСНОВНОГО БОТА (ВЕРСИЯ С НОВЫМИ ТОВАРАМИ)")
print("Переменные окружения, которые ВИДИТ контейнер:")
for key in os.environ.keys():
    if "TOKEN" in key or "ID" in key:
        val = os.environ[key]
        print(f"  {key} = {val[:10]}..." if val else f"  {key} = (пусто)")
print("=" * 50)

# ========== ПЕРЕМЕННЫЕ ИЗ ОКРУЖЕНИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")
ADMIN_ID_SUPPORT = os.getenv("ADMIN_ID_SUPPORT", "8740158116")

missing = []
if not BOT_TOKEN:
    missing.append("BOT_TOKEN")
    print("❌ ОТСУТСТВУЕТ BOT_TOKEN")
if not CRYPTO_PAY_TOKEN:
    missing.append("CRYPTO_PAY_TOKEN")
    print("❌ ОТСУТСТВУЕТ CRYPTO_PAY_TOKEN")

if missing:
    print(f"ОШИБКА: отсутствуют переменные: {', '.join(missing)}")
    print("Проверьте настройки окружения в панели BotHost.")
    raise SystemExit(f"Не заданы: {missing}")

print("✅ Все переменные найдены, запускаем основного бота...")
print("=" * 50)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN)

bot.set_my_commands([
    telebot.types.BotCommand("start", "🔄 Перезапустить бота")
])

users = {}
transactions = []
promocodes = {}
carts = {}
active_tickets = {}

def load_json(name):
    p = os.path.join(DATA_DIR, name)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(name, data):
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_users():
    global users
    data = load_json("users.json")
    if isinstance(data, dict):
        users = data
    else:
        users = {}

def save_users():
    save_json("users.json", users)

def load_transactions():
    global transactions
    data = load_json("transactions.json")
    if isinstance(data, list):
        transactions = data
    else:
        transactions = []

def save_transactions():
    save_json("transactions.json", transactions)

def load_promocodes():
    global promocodes
    data = load_json("promo.json")
    if isinstance(data, dict):
        promocodes = data
    else:
        promocodes = {}

def save_promocodes():
    save_json("promo.json", promocodes)

load_users()
load_transactions()
load_promocodes()

# ========== УВЕДОМЛЕНИЯ АДМИНУ ==========
def get_user_link(user_id):
    try:
        chat = bot.get_chat(user_id)
        username = chat.username
        if username:
            return f"@{username} (<code>{user_id}</code>)"
        else:
            return f"<code>{user_id}</code>"
    except:
        return f"<code>{user_id}</code>"

def notify_admin(text):
    try:
        bot.send_message(ADMIN_ID_SUPPORT, text, parse_mode="HTML")
    except Exception as e:
        print("Не удалось отправить уведомление админу:", e)

def log_tx(uid, typ, amount, item=None, ref=None, status="pending", invoice_id=None):
    global transactions
    if not isinstance(transactions, list):
        transactions = []
    transactions.append({
        "user_id": uid,
        "type": typ,
        "amount": amount,
        "item": item,
        "ref": ref,
        "status": status,
        "invoice_id": invoice_id,
        "timestamp": datetime.now().isoformat()
    })
    save_transactions()

class OrderData:
    def __init__(self):
        self.item_name = ""
        self.price = 0.0
        self.stock = 0
        self.min_qty = 1
        self.qty = 0
        self.country = ""
        self.install = ""
        self.no_qty = False
        self.invoice_id = None
        self.is_topup = False

user_orders = {}

country_flags = {
    "Испания": "🇪🇸", "Россия": "🇷🇺", "США": "🇺🇸", "Великобритания": "🇬🇧",
    "Германия": "🇩🇪", "Франция": "🇫🇷", "Бельгия": "🇧🇪", "Австрия": "🇦🇹",
    "Хорватия": "🇭🇷", "Чехия": "🇨🇿", "Дания": "🇩🇰", "Финляндия": "🇫🇮",
    "Греция": "🇬🇷", "Венгрия": "🇭🇺", "Ирландия": "🇮🇪", "Италия": "🇮🇹",
    "Литва": "🇱🇹", "Люксембург": "🇱🇺", "Нидерланды": "🇳🇱", "Польша": "🇵🇱",
    "Португалия": "🇵🇹", "Румыния": "🇷🇴", "Словакия": "🇸🇰", "Швеция": "🇸🇪"
}

def safe_edit(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    try:
        kwargs = dict(chat_id=chat_id, message_id=message_id, text=text)
        if reply_markup:
            kwargs['reply_markup'] = reply_markup
        if parse_mode:
            kwargs['parse_mode'] = parse_mode
        bot.edit_message_text(**kwargs)
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e):
            raise

def main_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("Промокод", callback_data="promo"),
           types.InlineKeyboardButton("Каталог", callback_data="catalog"),
           types.InlineKeyboardButton("Premium", callback_data="premium_menu"),
           types.InlineKeyboardButton("Пополнить баланс", callback_data="topup"),
           types.InlineKeyboardButton("Реферальная система", callback_data="referral_info"),
           types.InlineKeyboardButton("История покупок", callback_data="history"),
           types.InlineKeyboardButton("Тех.поддержка", callback_data="support_start"))
    return kb

def back_btn(cb="menu"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=cb))
    return kb

def edit_main_menu(call):
    uid = str(call.from_user.id)
    if uid not in users:
        users[uid] = {"balance": 0.0, "premium": False, "bought": 0, "total_spent": 0.0,
                      "used_promos": [], "referrer": None, "registered": datetime.now().isoformat(),
                      "referral_earned": 0.0, "referral_count": 0}
    users[uid].setdefault("registered", datetime.now().isoformat())
    uname = f"@{call.from_user.username}" if call.from_user.username else "нет"
    text = (f"🏛 Мой профиль ⌵\n\n"
            f"Телеграм ID: {uid}\n"
            f"Имя пользователя: {uname}\n\n"
            f"💰 Баланс: ${users[uid]['balance']:.2f}\n\n"
            f"Куплено товаров: {users[uid]['bought']}\n"
            f"Общая сумма покупок: ${users[uid]['total_spent']:.2f}")
    safe_edit(call.message.chat.id, call.message.message_id, text, main_menu_kb())

def create_invoice(amount, desc):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    amount_str = str(amount)
    payload = {
        "asset": "USDT",
        "amount": amount_str,
        "description": desc,
        "paid_btn_name": "callback",
        "paid_btn_url": "https://t.me/ваш_бот"
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        data = r.json()
        if data.get("ok"):
            return data["result"]["pay_url"], data["result"]["invoice_id"], None
        return None, None, data.get("error", "неизвестно")
    except Exception as e:
        return None, None, str(e)

def check_invoice(invoice_id):
    url = "https://pay.crypt.bot/api/getInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    params = {"invoice_id": invoice_id}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        if data.get("ok"):
            return data["result"]["status"]
    except:
        pass
    return None

def auto_check_payment(chat_id, uid, invoice_id):
    time.sleep(10)
    status = check_invoice(invoice_id)
    if status == "paid":
        order = user_orders.get(uid)
        if order and order.invoice_id == invoice_id:
            if order.is_topup:
                users[uid]["balance"] = users[uid].get("balance", 0) + order.price
                save_users()
                bot.send_message(chat_id, "✅ Баланс автоматически пополнен!")
                referrer = users[uid].get("referrer")
                user_link = get_user_link(uid)
                ref_link = get_user_link(referrer) if referrer else "пусто"
                notify_admin(
                    f"✅ <b>Успешное пополнение</b>\n"
                    f"👤 Пользователь: {user_link}\n"
                    f"👥 Реферал: {ref_link}\n"
                    f"💰 Сумма: ${order.price:.2f}"
                )
            else:
                qty = 1 if order.no_qty else (order.qty if order.qty > 0 else 1)
                users[uid]["bought"] = users[uid].get("bought", 0) + qty
                users[uid]["total_spent"] = users[uid].get("total_spent", 0) + order.price * qty
                save_users()
                bot.send_message(chat_id, "✅ Оплата получена! Товар будет выдан в ручном режиме.")
                referrer = users[uid].get("referrer")
                user_link = get_user_link(uid)
                ref_link = get_user_link(referrer) if referrer else "пусто"
                notify_admin(
                    f"🛒 <b>Покупка товара</b>\n"
                    f"👤 Пользователь: {user_link}\n"
                    f"👥 Реферал: {ref_link}\n"
                    f"📦 Товар: {order.item_name}\n"
                    f"🔢 Кол-во: {qty}\n"
                    f"💵 Сумма: ${order.price * qty:.2f}"
                )
            for t in transactions:
                if t.get("invoice_id") == invoice_id:
                    t["status"] = "paid"
            save_transactions()
            if uid in user_orders:
                del user_orders[uid]

@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    ref = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        ref = args[1][3:]
    if uid not in users:
        users[uid] = {
            "balance": 0.0, "premium": False, "bought": 0, "total_spent": 0.0,
            "used_promos": [], "referrer": ref,
            "registered": datetime.now().isoformat(),
            "referral_earned": 0.0, "referral_count": 0
        }
        save_users()
        if ref and ref in users:
            users[ref]["referral_count"] = users[ref].get("referral_count", 0) + 1
            save_users()
            new_user_name = message.from_user.username
            new_user_id = uid
            if new_user_name:
                link = f'<a href="tg://user?id={new_user_id}">@{new_user_name}</a>'
            else:
                link = f'<a href="tg://user?id={new_user_id}">Пользователь</a>'
            bot.send_message(ref, f"У вас новый реферал: {link}", parse_mode="HTML")
    else:
        if ref and not users[uid].get("referrer"):
            users[uid]["referrer"] = ref
            save_users()
            if ref in users:
                users[ref]["referral_count"] = users[ref].get("referral_count", 0) + 1
                save_users()
                new_user_name = message.from_user.username
                if new_user_name:
                    link = f'<a href="tg://user?id={uid}">@{new_user_name}</a>'
                else:
                    link = f'<a href="tg://user?id={uid}">Пользователь</a>'
                bot.send_message(ref, f"У вас новый реферал: {link}", parse_mode="HTML")
    uname = f"@{message.from_user.username}" if message.from_user.username else "нет"
    text = (f"🏛 Мой профиль ⌵\n\n"
            f"Телеграм ID: {uid}\n"
            f"Имя пользователя: {uname}\n\n"
            f"💰 Баланс: ${users[uid]['balance']:.2f}\n\n"
            f"Куплено товаров: {users[uid]['bought']}\n"
            f"Общая сумма покупок: ${users[uid]['total_spent']:.2f}")
    bot.send_message(message.chat.id, text, reply_markup=main_menu_kb())

# ======= Промокод =======
@bot.callback_query_handler(func=lambda c: c.data == "promo")
def promo_start(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.edit_message_text("Введите промокод:", call.message.chat.id, call.message.message_id,
                                reply_markup=back_btn("cancel_promo"))
    bot.register_next_step_handler(msg, promo_check)

@bot.callback_query_handler(func=lambda c: c.data == "cancel_promo")
def cancel_promo(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    edit_main_menu(call)

def promo_check(message):
    code = message.text.strip()
    uid = str(message.from_user.id)
    if uid not in users:
        users[uid] = {"balance": 0.0, "premium": False, "bought": 0, "total_spent": 0.0,
                      "used_promos": [], "referrer": None, "registered": datetime.now().isoformat(),
                      "referral_earned": 0.0, "referral_count": 0}
    load_promocodes()
    if code in promocodes:
        promo = promocodes[code]
        if not promo.get("active", True):
            bot.send_message(message.chat.id, "Промокод неактивен.")
            start(message)
            return
        if promo["activations"] >= promo["max_activations"]:
            bot.send_message(message.chat.id, "Промокод исчерпан.")
            start(message)
            return
        if code in users[uid].get("used_promos", []):
            bot.send_message(message.chat.id, "Вы уже использовали этот промокод.")
            start(message)
            return
        bonus = promo["bonus"]
        users[uid]["balance"] = users[uid].get("balance", 0) + bonus
        users[uid].setdefault("used_promos", []).append(code)
        save_users()
        promocodes[code]["activations"] += 1
        if promocodes[code]["activations"] >= promocodes[code]["max_activations"]:
            promocodes[code]["active"] = False
        save_promocodes()
        bot.send_message(message.chat.id, f"Промокод активирован! +${bonus:.2f}")
    else:
        bot.send_message(message.chat.id, "Промокод не найден.")
    start(message)

@bot.callback_query_handler(func=lambda c: c.data == "menu")
def go_menu(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    edit_main_menu(call)

# ======= КАТАЛОГ (расширенный) =======
@bot.callback_query_handler(func=lambda c: c.data == "catalog")
def catalog(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📱 Аккаунты", callback_data="cat_accounts"),
           types.InlineKeyboardButton("🌐 Социальные сети", callback_data="cat_social"),
           types.InlineKeyboardButton("🎓 Обучение", callback_data="cat_learning"),
           types.InlineKeyboardButton("🌐 Proxy", callback_data="cat_proxy"),
           types.InlineKeyboardButton("🎫 Купоны", callback_data="cat_coupons"),
           types.InlineKeyboardButton("📚 Базы данных", callback_data="cat_databases"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите категорию:", kb)

# ---------- Аккаунты (старые + новый Telegram с историей) ----------
@bot.callback_query_handler(func=lambda c: c.data == "cat_accounts")
def accounts(call):
    plats = ["Kleinanzeigen","Wallapop","Milanuncios","OfferUp","Poshmark",
             "Ricardo","Tutti","Subito","Marktplaats","Finn.no","Blocket",
             "Tori.fi","DBA.dk","Depop","Etsy","Reverb","OLX",
             "📱 Telegram с историей (6+ мес)"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for p in plats:
        kb.add(types.InlineKeyboardButton(p, callback_data=f"platform_{p}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите платформу:", kb)

# ----- Kleinanzeigen -----
@bot.callback_query_handler(func=lambda c: c.data == "platform_Kleinanzeigen")
def klein(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Brute", callback_data="klein_brute"))
    kb.add(types.InlineKeyboardButton("Hand-Reg", callback_data="klein_handreg"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_accounts"))
    safe_edit(call.message.chat.id, call.message.message_id, "Kleinanzeigen.de – тип:", kb)

klein_items = {
    "klein_brute_top_old": ("⭐️ TOP 2009 - 2024", 45.0, 6),
    "klein_brute_mix_old": ("⚡️ MIX 2009 - 2024", 35.0, 2),
    "klein_brute_top_new": ("⭐️ TOP 2025-2026", 50.0, 0),
    "klein_brute_mix_new": ("⚡️ MIX 2025-2026", 25.0, 16),
    "klein_hand_mix": ("🌍 Mix | BandianaFarm", 7.99, 0),
    "klein_hand_de": ("🇩🇪 De | Hand-Reg", 9.5, 6),
}

@bot.callback_query_handler(func=lambda c: c.data == "klein_brute")
def klein_brute(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("TOP 2009-2024 | 45$ | 6", callback_data="klein_brute_top_old"))
    kb.add(types.InlineKeyboardButton("MIX 2009-2024 | 35$ | 2", callback_data="klein_brute_mix_old"))
    kb.add(types.InlineKeyboardButton("TOP 2025-2026 | 50$ | 0", callback_data="klein_brute_top_new"))
    kb.add(types.InlineKeyboardButton("MIX 2025-2026 | 25$ | 16", callback_data="klein_brute_mix_new"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="platform_Kleinanzeigen"))
    safe_edit(call.message.chat.id, call.message.message_id, "Brute – выберите:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "klein_handreg")
def klein_handreg(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("MIX | 7.99$ | 23", callback_data="klein_hand_mix"))
    kb.add(types.InlineKeyboardButton("De | 9.5$ | 6", callback_data="klein_hand_de"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="platform_Kleinanzeigen"))
    safe_edit(call.message.chat.id, call.message.message_id, "Hand-Reg – выберите:", kb)

@bot.callback_query_handler(func=lambda c: c.data in klein_items)
def klein_item(call):
    name, price, stock = klein_items[call.data]
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ----- Wallapop -----
@bot.callback_query_handler(func=lambda c: c.data == "platform_Wallapop")
def wallapop(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Wallapop BRUTE", callback_data="wallapop_brute"))
    kb.add(types.InlineKeyboardButton("Hand-Reg 2026", callback_data="wallapop_handreg"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_accounts"))
    safe_edit(call.message.chat.id, call.message.message_id, "Wallapop – тип:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "wallapop_brute")
def wallapop_brute(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Cookie 2013-2023 | 9.9$ | 5", callback_data="wallapop_brute_old"))
    kb.add(types.InlineKeyboardButton("Cookie 2024-2025 | 5.99$ | 4", callback_data="wallapop_brute_new"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="platform_Wallapop"))
    safe_edit(call.message.chat.id, call.message.message_id, "Wallapop BRUTE:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "wallapop_handreg")
def wallapop_handreg(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = "Hand-Reg Wallapop 2026"
    user_orders[uid].price = 0.75
    user_orders[uid].stock = 105
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / 0.75))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

@bot.callback_query_handler(func=lambda c: c.data in ["wallapop_brute_old","wallapop_brute_new"])
def wallapop_brute_item(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    if call.data == "wallapop_brute_old":
        user_orders[uid].item_name = "🇪🇸 Brt Cookie 2013-2023"
        user_orders[uid].price = 9.9
        user_orders[uid].stock = 5
    else:
        user_orders[uid].item_name = "Cookie 2024-2025"
        user_orders[uid].price = 5.99
        user_orders[uid].stock = 4
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / user_orders[uid].price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ----- Milanuncios -----
@bot.callback_query_handler(func=lambda c: c.data == "platform_Milanuncios")
def milanuncios(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = "🇪🇸 Milanuncios.com • BRUTE • MIX"
    user_orders[uid].price = 2.50
    user_orders[uid].stock = 85
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / 2.5))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ----- OfferUp -----
@bot.callback_query_handler(func=lambda c: c.data == "platform_OfferUp")
def offerup(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("OfferUp BRUTE Mix", callback_data="offerup_brute"))
    kb.add(types.InlineKeyboardButton("Hand-Reg 1-3 Days", callback_data="offerup_handreg"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_accounts"))
    safe_edit(call.message.chat.id, call.message.message_id, "OfferUp – тип:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "offerup_brute")
def offerup_brute(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = "OfferUp.com BRUTE MIX"
    user_orders[uid].price = 5.0
    user_orders[uid].stock = 30
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / 5.0))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

@bot.callback_query_handler(func=lambda c: c.data == "offerup_handreg")
def offerup_handreg(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = "🇺🇸 Hand-Reg OfferUp 2026"
    user_orders[uid].price = 0.40
    user_orders[uid].stock = 84
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / 0.4))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ----- Простые платформы -----
simple_platforms = {
    "Poshmark": ("🇺🇸 Poshmark.com • BRUTE • MIX", 6.50, 56),
    "Ricardo": ("🇨🇭 Ricardo.ch • BRUTE • MIX", 17.00, 6),
    "Tutti": ("🇨🇭 Tutti.ch • BRUTE • MIX", 15.00, 3),
    "Subito": ("🇮🇹 Subito.it Mix 2007-2025", 4.50, 50),
    "Marktplaats": ("🇳🇱 Marktplaats.nl • BRUTE • MIX", 5.00, 0),
    "Finn.no": ("🇳🇴 Finn.no • BRUTE • MIX", 5.00, 0),
    "Blocket": ("🇸🇪 Blocket.se • BRUTE • MIX", 5.00, 0),
    "Tori.fi": ("🇫🇮 Tori.fi • BRUTE • MIX", 6.00, 1),
    "DBA.dk": ("🇩🇰 DBA.dk • BRUTE • MIX", 5.00, 2),
    "Depop": ("🌎 Depop.com • BRUTE • MIX", 4.00, 7),
    "Etsy": ("🌎 Etsy.com • BRUTE • MIX", 5.00, 0),
    "Reverb": ("🌎 Reverb.com • BRUTE • MIX", 5.00, 0),
}

@bot.callback_query_handler(func=lambda c: c.data.startswith("platform_") and c.data.split("_",1)[1] in simple_platforms)
def simple_platform(call):
    plat = call.data.split("_", 1)[1]
    name, price, stock = simple_platforms[plat]
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ----- OLX -----
olx_data = {
    "pl": {"mix": ("🇵🇱 Olx.pl 2008-2026 Mix", 2.25, 31, 5), "hand": ("🇵🇱 Hand-Reg Olx.pl", 0.20, 0, 1)},
    "ro": {"mix": ("🇷🇴 Olx.ro 2010-2026 Mix", 2.75, 16, 5), "hand": ("🇷🇴 Hand-Reg Olx.ro", 0.20, 0, 1)},
    "bg": {"mix": ("🇧🇬 Olx.bg 2005-2026 Mix", 5.00, 22, 5), "hand": ("🇧🇬 Hand-Reg Olx.bg", 0.20, 39, 10)},
    "pt": {"mix": ("🇵🇹 Olx.pt 2007-2026 Mix", 2.75, 12, 5), "hand": ("🇵🇹 Hand-Reg Olx.pt", 0.20, 23, 10)},
}

@bot.callback_query_handler(func=lambda c: c.data == "platform_OLX")
def olx_main(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("OLX.pl", callback_data="olx_pl"))
    kb.add(types.InlineKeyboardButton("OLX.ro", callback_data="olx_ro"))
    kb.add(types.InlineKeyboardButton("OLX.bg", callback_data="olx_bg"))
    kb.add(types.InlineKeyboardButton("OLX.pt", callback_data="olx_pt"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_accounts"))
    safe_edit(call.message.chat.id, call.message.message_id, "OLX – страна:", kb)

@bot.callback_query_handler(func=lambda c: c.data in ["olx_pl","olx_ro","olx_bg","olx_pt"])
def olx_type_choice(call):
    country = call.data.split("_")[1]
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].country = country
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(f"Olx.{country} Mix", callback_data=f"olx_{country}_mix"))
    kb.add(types.InlineKeyboardButton(f"Hand-Reg Olx.{country}", callback_data=f"olx_{country}_hand"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="platform_OLX"))
    safe_edit(call.message.chat.id, call.message.message_id, f"OLX.{country} – тип:", kb)

@bot.callback_query_handler(func=lambda c: c.data.endswith("_mix") or c.data.endswith("_hand"))
def olx_item(call):
    parts = call.data.split("_")
    country = parts[1]
    typ = parts[2]
    name, price, stock, minq_default = olx_data[country][typ]
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ----- Новый товар: Telegram с историей -----
@bot.callback_query_handler(func=lambda c: c.data == "platform_📱 Telegram с историей (6+ мес)")
def tg_history_item(call):
    uid = str(call.from_user.id)
    name = "📱 Telegram аккаунт с историей (6+ мес)"
    price = 5.0
    stock = 999
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ======= Социальные сети =======
@bot.callback_query_handler(func=lambda c: c.data == "cat_social")
def social(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("WhatsApp", callback_data="social_wa_start"),
           types.InlineKeyboardButton("Telegram", callback_data="social_tg_start"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Социальные сети:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "social_wa_start")
def social_wa_start(call):
    wa_start(call)

@bot.callback_query_handler(func=lambda c: c.data == "social_tg_start")
def tg_start(call):
    uid = str(call.from_user.id)
    if uid not in user_orders:
        user_orders[uid] = OrderData()
    order = user_orders[uid]
    countries = list(country_flags.keys())
    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in countries:
        btn_text = f"👉 {country_flags[c]} {c}" if c == order.country else f"{country_flags[c]} {c}"
        kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"tg_country_{c}"))
    kb.add(types.InlineKeyboardButton("Подтвердить выбор", callback_data="tg_confirm"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_social"))
    flag = country_flags.get(order.country, "")
    display = f"{flag} {order.country}" if order.country else "-"
    safe_edit(call.message.chat.id, call.message.message_id,
              f"📱 Telegram аккаунты\n🌍 Выбранная страна: {display}", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tg_country_"))
def tg_country(call):
    country = call.data.split("_", 2)[2]
    uid = str(call.from_user.id)
    if uid not in user_orders:
        user_orders[uid] = OrderData()
    user_orders[uid].country = country
    tg_start(call)

@bot.callback_query_handler(func=lambda c: c.data == "tg_confirm")
def tg_confirm(call):
    uid = str(call.from_user.id)
    if uid not in user_orders or not user_orders[uid].country:
        bot.answer_callback_query(call.id, "Сначала выберите страну!", show_alert=True)
        return
    order = user_orders[uid]
    flag = country_flags.get(order.country, "")
    order.item_name = f"📱 Telegram ({flag} {order.country})"
    order.price = 5.00
    order.stock = 999
    order.no_qty = True
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / order.price))
    else:
        order.min_qty = 1
    text = f"📱 Telegram аккаунт\nСтрана: {flag} {order.country}\nЦена: $5.00"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="social_tg_start"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, text, kb)

# WhatsApp
@bot.callback_query_handler(func=lambda c: c.data == "social_wa_start")
def wa_start(call):
    uid = str(call.from_user.id)
    if uid not in user_orders:
        user_orders[uid] = OrderData()
    order = user_orders[uid]
    countries = list(country_flags.keys())
    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in countries:
        btn_text = f"👉 {country_flags[c]} {c}" if c == order.country else f"{country_flags[c]} {c}"
        kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"wa_country_{c}"))
    kb.add(types.InlineKeyboardButton("Подтвердить выбор", callback_data="wa_confirm"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_social"))
    flag = country_flags.get(order.country, "")
    display = f"{flag} {order.country}" if order.country else "-"
    safe_edit(call.message.chat.id, call.message.message_id,
              f"📲WhatsApp аккаунты\n🌍 Выбранная страна: {display}", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("wa_country_"))
def wa_country(call):
    country = call.data.split("_", 2)[2]
    uid = str(call.from_user.id)
    if uid not in user_orders:
        user_orders[uid] = OrderData()
    user_orders[uid].country = country
    wa_start(call)

@bot.callback_query_handler(func=lambda c: c.data == "wa_confirm")
def wa_install(call):
    uid = str(call.from_user.id)
    if uid not in user_orders or not user_orders[uid].country:
        bot.answer_callback_query(call.id, "Сначала выберите страну!", show_alert=True)
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Эмулятор / Телефон", callback_data="wa_install_emu"))
    kb.add(types.InlineKeyboardButton("web.whatsapp.com", callback_data="wa_install_web"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="wa_confirm"))
    safe_edit(call.message.chat.id, call.message.message_id,
              "📲WhatsApp аккаунты\nКуда установить?", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("wa_install_"))
def wa_final(call):
    method = "Эмулятор / Телефон" if call.data == "wa_install_emu" else "web.whatsapp.com"
    uid = str(call.from_user.id)
    order = user_orders[uid]
    order.install = method
    flag = country_flags.get(order.country, "")
    order.item_name = f"📲 WhatsApp ({flag} {order.country}) на {method}"
    order.price = 11.99
    order.stock = 999
    order.no_qty = True
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / order.price))
    else:
        order.min_qty = 1
    text = f"Вы выбрали: {method}\nЦена: $11.99"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="wa_confirm"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, text, kb)

# ======= Корзина =======
@bot.callback_query_handler(func=lambda c: c.data == "add_to_cart")
def add_to_cart(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order:
        bot.answer_callback_query(call.id, "Сначала выберите товар.")
        return
    qty = 1 if order.no_qty else (order.qty if order.qty > 0 else 1)
    carts.setdefault(uid, []).append({
        "item_name": order.item_name,
        "price": order.price,
        "qty": qty,
        "specs": f"{order.country} {order.install}" if order.install else order.country
    })
    total_items = sum(i["qty"] for i in carts[uid])
    bot.answer_callback_query(call.id, f"Добавлено! Товаров в корзине: {total_items}")
    kb = call.message.reply_markup
    has_cart = any("view_cart" in btn.callback_data for row in kb.keyboard for btn in row)
    if not has_cart:
        kb.add(types.InlineKeyboardButton("🧺 Перейти в корзину", callback_data="view_cart"))
    safe_edit(call.message.chat.id, call.message.message_id, call.message.text, kb)

@bot.callback_query_handler(func=lambda c: c.data == "view_cart")
def view_cart(call):
    uid = str(call.from_user.id)
    if uid not in carts or not carts[uid]:
        bot.answer_callback_query(call.id, "Корзина пуста.")
        return
    total = 0
    lines = ["🛒 Ваша корзина:"]
    for idx, item in enumerate(carts[uid], 1):
        total += item["price"] * item["qty"]
        lines.append(f"{idx}. {item['item_name']} ×{item['qty']} – ${item['price']*item['qty']:.2f}")
    lines.append(f"\n💰 Общая сумма: ${total:.2f}")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🧾 Оплатить всё", callback_data="checkout_cart"))
    kb.add(types.InlineKeyboardButton("Очистить корзину", callback_data="clear_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines), kb)

@bot.callback_query_handler(func=lambda c: c.data == "clear_cart")
def clear_cart(call):
    carts.pop(str(call.from_user.id), None)
    safe_edit(call.message.chat.id, call.message.message_id, "Корзина очищена.", main_menu_kb())

@bot.callback_query_handler(func=lambda c: c.data == "checkout_cart")
def checkout_cart(call):
    uid = str(call.from_user.id)
    if uid not in carts or not carts[uid]:
        bot.answer_callback_query(call.id, "Корзина пуста.")
        return
    total = sum(item["price"] * item["qty"] for item in carts[uid])
    desc = ", ".join(f"{i['item_name']} x{i['qty']}" for i in carts[uid])
    inv_url, inv_id, err = create_invoice(total, desc)
    if err:
        bot.send_message(call.message.chat.id, f"Ошибка создания счёта: {err}")
        return
    order = user_orders.setdefault(uid, OrderData())
    order.invoice_id = inv_id
    order.is_topup = False
    for item in carts[uid]:
        log_tx(uid, "buy", item["price"] * item["qty"], item=item["item_name"], ref=users[uid].get("referrer"), invoice_id=inv_id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(call.message.chat.id,
                     f"Счёт на оплату корзины создан.\nТоваров: {len(carts[uid])}\nСумма: ${total:.2f}",
                     reply_markup=kb)
    carts.pop(uid, None)
    threading.Thread(target=auto_check_payment, args=(call.message.chat.id, uid, inv_id)).start()

@bot.callback_query_handler(func=lambda c: c.data == "buy_now")
def buy_now(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order:
        bot.answer_callback_query(call.id, "Сначала выберите товар.")
        return
    qty = 1 if order.no_qty else (order.qty if order.qty > 0 else 1)
    total = order.price * qty
    inv_url, inv_id, err = create_invoice(total, f"{order.item_name} x{qty}")
    if err:
        bot.send_message(call.message.chat.id, f"Ошибка создания счёта: {err}")
        return
    order.invoice_id = inv_id
    order.is_topup = False
    log_tx(uid, "buy", total, item=order.item_name, ref=users[uid].get("referrer"), invoice_id=inv_id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(call.message.chat.id,
                     f"Счёт на оплату создан.\nТовар: {order.item_name}\nКол-во: {qty}\nСумма: ${total:.2f}",
                     reply_markup=kb)
    threading.Thread(target=auto_check_payment, args=(call.message.chat.id, uid, inv_id)).start()

@bot.callback_query_handler(func=lambda c: c.data == "check_payment")
def check_payment(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order or not order.invoice_id:
        bot.answer_callback_query(call.id, "Нет активного счёта.")
        return
    status = check_invoice(order.invoice_id)
    if status == "paid":
        if order.is_topup:
            users[uid]["balance"] = users[uid].get("balance", 0) + order.price
            save_users()
            bot.send_message(call.message.chat.id, "✅ Баланс успешно пополнен.")
            referrer = users[uid].get("referrer")
            user_link = get_user_link(uid)
            ref_link = get_user_link(referrer) if referrer else "пусто"
            notify_admin(
                f"✅ <b>Успешное пополнение</b>\n"
                f"👤 Пользователь: {user_link}\n"
                f"👥 Реферал: {ref_link}\n"
                f"💰 Сумма: ${order.price:.2f}"
            )
        else:
            qty = 1 if order.no_qty else (order.qty if order.qty > 0 else 1)
            users[uid]["bought"] = users[uid].get("bought", 0) + qty
            users[uid]["total_spent"] = users[uid].get("total_spent", 0) + order.price * qty
            save_users()
            bot.send_message(call.message.chat.id, "✅ Оплата прошла успешно! Товар будет выдан в ручном режиме.")
            referrer = users[uid].get("referrer")
            user_link = get_user_link(uid)
            ref_link = get_user_link(referrer) if referrer else "пусто"
            notify_admin(
                f"🛒 <b>Покупка товара</b>\n"
                f"👤 Пользователь: {user_link}\n"
                f"👥 Реферал: {ref_link}\n"
                f"📦 Товар: {order.item_name}\n"
                f"🔢 Кол-во: {qty}\n"
                f"💵 Сумма: ${order.price * qty:.2f}"
            )
        for t in transactions:
            if t.get("invoice_id") == order.invoice_id:
                t["status"] = "paid"
        save_transactions()
        user_orders.pop(uid, None)
    else:
        bot.answer_callback_query(call.id, "Оплата ещё не поступила.")

# ======= Обучение (расширенное) =======
@bot.callback_query_handler(func=lambda c: c.data == "cat_learning")
def learning(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎓 Обучение EU olx | 25$ | ∞ шт", callback_data="learn_olx"))
    kb.add(types.InlineKeyboardButton("🎓 Курс: как найти клиентов на OLX (видео+мануал)", callback_data="learn_olx_course"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Обучение:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "learn_olx")
def learn_olx(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = "Полное обучение + мануалы по EU OLX. Личный наставник 2 нед."
    user_orders[uid].price = 25.00
    user_orders[uid].no_qty = True
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / user_orders[uid].price))
    else:
        user_orders[uid].min_qty = 1
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("🏠 Меню", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id,
              "Полное обучение + мануалы по EU OLX (страна любая). Личный наставник на 2 недели доведёт до профита. Если нет – вернём деньги.", kb)

@bot.callback_query_handler(func=lambda c: c.data == "learn_olx_course")
def learn_olx_course(call):
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = "🎓 Курс: Как найти клиентов на OLX (видео+мануал)"
    user_orders[uid].price = 25.00
    user_orders[uid].no_qty = True
    if user_orders[uid].price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / user_orders[uid].price))
    else:
        user_orders[uid].min_qty = 1
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_learning"))
    safe_edit(call.message.chat.id, call.message.message_id,
              "Видеокурс + мануал по привлечению клиентов на OLX. Готовые шаблоны, схема регистрации аккаунтов, переговоры с клиентами.", kb)

# ======= Proxy (расширенный) =======
@bot.callback_query_handler(func=lambda c: c.data == "cat_proxy")
def proxy(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("300 IPs | 13$ | 235 шт", callback_data="proxy_300"),
           types.InlineKeyboardButton("800 IPs | 33$ | 36 шт", callback_data="proxy_800"),
           types.InlineKeyboardButton("5000 IPs | 165$ | 6 шт", callback_data="proxy_5000"),
           types.InlineKeyboardButton("🌐 Резидентные прокси (10 шт)", callback_data="proxy_residential"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите тип прокси:", kb)

proxy_data = {
    "proxy_300": ("9Proxy 300 IPs", 13, 235, 1),
    "proxy_800": ("9Proxy 800 IPs", 33, 36, 1),
    "proxy_5000": ("9Proxy 5000 IPs", 165, 6, 1),
}

@bot.callback_query_handler(func=lambda c: c.data in proxy_data)
def proxy_item(call):
    name, price, stock, minq_default = proxy_data[call.data]
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

@bot.callback_query_handler(func=lambda c: c.data == "proxy_residential")
def proxy_residential(call):
    uid = str(call.from_user.id)
    name = "🌐 Резидентные прокси (10 шт, ротация)"
    price = 12.0
    stock = 999
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ======= Купоны =======
@bot.callback_query_handler(func=lambda c: c.data == "cat_coupons")
def coupons_menu(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎫 ChatGPT Plus (1 мес)", callback_data="coupon_chatgpt"))
    kb.add(types.InlineKeyboardButton("🎫 Midjourney (1 мес)", callback_data="coupon_midjourney"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите купон:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "coupon_chatgpt")
def coupon_chatgpt(call):
    uid = str(call.from_user.id)
    name = "🎫 Купон ChatGPT Plus (1 месяц)"
    price = 8.0
    stock = 999
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

@bot.callback_query_handler(func=lambda c: c.data == "coupon_midjourney")
def coupon_midjourney(call):
    uid = str(call.from_user.id)
    name = "🎫 Купон Midjourney (полный доступ, 1 месяц)"
    price = 9.5
    stock = 999
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ======= Базы данных =======
@bot.callback_query_handler(func=lambda c: c.data == "cat_databases")
def databases_menu(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📚 База Telegram-каналов (5000)", callback_data="db_tg_channels"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите базу данных:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "db_tg_channels")
def db_tg_channels(call):
    uid = str(call.from_user.id)
    name = "📚 База Telegram-каналов (5000, 3 темы)"
    price = 15.0
    stock = 999
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].stock = stock
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    show_item_qty(call, uid)

# ======= Премиум =======
@bot.callback_query_handler(func=lambda c: c.data == "premium_menu")
def premium_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("1 мес – $5", callback_data="prem_1"))
    kb.add(types.InlineKeyboardButton("3 мес – $9", callback_data="prem_3"))
    kb.add(types.InlineKeyboardButton("6 мес – $20", callback_data="prem_6"))
    kb.add(types.InlineKeyboardButton("12 мес – $36", callback_data="prem_12"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, "💎 Premium подписка Telegram", kb)

prem_data = {
    "prem_1": ("Премиум 1 месяц", 5),
    "prem_3": ("Премиум 3 месяца", 9),
    "prem_6": ("Премиум 6 месяцев", 20),
    "prem_12": ("Премиум 12 месяцев", 36),
}

@bot.callback_query_handler(func=lambda c: c.data in prem_data)
def prem_buy(call):
    name, price = prem_data[call.data]
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_name = name
    user_orders[uid].price = price
    user_orders[uid].no_qty = True
    if price < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / price))
    else:
        user_orders[uid].min_qty = 1
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="premium_menu"))
    safe_edit(call.message.chat.id, call.message.message_id, f"{name}\nЦена: ${price}", kb)

# ======= Пополнение баланса (мин 10$) =======
@bot.callback_query_handler(func=lambda c: c.data == "topup")
def topup_start(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.edit_message_text("💰 Введите сумму пополнения в $ (минимальная сумма – 10$):", call.message.chat.id, call.message.message_id,
                                reply_markup=back_btn("cancel_topup"))
    bot.register_next_step_handler(msg, topup_amount)

@bot.callback_query_handler(func=lambda c: c.data == "cancel_topup")
def cancel_topup(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    go_menu(call)

def topup_amount(message):
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError
        if amount < 10:
            bot.send_message(message.chat.id, "❌ Минимальная сумма пополнения – 10$. Попробуйте ещё раз.")
            start(message)
            return
    except:
        bot.send_message(message.chat.id, "❌ Неверная сумма. Введите число больше 0 (минимальная сумма 10$).")
        start(message)
        return
    uid = str(message.from_user.id)
    inv_url, inv_id, err = create_invoice(amount, "Пополнение баланса")
    if err:
        bot.send_message(message.chat.id, f"Ошибка создания счёта: {err}")
        start(message)
        return
    order = user_orders.setdefault(uid, OrderData())
    order.invoice_id = inv_id
    order.is_topup = True
    order.price = amount
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(message.chat.id, f"💸 Счёт на пополнение ${amount:.2f} создан.", reply_markup=kb)
    log_tx(uid, "topup", amount, ref=users[uid].get("referrer"), invoice_id=inv_id)
    threading.Thread(target=auto_check_payment, args=(message.chat.id, uid, inv_id)).start()

# ======= Реферальная система =======
@bot.callback_query_handler(func=lambda c: c.data == "referral_info")
def referral_info(call):
    uid = str(call.from_user.id)
    u = users.get(uid, {})
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start=ref{uid}"
    earned = u.get("referral_earned", 0.0)
    invited = u.get("referral_count", 0)
    text = (f"📨 Приглашай друзей в AURORA Shop и получай 2% кешбэка с их покупок!\n"
            f"Заработано всего: ${earned:.2f}\n"
            f"Приглашено всего: {invited}\n\n"
            f"🔗 Твоя реферальная ссылка:\n{ref_link}")
    safe_edit(call.message.chat.id, call.message.message_id, text, back_btn("menu"))

# ======= История покупок =======
@bot.callback_query_handler(func=lambda c: c.data == "history")
def history(call):
    uid = str(call.from_user.id)
    my_tx = [t for t in transactions if t["user_id"] == uid and t["type"] == "buy" and t["status"] == "paid"]
    if not my_tx:
        txt = "Пока нет завершённых покупок."
    else:
        txt = "📜 История покупок:\n" + "\n".join(f"{t['item']} – ${t['amount']:.2f}" for t in my_tx[-10:])
    safe_edit(call.message.chat.id, call.message.message_id, txt, back_btn("menu"))

# ======= Техподдержка =======
@bot.callback_query_handler(func=lambda c: c.data == "support_start")
def support_start(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.send_message(call.message.chat.id, "Опишите вашу проблему, и техподдержка скоро ответит.\nВведите сообщение:")
    bot.register_next_step_handler(msg, support_forward_to_admin)

def support_forward_to_admin(message):
    uid = str(message.from_user.id)
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID {uid}"
    text = f"📩 Новое обращение от {username}:\n{message.text}"
    admin_id = int(ADMIN_ID_SUPPORT)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Ответить", callback_data=f"support_reply_{uid}"))
    kb.add(types.InlineKeyboardButton("❌ Скрыть", callback_data=f"support_hide_{uid}"))
    try:
        bot.send_message(admin_id, text, reply_markup=kb)
        active_tickets[uid] = {"admin_id": admin_id, "history": [(uid, message.text)]}
        bot.send_message(message.chat.id, "✅ Ваше сообщение отправлено в техподдержку. Ожидайте ответа.")
    except telebot.apihelper.ApiTelegramException as e:
        if e.error_code == 403:
            bot.send_message(message.chat.id, "Извините, техподдержка временно недоступна. Попробуйте позже.")
        else:
            bot.send_message(message.chat.id, "Ошибка отправки сообщения.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_reply_"))
def support_reply_start(call):
    user_id = call.data.split("_")[2]
    if call.from_user.id != int(ADMIN_ID_SUPPORT):
        bot.answer_callback_query(call.id, "Нет прав.")
        return
    msg = bot.send_message(call.message.chat.id, "Введите ответ пользователю:")
    bot.register_next_step_handler(msg, support_send_reply, user_id)
    bot.answer_callback_query(call.id)

def support_send_reply(message, user_id):
    text = f"📬 Ответ от техподдержки:\n{message.text}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Ответить", callback_data=f"support_user_reply_{user_id}"))
    bot.send_message(user_id, text, reply_markup=kb)
    if user_id in active_tickets:
        active_tickets[user_id]["history"].append(("admin", message.text))
    bot.send_message(message.chat.id, "✅ Ответ отправлен.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_user_reply_"))
def support_user_reply_start(call):
    user_id = call.data.split("_")[3]
    if str(call.from_user.id) != user_id:
        bot.answer_callback_query(call.id, "Это не ваш тикет.")
        return
    msg = bot.send_message(call.message.chat.id, "Введите ваше сообщение:")
    bot.register_next_step_handler(msg, support_forward_to_admin)

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_hide_"))
def support_hide(call):
    if call.from_user.id != int(ADMIN_ID_SUPPORT):
        bot.answer_callback_query(call.id, "Нет прав.")
        return
    bot.delete_message(call.message.chat.id, call.message.message_id)

# ======= Показ товара с вводом количества =======
def show_item_qty(call, uid):
    order = user_orders[uid]
    if order.stock == 0:
        text = f"⭐️ {order.item_name}\nЦена: ${order.price:.2f}\nВ наличии: 0 шт."
        safe_edit(call.message.chat.id, call.message.message_id, text, back_btn("menu"))
        return
    text = (f"⭐️ {order.item_name}\nЦена: ${order.price:.2f} за шт.\n"
            f"В наличии: {order.stock} шт.\nМинимально: {order.min_qty} шт.\n\n"
            "✏️ Введите желаемое количество:")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_qty)

def process_qty(message):
    uid = str(message.from_user.id)
    order = user_orders.get(uid)
    if not order: return
    try:
        qty = int(message.text.strip())
    except ValueError:
        msg = bot.send_message(message.chat.id, "Неверное число.")
        bot.register_next_step_handler(msg, process_qty)
        return
    if qty < order.min_qty:
        msg = bot.send_message(message.chat.id, f"Минимальное количество для этого товара: {order.min_qty} шт. (чтобы сумма была не менее 11$)")
        bot.register_next_step_handler(msg, process_qty)
        return
    if qty > order.stock:
        msg = bot.send_message(message.chat.id, f"Максимум: {order.stock}")
        bot.register_next_step_handler(msg, process_qty)
        return
    order.qty = qty
    total = order.price * qty
    text = f"{order.item_name} × {qty}\nИтого: ${total:.2f}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    bot.send_message(message.chat.id, text, reply_markup=kb)

print("Основной бот запущен и готов к работе!")
bot.infinity_polling()
