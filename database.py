import sqlite3
import json
import threading
from datetime import datetime

_db_lock = threading.Lock()

def get_connection():
    """Возвращает соединение с БД (не блокирует, блокировка снаружи)"""
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Создаёт таблицы, если их нет"""
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                balance REAL DEFAULT 0,
                premium INTEGER DEFAULT 0,
                bought INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                used_promos TEXT DEFAULT '[]',
                referrer TEXT,
                referral_earned REAL DEFAULT 0,
                referral_count INTEGER DEFAULT 0,
                registered TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                type TEXT,
                amount REAL,
                item TEXT,
                ref TEXT,
                status TEXT,
                invoice_id TEXT,
                timestamp TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                bonus REAL,
                max_activations INTEGER,
                activations INTEGER,
                active INTEGER
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pending_invoices (
                invoice_id TEXT PRIMARY KEY,
                chat_id INTEGER,
                user_id TEXT,
                is_topup INTEGER,
                price REAL,
                item_name TEXT,
                qty INTEGER,
                created_at TEXT
            )
        ''')

# ---- Пользователи ----
def get_user(user_id):
    with _db_lock:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                user = dict(row)
                user['used_promos'] = json.loads(user['used_promos'])
                user['premium'] = bool(user['premium'])
                return user
            return None

def save_user(user):
    with _db_lock:
        with get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, balance, premium, bought, total_spent, used_promos, referrer, referral_earned, referral_count, registered)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            ''', (
                user['user_id'],
                user.get('balance', 0.0),
                1 if user.get('premium', False) else 0,
                user.get('bought', 0),
                user.get('total_spent', 0.0),
                json.dumps(user.get('used_promos', [])),
                user.get('referrer'),
                user.get('referral_earned', 0.0),
                user.get('referral_count', 0),
                user.get('registered', datetime.now().isoformat())
            ))

def get_all_users():
    with _db_lock:
        with get_connection() as conn:
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            return [row['user_id'] for row in rows]

# ---- Транзакции ----
def add_transaction(uid, typ, amount, item=None, ref=None, status='pending', invoice_id=None):
    with _db_lock:
        with get_connection() as conn:
            conn.execute('''
                INSERT INTO transactions (user_id, type, amount, item, ref, status, invoice_id, timestamp)
                VALUES (?,?,?,?,?,?,?,?)
            ''', (uid, typ, amount, item, ref, status, invoice_id, datetime.now().isoformat()))

def update_transaction_status(invoice_id, status):
    with _db_lock:
        with get_connection() as conn:
            conn.execute("UPDATE transactions SET status = ? WHERE invoice_id = ?", (status, invoice_id))

def get_all_transactions(typ=None, status=None):
    with _db_lock:
        with get_connection() as conn:
            query = "SELECT * FROM transactions"
            params = []
            conditions = []
            if typ:
                conditions.append("type = ?")
                params.append(typ)
            if status:
                conditions.append("status = ?")
                params.append(status)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

def get_user_transactions(uid, typ=None, status=None):
    with _db_lock:
        with get_connection() as conn:
            query = "SELECT * FROM transactions WHERE user_id = ?"
            params = [uid]
            if typ:
                query += " AND type = ?"
                params.append(typ)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY timestamp DESC"
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

# ---- Промокоды ----
def get_promocode(code):
    with _db_lock:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM promocodes WHERE code = ?", (code,)).fetchone()
            return dict(row) if row else None

def save_promocode(code, bonus, max_activations, activations=0, active=True):
    with _db_lock:
        with get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO promocodes (code, bonus, max_activations, activations, active)
                VALUES (?,?,?,?,?)
            ''', (code, bonus, max_activations, activations, 1 if active else 0))

def get_all_promocodes():
    with _db_lock:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM promocodes").fetchall()
            return {row['code']: dict(row) for row in rows}

# ---- Ожидающие инвойсы ----
def add_pending_invoice(invoice_id, chat_id, user_id, is_topup, price, item_name, qty):
    with _db_lock:
        with get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO pending_invoices (invoice_id, chat_id, user_id, is_topup, price, item_name, qty, created_at)
                VALUES (?,?,?,?,?,?,?,?)
            ''', (invoice_id, chat_id, user_id, 1 if is_topup else 0, price, item_name, qty, datetime.now().isoformat()))

def get_all_pending_invoices():
    with _db_lock:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM pending_invoices").fetchall()
            return [dict(row) for row in rows]

def remove_pending_invoice(invoice_id):
    with _db_lock:
        with get_connection() as conn:
            conn.execute("DELETE FROM pending_invoices WHERE invoice_id = ?", (invoice_id,))

# ---- Миграция из JSON (если файлы существуют) ----
def migrate_from_json():
    import os
    import json
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
    users_json = os.path.join(DATA_DIR, "users.json")
    transactions_json = os.path.join(DATA_DIR, "transactions.json")
    promo_json = os.path.join(DATA_DIR, "promo.json")
    
    # Миграция пользователей
    if os.path.exists(users_json):
        with open(users_json, 'r', encoding='utf-8') as f:
            old_users = json.load(f)
        for uid, data in old_users.items():
            if not get_user(uid):
                user = {
                    'user_id': uid,
                    'balance': data.get('balance', 0.0),
                    'premium': data.get('premium', False),
                    'bought': data.get('bought', 0),
                    'total_spent': data.get('total_spent', 0.0),
                    'used_promos': data.get('used_promos', []),
                    'referrer': data.get('referrer'),
                    'referral_earned': data.get('referral_earned', 0.0),
                    'referral_count': data.get('referral_count', 0),
                    'registered': data.get('registered', datetime.now().isoformat())
                }
                save_user(user)
    # Миграция транзакций
    if os.path.exists(transactions_json):
        with open(transactions_json, 'r', encoding='utf-8') as f:
            old_tx = json.load(f)
        for tx in old_tx:
            add_transaction(
                tx['user_id'], tx['type'], tx['amount'],
                tx.get('item'), tx.get('ref'), tx.get('status', 'pending'), tx.get('invoice_id')
            )
    # Миграция промокодов
    if os.path.exists(promo_json):
        with open(promo_json, 'r', encoding='utf-8') as f:
            old_promo = json.load(f)
        for code, data in old_promo.items():
            if not get_promocode(code):
                save_promocode(code, data['bonus'], data['max_activations'], data.get('activations', 0), data.get('active', True))