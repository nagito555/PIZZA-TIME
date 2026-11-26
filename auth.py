"""Аутентификация, проверка ролей и защита POST-форм от CSRF."""
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, g, redirect, request, session, url_for

from db import fetch_one


def load_user():
    """Загружает текущий аккаунт и профиль клиента, если он есть."""
    g.user = None
    user_id = session.get("user_id")
    if not user_id:
        return

    # LEFT JOIN объединяет аккаунт с профилем: администратор может не иметь профиля клиента.
    user = fetch_one(
        """SELECT users.id, users.email, users.role, users.is_active,
                  customers.id AS customer_id, customers.full_name, customers.phone
           FROM users
           LEFT JOIN customers ON customers.user_id = users.id
           WHERE users.id = ?""",
        (user_id,),
    )
    if user and user["is_active"]:
        g.user = user
    else:
        session.clear()


def role_required(*allowed_roles):
    """Декоратор: разрешает доступ только вошедшим с нужной ролью."""
    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Войдите в аккаунт, чтобы продолжить.", "error")
                return redirect(url_for("login", next=request.path))
            if allowed_roles and g.user["role"] not in allowed_roles:
                abort(403)
            return view_function(*args, **kwargs)
        return wrapped
    return decorator


def safe_next(target):
    """Не разрешает редиректы на сторонние сайты."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(24)
    return session["csrf"]


def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not token or not secrets.compare_digest(token, session.get("csrf", "")):
            abort(400, "Проверка формы не пройдена. Обновите страницу.")
