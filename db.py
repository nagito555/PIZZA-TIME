"""Работа с SQLite: соединения, транзакции, схема и начальные данные."""
import os
import sqlite3
from contextlib import contextmanager

from flask import g
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.environ.get("PIZZAPOINT_DB", os.path.join(INSTANCE_DIR, "pizzapoint.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def connect():
    """Создаёт соединение и включает контроль внешних ключей SQLite."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.isolation_level = None
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_db():
    """Возвращает одно соединение на текущий Flask-запрос."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@contextmanager
def transaction(connection=None):
    """Выполняет блок в транзакции; при ошибке изменения откатываются."""
    connection = connection or get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def fetch_all(sql, parameters=()):
    return get_db().execute(sql, parameters).fetchall()


def fetch_one(sql, parameters=()):
    return get_db().execute(sql, parameters).fetchone()


def fetch_value(sql, parameters=(), default=0):
    row = fetch_one(sql, parameters)
    return row[0] if row and row[0] is not None else default


def initialize_database():
    """Создаёт таблицы и гарантирует наличие администраторской учётной записи."""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    connection = connect()
    with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
        connection.executescript(schema_file.read())

    seed_menu(connection)
    ensure_admin(connection)
    connection.close()


def seed_menu(connection):
    """Добавляет категории и демонстрационное меню, если каталог пуст."""
    categories = ["Классика", "Мясная", "Сырная"]
    for category in categories:
        connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (category,))

    if connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
        return

    menu = [
        ("Классика", "Маргарита", "Томатный соус, моцарелла, свежий базилик", 590,
         "https://images.unsplash.com/photo-1579751626657-72bc17010498?auto=format&fit=crop&w=800&q=80"),
        ("Мясная", "Пепперони", "Томатный соус, моцарелла, пикантная пепперони", 690,
         "https://images.unsplash.com/photo-1628840042765-356cda07504e?auto=format&fit=crop&w=800&q=80"),
        ("Сырная", "Четыре сыра", "Моцарелла, дорблю, пармезан, сливочный сыр", 750,
         "https://images.unsplash.com/photo-1574063382-2b2f3e8f5?auto=format&fit=crop&w=800&q=80"),
        ("Мясная", "Ветчина и грибы", "Ветчина, шампиньоны, моцарелла, томатный соус", 720,
         "https://images.unsplash.com/photo-1593560708920-61dd98c46a4e?auto=format&fit=crop&w=800&q=80"),
    ]
    for category, name, description, price, image_url in menu:
        category_row = connection.execute(
            "SELECT id FROM categories WHERE name = ?", (category,)
        ).fetchone()
        connection.execute(
            """INSERT INTO products(category_id, name, description, price, image_url)
               VALUES (?, ?, ?, ?, ?)""",
            (category_row["id"], name, description, price, image_url),
        )


def ensure_admin(connection):
    """Создаёт администратора или обновляет его роль и пароль из переменных среды."""
    email = os.environ.get("ADMIN_EMAIL", "admin@pizzapoint.local").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "123123")
    password_hash = generate_password_hash(password)
    existing = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

    if existing:
        connection.execute(
            "UPDATE users SET password_hash = ?, role = 'admin', is_active = 1 WHERE id = ?",
            (password_hash, existing["id"]),
        )
        return

    connection.execute(
        "INSERT INTO users(email, password_hash, role) VALUES (?, ?, 'admin')",
        (email, password_hash),
    )
