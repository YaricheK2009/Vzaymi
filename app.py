import html
import hashlib
import hmac
import mimetypes
import os
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "rentify.db"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"
SESSION_COOKIE = "vz_session"
CODE_TTL_MINUTES = 10
SESSION_TTL_DAYS = 30
PASSWORD_HASH_ITERATIONS = 120_000

SEED_USERS = [
    {
        "email": "maksim@example.com",
        "name": "Максим",
        "phone": "+7 905 777-44-11",
        "city": "Москва",
        "bio": "Сдаю технику и приставки для вечеринок, отдыха и коротких поездок.",
    },
    {
        "email": "ilya@example.com",
        "name": "Илья",
        "phone": "+7 901 555-10-10",
        "city": "Казань",
        "bio": "Люблю технику и даю вещи в аренду аккуратным людям.",
    },
    {
        "email": "sergey@example.com",
        "name": "Сергей",
        "phone": "+7 912 330-41-08",
        "city": "Екатеринбург",
        "bio": "Есть набор инструментов и полезных вещей для дома и ремонта.",
    },
    {
        "email": "anton@example.com",
        "name": "Антон",
        "phone": "+7 921 100-18-90",
        "city": "Санкт-Петербург",
        "bio": "Сдаю городской транспорт и вещи для прогулок по городу.",
    },
]

SEED_LISTINGS = [
    {
        "owner_email": "maksim@example.com",
        "title": "PlayStation 5 на выходные",
        "category": "Игры и приставки",
        "city": "Москва",
        "price": 1900,
        "period": "за сутки",
        "description": "Консоль с двумя геймпадами и подпиской PS Plus. Подходит для вечеринок, турниров и уютных вечеров дома.",
        "condition": "Как новая",
        "deposit": 10000,
        "delivery": "Самовывоз или курьер по городу",
        "image_path": "",
    },
    {
        "owner_email": "ilya@example.com",
        "title": "Проектор для кино и презентаций",
        "category": "Техника",
        "city": "Казань",
        "price": 1500,
        "period": "за сутки",
        "description": "Яркий Full HD проектор с HDMI и штативом. Подойдет для фильмов дома, лекций и праздников.",
        "condition": "Отличное",
        "deposit": 5000,
        "delivery": "Самовывоз, могу подвезти вечером",
        "image_path": "",
    },
    {
        "owner_email": "sergey@example.com",
        "title": "Дрель-шуруповерт с набором бит",
        "category": "Инструменты",
        "city": "Екатеринбург",
        "price": 700,
        "period": "за сутки",
        "description": "Для сборки мебели, ремонта и мелких работ по дому. Аккумулятор держит долго, зарядка в комплекте.",
        "condition": "Рабочее",
        "deposit": 3000,
        "delivery": "Только самовывоз",
        "image_path": "",
    },
    {
        "owner_email": "anton@example.com",
        "title": "Электросамокат Ninebot",
        "category": "Транспорт",
        "city": "Санкт-Петербург",
        "price": 1300,
        "period": "за сутки",
        "description": "Запас хода до 30 км. Удобно для прогулок и поездок по центру.",
        "condition": "Хорошее",
        "deposit": 8000,
        "delivery": "Самовывоз у метро",
        "image_path": "",
    },
]

SEED_REVIEWS = [
    {
        "owner_email": "maksim@example.com",
        "author_name": "Данил",
        "rating": 5,
        "rented_item": "PlayStation 5 на выходные",
        "text": "Все четко, приставка в идеальном состоянии, договорились быстро.",
    },
    {
        "owner_email": "maksim@example.com",
        "author_name": "Кристина",
        "rating": 5,
        "rented_item": "PlayStation 5 на выходные",
        "text": "Брали на день рождения, все прошло отлично.",
    },
    {
        "owner_email": "ilya@example.com",
        "author_name": "Руслан",
        "rating": 4,
        "rented_item": "Проектор для кино и презентаций",
        "text": "Проектор хороший, владелец помог настроить подключение.",
    },
]

CATEGORY_META = {
    "Игры и приставки": "🎮",
    "Техника": "💻",
    "Инструменты": "🛠",
    "Транспорт": "🛴",
    "Туризм": "⛺",
    "Фото и видео": "📷",
    "Для детей": "🧸",
    "Для мероприятий": "🎉",
}


def load_local_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_local_env()


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def stars(value: int) -> str:
    return "★" * value + "☆" * (5 - value)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def db_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_db_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}


def parse_multipart_form(content_type: str, payload: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    if "boundary=" not in content_type:
        return {}, {}

    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    boundary_bytes = ("--" + boundary).encode("utf-8")
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}

    for part in payload.split(boundary_bytes):
        part = part.strip()
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip()
        if b"\r\n\r\n" not in part:
            continue

        header_block, body = part.split(b"\r\n\r\n", 1)
        body = body.rstrip(b"\r\n")
        headers = header_block.decode("utf-8", errors="ignore").split("\r\n")
        disposition = next((line for line in headers if line.lower().startswith("content-disposition:")), "")
        if 'name="' not in disposition:
            continue

        name = disposition.split('name="', 1)[1].split('"', 1)[0]
        if 'filename="' in disposition:
            filename = disposition.split('filename="', 1)[1].split('"', 1)[0]
            files[name] = (filename, body)
        else:
            fields[name] = body.decode("utf-8", errors="ignore").strip()

    return fields, files


