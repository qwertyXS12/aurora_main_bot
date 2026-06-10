import telebot
from telebot import types
import os
import requests
import time
import threading
import math
import html
import logging
from datetime import datetime
from dotenv import load_dotenv
from database import (
    init_db, migrate_from_json,
    get_user, save_user, get_all_users,
    add_transaction, update_transaction_status, get_user_transactions,
    get_all_transactions,
    get_promocode, save_promocode, get_all_promocodes,
    add_pending_invoice, get_all_pending_invoices, remove_pending_invoice
)

# ========== ЗАГРУЗКА .env ==========
load_dotenv()

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
logger.info("=" * 50)
logger.info("ЗАПУСК ОСНОВНОГО БОТА")
logger.info("Переменные окружения, которые видит контейнер:")
for key in os.environ.keys():
    if "TOKEN" in key or "ID" in key:
        val = os.environ[key]
        logger.info(f"  {key} = {val[:10]}..." if val else f"  {key} = (пусто)")
logger.info("=" * 50)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")
ADMIN_ID_SUPPORT = os.getenv("ADMIN_ID_SUPPORT", "8740158116")

missing = []
if not BOT_TOKEN:
    missing.append("BOT_TOKEN")
    logger.error("❌ ОТСУТСТВУЕТ BOT_TOKEN")
if not CRYPTO_PAY_TOKEN:
    missing.append("CRYPTO_PAY_TOKEN")
    logger.error("❌ ОТСУТСТВУЕТ CRYPTO_PAY_TOKEN")

if missing:
    logger.error(f"ОШИБКА: отсутствуют переменные: {', '.join(missing)}")
    raise SystemExit(f"Не заданы: {missing}")

logger.info("✅ Все переменные найдены, запускаем основного бота...")
logger.info("=" * 50)

# ========== ИНИЦИАЛИЗАЦИЯ БД И МИГРАЦИЯ ==========
init_db()
migrate_from_json()  # переносит старые данные из data/*.json в SQLite

bot = telebot.TeleBot(BOT_TOKEN)

# Получаем username бота один раз и кешируем
BOT_USERNAME = bot.get_me().username

bot.set_my_commands([
    telebot.types.BotCommand("start", "🔄 Перезапустить бота"),
    telebot.types.BotCommand("admin", "👑 Админ-панель"),
])

# ========== ГЛОБАЛЬНЫЕ СТРУКТУРЫ (теперь в памяти только для кеша) ==========
# Используем БД как источник истины, но для скорости оставляем кеш с блокировкой
_cache_lock = threading.Lock()
_user_cache = {}      # {user_id: user_dict}
_promo_cache = {}     # {code: promo_dict}
_promo_cache_time = 0

def refresh_promo_cache():
    global _promo_cache, _promo_cache_time
    with _cache_lock:
        if time.time() - _promo_cache_time > 60:  # обновляем раз в минуту
            _promo_cache = get_all_promocodes()
            _promo_cache_time = time.time()

def get_user_cached(uid):
    with _cache_lock:
        if uid not in _user_cache:
            user = get_user(uid)
            if not user:
                user = {
                    'user_id': uid,
                    'balance': 0.0,
                    'premium': False,
                    'bought': 0,
                    'total_spent': 0.0,
                    'used_promos': [],
                    'referrer': None,
                    'referral_earned': 0.0,
                    'referral_count': 0,
                    'registered': datetime.now().isoformat()
                }
                save_user(user)
            _user_cache[uid] = user
        return _user_cache[uid].copy()

def save_user_cached(user):
    with _cache_lock:
        _user_cache[user['user_id']] = user.copy()
    save_user(user)

# Корзины и временные заказы (оставляем в памяти, они не критичны)
carts = {}
user_orders = {}

