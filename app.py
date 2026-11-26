"""Веб-приложение PizzaPoint. Структура и авторизация сделаны по образцу SportClub."""
import os
import secrets
import sqlite3
from datetime import date

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from auth import check_csrf, csrf_token, load_user, role_required, safe_next
from db import close_db, fetch_all, fetch_one, get_db, initialize_database, transaction
from admin import admin_bp

SITE_NAME = "PizzaPoint"
app = Flask(__name__)


def get_secret_key():
    """Использует заданный SECRET_KEY или хранит случайный ключ в instance."""
    secret = os.environ.get("SECRET_KEY")
    if secret:
        return secret
    os.makedirs(os.path.join(os.path.dirname(__file__), "instance"), exist_ok=True)
    secret_path = os.path.join(os.path.dirname(__file__), "instance", "secret.key")
    if not os.path.exists(secret_path):
        with open(secret_path, "w", encoding="utf-8") as secret_file:
            secret_file.write(secrets.token_hex(32))
    with open(secret_path, encoding="utf-8") as secret_file:
        return secret_file.read().strip()


app.config.update(
    SECRET_KEY=get_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,
)
app.teardown_appcontext(close_db)
app.register_blueprint(admin_bp)


@app.template_filter("rub")
def rubles(value):
    return f"{int(value or 0):,}".replace(",", " ") + " ₽"


@app.context_processor
def template_globals():
    cart_count = 0
    if g.get("user") and g.user["role"] == "customer":
        cart_count = fetch_one(
            "SELECT COALESCE(SUM(quantity), 0) FROM cart_items WHERE customer_id = ?",
            (g.user["customer_id"],),
        )[0]
    return {"SITE_NAME": SITE_NAME, "csrf_token": csrf_token, "cart_count": cart_count,
            "year": date.today().year}


@app.before_request
def prepare_request():
    load_user()
    check_csrf()


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="Недостаточно прав для просмотра страницы."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Страница не найдена."), 404


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400,
                          message=getattr(error, "description", "Некорректный запрос.")), 400