def save_uploaded_image(filename: str, content: bytes) -> str:
    filename = Path(filename or "").name
    if not filename or not content:
        return ""

    ext = Path(filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ""

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{ext}"
    target = UPLOAD_DIR / safe_name
    target.write_bytes(content)
    return f"/static/uploads/{quote(safe_name)}"


def smtp_is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip())


def reset_schema_if_needed(connection: sqlite3.Connection) -> None:
    user_columns = table_columns(connection, "users")
    if user_columns and "password_hash" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE password_hash = ''",
            (hash_password("demo12345"),),
        )

    expected = {
        "users": {"id", "email", "name", "phone", "city", "bio", "password_hash", "created_at"},
        "listings": {
            "id",
            "owner_id",
            "title",
            "category",
            "city",
            "price",
            "period",
            "description",
            "condition",
            "deposit",
            "delivery",
            "image_path",
            "created_at",
        },
        "reviews": {"id", "seller_id", "author_name", "rating", "rented_item", "text", "created_at"},
        "login_codes": {"id", "email", "code", "purpose", "name", "expires_at", "used_at", "created_at"},
        "sessions": {"id", "user_id", "token", "expires_at", "created_at"},
    }

    recreate = False
    for table_name, columns in expected.items():
        current = table_columns(connection, table_name)
        if current and current != columns:
            recreate = True
            break

    if recreate:
        connection.execute("DROP TABLE IF EXISTS sessions")
        connection.execute("DROP TABLE IF EXISTS login_codes")
        connection.execute("DROP TABLE IF EXISTS reviews")
        connection.execute("DROP TABLE IF EXISTS listings")
        connection.execute("DROP TABLE IF EXISTS users")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            city TEXT NOT NULL,
            bio TEXT NOT NULL,
            password_hash TEXT NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            city TEXT NOT NULL,
            price INTEGER NOT NULL,
            period TEXT NOT NULL,
            description TEXT NOT NULL,
            condition TEXT NOT NULL,
            deposit INTEGER NOT NULL,
            delivery TEXT NOT NULL,
            image_path TEXT NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            author_name TEXT NOT NULL,
            rating INTEGER NOT NULL,
            rented_item TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_id) REFERENCES users(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS login_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            purpose TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL,
            used_at TEXT DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def init_db() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        reset_schema_if_needed(connection)
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            connection.commit()
            return

        user_ids: dict[str, int] = {}
        demo_password_hash = hash_password("demo12345")
        for user in SEED_USERS:
            cursor = connection.execute(
                "INSERT INTO users (email, name, phone, city, bio, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (user["email"], user["name"], user["phone"], user["city"], user["bio"], demo_password_hash),
            )
            user_ids[user["email"]] = cursor.lastrowid

        for listing in SEED_LISTINGS:
            connection.execute(
                """
                INSERT INTO listings
                (owner_id, title, category, city, price, period, description, condition, deposit, delivery, image_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_ids[listing["owner_email"]],
                    listing["title"],
                    listing["category"],
                    listing["city"],
                    listing["price"],
                    listing["period"],
                    listing["description"],
                    listing["condition"],
                    listing["deposit"],
                    listing["delivery"],
                    listing["image_path"],
                ),
            )

        for review in SEED_REVIEWS:
            connection.execute(
                "INSERT INTO reviews (seller_id, author_name, rating, rented_item, text) VALUES (?, ?, ?, ?, ?)",
                (
                    user_ids[review["owner_email"]],
                    review["author_name"],
                    review["rating"],
                    review["rented_item"],
                    review["text"],
                ),
            )
        connection.commit()


def query_db(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql, params).fetchall()


def execute_db(sql: str, params: tuple) -> int:
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(sql, params)
        connection.commit()
        return cursor.lastrowid


def find_user_by_email(email: str) -> sqlite3.Row | None:
    rows = query_db("SELECT * FROM users WHERE email = ?", (email,))
    return rows[0] if rows else None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        iterations_raw, salt, expected_digest = password_hash.split("$", 2)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations_raw),
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected_digest)


def create_user(email: str, name: str, password: str) -> sqlite3.Row:
    execute_db(
        "INSERT INTO users (email, name, phone, city, bio, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
        (email, name, "", "", "", hash_password(password)),
    )
    return query_db("SELECT * FROM users WHERE email = ?", (email,))[0]


def update_user_profile(user_id: int, name: str, phone: str, city: str, bio: str) -> None:
    execute_db(
        "UPDATE users SET name = ?, phone = ?, city = ?, bio = ? WHERE id = ?",
        (name, phone, city, bio, user_id),
    )


def send_email_code(email: str, code: str, purpose: str) -> str:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "no-reply@vzaymy.local").strip()
    smtp_tls = os.getenv("SMTP_TLS", "true").lower() != "false"

    if purpose == "register":
        subject = "Код для регистрации"
    elif purpose == "reset":
        subject = "Код для восстановления доступа"
    else:
        subject = "Код для входа"

    text = (
        f"Ваш код: {code}\n"
        f"Он действует {CODE_TTL_MINUTES} минут.\n"
        "Если это были не вы, просто проигнорируйте письмо."
    )

    if not smtp_host:
        return code

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(text)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        if smtp_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.send_message(message)
    return ""


def store_login_code(email: str, purpose: str, name: str = "") -> str:
    code = f"{uuid.uuid4().int % 1000000:06d}"
    expires_at = db_timestamp(utc_now() + timedelta(minutes=CODE_TTL_MINUTES))
    execute_db("DELETE FROM login_codes WHERE email = ? AND purpose = ?", (email, purpose))
    execute_db(
        "INSERT INTO login_codes (email, code, purpose, name, expires_at) VALUES (?, ?, ?, ?, ?)",
        (email, code, purpose, name, expires_at),
    )
    return send_email_code(email, code, purpose)


def verify_login_code(email: str, code: str, purpose: str) -> sqlite3.Row | None:
    rows = query_db(
        """
        SELECT * FROM login_codes
        WHERE email = ? AND purpose = ? AND code = ?
        ORDER BY id DESC LIMIT 1
        """,
        (email, purpose, code),
    )
    if not rows:
        return None

    row = rows[0]
    if row["used_at"]:
        return None
    if parse_db_timestamp(row["expires_at"]) < utc_now():
        return None

    execute_db("UPDATE login_codes SET used_at = ? WHERE id = ?", (db_timestamp(utc_now()), row["id"]))
    return row


def create_session(user_id: int) -> str:
    token = uuid.uuid4().hex
    expires_at = db_timestamp(utc_now() + timedelta(days=SESSION_TTL_DAYS))
    execute_db("INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)", (user_id, token, expires_at))
    return token


def get_cookie_token(headers) -> str:
    raw = headers.get("Cookie", "")
    if not raw:
        return ""
    cookie = SimpleCookie()
    cookie.load(raw)
    return cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else ""


def get_current_user(headers) -> sqlite3.Row | None:
    token = get_cookie_token(headers)
    if not token:
        return None

    rows = query_db(
        """
        SELECT u.*, s.expires_at
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
        ORDER BY s.id DESC LIMIT 1
        """,
        (token,),
    )
    if not rows:
        return None

    row = rows[0]
    if parse_db_timestamp(row["expires_at"]) < utc_now():
        execute_db("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return row


def build_session_cookie(token: str) -> str:
    expires = (utc_now() + timedelta(days=SESSION_TTL_DAYS)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Expires={expires}"


def build_delete_cookie() -> str:
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Expires=Thu, 01 Jan 1970 00:00:00 GMT"


def render_layout(title: str, content: str, user: sqlite3.Row | None = None) -> bytes:
    if user:
        display_name = user["name"] or user["email"]
        user_nav = (
            f'<span class="topbar__user">{esc(display_name)}</span>'
            f'<a href="/seller?id={user["id"]}">Мой профиль</a>'
            '<a href="/create">Сдать вещь</a>'
            '<a href="/logout">Выйти</a>'
        )
    else:
        user_nav = '<a href="/login">Войти</a><a href="/register">Регистрация</a>'

    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <div class="site-shell">
    <header class="topbar">
      <div class="container topbar__inner">
        <a class="brand" href="/">взаймы</a>
        <nav class="topbar__nav">
          <a href="/">Объявления</a>
          {user_nav}
        </nav>
      </div>
    </header>
    <main class="container">
      {content}
    </main>
  </div>
</body>
</html>"""
    return page.encode("utf-8")


def build_options(items: list[str], current: str, placeholder: str) -> str:
    options = [f'<option value="">{esc(placeholder)}</option>']
    for item in items:
        selected = " selected" if item == current else ""
        options.append(f'<option value="{esc(item)}"{selected}>{esc(item)}</option>')
    return "".join(options)


def render_media(image_path: str, category: str, large: bool = False) -> str:
    class_name = "details__visual" if large else "listing-card__media"
    if image_path:
        return f'<div class="{class_name}"><img src="{esc(image_path)}" alt="{esc(category)}"></div>'
    return f'<div class="{class_name}"><span>{esc(CATEGORY_META.get(category, "📦"))}</span></div>'


def render_flash(error: str = "", success: str = "", info: str = "") -> str:
    if error:
        return f'<div class="error-box">{esc(error)}</div>'
    if success:
        return f'<div class="success-box">{esc(success)}</div>'
    if info:
        return f'<div class="info-box">{esc(info)}</div>'
    return ""


def render_home(params: dict[str, list[str]], user: sqlite3.Row | None) -> bytes:
    search = params.get("q", [""])[0].strip()
    city = params.get("city", [""])[0].strip()
    category = params.get("category", [""])[0].strip()

    where = []
    values: list[str] = []
    if search:
        where.append("(l.title LIKE ? OR l.description LIKE ? OR l.category LIKE ?)")
        values.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if city:
        where.append("l.city = ?")
        values.append(city)
    if category:
        where.append("l.category = ?")
        values.append(category)

    sql = """
        SELECT l.*, u.name AS owner_name, u.id AS owner_id, u.email AS owner_email
        FROM listings l
        JOIN users u ON u.id = l.owner_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY l.created_at DESC, l.id DESC"

    listings = query_db(sql, tuple(values))
    categories = [row["category"] for row in query_db("SELECT DISTINCT category FROM listings ORDER BY category")]
    cities = [row["city"] for row in query_db("SELECT DISTINCT city FROM listings ORDER BY city")]
    total = query_db("SELECT COUNT(*) AS total FROM listings")[0]["total"]

    cards = []
    for listing in listings:
        owner_name = listing["owner_name"] or listing["owner_email"]
        cards.append(
            f"""
            <article class="listing-card">
              {render_media(listing["image_path"], listing["category"])}
              <div class="listing-card__body">
                <div class="listing-card__price">{money(listing["price"])} ₽ <span>{esc(listing["period"])}</span></div>
                <h3><a href="/listing?id={listing["id"]}">{esc(listing["title"])}</a></h3>
                <p class="listing-card__meta">{esc(listing["category"])} • {esc(listing["city"])} • {esc(listing["condition"])}</p>
                <p class="listing-card__text">{esc(listing["description"])}</p>
                <div class="listing-card__footer">
                  <a class="seller-link" href="/seller?id={listing["owner_id"]}">{esc(owner_name)}</a>
                  <span>Залог {money(listing["deposit"])} ₽</span>
                </div>
              </div>
            </article>
            """
        )

    if not cards:
        cards.append(
            """
            <div class="empty-state">
              <h2>Ничего не найдено</h2>
              <p>Сбрось часть фильтров или добавь новое объявление.</p>
            </div>
            """
        )

    chips = []
    for item in categories:
        active = " category-chip--active" if item == category else ""
        chips.append(f'<a class="category-chip{active}" href="/?category={quote(item)}">{esc(CATEGORY_META.get(item, "📦"))} {esc(item)}</a>')

    action_button = '<a class="button button--primary" href="/create">Разместить объявление</a>' if user else '<a class="button button--primary" href="/register">Зарегистрироваться</a>'

    content = f"""
    <section class="hero">
      <div class="hero__copy">
        <span class="badge">Аренда вещей по объявлениям</span>
        <h1>Бери вещи напрокат, а не покупай на один раз</h1>
        <p>Техника, инструменты, транспорт, детские товары и все, что удобно арендовать у людей рядом.</p>
      </div>
      <div class="hero__panel">
        <div class="hero__stat"><strong>{total}</strong><span>активных объявлений</span></div>
        <div class="hero__stat"><strong>{len(categories)}</strong><span>категорий</span></div>
        <div class="hero__stat"><strong>{len(cities)}</strong><span>городов</span></div>
        {action_button}
      </div>
    </section>

    <section class="search-panel">
      <form class="search-form" method="get" action="/">
        <input type="text" name="q" placeholder="Что хочешь арендовать?" value="{esc(search)}">
        <select name="category">{build_options(categories, category, "Все категории")}</select>
        <select name="city">{build_options(cities, city, "Все города")}</select>
        <button class="button button--primary" type="submit">Найти</button>
      </form>
      <div class="category-row">{''.join(chips)}</div>
    </section>

    <section class="info-strip">
      <div><strong>Фото в объявлении</strong><span>можно сразу показать состояние вещи</span></div>
      <div><strong>Имена пользователей</strong><span>другие видят твое имя, а не почту</span></div>
      <div><strong>Пароль для входа</strong><span>регистрация по почте и обычный вход без одноразовых кодов</span></div>
    </section>

    <section class="section-head">
      <h2>Объявления</h2>
      <p>{len(listings)} результатов по текущему поиску</p>
    </section>

    <section class="listing-grid">
      {''.join(cards)}
    </section>
    """
    return render_layout("Взаймы", content, user)


def render_listing(listing_id: str, user: sqlite3.Row | None) -> bytes:
    if not listing_id.isdigit():
        return render_not_found(user)

    rows = query_db(
        """
        SELECT l.*, u.id AS owner_id, u.name AS owner_name, u.email AS owner_email, u.phone, u.bio
        FROM listings l
        JOIN users u ON u.id = l.owner_id
        WHERE l.id = ?
        """,
        (listing_id,),
    )
    if not rows:
        return render_not_found(user)

    listing = rows[0]
    review_stats = query_db(
        "SELECT COUNT(*) AS total, ROUND(AVG(rating), 1) AS avg_rating FROM reviews WHERE seller_id = ?",
        (listing["owner_id"],),
    )[0]
    owner_name = listing["owner_name"] or listing["owner_email"]

    content = f"""
    <section class="breadcrumbs">
      <a href="/">Объявления</a>
      <span> / </span>
      <span>{esc(listing["title"])}</span>
    </section>

    <article class="details">
      <div class="details__main">
        {render_media(listing["image_path"], listing["category"], large=True)}
        <p class="details__meta">{esc(listing["category"])} • {esc(listing["city"])}</p>
        <h1>{esc(listing["title"])}</h1>
        <div class="details__price">{money(listing["price"])} ₽ <span>{esc(listing["period"])}</span></div>
        <p class="details__description">{esc(listing["description"])}</p>
        <div class="details__facts">
          <div><span>Состояние</span><strong>{esc(listing["condition"])}</strong></div>
          <div><span>Залог</span><strong>{money(listing["deposit"])} ₽</strong></div>
          <div><span>Получение</span><strong>{esc(listing["delivery"])}</strong></div>
        </div>
      </div>
      <aside class="details__side">
        <span class="badge">Владелец</span>
        <h2>{esc(owner_name)}</h2>
        <p class="details__phone">{esc(listing["phone"] or listing["owner_email"])}</p>
        <p class="details__hint">{esc(listing["bio"] or "Пользователь пока не добавил описание.")}</p>
        <div class="seller-mini-stats">
          <div><strong>{int(review_stats["total"])}</strong><span>отзывов</span></div>
          <div><strong>{review_stats["avg_rating"] or "—"}</strong><span>средняя оценка</span></div>
        </div>
        <a class="button button--primary" href="/seller?id={listing["owner_id"]}">Профиль владельца</a>
      </aside>
    </article>
    """
    return render_layout(listing["title"], content, user)


def render_seller_profile(seller_id: str, viewer: sqlite3.Row | None, error: str = "", success: str = "", info: str = "") -> bytes:
    if not seller_id.isdigit():
        return render_not_found(viewer)

    users = query_db("SELECT * FROM users WHERE id = ?", (seller_id,))
    if not users:
        return render_not_found(viewer)

    seller = users[0]
    listings = query_db("SELECT * FROM listings WHERE owner_id = ? ORDER BY created_at DESC, id DESC", (seller_id,))
    reviews = query_db("SELECT * FROM reviews WHERE seller_id = ? ORDER BY created_at DESC, id DESC", (seller_id,))
    stats = query_db(
        "SELECT COUNT(*) AS total, ROUND(AVG(rating), 1) AS avg_rating FROM reviews WHERE seller_id = ?",
        (seller_id,),
    )[0]

    cards = []
    for listing in listings:
        cards.append(
            f"""
            <article class="mini-card">
              {render_media(listing["image_path"], listing["category"])}
              <div class="mini-card__body">
                <h3><a href="/listing?id={listing["id"]}">{esc(listing["title"])}</a></h3>
                <p>{money(listing["price"])} ₽ {esc(listing["period"])}</p>
              </div>
            </article>
            """
        )

    review_cards = []
    for review in reviews:
        review_cards.append(
            f"""
            <article class="review-card">
              <div class="review-card__head">
                <strong>{esc(review["author_name"])}</strong>
                <span>{esc(stars(int(review["rating"])))}</span>
              </div>
              <p class="review-card__item">Арендовал: {esc(review["rented_item"])}</p>
              <p>{esc(review["text"])}</p>
            </article>
            """
        )

    if not viewer:
        form_block = '<div class="info-box">Чтобы оставить отзыв, сначала войди в аккаунт.</div>'
    elif int(viewer["id"]) == int(seller["id"]):
        form_block = '<div class="info-box">Это твой профиль. Отзыв самому себе оставить нельзя.</div>'
    else:
        default_name = viewer["name"] or viewer["email"]
        form_block = f"""
        <form class="review-form" method="post" action="/review">
          <input type="hidden" name="seller_id" value="{seller["id"]}">
          <label>Ваше имя
            <input type="text" name="author_name" value="{esc(default_name)}" required>
          </label>
          <label>Что арендовали
            <input type="text" name="rented_item" placeholder="Например, PlayStation 5" required>
          </label>
          <label>Оценка
            <select name="rating" required>
              <option value="">Выбери оценку</option>
              <option value="5">5</option>
              <option value="4">4</option>
              <option value="3">3</option>
              <option value="2">2</option>
              <option value="1">1</option>
            </select>
          </label>
          <label class="review-form__wide">Отзыв
            <textarea name="text" rows="4" required></textarea>
          </label>
          <button class="button button--primary" type="submit">Оставить отзыв</button>
        </form>
        """

    title = seller["name"] or seller["email"]
    contact = seller["phone"] or seller["email"]
    about = seller["bio"] or "Пользователь пока не добавил описание."
    city = seller["city"] or "Город не указан"

    content = f"""
    <section class="profile-hero">
      <div>
        <span class="badge">Профиль пользователя</span>
        <h1>{esc(title)}</h1>
        <p>{esc(about)}</p>
        <p class="profile-hero__meta">{esc(city)} • {esc(contact)}</p>
      </div>
      <div class="profile-hero__stats">
        <div><strong>{len(listings)}</strong><span>объявлений</span></div>
        <div><strong>{stats["total"]}</strong><span>отзывов</span></div>
        <div><strong>{stats["avg_rating"] or "—"}</strong><span>средняя оценка</span></div>
      </div>
    </section>

    {render_flash(error, success, info)}

    <section class="profile-columns">
      <div class="profile-panel">
        <div class="section-head section-head--left">
          <h2>Объявления пользователя</h2>
          <p>Все вещи, которые сейчас можно арендовать.</p>
        </div>
        <div class="mini-grid">
          {''.join(cards) or '<div class="empty-state"><p>Пока нет объявлений.</p></div>'}
        </div>
      </div>

      <div class="profile-panel">
        <div class="section-head section-head--left">
          <h2>Отзывы после аренды</h2>
          <p>Отзывы о том, как прошла аренда у этого владельца.</p>
        </div>
        <div class="review-list">
          {''.join(review_cards) or '<div class="empty-state"><p>Пока нет отзывов.</p></div>'}
        </div>
        {form_block}
      </div>
    </section>
    """
    return render_layout(f"Профиль {title}", content, viewer)


def render_create_form(user: sqlite3.Row | None, error: str = "", success: str = "", form_data: dict[str, str] | None = None) -> bytes:
    if not user:
        return render_auth_required("Чтобы разместить объявление, сначала зарегистрируйся или войди.")

    data = form_data or {}
    content = f"""
    <section class="form-page">
      <div class="section-head section-head--left">
        <h1>Сдать вещь в аренду</h1>
        <p>Объявление публикуется от имени текущего пользователя. Профиль можно обновить прямо при публикации.</p>
      </div>
      {render_flash(error, success)}
      <form class="listing-form" method="post" action="/create" enctype="multipart/form-data">
        <label>Название
          <input type="text" name="title" value="{esc(data.get("title", ""))}" placeholder="Например, Проектор Epson" required>
        </label>
        <label>Категория
          <input type="text" name="category" value="{esc(data.get("category", ""))}" placeholder="Техника, инструменты, транспорт" required>
        </label>
        <label>Город
          <input type="text" name="city" value="{esc(data.get("city", user["city"] or ""))}" placeholder="Москва" required>
        </label>
        <label>Цена
          <input type="number" min="1" name="price" value="{esc(data.get("price", ""))}" placeholder="1500" required>
        </label>
        <label>Период оплаты
          <input type="text" name="period" value="{esc(data.get("period", "за сутки"))}" placeholder="за сутки / за неделю" required>
        </label>
        <label>Состояние вещи
          <input type="text" name="condition" value="{esc(data.get("condition", ""))}" placeholder="Отличное" required>
        </label>
        <label>Залог
          <input type="number" min="0" name="deposit" value="{esc(data.get("deposit", "0"))}" placeholder="5000" required>
        </label>
        <label>Получение и доставка
          <input type="text" name="delivery" value="{esc(data.get("delivery", ""))}" placeholder="Самовывоз у метро" required>
        </label>
        <label class="listing-form__wide">Описание
          <textarea name="description" rows="5" required>{esc(data.get("description", ""))}</textarea>
        </label>
        <label>Ваше имя
          <input type="text" name="owner_name" value="{esc(data.get("owner_name", user["name"] or ""))}" required>
        </label>
        <label>Ваша почта
          <input type="email" value="{esc(user["email"])}" disabled>
        </label>
        <label>Ваш телефон
          <input type="text" name="phone" value="{esc(data.get("phone", user["phone"] or ""))}" placeholder="+7 900 000-00-00" required>
        </label>
        <label>О себе
          <input type="text" name="owner_bio" value="{esc(data.get("owner_bio", user["bio"] or ""))}" placeholder="Коротко о себе и условиях аренды" required>
        </label>
        <label class="listing-form__wide">Фото вещи
          <input type="file" name="image" accept=".jpg,.jpeg,.png,.webp,.gif">
        </label>
        <div class="form-actions">
          <button class="button button--primary" type="submit">Опубликовать</button>
          <a class="button button--ghost" href="/">Отмена</a>
        </div>
      </form>
    </section>
    """
    return render_layout("Сдать вещь", content, user)


def render_auth_page(
    mode: str,
    user: sqlite3.Row | None,
    error: str = "",
    success: str = "",
    info: str = "",
    email: str = "",
    name: str = "",
    debug_code: str = "",
) -> bytes:
    titles = {
        "login": ("Вход", "Введи почту и пароль, чтобы открыть свой профиль и размещать объявления."),
        "register": ("Регистрация", "Создай аккаунт по почте и сразу задай пароль для входа."),
    }
    title, subtitle = titles[mode]

    if mode == "register":
        form_action = "/auth/register"
        submit_label = "Создать аккаунт"
        fields_markup = f"""
          <label>Ваше имя
            <input type="text" name="name" value="{esc(name)}" placeholder="Например, Алина" required>
          </label>
          <label>Почта
            <input type="email" name="email" value="{esc(email)}" placeholder="name@example.com" required>
          </label>
          <label>Пароль
            <input type="password" name="password" placeholder="Минимум 8 символов" minlength="8" required>
          </label>
          <label>Повтори пароль
            <input type="password" name="password_confirm" placeholder="Повтори пароль" minlength="8" required>
          </label>
        """
        alt_link = '<p class="auth-links"><a href="/login">Уже есть аккаунт? Войти</a></p>'
    else:
        form_action = "/auth/login"
        submit_label = "Войти"
        fields_markup = f"""
          <label>Почта
            <input type="email" name="email" value="{esc(email)}" placeholder="name@example.com" required>
          </label>
          <label>Пароль
            <input type="password" name="password" placeholder="Ваш пароль" required>
          </label>
        """
        alt_link = '<p class="auth-links"><a href="/register">Нет аккаунта? Зарегистрироваться</a></p>'

    content = f"""
    <section class="auth-shell">
      <div class="auth-panel">
        <span class="badge">{esc(title)}</span>
        <h1>{esc(title)}</h1>
        <p>{esc(subtitle)}</p>
        {render_flash(error, success, info)}
        <form class="auth-form" method="post" action="{form_action}">
          {fields_markup}
          <button class="button button--primary" type="submit">{submit_label}</button>
        </form>
        {alt_link}
      </div>

      <div class="auth-panel">
        <span class="badge">Как это работает</span>
        <h2>Быстрый доступ</h2>
        <p>После регистрации аккаунт создается сразу. Подтверждение по коду на почту больше не нужно.</p>
        <p>Имя из регистрации будет видно в карточках и в профиле вместо адреса почты.</p>
        <p class="auth-links"><a href="/login">Вход</a> • <a href="/register">Регистрация</a></p>
      </div>
    </section>
    """
    return render_layout(title, content, user)


def render_auth_required(message: str) -> bytes:
    content = f"""
    <section class="empty-state empty-state--big">
      <h1>Нужен вход</h1>
      <p>{esc(message)}</p>
      <a class="button button--primary" href="/login">Войти</a>
    </section>
    """
    return render_layout("Нужен вход", content, None)


def render_not_found(user: sqlite3.Row | None) -> bytes:
    content = """
    <section class="empty-state empty-state--big">
      <h1>Страница не найдена</h1>
      <p>Такого объявления или профиля нет.</p>
      <a class="button button--primary" href="/">На главную</a>
    </section>
    """
    return render_layout("Не найдено", content, user)


class RentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        user = get_current_user(self.headers)
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_html(render_home(parse_qs(parsed.query), user))
            return
        if parsed.path == "/listing":
            self.send_html(render_listing(parse_qs(parsed.query).get("id", [""])[0], user))
            return
        if parsed.path == "/seller":
            self.send_html(render_seller_profile(parse_qs(parsed.query).get("id", [""])[0], user))
            return
        if parsed.path == "/create":
            self.send_html(render_create_form(user))
            return
        if parsed.path == "/login":
            self.send_html(render_auth_page("login", user))
            return
        if parsed.path == "/register":
            self.send_html(render_auth_page("register", user))
            return
        if parsed.path == "/reset-password":
            self.redirect("/login")
            return
        if parsed.path == "/logout":
            token = get_cookie_token(self.headers)
            if token:
                execute_db("DELETE FROM sessions WHERE token = ?", (token,))
            self.redirect("/", delete_cookie=True)
            return
        if parsed.path.startswith("/static/"):
            self.send_static_file(parsed.path.removeprefix("/static/"))
            return

        self.send_html(render_not_found(user), status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        user = get_current_user(self.headers)
        parsed = urlparse(self.path)

        if parsed.path == "/create":
            self.handle_create(user)
            return
        if parsed.path == "/review":
            self.handle_review(user)
            return
        if parsed.path == "/auth/register":
            self.handle_register(user)
            return
        if parsed.path == "/auth/login":
            self.handle_login(user)
            return

        self.send_html(render_not_found(user), status=HTTPStatus.NOT_FOUND)

    def handle_register(self, user: sqlite3.Row | None) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        fields = {key: values[0].strip() for key, values in parse_qs(raw).items()}
        email = fields.get("email", "").lower()
        name = fields.get("name", "")
        password = fields.get("password", "")
        password_confirm = fields.get("password_confirm", "")

        if "@" not in email or "." not in email:
            self.send_html(render_auth_page("register", user, error="Укажи корректную почту.", email=email, name=name))
            return

        if not name:
            self.send_html(render_auth_page("register", user, error="При регистрации нужно указать имя.", email=email, name=name))
            return

        if len(password) < 8:
            self.send_html(render_auth_page("register", user, error="Пароль должен содержать минимум 8 символов.", email=email, name=name))
            return

        if password != password_confirm:
            self.send_html(render_auth_page("register", user, error="Пароли не совпадают.", email=email, name=name))
            return

        existing_user = find_user_by_email(email)
        if existing_user:
            self.send_html(render_auth_page("register", user, error="Пользователь с такой почтой уже есть. Используй вход.", email=email, name=name))
            return

        account = create_user(email, name, password)
        token = create_session(int(account["id"]))
        self.redirect("/", set_cookie=build_session_cookie(token))

    def handle_login(self, user: sqlite3.Row | None) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        fields = {key: values[0].strip() for key, values in parse_qs(raw).items()}
        email = fields.get("email", "").lower()
        password = fields.get("password", "")

        if "@" not in email or "." not in email:
            self.send_html(render_auth_page("login", user, error="Укажи корректную почту.", email=email))
            return

        account = find_user_by_email(email)
        if not account:
            self.send_html(render_auth_page("login", user, error="Аккаунт не найден. Сначала зарегистрируйся.", email=email))
            return

        if not verify_password(password, account["password_hash"]):
            self.send_html(render_auth_page("login", user, error="Неверный пароль.", email=email))
            return

        token = create_session(int(account["id"]))
        self.redirect("/", set_cookie=build_session_cookie(token))

    def handle_create(self, user: sqlite3.Row | None) -> None:
        if not user:
            self.send_html(render_auth_required("Чтобы разместить объявление, сначала зарегистрируйся или войди."), status=HTTPStatus.UNAUTHORIZED)
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        raw_fields, raw_files = parse_multipart_form(self.headers.get("Content-Type", ""), payload)

        text_fields = {
            key: raw_fields.get(key, "").strip()
            for key in [
                "title",
                "category",
                "city",
                "price",
                "period",
                "condition",
                "deposit",
                "delivery",
                "description",
                "owner_name",
                "phone",
                "owner_bio",
            ]
        }

        if any(not text_fields[field] for field in text_fields):
            self.send_html(render_create_form(user, error="Нужно заполнить все поля, кроме фото.", form_data=text_fields))
            return

        if not text_fields["price"].isdigit() or not text_fields["deposit"].isdigit():
            self.send_html(render_create_form(user, error="Цена и залог должны быть числами.", form_data=text_fields))
            return

        image_path = ""
        if "image" in raw_files:
            image_filename, image_content = raw_files["image"]
            image_path = save_uploaded_image(image_filename, image_content)
            if not image_path:
                self.send_html(render_create_form(user, error="Фото должно быть в формате JPG, PNG, WEBP или GIF.", form_data=text_fields))
                return

        update_user_profile(int(user["id"]), text_fields["owner_name"], text_fields["phone"], text_fields["city"], text_fields["owner_bio"])
        execute_db(
            """
            INSERT INTO listings
            (owner_id, title, category, city, price, period, description, condition, deposit, delivery, image_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user["id"]),
                text_fields["title"],
                text_fields["category"],
                text_fields["city"],
                int(text_fields["price"]),
                text_fields["period"],
                text_fields["description"],
                text_fields["condition"],
                int(text_fields["deposit"]),
                text_fields["delivery"],
                image_path,
            ),
        )

        refreshed = query_db("SELECT * FROM users WHERE id = ?", (user["id"],))[0]
        self.send_html(render_create_form(refreshed, success="Объявление опубликовано. Профиль тоже обновлен."))

    def handle_review(self, user: sqlite3.Row | None) -> None:
        if not user:
            self.send_html(render_auth_required("Чтобы оставить отзыв, сначала войди в аккаунт."), status=HTTPStatus.UNAUTHORIZED)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        fields = {key: values[0].strip() for key, values in parse_qs(raw).items()}
        seller_id = fields.get("seller_id", "")

        required = ["seller_id", "author_name", "rented_item", "rating", "text"]
        if any(not fields.get(field) for field in required):
            self.send_html(render_seller_profile(seller_id, user, error="Нужно заполнить все поля отзыва."))
            return

        if not seller_id.isdigit() or fields["rating"] not in {"1", "2", "3", "4", "5"}:
            self.send_html(render_seller_profile(seller_id, user, error="Оценка должна быть от 1 до 5."))
            return

        if int(seller_id) == int(user["id"]):
            self.send_html(render_seller_profile(seller_id, user, error="Нельзя оставлять отзыв самому себе."))
            return

        execute_db(
            "INSERT INTO reviews (seller_id, author_name, rating, rented_item, text) VALUES (?, ?, ?, ?, ?)",
            (
                int(seller_id),
                fields["author_name"],
                int(fields["rating"]),
                fields["rented_item"],
                fields["text"],
            ),
        )
        self.send_html(render_seller_profile(seller_id, user, success="Отзыв сохранен."))

    def send_html(self, payload: bytes, status: HTTPStatus = HTTPStatus.OK, set_cookie: str = "") -> None:
        self.send_response(status)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str, set_cookie: str = "", delete_cookie: bool = False) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        if delete_cookie:
            self.send_header("Set-Cookie", build_delete_cookie())
        self.end_headers()

    def send_static_file(self, relative_path: str) -> None:
        target = (STATIC_DIR / relative_path).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return

        content = target.read_bytes()
        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), RentHandler)
    print("Сайт запущен: http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