country_flags = {
    "Испания": "🇪🇸", "Россия": "🇷🇺", "США": "🇺🇸", "Великобритания": "🇬🇧",
    "Германия": "🇩🇪", "Франция": "🇫🇷", "Бельгия": "🇧🇪", "Австрия": "🇦🇹",
    "Хорватия": "🇭🇷", "Чехия": "🇨🇿", "Дания": "🇩🇰", "Финляндия": "🇫🇮",
    "Греция": "🇬🇷", "Венгрия": "🇭🇺", "Ирландия": "🇮🇪", "Италия": "🇮🇹",
    "Литва": "🇱🇹", "Люксембург": "🇱🇺", "Нидерланды": "🇳🇱", "Польша": "🇵🇱",
    "Португалия": "🇵🇹", "Румыния": "🇷🇴", "Словакия": "🇸🇰", "Швеция": "🇸🇪"
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
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

def get_user_link(user_id):
    try:
        chat = bot.get_chat(user_id)
        username = chat.username
        safe_name = html.escape(username) if username else str(user_id)
        if username:
            return f"@{safe_name} (<code>{user_id}</code>)"
        else:
            return f"<code>{user_id}</code>"
    except:
        return f"<code>{user_id}</code>"

def notify_admin(text):
    try:
        bot.send_message(ADMIN_ID_SUPPORT, text, parse_mode="HTML")
    except Exception as e:
        logger.exception("Не удалось отправить уведомление админу")

def is_admin(user_id):
    return str(user_id) == ADMIN_ID_SUPPORT

# ========== ПЛАТЁЖНЫЕ ФУНКЦИИ ==========
def create_invoice(amount, desc):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    amount_str = str(amount)
    payload = {
        "asset": "USDT",
        "amount": amount_str,
        "description": desc,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{BOT_USERNAME}"
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

def process_paid_invoice(invoice_id, chat_id, uid, is_topup, price, item_name, qty):
    """Обрабатывает успешную оплату"""
    user = get_user_cached(uid)
    if is_topup:
        user['balance'] += price
        save_user_cached(user)
        bot.send_message(chat_id, "✅ Баланс автоматически пополнен!")
        referrer = user.get('referrer')
        user_link = get_user_link(uid)
        ref_link = get_user_link(referrer) if referrer else "пусто"
        notify_admin(
            f"✅ <b>Успешное пополнение</b>\n"
            f"👤 Пользователь: {user_link}\n"
            f"👥 Реферал: {ref_link}\n"
            f"💰 Сумма: ${price:.2f}"
        )
    else:
        user['bought'] += qty
        user['total_spent'] += price * qty
        save_user_cached(user)
        bot.send_message(chat_id, "✅ Оплата получена! Товар будет выдан в ручном режиме.")
        referrer = user.get('referrer')
        user_link = get_user_link(uid)
        ref_link = get_user_link(referrer) if referrer else "пусто"
        notify_admin(
            f"🛒 <b>Покупка товара</b>\n"
            f"👤 Пользователь: {user_link}\n"
            f"👥 Реферал: {ref_link}\n"
            f"📦 Товар: {item_name}\n"
            f"🔢 Кол-во: {qty}\n"
            f"💵 Сумма: ${price * qty:.2f}"
        )
    update_transaction_status(invoice_id, "paid")
    remove_pending_invoice(invoice_id)

# ========== ФОНОВАЯ ПРОВЕРКА ПЛАТЕЖЕЙ ==========
def background_payment_checker():
    while True:
        time.sleep(30)
        try:
            pending = get_all_pending_invoices()
            for inv in pending:
                status = check_invoice(inv['invoice_id'])
                if status == "paid":
                    process_paid_invoice(
                        inv['invoice_id'], inv['chat_id'], inv['user_id'],
                        bool(inv['is_topup']), inv['price'], inv['item_name'], inv['qty']
                    )
                elif status == "expired":
                    update_transaction_status(inv['invoice_id'], "expired")
                    remove_pending_invoice(inv['invoice_id'])
        except Exception as e:
            logger.exception("Ошибка в фоновой проверке платежей")

# Запуск фонового потока
threading.Thread(target=background_payment_checker, daemon=True).start()

# ========== КЛАВИАТУРЫ ==========
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
    user = get_user_cached(uid)
    uname = f"@{call.from_user.username}" if call.from_user.username else "нет"
    text = (f"🏛 Мой профиль ⌵\n\n"
            f"Телеграм ID: {uid}\n"
            f"Имя пользователя: {uname}\n\n"
            f"💰 Баланс: ${user['balance']:.2f}\n\n"
            f"Куплено товаров: {user['bought']}\n"
            f"Общая сумма покупок: ${user['total_spent']:.2f}")
    safe_edit(call.message.chat.id, call.message.message_id, text, main_menu_kb())

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    ref = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        ref = args[1][3:]

    user = get_user_cached(uid)
    if not user.get('referrer') and ref and ref != uid:
        user['referrer'] = ref
        save_user_cached(user)
        # Увеличиваем счётчик рефералов у реферера
        referrer = get_user_cached(ref)
        if referrer:
            referrer['referral_count'] = referrer.get('referral_count', 0) + 1
            save_user_cached(referrer)
            new_user_name = message.from_user.username
            new_user_id = uid
            if new_user_name:
                link = f'<a href="tg://user?id={new_user_id}">@{html.escape(new_user_name)}</a>'
            else:
                link = f'<a href="tg://user?id={new_user_id}">Пользователь</a>'
            bot.send_message(ref, f"У вас новый реферал: {link}", parse_mode="HTML")

    uname = f"@{message.from_user.username}" if message.from_user.username else "нет"
    text = (f"🏛 Мой профиль ⌵\n\n"
            f"Телеграм ID: {uid}\n"
            f"Имя пользователя: {uname}\n\n"
            f"💰 Баланс: ${user['balance']:.2f}\n\n"
            f"Куплено товаров: {user['bought']}\n"
            f"Общая сумма покупок: ${user['total_spent']:.2f}")
    bot.send_message(message.chat.id, text, reply_markup=main_menu_kb())

@bot.callback_query_handler(func=lambda c: c.data == "menu")
def go_menu(call):
    edit_main_menu(call)

# ========== ПРОМОКОД ==========
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

    refresh_promo_cache()
    promo = _promo_cache.get(code)
    user = get_user_cached(uid)

    if not promo:
        bot.send_message(message.chat.id, "Промокод не найден.")
        start(message)
        return

    if not promo.get("active", True):
        bot.send_message(message.chat.id, "Промокод неактивен.")
        start(message)
        return

    if promo["activations"] >= promo["max_activations"]:
        bot.send_message(message.chat.id, "Промокод исчерпан.")
        start(message)
        return

    if code in user.get("used_promos", []):
        bot.send_message(message.chat.id, "Вы уже использовали этот промокод.")
        start(message)
        return

    bonus = promo["bonus"]
    user['balance'] += bonus
    user.setdefault("used_promos", []).append(code)
    save_user_cached(user)

    promo["activations"] += 1
    if promo["activations"] >= promo["max_activations"]:
        promo["active"] = False
    save_promocode(code, promo["bonus"], promo["max_activations"], promo["activations"], promo["active"])
    refresh_promo_cache()

    bot.send_message(message.chat.id, f"Промокод активирован! +${bonus:.2f}")
    start(message)

# ========== КАТАЛОГ И ТОВАРЫ (базовая структура, как у вас) ==========
@bot.callback_query_handler(func=lambda c: c.data == "catalog")
def catalog(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📱 Аккаунты", callback_data="cat_accounts"),
           types.InlineKeyboardButton("🌐 Социальные сети", callback_data="cat_social"),
           types.InlineKeyboardButton("🎓 Обучение", callback_data="cat_learning"),
           types.InlineKeyboardButton("🌐 Proxy", callback_data="cat_proxy"),
           types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите категорию:", kb)

# ---------- Аккаунты ----------
@bot.callback_query_handler(func=lambda c: c.data == "cat_accounts")
def accounts(call):
    plats = ["Kleinanzeigen","Wallapop","Milanuncios","OfferUp","Poshmark",
             "Ricardo","Tutti","Subito","Marktplaats","Finn.no","Blocket",
             "Tori.fi","DBA.dk","Depop","Etsy","Reverb","OLX"]
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
    class OrderData: pass
    order = OrderData()
    order.item_name = name
    order.price = price
    order.stock = stock
    if price < 10:
        order.min_qty = max(1, math.ceil(11 / price))
    else:
        order.min_qty = 1
    order.back_cb = "cat_accounts"
    user_orders[uid] = order
    show_item_qty(call, uid)

# ----- Wallapop (сокращённо, как в оригинале) -----
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
    class OrderData: pass
    order = OrderData()
    order.item_name = "Hand-Reg Wallapop 2026"
    order.price = 0.75
    order.stock = 105
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / 0.75))
    else:
        order.min_qty = 1
    order.back_cb = "platform_Wallapop"
    user_orders[uid] = order
    show_item_qty(call, uid)

@bot.callback_query_handler(func=lambda c: c.data in ["wallapop_brute_old","wallapop_brute_new"])
def wallapop_brute_item(call):
    uid = str(call.from_user.id)
    class OrderData: pass
    order = OrderData()
    if call.data == "wallapop_brute_old":
        order.item_name = "🇪🇸 Brt Cookie 2013-2023"
        order.price = 9.9
        order.stock = 5
    else:
        order.item_name = "Cookie 2024-2025"
        order.price = 5.99
        order.stock = 4
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / order.price))
    else:
        order.min_qty = 1
    order.back_cb = "wallapop_brute"
    user_orders[uid] = order
    show_item_qty(call, uid)

