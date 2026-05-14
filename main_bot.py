import telebot
from telebot import types
import json, os, requests, time, threading
from datetime import datetime
import math

print("=" * 50)
print("ЗАПУСК ОСНОВНОГО БОТА (ИСПРАВЛЕННЫЙ, С ЗАПРОСОМ КОЛИЧЕСТВА)")
print("Переменные окружения, которые ВИДИТ контейнер:")
for key in os.environ.keys():
    if "TOKEN" in key or "ID" in key:
        val = os.environ[key]
        print(f"  {key} = {val[:10]}..." if val else f"  {key} = (пусто)")
print("=" * 50)

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

def get_current_goods():
    goods_path = os.path.join(DATA_DIR, "goods.json")
    if os.path.exists(goods_path):
        with open(goods_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

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
        self.item_key = ""
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
                    f"📦 Товар: {order.item_key}\n"
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

# ======================= ДИНАМИЧЕСКИЙ КАТАЛОГ =======================
@bot.callback_query_handler(func=lambda c: c.data == "catalog")
def catalog(call):
    goods = get_current_goods()
    categories = sorted(set(item["category"] for item in goods.values()))
    if not categories:
        safe_edit(call.message.chat.id, call.message.message_id, "Каталог пуст.", back_btn("menu"))
        return
    kb = types.InlineKeyboardMarkup(row_width=2)
    for cat in categories:
        kb.add(types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    safe_edit(call.message.chat.id, call.message.message_id, "Выберите категорию:", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def show_category(call):
    cat = call.data[4:]
    goods = get_current_goods()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for key, item in goods.items():
        if item["category"] == cat:
            kb.add(types.InlineKeyboardButton(f"{item['name']} - ${item['price']} (в наличии: {item['stock']})", callback_data=f"item_{key}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="catalog"))
    safe_edit(call.message.chat.id, call.message.message_id, f"Категория: {cat}", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("item_"))
def show_item(call):
    key = call.data[5:]
    goods = get_current_goods()
    item = goods.get(key)
    if not item:
        bot.answer_callback_query(call.id, "Товар не найден")
        return
    uid = str(call.from_user.id)
    user_orders[uid] = OrderData()
    user_orders[uid].item_key = key
    user_orders[uid].price = item["price"]
    user_orders[uid].stock = item["stock"]
    if item["price"] < 10:
        user_orders[uid].min_qty = max(1, math.ceil(11 / item["price"]))
    else:
        user_orders[uid].min_qty = 1
    text = f"⭐️ {item['name']}\nЦена: ${item['price']:.2f}\nВ наличии: {item['stock']} шт.\n\n{item.get('description', '')}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_request"))
    kb.add(types.InlineKeyboardButton("В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{item['category']}"))
    safe_edit(call.message.chat.id, call.message.message_id, text, kb)

# ======= Запрос количества и обработка =======
@bot.callback_query_handler(func=lambda c: c.data == "buy_request")
def buy_request(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order:
        bot.answer_callback_query(call.id, "Сначала выберите товар.")
        return
    show_item_qty(call, uid)

def show_item_qty(call, uid):
    order = user_orders[uid]
    if order.stock == 0:
        text = f"⭐️ {order.item_key}\nЦена: ${order.price:.2f}\nВ наличии: 0 шт."
        safe_edit(call.message.chat.id, call.message.message_id, text, back_btn("menu"))
        return
    text = (f"⭐️ {order.item_key}\nЦена: ${order.price:.2f} за шт.\n"
            f"В наличии: {order.stock} шт.\nМинимально: {order.min_qty} шт.\n\n"
            "✏️ Введите желаемое количество:")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_qty)

def process_qty(message):
    uid = str(message.from_user.id)
    order = user_orders.get(uid)
    if not order:
        return
    try:
        qty = int(message.text.strip())
    except ValueError:
        msg = bot.send_message(message.chat.id, "Неверное число.")
        bot.register_next_step_handler(msg, process_qty)
        return
    if qty < order.min_qty:
        msg = bot.send_message(message.chat.id, f"Минимальное количество: {order.min_qty} шт. (чтобы сумма была не менее 11$)")
        bot.register_next_step_handler(msg, process_qty)
        return
    if qty > order.stock:
        msg = bot.send_message(message.chat.id, f"Максимум: {order.stock}")
        bot.register_next_step_handler(msg, process_qty)
        return
    order.qty = qty
    total = order.price * qty
    text = f"{order.item_key} × {qty}\nИтого: ${total:.2f}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Купить", callback_data="buy_now"))
    kb.add(types.InlineKeyboardButton("В корзину", callback_data="add_to_cart"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu"))
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "add_to_cart")
def add_to_cart(call):
    uid = str(call.from_user.id)
    order = user_orders.get(uid)
    if not order:
        bot.answer_callback_query(call.id, "Сначала выберите товар.")
        return
    qty = 1 if order.no_qty else (order.qty if order.qty > 0 else 1)
    carts.setdefault(uid, []).append({
        "item_key": order.item_key,
        "price": order.price,
        "qty": qty
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
        lines.append(f"{idx}. {item['item_key']} ×{item['qty']} – ${item['price']*item['qty']:.2f}")
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
    desc = ", ".join(f"{i['item_key']} x{i['qty']}" for i in carts[uid])
    inv_url, inv_id, err = create_invoice(total, desc)
    if err:
        bot.send_message(call.message.chat.id, f"Ошибка создания счёта: {err}")
        return
    order = user_orders.setdefault(uid, OrderData())
    order.invoice_id = inv_id
    order.is_topup = False
    for item in carts[uid]:
        log_tx(uid, "buy", item["price"] * item["qty"], item=item["item_key"], ref=users[uid].get("referrer"), invoice_id=inv_id)
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
    inv_url, inv_id, err = create_invoice(total, f"{order.item_key} x{qty}")
    if err:
        bot.send_message(call.message.chat.id, f"Ошибка создания счёта: {err}")
        return
    order.invoice_id = inv_id
    order.is_topup = False
    log_tx(uid, "buy", total, item=order.item_key, ref=users[uid].get("referrer"), invoice_id=inv_id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Оплатить", url=inv_url))
    kb.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment"))
    kb.add(types.InlineKeyboardButton("Меню", callback_data="menu"))
    bot.send_message(call.message.chat.id,
                     f"Счёт на оплату создан.\nТовар: {order.item_key}\nКол-во: {qty}\nСумма: ${total:.2f}",
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
                f"📦 Товар: {order.item_key}\n"
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

# ======================= ПОПОЛНЕНИЕ БАЛАНСА =======================
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

# ======================= РЕФЕРАЛЫ =======================
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

# ======================= ИСТОРИЯ ПОКУПОК =======================
@bot.callback_query_handler(func=lambda c: c.data == "history")
def history(call):
    uid = str(call.from_user.id)
    my_tx = [t for t in transactions if t["user_id"] == uid and t["type"] == "buy" and t["status"] == "paid"]
    if not my_tx:
        txt = "Пока нет завершённых покупок."
    else:
        txt = "📜 История покупок:\n" + "\n".join(f"{t['item']} – ${t['amount']:.2f}" for t in my_tx[-10:])
    safe_edit(call.message.chat.id, call.message.message_id, txt, back_btn("menu"))

# ======================= ТЕХПОДДЕРЖКА =======================
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

print("Основной бот запущен и готов к работе!")
bot.infinity_polling()