@app.route("/")
def index():
    category_id = request.args.get("category", type=int)
    categories = fetch_all("SELECT * FROM categories ORDER BY name")

    # JOIN связывает меню с категориями по products.category_id = categories.id.
    sql = """SELECT products.*, categories.name AS category_name
             FROM products
             INNER JOIN categories ON products.category_id = categories.id
             WHERE products.is_available = 1"""
    parameters = []
    if category_id:
        sql += " AND products.category_id = ?"
        parameters.append(category_id)
    sql += " ORDER BY products.id"
    products = fetch_all(sql, parameters)
    return render_template("index.html", products=products, categories=categories,
                           selected_category=category_id)


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("cabinet" if g.user["role"] == "customer" else "admin.dashboard"))

    next_url = safe_next(request.values.get("next"))
    form_data = {}
    if request.method == "POST":
        form_data = {field: request.form.get(field, "").strip()
                     for field in ("full_name", "phone", "email")}
        password = request.form.get("password", "")
        password_repeat = request.form.get("password_repeat", "")
        email = form_data["email"].lower()
        phone = "".join(character for character in form_data["phone"] if character.isdigit())

        error = None
        if len(form_data["full_name"]) < 2:
            error = "Введите ФИО (не менее 2 символов)."
        elif len(phone) < 10 or len(phone) > 15:
            error = "Укажите корректный номер телефона."
        elif "@" not in email or "." not in email.split("@")[-1]:
            error = "Введите корректный email."
        elif len(password) < 6:
            error = "Пароль должен содержать не менее 6 символов."
        elif password != password_repeat:
            error = "Пароли не совпадают."
        elif fetch_one("SELECT id FROM users WHERE email = ?", (email,)):
            error = "Аккаунт с таким email уже зарегистрирован."
        elif fetch_one("SELECT id FROM customers WHERE phone = ?", (phone,)):
            error = "Этот телефон уже используется другим аккаунтом."

        if error:
            flash(error, "error")
        else:
            connection = get_db()
            try:
                # Создание пользователя и профиля выполняется вместе: если одна вставка
                # не удастся, транзакция откатит обе операции.
                with transaction(connection):
                    user_id = connection.execute(
                        "INSERT INTO users(email, password_hash, role) VALUES (?, ?, 'customer')",
                        (email, generate_password_hash(password)),
                    ).lastrowid
                    connection.execute(
                        "INSERT INTO customers(user_id, full_name, phone) VALUES (?, ?, ?)",
                        (user_id, form_data["full_name"], phone),
                    )
            except sqlite3.IntegrityError:
                flash("Не удалось зарегистрироваться: email или телефон уже заняты.", "error")
            else:
                session.clear()
                session["user_id"] = user_id
                flash("Регистрация завершена. Добро пожаловать в PizzaPoint!", "success")
                return redirect(next_url or url_for("cabinet"))

    return render_template("register.html", form=form_data, next_url=next_url)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("cabinet" if g.user["role"] == "customer" else "admin.dashboard"))
    next_url = safe_next(request.values.get("next"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = fetch_one("SELECT * FROM users WHERE email = ?", (email,))
        if user and user["is_active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = bool(request.form.get("remember"))
            flash("Вы вошли в аккаунт.", "success")
            return redirect(next_url or (url_for("admin.dashboard") if user["role"] == "admin" else url_for("cabinet")))
        flash("Неверный email или пароль.", "error")
    return render_template("login.html", next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))


@app.route("/cart")
@role_required("customer")
def cart():
    # JOIN берёт название, цену и изображение из Products, а количество — из корзины.
    items = fetch_all(
        """SELECT cart_items.id, cart_items.quantity, products.id AS product_id,
                  products.name, products.price, products.image_url,
                  products.is_available, categories.name AS category_name
           FROM cart_items
           INNER JOIN products ON cart_items.product_id = products.id
           INNER JOIN categories ON products.category_id = categories.id
           WHERE cart_items.customer_id = ? ORDER BY cart_items.id""",
        (g.user["customer_id"],),
    )
    total = sum(item["price"] * item["quantity"] for item in items)
    return render_template("cart.html", items=items, total=total)


@app.route("/cart/add/<int:product_id>", methods=["POST"])
@role_required("customer")
def cart_add(product_id):
    product = fetch_one("SELECT id FROM products WHERE id = ? AND is_available = 1", (product_id,))
    if product is None:
        abort(404)
    customer_id = g.user["customer_id"]
    with transaction():
        existing = fetch_one(
            "SELECT id FROM cart_items WHERE customer_id = ? AND product_id = ?",
            (customer_id, product_id),
        )
        if existing:
            get_db().execute("UPDATE cart_items SET quantity = quantity + 1 WHERE id = ?", (existing["id"],))
        else:
            get_db().execute(
                "INSERT INTO cart_items(customer_id, product_id, quantity) VALUES (?, ?, 1)",
                (customer_id, product_id),
            )
    flash("Пицца добавлена в корзину.", "success")
    return redirect(request.form.get("back") or url_for("index"))


@app.route("/cart/update/<int:item_id>", methods=["POST"])
@role_required("customer")
def cart_update(item_id):
    quantity = request.form.get("quantity", type=int)
    if quantity is None or quantity < 1 or quantity > 99:
        flash("Количество должно быть от 1 до 99.", "error")
    else:
        get_db().execute(
            "UPDATE cart_items SET quantity = ? WHERE id = ? AND customer_id = ?",
            (quantity, item_id, g.user["customer_id"]),
        )
    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:item_id>", methods=["POST"])
@role_required("customer")
def cart_remove(item_id):
    get_db().execute("DELETE FROM cart_items WHERE id = ? AND customer_id = ?",
                     (item_id, g.user["customer_id"]))
    flash("Товар удалён из корзины.", "success")
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["POST"])
@role_required("customer")
def checkout():
    customer_id = g.user["customer_id"]
    comment = request.form.get("comment", "").strip()[:500]
    items = fetch_all(
        """SELECT cart_items.product_id, cart_items.quantity, products.name, products.price
           FROM cart_items JOIN products ON products.id = cart_items.product_id
           WHERE cart_items.customer_id = ? AND products.is_available = 1""",
        (customer_id,),
    )
    if not items:
        flash("Корзина пустая.", "error")
        return redirect(url_for("cart"))

    total = sum(item["price"] * item["quantity"] for item in items)
    with transaction():
        order_id = get_db().execute(
            "INSERT INTO orders(customer_id, total, comment) VALUES (?, ?, ?)",
            (customer_id, total, comment or None),
        ).lastrowid
        for item in items:
            get_db().execute(
                """INSERT INTO order_items(order_id, product_id, product_name, unit_price, quantity)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, item["product_id"], item["name"], item["price"], item["quantity"]),
            )
        get_db().execute("DELETE FROM cart_items WHERE customer_id = ?", (customer_id,))
    flash(f"Заказ №{order_id} создан!", "success")
    return redirect(url_for("cabinet"))


@app.route("/cabinet")
@role_required("customer")
def cabinet():
    customer = fetch_one("SELECT * FROM customers WHERE id = ?", (g.user["customer_id"],))
    orders = fetch_all("SELECT * FROM orders WHERE customer_id = ? ORDER BY id DESC",
                      (g.user["customer_id"],))
    # JOIN показывает названия блюд, сохранённые в строках заказа, и их количество.
    order_items = fetch_all(
        """SELECT order_items.*, orders.id AS order_number
           FROM order_items JOIN orders ON orders.id = order_items.order_id
           WHERE orders.customer_id = ? ORDER BY orders.id DESC, order_items.id""",
        (g.user["customer_id"],),
    )
    return render_template("cabinet.html", customer=customer, orders=orders, order_items=order_items)


@app.route("/cabinet/profile", methods=["POST"])
@role_required("customer")
def profile_update():
    name = request.form.get("full_name", "").strip()
    phone = "".join(character for character in request.form.get("phone", "") if character.isdigit())
    if len(name) < 2 or len(phone) < 10 or len(phone) > 15:
        flash("Проверьте ФИО и номер телефона.", "error")
    elif fetch_one("SELECT id FROM customers WHERE phone = ? AND id <> ?",
                  (phone, g.user["customer_id"])):
        flash("Этот телефон уже зарегистрирован.", "error")
    else:
        get_db().execute("UPDATE customers SET full_name = ?, phone = ? WHERE id = ?",
                         (name, phone, g.user["customer_id"]))
        flash("Профиль обновлён.", "success")
    return redirect(url_for("cabinet"))


@app.route("/cabinet/password", methods=["POST"])
@role_required("customer", "admin")
def password_change():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    repeat_password = request.form.get("password_repeat", "")
    account = fetch_one("SELECT * FROM users WHERE id = ?", (g.user["id"],))
    error = None
    if not check_password_hash(account["password_hash"], current_password):
        error = "Текущий пароль указан неверно."
    elif len(new_password) < 6:
        error = "Новый пароль должен быть не короче 6 символов."
    elif new_password != repeat_password:
        error = "Новые пароли не совпадают."
    if error:
        flash(error, "error")
    else:
        get_db().execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(new_password), g.user["id"]))
        flash("Пароль успешно изменён.", "success")
    return redirect(url_for("cabinet" if g.user["role"] == "customer" else "admin.dashboard"))


if __name__ == "__main__":
    initialize_database()
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