# ----- Milanuncios -----
@bot.callback_query_handler(func=lambda c: c.data == "platform_Milanuncios")
def milanuncios(call):
    uid = str(call.from_user.id)
    class OrderData: pass
    order = OrderData()
    order.item_name = "🇪🇸 Milanuncios.com • BRUTE • MIX"
    order.price = 2.50
    order.stock = 85
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / 2.5))
    else:
        order.min_qty = 1
    order.back_cb = "platform_Milanuncios"
    user_orders[uid] = order
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
    class OrderData: pass
    order = OrderData()
    order.item_name = "OfferUp.com BRUTE MIX"
    order.price = 5.0
    order.stock = 30
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / 5.0))
    else:
        order.min_qty = 1
    order.back_cb = "platform_OfferUp"
    user_orders[uid] = order
    show_item_qty(call, uid)

@bot.callback_query_handler(func=lambda c: c.data == "offerup_handreg")
def offerup_handreg(call):
    uid = str(call.from_user.id)
    class OrderData: pass
    order = OrderData()
    order.item_name = "🇺🇸 Hand-Reg OfferUp 2026"
    order.price = 0.40
    order.stock = 84
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / 0.4))
    else:
        order.min_qty = 1
    order.back_cb = "platform_OfferUp"
    user_orders[uid] = order
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
    class OrderData: pass
    order = OrderData()
    order.item_name = name
    order.price = price
    order.stock = stock
    if price < 10:
        order.min_qty = max(1, math.ceil(11 / price))
    else:
        order.min_qty = 1
    order.back_cb = "cat_accounts"
    user_orders[uid] = order
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
    class OrderData: pass
    order = OrderData()
    order.country = country
    user_orders[uid] = order
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
    name, price, stock, _ = olx_data[country][typ]
    uid = str(call.from_user.id)
    class OrderData: pass
    order = OrderData()
    order.item_name = name
    order.price = price
    order.stock = stock
    if price < 10:
        order.min_qty = max(1, math.ceil(11 / price))
    else:
        order.min_qty = 1
    order.back_cb = f"olx_{country}"
    user_orders[uid] = order
    show_item_qty(call, uid)

