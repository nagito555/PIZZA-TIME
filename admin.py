"""Административная часть: меню пицц, категории и управление заказами."""
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from auth import role_required
from db import fetch_all, fetch_one, fetch_value, get_db, transaction

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
@role_required("admin")
def check_admin():
    """Все страницы blueprint доступны только администратору."""
    return None


@admin_bp.route("/")
def dashboard():
    statistics = {
        "products": fetch_value("SELECT COUNT(*) FROM products"),
        "categories": fetch_value("SELECT COUNT(*) FROM categories"),
        "orders": fetch_value("SELECT COUNT(*) FROM orders"),
        "new_orders": fetch_value("SELECT COUNT(*) FROM orders WHERE status = 'new'"),
        "customers": fetch_value("SELECT COUNT(*) FROM customers"),
        "revenue": fetch_value("SELECT SUM(total) FROM orders WHERE status <> 'cancelled'"),
    }
    recent_orders = fetch_all(
        """SELECT orders.*, customers.full_name, customers.phone
           FROM orders INNER JOIN customers ON orders.customer_id = customers.id
           ORDER BY orders.id DESC LIMIT 8"""
    )
    return render_template("admin/dashboard.html", statistics=statistics, recent_orders=recent_orders)


@admin_bp.route("/menu")
def menu():
    # JOIN выводит название категории вместо category_id.
    products = fetch_all(
        """SELECT products.*, categories.name AS category_name
           FROM products INNER JOIN categories ON products.category_id = categories.id
           ORDER BY products.id"""
    )
    return render_template("admin/menu.html", products=products)


def product_form(product=None):
    categories = fetch_all("SELECT * FROM categories ORDER BY name")
    return render_template("admin/product_form.html", product=product, categories=categories)


@admin_bp.route("/menu/add", methods=["GET", "POST"])
def product_add():
    form = request.form if request.method == "POST" else None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_name = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        image_url = request.form.get("image_url", "").strip()
        price = request.form.get("price", type=int)
        if not name or not category_name or not description or price is None or price < 0:
            flash("Заполните обязательные поля и укажите корректную цену.", "error")
        else:
            connection = get_db()
            try:
                with transaction(connection):
                    connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (category_name,))
                    category_id = connection.execute(
                        "SELECT id FROM categories WHERE name = ?", (category_name,)
                    ).fetchone()["id"]
                    connection.execute(
                        """INSERT INTO products(category_id, name, description, price, image_url)
                           VALUES (?, ?, ?, ?, ?)""",
                        (category_id, name, description, price, image_url or None),
                    )
            except sqlite3.IntegrityError:
                flash("Не удалось добавить пиццу. Проверьте данные.", "error")
            else:
                flash("Позиция меню добавлена.", "success")
                return redirect(url_for("admin.menu"))
    if request.method == "POST":
        product = {
            "name": request.form.get("name", ""),
            "category_name": request.form.get("category", ""),
            "description": request.form.get("description", ""),
            "price": request.form.get("price", ""),
            "image_url": request.form.get("image_url", ""),
            "is_available": bool(request.form.get("is_available")),
        }
    return product_form(product)


@admin_bp.route("/menu/<int:product_id>/edit", methods=["GET", "POST"])
def product_edit(product_id):
    product = fetch_one(
        """SELECT products.*, categories.name AS category_name
           FROM products JOIN categories ON products.category_id = categories.id
           WHERE products.id = ?""",
        (product_id,),
    )
    if product is None:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_name = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        image_url = request.form.get("image_url", "").strip()
        price = request.form.get("price", type=int)
        available = 1 if request.form.get("is_available") else 0
        if not name or not category_name or not description or price is None or price < 0:
            flash("Заполните обязательные поля и укажите корректную цену.", "error")
        else:
            connection = get_db()
            with transaction(connection):
                connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (category_name,))
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = ?", (category_name,)
                ).fetchone()["id"]
                connection.execute(
                    """UPDATE products SET category_id = ?, name = ?, description = ?,
                       price = ?, image_url = ?, is_available = ? WHERE id = ?""",
                    (category_id, name, description, price, image_url or None, available, product_id),
                )
            flash("Изменения сохранены.", "success")
            return redirect(url_for("admin.menu"))
        product = dict(product)
        product.update(name=name, category_name=category_name, description=description,
                      image_url=image_url, price=price, is_available=available)
    return product_form(product)


@admin_bp.route("/menu/<int:product_id>/delete", methods=["POST"])
def product_delete(product_id):
    product = fetch_one("SELECT id FROM products WHERE id = ?", (product_id,))
    if not product:
        abort(404)
    try:
        get_db().execute("DELETE FROM products WHERE id = ?", (product_id,))
    except sqlite3.IntegrityError:
        # Если товар уже входит в заказ, его лучше скрыть из меню, а не удалять.
        get_db().execute("UPDATE products SET is_available = 0 WHERE id = ?", (product_id,))
        flash("Пицца уже есть в заказах, поэтому она скрыта из меню.", "success")
    else:
        flash("Позиция меню удалена.", "success")
    return redirect(url_for("admin.menu"))


@admin_bp.route("/orders")
def orders():
    rows = fetch_all(
        """SELECT orders.*, customers.full_name, customers.phone, users.email
           FROM orders
           INNER JOIN customers ON orders.customer_id = customers.id
           INNER JOIN users ON customers.user_id = users.id
           ORDER BY orders.id DESC"""
    )
    return render_template("admin/orders.html", orders=rows)


@admin_bp.route("/orders/<int:order_id>")
def order_detail(order_id):
    order = fetch_one(
        """SELECT orders.*, customers.full_name, customers.phone, users.email
           FROM orders JOIN customers ON orders.customer_id = customers.id
           JOIN users ON customers.user_id = users.id WHERE orders.id = ?""",
        (order_id,),
    )
    if not order:
        abort(404)
    items = fetch_all("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
    return render_template("admin/order_detail.html", order=order, items=items)


@admin_bp.route("/orders/<int:order_id>/status", methods=["POST"])
def order_status(order_id):
    status = request.form.get("status", "")
    allowed_statuses = {"new", "accepted", "cooking", "ready", "delivered", "cancelled"}
    if status not in allowed_statuses:
        abort(400)
    get_db().execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    flash("Статус заказа обновлён.", "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@admin_bp.route("/customers")
def customers():
    rows = fetch_all(
        """SELECT customers.*, users.email, COUNT(orders.id) AS orders_count
           FROM customers JOIN users ON users.id = customers.user_id
           LEFT JOIN orders ON orders.customer_id = customers.id
           GROUP BY customers.id ORDER BY customers.id DESC"""
    )
    return render_template("admin/customers.html", customers=rows)