# ========== СОЦИАЛЬНЫЕ СЕТИ (WhatsApp, Telegram) ==========
# (полностью аналогично вашему коду, но с использованием безопасных функций)
# Я оставлю их как у вас, просто скопирую с небольшими правками для совместимости

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
        class OrderData: pass
        user_orders[uid] = OrderData()
    order = user_orders[uid]
    countries = list(country_flags.keys())
    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in countries:
        btn_text = f"👉 {country_flags[c]} {c}" if c == getattr(order, 'country', None) else f"{country_flags[c]} {c}"
        kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"tg_country_{c}"))
    kb.add(types.InlineKeyboardButton("Подтвердить выбор", callback_data="tg_confirm"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_social"))
    flag = country_flags.get(getattr(order, 'country', ''), '')
    display = f"{flag} {order.country}" if hasattr(order, 'country') and order.country else "-"
    safe_edit(call.message.chat.id, call.message.message_id,
              f"📱 Telegram аккаунты\n🌍 Выбранная страна: {display}", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tg_country_"))
def tg_country(call):
    country = call.data.split("_", 2)[2]
    uid = str(call.from_user.id)
    if uid not in user_orders:
        class OrderData: pass
        user_orders[uid] = OrderData()
    user_orders[uid].country = country
    tg_start(call)

@bot.callback_query_handler(func=lambda c: c.data == "tg_confirm")
def tg_confirm(call):
    uid = str(call.from_user.id)
    if uid not in user_orders or not hasattr(user_orders[uid], 'country') or not user_orders[uid].country:
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
def wa_start(call):
    uid = str(call.from_user.id)
    if uid not in user_orders:
        class OrderData: pass
        user_orders[uid] = OrderData()
    order = user_orders[uid]
    countries = list(country_flags.keys())
    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in countries:
        btn_text = f"👉 {country_flags[c]} {c}" if c == getattr(order, 'country', None) else f"{country_flags[c]} {c}"
        kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"wa_country_{c}"))
    kb.add(types.InlineKeyboardButton("Подтвердить выбор", callback_data="wa_confirm"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cat_social"))
    flag = country_flags.get(getattr(order, 'country', ''), '')
    display = f"{flag} {order.country}" if hasattr(order, 'country') and order.country else "-"
    safe_edit(call.message.chat.id, call.message.message_id,
              f"📲WhatsApp аккаунты\n🌍 Выбранная страна: {display}", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("wa_country_"))
def wa_country(call):
    country = call.data.split("_", 2)[2]
    uid = str(call.from_user.id)
    if uid not in user_orders:
        class OrderData: pass
        user_orders[uid] = OrderData()
    user_orders[uid].country = country
    wa_start(call)

@bot.callback_query_handler(func=lambda c: c.data == "wa_confirm")
def wa_install(call):
    uid = str(call.from_user.id)
    if uid not in user_orders or not hasattr(user_orders[uid], 'country') or not user_orders[uid].country:
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

# ========== КОРЗИНА ==========
@bot.callback_query_handler(func=lambda c: c.data == "add_to_cart")
def add_to_cart(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order:
        bot.answer_callback_query(call.id, "Сначала выберите товар.")
        return
    qty = 1 if getattr(order, 'no_qty', False) else (order.qty if order.qty > 0 else 1)
    carts.setdefault(uid, []).append({
        "item_name": order.item_name,
        "price": order.price,
        "qty": qty,
        "specs": f"{getattr(order, 'country', '')} {getattr(order, 'install', '')}"
    })
    total_items = sum(i["qty"] for i in carts[uid])
    bot.answer_callback_query(call.id, f"Добавлено! Товаров в корзине: {total_items}")
    # добавляем кнопку корзины в текущее меню
    kb = call.message.reply_markup
    if kb and not any("view_cart" in btn.callback_data for row in kb.keyboard for btn in row):
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
    # Проверка минимальной суммы 10$
    if total < 10:
        bot.answer_callback_query(call.id, "Минимальная сумма заказа в корзине – 10$", show_alert=True)
        return
    desc = ", ".join(f"{i['item_name']} x{i['qty']}" for i in carts[uid])
    inv_url, inv_id, err = create_invoice(total, desc)
    if err:
        bot.send_message(call.message.chat.id, f"Ошибка создания счёта: {err}")
        return
    # Сохраняем информацию об инвойсе в БД для фоновой проверки
    add_pending_invoice(inv_id, call.message.chat.id, uid, False, total, desc, 1)
    # Логируем транзакцию
    user = get_user_cached(uid)
    add_transaction(uid, "buy", total, item=desc, ref=user.get('referrer'), invoice_id=inv_id)
    # Создаём временный заказ для ручной проверки (опционально)
    class TempOrder: pass
    temp = TempOrder()
    temp.invoice_id = inv_id
    temp.is_topup = False
    user_orders[uid] = temp
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(call.message.chat.id,
                     f"Счёт на оплату корзины создан.\nТоваров: {len(carts[uid])}\nСумма: ${total:.2f}",
                     reply_markup=kb)
    carts.pop(uid, None)

@bot.callback_query_handler(func=lambda c: c.data == "buy_now")
def buy_now(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order:
        bot.answer_callback_query(call.id, "Сначала выберите товар.")
        return
    qty = 1 if getattr(order, 'no_qty', False) else (order.qty if order.qty > 0 else 1)
    total = order.price * qty
    # Проверка минимальной суммы 10$
    if total < 10:
        bot.answer_callback_query(call.id, "Минимальная сумма заказа – 10$", show_alert=True)
        return
    inv_url, inv_id, err = create_invoice(total, f"{order.item_name} x{qty}")
    if err:
        bot.send_message(call.message.chat.id, "Ошибка создания счёта.")
        return
    add_pending_invoice(inv_id, call.message.chat.id, uid, False, order.price, order.item_name, qty)
    user = get_user_cached(uid)
    add_transaction(uid, "buy", total, item=order.item_name, ref=user.get('referrer'), invoice_id=inv_id)
    order.invoice_id = inv_id
    order.is_topup = False
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(call.message.chat.id,
                     f"Счёт на оплату создан.\nТовар: {order.item_name}\nКол-во: {qty}\nСумма: ${total:.2f}",
                     reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "check_payment")
def check_payment(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order or not hasattr(order, 'invoice_id') or not order.invoice_id:
        bot.answer_callback_query(call.id, "Нет активного счёта.")
        return
    status = check_invoice(order.invoice_id)
    if status == "paid":
        # Если оплачено, обрабатываем (используем ту же функцию)
        process_paid_invoice(order.invoice_id, call.message.chat.id, uid,
                             getattr(order, 'is_topup', False),
                             order.price,
                             getattr(order, 'item_name', ''),
                             1 if getattr(order, 'no_qty', False) else (order.qty if hasattr(order, 'qty') else 1))
        user_orders.pop(uid, None)
        bot.send_message(call.message.chat.id, "✅ Оплата подтверждена!")
    else:
        bot.answer_callback_query(call.id, "Оплата ещё не поступила. Подождите или попробуйте позже.")

# ========== ОБУЧЕНИЕ, PROXY, PREMIUM, ПОПОЛНЕНИЕ, РЕФЕРАЛКА, ИСТОРИЯ, ПОДДЕРЖКА ==========
# (Оставлены как в оригинале, но с использованием новых функций БД и проверок)

@bot.callback_query_handler(func=lambda c: c.data == "cat_learning")
def learning(call):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎓 Обучение EU olx | 25$ | ∞ шт", callback_data="learn_buy"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "Обучение :", kb)

@bot.callback_query_handler(func=lambda c: c.data == "learn_buy")
def learning_buy(call):
    uid = str(call.from_user.id)
    class OrderData: pass
    order = OrderData()
    order.item_name = "Полное обучение + мануалы по EU OLX. Личный наставник 2 нед."
    order.price = 25.00
    order.no_qty = True
    if order.price < 10:
        order.min_qty = max(1, math.ceil(11 / order.price))
    else:
        order.min_qty = 1
    user_orders[uid] = order
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("🏠 Меню", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id,
              "Полное обучение + мануалы по EU OLX (страна любая). Личный наставник на 2 недели доведёт до профита. Если нет – вернём деньги.", kb)

@bot.callback_query_handler(func=lambda c: c.data == "cat_proxy")
def proxy(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("300 IPs | 13$ | 235 шт", callback_data="proxy_300"))
    kb.add(types.InlineKeyboardButton("800 IPs | 33$ | 36 шт", callback_data="proxy_800"))
    kb.add(types.InlineKeyboardButton("5000 IPs | 165$ | 6 шт", callback_data="proxy_5000"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, "9Proxy – тариф:", kb)

proxy_data = {
    "proxy_300": ("9Proxy 300 IPs", 13, 235, 1),
    "proxy_800": ("9Proxy 800 IPs", 33, 36, 1),
    "proxy_5000": ("9Proxy 5000 IPs", 165, 6, 1),
}

@bot.callback_query_handler(func=lambda c: c.data in proxy_data)
def proxy_item(call):
    name, price, stock, minq = proxy_data[call.data]
    uid = str(call.from_user.id)
    class OrderData: pass
    order = OrderData()
    order.item_name = name
    order.price = price
    order.stock = stock
    if price < 10:
        order.min_qty = max(1, math.ceil(11 / price))
    else:
        order.min_qty = 1
    order.back_cb = "cat_proxy"
    user_orders[uid] = order
    show_item_qty(call, uid)

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
    class OrderData: pass
    order = OrderData()
    order.item_name = name
    order.price = price
    order.no_qty = True
    if price < 10:
        order.min_qty = max(1, math.ceil(11 / price))
    else:
        order.min_qty = 1
    user_orders[uid] = order
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="premium_menu"))
    safe_edit(call.message.chat.id, call.message.message_id, f"{name}\nЦена: ${price}", kb)

@bot.callback_query_handler(func=lambda c: c.data == "topup")
def topup_start(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.edit_message_text("Введите сумму пополнения в $ (мин 10$):", call.message.chat.id, call.message.message_id,
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
        bot.send_message(message.chat.id, "Неверная сумма.")
        start(message)
        return
    uid = str(message.from_user.id)
    inv_url, inv_id, err = create_invoice(amount, "Пополнение баланса")
    if err:
        bot.send_message(message.chat.id, "Ошибка создания счёта.")
        start(message)
        return
    add_pending_invoice(inv_id, message.chat.id, uid, True, amount, "Пополнение баланса", 1)
    user = get_user_cached(uid)
    add_transaction(uid, "topup", amount, ref=user.get('referrer'), invoice_id=inv_id)
    class TempOrder: pass
    temp = TempOrder()
    temp.invoice_id = inv_id
    temp.is_topup = True
    temp.price = amount
    temp.item_name = "Пополнение баланса"
    user_orders[uid] = temp
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(message.chat.id, f"Счёт на пополнение ${amount:.2f} создан.", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "referral_info")
def referral_info(call):
    uid = str(call.from_user.id)
    user = get_user_cached(uid)
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start=ref{uid}"
    earned = user.get("referral_earned", 0.0)
    invited = user.get("referral_count", 0)
    text = (f"📨 Приглашай друзей в AURORA Shop и получай 2% кешбэка с их покупок!\n"
            f"Заработано всего: ${earned:.2f}\n"
            f"Приглашено всего: {invited}\n\n"
            f"🔗 Твоя реферальная ссылка:\n{ref_link}")
    safe_edit(call.message.chat.id, call.message.message_id, text, back_btn("menu"))

@bot.callback_query_handler(func=lambda c: c.data == "history")
def history(call):
    uid = str(call.from_user.id)
    my_tx = get_user_transactions(uid, typ="buy", status="paid")
    if not my_tx:
        txt = "Пока нет завершённых покупок."
    else:
        txt = "📜 История покупок:\n" + "\n".join(f"{t['item']} – ${t['amount']:.2f}" for t in my_tx[-10:])
    safe_edit(call.message.chat.id, call.message.message_id, txt, back_btn("menu"))

# ========== АДМИН-ПАНЕЛЬ ==========
@bot.message_handler(commands=['admin'])
def admin_command(message):
    if not is_admin(message.from_user.id):
        return
    show_admin_panel(message.chat.id, None)

def show_admin_panel(chat_id, edit_msg_id):
    all_users = get_all_users()
    paid_tx = get_all_transactions(status="paid")
    total_revenue = sum(t['amount'] for t in paid_tx)
    pending_tx = get_all_transactions(status="pending")
    promos = get_all_promocodes()

    text = (
        f"👑 **Админ-панель AURORA Shop**\n\n"
        f"👤 Пользователей: {len(all_users)}\n"
        f"💰 Выручка: ${total_revenue:.2f}\n"
        f"⏳ Ожидают оплаты: {len(pending_tx)}\n"
        f"🏷 Промокодов: {len(promos)}\n"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("🏷 Добавить промокод", callback_data="admin_add_promo"),
        types.InlineKeyboardButton("📋 Список промокодов", callback_data="admin_list_promos"),
        types.InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
        types.InlineKeyboardButton("🔄 Обновить", callback_data="admin_refresh"),
    )
    if edit_msg_id:
        safe_edit(chat_id, edit_msg_id, text, kb, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "admin_refresh")
def admin_refresh(call):
    if not is_admin(call.from_user.id):
        return
    show_admin_panel(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == "admin_broadcast")
def admin_broadcast_start(call):
    if not is_admin(call.from_user.id):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.send_message(call.message.chat.id, "Введите текст для рассылки всем пользователям:")
    bot.register_next_step_handler(msg, admin_broadcast_send)
    bot.answer_callback_query(call.id)

def admin_broadcast_send(message):
    if not is_admin(message.from_user.id):
        return
    text = message.text
    all_users = get_all_users()
    sent = 0
    failed = 0
    for uid in all_users:
        try:
            bot.send_message(uid, f"📢 **Сообщение от администрации:**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception as e:
            failed += 1
    bot.send_message(message.chat.id, f"✅ Рассылка завершена.\n📨 Отправлено: {sent}\n❌ Ошибок: {failed}")

@bot.callback_query_handler(func=lambda c: c.data == "admin_add_promo")
def admin_add_promo_start(call):
    if not is_admin(call.from_user.id):
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.send_message(call.message.chat.id, "Введите код промокода:")
    bot.register_next_step_handler(msg, admin_add_promo_code)
    bot.answer_callback_query(call.id)

def admin_add_promo_code(message):
    code = message.text.strip().upper()
    msg = bot.send_message(message.chat.id, f"Код: {code}\nВведите сумму бонуса в $:")
    bot.register_next_step_handler(msg, admin_add_promo_bonus, code)

def admin_add_promo_bonus(message, code):
    try:
        bonus = float(message.text.strip())
        if bonus <= 0:
            raise ValueError
    except ValueError:
        msg = bot.send_message(message.chat.id, "Неверная сумма. Введите число больше 0:")
        bot.register_next_step_handler(msg, admin_add_promo_bonus, code)
        return
    msg = bot.send_message(message.chat.id, f"Бонус: ${bonus:.2f}\nВведите макс. количество активаций:")
    bot.register_next_step_handler(msg, admin_add_promo_max, code, bonus)

def admin_add_promo_max(message, code, bonus):
    try:
        max_act = int(message.text.strip())
        if max_act <= 0:
            raise ValueError
    except ValueError:
        msg = bot.send_message(message.chat.id, "Неверное число. Введите целое положительное число:")
        bot.register_next_step_handler(msg, admin_add_promo_max, code, bonus)
        return
    save_promocode(code, bonus, max_act)
    bot.send_message(message.chat.id, f"✅ Промокод **{code}** создан!\nБонус: ${bonus:.2f}\nАктиваций: {max_act}")
    show_admin_panel(message.chat.id, None)

@bot.callback_query_handler(func=lambda c: c.data == "admin_list_promos")
def admin_list_promos(call):
    if not is_admin(call.from_user.id):
        return
    promos = get_all_promocodes()
    if not promos:
        text = "Нет созданных промокодов."
    else:
        lines = ["📋 **Список промокодов:**"]
        for code, data in sorted(promos.items(), key=lambda x: x[1].get('activations', 0), reverse=True):
            status = "✅" if data.get('active') else "❌"
            lines.append(f"{status} `{code}` — ${data['bonus']:.2f} ({data['activations']}/{data['max_activations']})")
        text = "\n".join(lines)
    safe_edit(call.message.chat.id, call.message.message_id, text, back_btn("admin_refresh"), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "admin_users")
def admin_users(call):
    if not is_admin(call.from_user.id):
        return
    all_uids = get_all_users()
    if not all_uids:
        bot.send_message(call.message.chat.id, "Нет зарегистрированных пользователей.")
        return
    text_lines = [f"👥 **Пользователи ({len(all_uids)})**"]
    for uid in sorted(all_uids, key=lambda x: int(x)):
        user = get_user_cached(uid)
        spent = user.get('total_spent', 0)
        text_lines.append(f"`{uid}` — куплено: {user.get('bought', 0)} шт., потрачено: ${spent:.2f}")
    # Отправляем частями, если много пользователей
    full_text = "\n".join(text_lines)
    if len(full_text) > 3000:
        for chunk in [full_text[i:i+3000] for i in range(0, len(full_text), 3000)]:
            bot.send_message(call.message.chat.id, chunk, parse_mode="Markdown")
    else:
        safe_edit(call.message.chat.id, call.message.message_id, full_text, back_btn("admin_refresh"), parse_mode="Markdown")

# ========== ПОДДЕРЖКА ==========
active_tickets = {}  # временно в памяти (можно потом перенести в БД, но для простоты оставим)

@bot.callback_query_handler(func=lambda c: c.data == "support_start")
def support_start(call):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.send_message(call.message.chat.id, "Опишите вашу проблему, и техподдержка скоро ответит.\nВведите сообщение:")
    bot.register_next_step_handler(msg, support_forward_to_admin)

def support_forward_to_admin(message):
    uid = str(message.from_user.id)
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID {uid}"
    safe_text = html.escape(message.text)
    text = f"📩 Новое обращение от {html.escape(username)}:\n{safe_text}"
    admin_id = int(ADMIN_ID_SUPPORT)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Ответить", callback_data=f"support_reply_{uid}"))
    kb.add(types.InlineKeyboardButton("❌ Скрыть", callback_data=f"support_hide_{uid}"))
    try:
        bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
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
    if str(call.from_user.id) != ADMIN_ID_SUPPORT:
        bot.answer_callback_query(call.id, "Нет прав.")
        return
    msg = bot.send_message(call.message.chat.id, "Введите ответ пользователю:")
    bot.register_next_step_handler(msg, support_send_reply, user_id)
    bot.answer_callback_query(call.id)

def support_send_reply(message, user_id):
    safe_text = html.escape(message.text)
    text = f"📬 Ответ от техподдержки:\n{safe_text}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Ответить", callback_data=f"support_user_reply_{user_id}"))
    bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
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
    if str(call.from_user.id) != ADMIN_ID_SUPPORT:
        bot.answer_callback_query(call.id, "Нет прав.")
        return
    bot.delete_message(call.message.chat.id, call.message.message_id)

# ========== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ПОКАЗА ТОВАРА С КОЛИЧЕСТВОМ ==========
def show_item_qty(call, uid):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    order = user_orders[uid]
    if order.stock == 0:
        text = f"⭐️ {order.item_name}\nЦена: ${order.price:.2f}\nВ наличии: 0 шт."
        safe_edit(call.message.chat.id, call.message.message_id, text, back_btn("menu"))
        return
    text = (f"⭐️ {order.item_name}\nЦена: ${order.price:.2f} за шт.\n"
            f"В наличии: {order.stock} шт.\nМинимально: {order.min_qty} шт.\n\n"
            "✏️ Введите желаемое количество:")
    kb = types.InlineKeyboardMarkup()
    back_cb = getattr(order, 'back_cb', 'catalog')
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=back_cb))
    safe_edit(call.message.chat.id, call.message.message_id, text, kb)
    bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_qty)

def process_qty(message):
    uid = str(message.from_user.id)
    order = user_orders.get(uid)
    if not order:
        return
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        msg = bot.send_message(message.chat.id, "Неверное число. Введите целое положительное число.")
        bot.register_next_step_handler(msg, process_qty)
        return
    if qty < order.min_qty:
        msg = bot.send_message(message.chat.id, f"Минимальное количество: {order.min_qty} шт. (чтобы сумма была не менее 10$)")
        bot.register_next_step_handler(msg, process_qty)
        return
    if qty > order.stock:
        msg = bot.send_message(message.chat.id, f"Максимум: {order.stock} шт.")
        bot.register_next_step_handler(msg, process_qty)
        return
    order.qty = qty
    total = order.price * qty
    if total < 10:
        msg = bot.send_message(message.chat.id, f"Сумма заказа должна быть не менее 10$. Увеличьте количество (мин. {order.min_qty} шт.).")
        bot.register_next_step_handler(msg, process_qty)
        return
    text = f"{order.item_name} × {qty}\nИтого: ${total:.2f}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("🧺 В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    bot.send_message(message.chat.id, text, reply_markup=kb)

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    logger.info("Основной бот запущен и готов к работе!")
    bot.infinity_polling()