import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import uuid
from email.message import EmailMessage
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

from database import STORE_CATEGORIES, get_connection, hash_pw, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "buyasap-development-key")

PAYMENT_METHODS = ("UPI", "Card", "Net Banking", "Cash on Delivery")
STORE_CATEGORY_IDS = tuple(category[0] for category in STORE_CATEGORIES)
OTP_LIFETIME = datetime.timedelta(minutes=10)
OTP_RESEND_COOLDOWN = datetime.timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5


def valid_email(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email))


def generate_otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp, salt):
    return hashlib.sha256(f"{salt}:{otp}".encode("utf-8")).hexdigest()


def send_otp_email(recipient, otp, purpose="BuyASAP email verification"):
    sender = os.environ.get("BUYASAP_GMAIL_ADDRESS", "").strip()
    app_password = os.environ.get("BUYASAP_GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not sender or not app_password:
        raise RuntimeError(
            "Email delivery is not configured. Set BUYASAP_GMAIL_ADDRESS and "
            "BUYASAP_GMAIL_APP_PASSWORD to a Gmail sender and its App Password."
        )

    message = EmailMessage()
    message["Subject"] = f"{purpose} code"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Your BuyASAP verification code is {otp}.\n\n"
        "It expires in 10 minutes. If you did not request this, you can ignore this email."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(sender, app_password)
        smtp.send_message(message)


def demo_otp_enabled():
    return app.debug or os.environ.get("BUYASAP_DEMO_OTP", "").strip() == "1"


# Fall back to on-screen/logged OTP when Gmail SMTP isn't configured locally
def deliver_otp(recipient, otp, purpose):
    sender = os.environ.get("BUYASAP_GMAIL_ADDRESS", "").strip()
    app_password = os.environ.get("BUYASAP_GMAIL_APP_PASSWORD", "").replace(" ", "")
    if sender and app_password:
        send_otp_email(recipient, otp, purpose)
        return False
    app.logger.warning(
        "BUYASAP demo OTP for %s: %s",
        recipient,
        otp,
    )
    return True


def customer_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "userID" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def get_customer():
    if "userID" not in session:
        return None
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT userID, customerID, name, email, phoneNumber FROM users WHERE userID=?",
            (session["userID"],),
        ).fetchone()
    finally:
        conn.close()


def load_saved_cart(customer_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT p.productID, p.name, p.discountPrice AS price, ci.quantity
               FROM cart_items ci JOIN products p ON p.productID=ci.productID
               WHERE ci.customerID=? AND p.isActive=1 ORDER BY ci.addDate""",
            (customer_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# Cart lives in the Flask session during browsing; DB persistence is intentionally skipped here
def persist_cart(cart, customer_id):
    pass


def get_cart():
    cart = session.get("cart", [])
    if not isinstance(cart, list):
        cart = []
        session["cart"] = cart
    if "userID" in session and "cart" not in session:
        customer = get_customer()
        cart = load_saved_cart(customer["customerID"]) if customer else []
        session["cart"] = cart
    return cart


def cart_subtotal(cart):
    return sum(item["price"] * item["quantity"] for item in cart)


def coupon_discount(code, subtotal, conn):
    if not code:
        return 0, None
    coupon = conn.execute(
        """SELECT couponCode, description, discountType, discountValue, expiryDate,
                  usageLimit, isActive
           FROM coupons WHERE couponCode=? COLLATE NOCASE""",
        (code.strip(),),
    ).fetchone()
    today = datetime.date.today().isoformat()
    if not coupon or not coupon["isActive"] or coupon["expiryDate"] < today:
        return 0, None
    used = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE couponCode=?",
        (coupon["couponCode"],),
    ).fetchone()[0]
    if used >= coupon["usageLimit"]:
        return 0, None
    if coupon["discountType"] == "fixed":
        if coupon["couponCode"] == "SAVE500" and subtotal < 2000:
            return 0, None
        amount = coupon["discountValue"]
    else:
        amount = subtotal * coupon["discountValue"] / 100
    return min(round(amount, 2), subtotal), coupon


def get_checkout_totals(cart, conn):
    subtotal = cart_subtotal(cart)
    discount, coupon = coupon_discount(session.get("coupon_code"), subtotal, conn)
    return subtotal, discount, max(0, round(subtotal - discount, 2)), coupon


@app.context_processor
def shared_template_values():
    return {
        "cart_count": sum(item.get("quantity", 0) for item in get_cart()),
        "store_categories": STORE_CATEGORIES,
    }


@app.route("/")
def home():
    session.clear()
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and "userID" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = hash_pw(request.form.get("password", ""))
        conn = get_connection()
        try:
            user = conn.execute(
                """SELECT userID, customerID, name FROM users
                   WHERE lower(email)=? AND password=? AND role='Customer' AND emailVerified=1""",
                (email, password),
            ).fetchone()
        finally:
            conn.close()

        if user:
            session.clear()
            session["userID"] = user["userID"]
            session["name"] = user["name"]
            session["cart"] = load_saved_cart(user["customerID"])
            flash("Welcome back, Friend!", "success")
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.", "error")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password_text = request.form.get("password", "")
        if not name or not valid_email(email) or len(password_text) < 6:
            flash("Enter your name, a valid email address, and a password with at least 6 characters.", "error")
            return render_template("register.html")

        conn = get_connection()
        try:
            existing_user = conn.execute(
                "SELECT 1 FROM users WHERE lower(email)=?", (email,)
            ).fetchone()
            pending_registration = conn.execute(
                "SELECT createdAt FROM pending_registrations WHERE email=?", (email,)
            ).fetchone()
        finally:
            conn.close()
        if existing_user:
            flash("An account with that email already exists.", "error")
            return render_template("register.html")
        now = datetime.datetime.now(datetime.timezone.utc)
        if pending_registration:
            last_sent = datetime.datetime.fromisoformat(pending_registration["createdAt"])
            if now - last_sent < OTP_RESEND_COOLDOWN:
                session["pending_registration_email"] = email
                flash("A code was just sent. Please wait a minute before requesting another.", "error")
                return redirect(url_for("verify_email"))

        otp = generate_otp()
        otp_salt = secrets.token_hex(16)
        try:
            is_demo_otp = deliver_otp(email, otp, "BuyASAP registration")
        except (RuntimeError, OSError, smtplib.SMTPException) as exc:
            app.logger.error("Could not send registration OTP: %s", exc)
            flash(str(exc), "error")
            return render_template("register.html")

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO pending_registrations
                   (email, name, phoneNumber, passwordHash, otpSalt, otpHash, expiresAt, attempts, createdAt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(email) DO UPDATE SET
                       name=excluded.name, phoneNumber=excluded.phoneNumber,
                       passwordHash=excluded.passwordHash, otpSalt=excluded.otpSalt,
                       otpHash=excluded.otpHash, expiresAt=excluded.expiresAt,
                       attempts=0, createdAt=excluded.createdAt""",
                (
                    email,
                    name,
                    phone,
                    hash_pw(password_text),
                    otp_salt,
                    hash_otp(otp, otp_salt),
                    (now + OTP_LIFETIME).isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        session["pending_registration_email"] = email
        if is_demo_otp:
            session["demo_registration_otp"] = otp
            flash(f"Demo mode: use verification code {otp} for this email.", "success")
        else:
            flash("We sent a 6-digit verification code to your email.", "success")
        return redirect(url_for("verify_email"))
    return render_template("register.html")


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    email = session.get("pending_registration_email")
    if not email:
        flash("Start registration to request an email verification code.", "error")
        return redirect(url_for("register"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        conn = get_connection()
        try:
            pending = conn.execute(
                "SELECT * FROM pending_registrations WHERE email=?", (email,)
            ).fetchone()
            if not pending:
                session.pop("pending_registration_email", None)
                flash("Registration request expired. Please register again.", "error")
                return redirect(url_for("register"))

            now = datetime.datetime.now(datetime.timezone.utc)
            if datetime.datetime.fromisoformat(pending["expiresAt"]) <= now:
                conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
                conn.commit()
                session.pop("pending_registration_email", None)
                flash("That verification code expired. Please register again.", "error")
                return redirect(url_for("register"))

            if pending["attempts"] >= OTP_MAX_ATTEMPTS:
                flash("Too many incorrect attempts. Request a new code.", "error")
                return render_template(
                    "verify_email.html",
                    email=email,
                    can_resend=True,
                    demo_otp=session.get("demo_registration_otp"),
                )

            if not hmac.compare_digest(hash_otp(otp, pending["otpSalt"]), pending["otpHash"]):
                conn.execute(
                    "UPDATE pending_registrations SET attempts=attempts+1 WHERE email=?",
                    (email,),
                )
                conn.commit()
                flash("That code is not correct.", "error")
                return render_template(
                    "verify_email.html",
                    email=email,
                    can_resend=True,
                    demo_otp=session.get("demo_registration_otp"),
                )

            user_id = "USR-" + uuid.uuid4().hex[:8].upper()
            customer_id = "CUST-" + uuid.uuid4().hex[:8].upper()
            try:
                with conn:
                    conn.execute(
                        """INSERT INTO users
                           (userID, name, email, phoneNumber, password, emailVerified, role, customerID)
                           VALUES (?, ?, ?, ?, ?, 1, 'Customer', ?)""",
                        (
                            user_id,
                            pending["name"],
                            email,
                            pending["phoneNumber"],
                            pending["passwordHash"],
                            customer_id,
                        ),
                    )
                    conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
            except sqlite3.IntegrityError:
                session.pop("pending_registration_email", None)
                flash("An account with that email now exists. Please sign in.", "error")
                return redirect(url_for("login"))
        finally:
            conn.close()

        session.pop("pending_registration_email", None)
        session["userID"] = user_id
        session["name"] = pending["name"]
        session["cart"] = []
        flash("Email verified. Welcome to BuyASAP, Friend!", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "verify_email.html",
        email=email,
        can_resend=True,
        demo_otp=session.get("demo_registration_otp"),
    )


@app.route("/verify-email/resend", methods=["POST"])
def resend_registration_otp():
    email = session.get("pending_registration_email")
    if not email:
        return redirect(url_for("register"))
    conn = get_connection()
    try:
        pending = conn.execute(
            "SELECT email, createdAt FROM pending_registrations WHERE email=?", (email,)
        ).fetchone()
    finally:
        conn.close()
    if not pending:
        session.pop("pending_registration_email", None)
        flash("Registration request expired. Please register again.", "error")
        return redirect(url_for("register"))
    now = datetime.datetime.now(datetime.timezone.utc)
    last_sent = datetime.datetime.fromisoformat(pending["createdAt"])
    if now - last_sent < OTP_RESEND_COOLDOWN:
        flash("Please wait a minute between verification-code requests.", "error")
        return redirect(url_for("verify_email"))

    otp = generate_otp()
    salt = secrets.token_hex(16)
    try:
        is_demo_otp = deliver_otp(email, otp, "BuyASAP registration")
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        app.logger.error("Could not resend registration OTP: %s", exc)
        flash(str(exc), "error")
        return redirect(url_for("verify_email"))
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE pending_registrations SET otpSalt=?, otpHash=?, expiresAt=?, attempts=0,
               createdAt=? WHERE email=?""",
            (salt, hash_otp(otp, salt), (now + OTP_LIFETIME).isoformat(), now.isoformat(), email),
        )
        conn.commit()
    finally:
        conn.close()
    if is_demo_otp:
        session["demo_registration_otp"] = otp
        flash(f"Demo mode: use verification code {otp}.", "success")
    else:
        flash("A new verification code was sent.", "success")
    return redirect(url_for("verify_email"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@customer_required
def dashboard():
    conn = get_connection()
    try:
        placeholders = ", ".join("?" for _ in STORE_CATEGORY_IDS)
        products = conn.execute(
            f"""SELECT p.productID, p.name, p.discountPrice AS price, p.price AS listPrice,
                      p.image, p.rating, p.totalReviews
               FROM products p WHERE p.isActive=1 AND p.categoryID IN ({placeholders})
               ORDER BY p.rating DESC LIMIT 8""",
            STORE_CATEGORY_IDS,
        ).fetchall()
    finally:
        conn.close()
    return render_template("dashboard.html", products=products, store_categories=STORE_CATEGORIES)


@app.route("/products")
@customer_required
def products():
    query = request.args.get("q", "").strip()
    category_id = request.args.get("category", "").strip()
    brand = request.args.get("brand", "").strip()
    sort = request.args.get("sort", "featured")
    min_price = request.args.get("min_price", "").strip()
    max_price = request.args.get("max_price", "").strip()

    conditions = ["p.isActive=1"]
    params = []
    if query:
        conditions.append("(p.name LIKE ? OR p.description LIKE ? OR p.brand LIKE ?)")
        search = f"%{query}%"
        params.extend((search, search, search))
    if category_id:
        conditions.append("p.categoryID=?")
        params.append(category_id)
    if brand:
        conditions.append("p.brand=?")
        params.append(brand)
    try:
        if min_price:
            conditions.append("p.discountPrice>=?")
            params.append(max(0, float(min_price)))
        if max_price:
            conditions.append("p.discountPrice<=?")
            params.append(max(0, float(max_price)))
    except ValueError:
        flash("Enter a valid price range.", "error")

    sort_options = {
        "featured": "p.rating DESC, p.name",
        "price_low": "p.discountPrice ASC",
        "price_high": "p.discountPrice DESC",
        "rating": "p.rating DESC",
        "name": "p.name ASC",
    }
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT p.productID, p.name, p.description, p.discountPrice AS price,
                      p.price AS listPrice, p.image, p.brand, p.categoryID, p.rating,
                      p.totalReviews, c.name AS categoryName, COALESCE(i.quantity, 0) AS stock
               FROM products p JOIN categories c ON c.categoryID=p.categoryID
               LEFT JOIN inventory i ON i.productID=p.productID
               WHERE """ + " AND ".join(conditions) + " ORDER BY " + sort_options.get(sort, sort_options["featured"]),
            params,
        ).fetchall()
        placeholders = ", ".join("?" for _ in STORE_CATEGORY_IDS)
        categories = conn.execute(
            f"""SELECT categoryID, name FROM categories
               WHERE categoryID IN ({placeholders}) ORDER BY name""",
            STORE_CATEGORY_IDS,
        ).fetchall()
        brands = conn.execute(
            "SELECT DISTINCT brand FROM products WHERE isActive=1 ORDER BY brand"
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        "products.html",
        products=rows,
        categories=categories,
        brands=brands,
        filters=request.args,
    )


# Friendly shortcut URLs that redirect to the filtered /products view
@app.route("/smartphones")
@customer_required
def smartphones():
    return redirect(url_for("products", category="CAT-INDIAN-PHONES"))


@app.route("/laptops")
@customer_required
def laptops():
    return redirect(url_for("products", category="CAT-INDIAN-LAPTOPS"))


@app.route("/headphones")
@customer_required
def headphones():
    return redirect(url_for("products", category="CAT-EARPHONES"))


@app.route("/smartwatches")
@customer_required
def smartwatches():
    return redirect(url_for("products", category="CAT-SMARTWATCHES"))


@app.route("/products/<product_id>")
@customer_required
def product_detail(product_id):
    conn = get_connection()
    try:
        product = conn.execute(
            """SELECT p.*, p.discountPrice AS currentPrice, COALESCE(i.quantity, 0) AS stock
               FROM products p LEFT JOIN inventory i ON i.productID=p.productID
               WHERE p.productID=? AND p.isActive=1""",
            (product_id,),
        ).fetchone()
        if not product:
            abort(404)
        reviews = conn.execute(
            "SELECT customerName, rating, comment, reviewDate FROM reviews WHERE productID=? ORDER BY reviewDate DESC",
            (product_id,),
        ).fetchall()
        can_review = conn.execute(
            """SELECT 1 FROM order_items oi JOIN orders o ON o.orderId=oi.orderId
               WHERE oi.productID=? AND o.customerID=? AND o.status='Delivered' LIMIT 1""",
            (product_id, get_customer()["customerID"]),
        ).fetchone() is not None
        in_wishlist = conn.execute(
            "SELECT 1 FROM wishlist_items WHERE customerID=? AND productID=?",
            (get_customer()["customerID"], product_id),
        ).fetchone() is not None
    finally:
        conn.close()
    try:
        details = json.loads(product["specifications"] or "{}")
    except (TypeError, json.JSONDecodeError):
        details = {}
    savings = max(0, product["price"] - product["currentPrice"])
    discount_percent = round((savings / product["price"]) * 100) if product["price"] else 0
    return render_template(
        "product_detail.html",
        product=product,
        reviews=reviews,
        can_review=can_review,
        in_wishlist=in_wishlist,
        details=details,
        savings=savings,
        discount_percent=discount_percent,
    )


@app.route("/add_to_cart/<product_id>", methods=["GET", "POST"])
@customer_required
def add_to_cart(product_id):
    conn = get_connection()
    try:
        product = conn.execute(
            """SELECT p.productID, p.name, p.discountPrice AS price,
                      COALESCE(i.quantity, 0) AS stock
               FROM products p LEFT JOIN inventory i ON i.productID=p.productID
               WHERE p.productID=? AND p.isActive=1""",
            (product_id,),
        ).fetchone()
    finally:
        conn.close()
    if not product:
        abort(404)
    cart = get_cart()
    item = next((entry for entry in cart if entry["productID"] == product_id), None)
    if product["stock"] < (item["quantity"] + 1 if item else 1):
        flash("There is not enough stock to add another item.", "error")
    elif item:
        item["quantity"] += 1
        flash("Cart quantity updated.", "success")
    else:
        cart.append(
            {
                "productID": product["productID"],
                "name": product["name"],
                "price": product["price"],
                "quantity": 1,
            }
        )
        flash("Added to your cart, Friend!", "success")
    session["cart"] = cart
    persist_cart(cart, get_customer()["customerID"])
    session.modified = True
    return redirect(request.referrer or url_for("view_cart"))


@app.route("/cart")
@customer_required
def view_cart():
    cart = get_cart()
    conn = get_connection()
    try:
        subtotal, discount, total, coupon = get_checkout_totals(cart, conn)
    finally:
        conn.close()
    return render_template(
        "cart.html", cart=cart, subtotal=subtotal, discount=discount, total=total, coupon=coupon
    )


@app.route("/cart/update", methods=["POST"])
@customer_required
def update_cart():
    product_id = request.form.get("product_id", "")
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        flash("Enter a valid quantity.", "error")
        return redirect(url_for("view_cart"))
    cart = get_cart()
    item = next((entry for entry in cart if entry["productID"] == product_id), None)
    if not item:
        abort(404)
    if quantity <= 0:
        cart.remove(item)
    else:
        conn = get_connection()
        try:
            stock = conn.execute(
                "SELECT quantity FROM inventory WHERE productID=?", (product_id,)
            ).fetchone()
        finally:
            conn.close()
        if not stock or quantity > stock["quantity"]:
            flash("The requested quantity is not available.", "error")
            return redirect(url_for("view_cart"))
        item["quantity"] = quantity
    session["cart"] = cart
    persist_cart(cart, get_customer()["customerID"])
    session.modified = True
    return redirect(url_for("view_cart"))


@app.route("/remove_from_cart/<product_id>", methods=["POST"])
@customer_required
def remove_from_cart(product_id):
    session["cart"] = [item for item in get_cart() if item["productID"] != product_id]
    persist_cart(session["cart"], get_customer()["customerID"])
    session.modified = True
    flash("Item removed from your cart.", "success")
    return redirect(url_for("view_cart"))


@app.route("/cart/coupon", methods=["POST"])
@customer_required
def apply_coupon():
    code = request.form.get("coupon_code", "").strip().upper()
    conn = get_connection()
    try:
        discount, coupon = coupon_discount(code, cart_subtotal(get_cart()), conn)
    finally:
        conn.close()
    if not coupon or discount <= 0:
        session.pop("coupon_code", None)
        flash("That coupon is invalid, expired, used up, or does not apply to this cart.", "error")
    else:
        session["coupon_code"] = coupon["couponCode"]
        flash(f"Coupon {coupon['couponCode']} applied.", "success")
    return redirect(url_for("view_cart"))


@app.route("/wishlist")
@customer_required
def wishlist():
    customer = get_customer()
    conn = get_connection()
    try:
        items = conn.execute(
            """SELECT p.productID, p.name, p.discountPrice AS price, p.image, p.rating
               FROM wishlist_items w JOIN products p ON p.productID=w.productID
               WHERE w.customerID=? AND p.isActive=1 ORDER BY w.addDate DESC""",
            (customer["customerID"],),
        ).fetchall()
    finally:
        conn.close()
    return render_template("wishlist.html", items=items)


@app.route("/wishlist/<product_id>", methods=["POST"])
@customer_required
def toggle_wishlist(product_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        product = conn.execute(
            "SELECT productID FROM products WHERE productID=? AND isActive=1", (product_id,)
        ).fetchone()
        if not product:
            abort(404)
        existing = conn.execute(
            "SELECT 1 FROM wishlist_items WHERE customerID=? AND productID=?",
            (customer_id, product_id),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM wishlist_items WHERE customerID=? AND productID=?",
                (customer_id, product_id),
            )
            flash("Removed from your wishlist.", "success")
        else:
            conn.execute(
                """INSERT INTO wishlist_items (wishlistID, customerID, productID, addDate)
                   VALUES (?, ?, ?, ?)""",
                ("WISH-" + uuid.uuid4().hex[:10].upper(), customer_id, product_id, datetime.datetime.now().isoformat()),
            )
            flash("Added to your wishlist.", "success")
        conn.commit()
    finally:
        conn.close()
    return redirect(request.referrer or url_for("wishlist"))


@app.route("/account", methods=["GET", "POST"])
@customer_required
def account():
    customer = get_customer()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        if not name:
            flash("Your name is required.", "error")
            return redirect(url_for("account"))
        conn = get_connection()
        try:
            if new_password:
                if len(new_password) < 6:
                    flash("The new password must have at least 6 characters.", "error")
                    return redirect(url_for("account"))
                stored_password = conn.execute(
                    "SELECT password FROM users WHERE userID=?", (customer["userID"],)
                ).fetchone()
                if not stored_password or hash_pw(current_password) != stored_password["password"]:
                    flash("Enter your current password to change it.", "error")
                    return redirect(url_for("account"))
                conn.execute(
                    "UPDATE users SET password=? WHERE userID=?",
                    (hash_pw(new_password), customer["userID"]),
                )
            conn.execute(
                "UPDATE users SET name=?, phoneNumber=? WHERE userID=?",
                (name, phone, customer["userID"]),
            )
            conn.commit()
            session["name"] = name
        except sqlite3.IntegrityError:
            conn.rollback()
            raise
        finally:
            conn.close()
        flash("Your account information was updated.", "success")
        return redirect(url_for("account"))

    conn = get_connection()
    try:
        addresses = conn.execute(
            "SELECT * FROM addresses WHERE customerID=? ORDER BY isDefault DESC, addressID",
            (customer["customerID"],),
        ).fetchall()
    finally:
        conn.close()
    return render_template("account.html", customer=customer, addresses=addresses)


@app.route("/account/email", methods=["POST"])
@customer_required
def request_email_change():
    customer = get_customer()
    email = request.form.get("email", "").strip().lower()
    if not valid_email(email):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("account"))
    if email == customer["email"].lower():
        flash("That is already your current email address.", "error")
        return redirect(url_for("account"))

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE lower(email)=? AND userID!=?",
            (email, customer["userID"]),
        ).fetchone()
        pending_change = conn.execute(
            "SELECT createdAt FROM email_change_otps WHERE userID=?",
            (customer["userID"],),
        ).fetchone()
    finally:
        conn.close()
    if existing:
        flash("That email address is already linked to another account.", "error")
        return redirect(url_for("account"))
    if pending_change:
        now = datetime.datetime.now(datetime.timezone.utc)
        last_sent = datetime.datetime.fromisoformat(pending_change["createdAt"])
        if now - last_sent < OTP_RESEND_COOLDOWN:
            flash("Please wait a minute before requesting another email verification code.", "error")
            return redirect(url_for("verify_email_change"))

    otp = generate_otp()
    salt = secrets.token_hex(16)
    try:
        is_demo_otp = deliver_otp(email, otp, "BuyASAP email change")
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        app.logger.error("Could not send email-change OTP: %s", exc)
        flash(str(exc), "error")
        return redirect(url_for("account"))

    now = datetime.datetime.now(datetime.timezone.utc)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO email_change_otps
               (userID, newEmail, otpSalt, otpHash, expiresAt, attempts, createdAt)
               VALUES (?, ?, ?, ?, ?, 0, ?)
               ON CONFLICT(userID) DO UPDATE SET
                   newEmail=excluded.newEmail, otpSalt=excluded.otpSalt,
                   otpHash=excluded.otpHash, expiresAt=excluded.expiresAt,
                   attempts=0, createdAt=excluded.createdAt""",
            (
                customer["userID"],
                email,
                salt,
                hash_otp(otp, salt),
                (now + OTP_LIFETIME).isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("A verification request for that email is already in progress.", "error")
        return redirect(url_for("account"))
    finally:
        conn.close()
    if is_demo_otp:
        session["demo_email_change_otp"] = otp
        flash(f"Demo mode: use verification code {otp} for {email}.", "success")
    else:
        flash(f"We sent a verification code to {email}.", "success")
    return redirect(url_for("verify_email_change"))


@app.route("/account/verify-email", methods=["GET", "POST"])
@customer_required
def verify_email_change():
    customer = get_customer()
    conn = get_connection()
    try:
        pending = conn.execute(
            "SELECT * FROM email_change_otps WHERE userID=?", (customer["userID"],)
        ).fetchone()
        if not pending:
            flash("Request an email change first.", "error")
            return redirect(url_for("account"))

        if request.method == "GET":
            return render_template(
                "email_change_verify.html",
                email=pending["newEmail"],
                demo_otp=session.get("demo_email_change_otp"),
            )

        if request.method == "POST":
            now = datetime.datetime.now(datetime.timezone.utc)
            if datetime.datetime.fromisoformat(pending["expiresAt"]) <= now:
                conn.execute(
                    "DELETE FROM email_change_otps WHERE userID=?", (customer["userID"],)
                )
                conn.commit()
                flash("That code expired. Request another email change code.", "error")
                return redirect(url_for("account"))
            if pending["attempts"] >= OTP_MAX_ATTEMPTS:
                flash("Too many incorrect attempts. Request another code.", "error")
                return render_template("email_change_verify.html", email=pending["newEmail"])
            otp = request.form.get("otp", "").strip()
            if not hmac.compare_digest(hash_otp(otp, pending["otpSalt"]), pending["otpHash"]):
                conn.execute(
                    "UPDATE email_change_otps SET attempts=attempts+1 WHERE userID=?",
                    (customer["userID"],),
                )
                conn.commit()
                flash("That code is not correct.", "error")
                return render_template("email_change_verify.html", email=pending["newEmail"])

            try:
                with conn:
                    conn.execute(
                        "UPDATE users SET email=? WHERE userID=?",
                        (pending["newEmail"], customer["userID"]),
                    )
                    conn.execute(
                        "DELETE FROM email_change_otps WHERE userID=?", (customer["userID"],)
                    )
            except sqlite3.IntegrityError:
                flash("That email address is already linked to another account.", "error")
                return redirect(url_for("account"))
    finally:
        conn.close()

    flash("Your email address has been verified and updated.", "success")
    return redirect(url_for("account"))


@app.route("/addresses", methods=["POST"])
@customer_required
def add_address():
    customer_id = get_customer()["customerID"]
    street = request.form.get("street", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    landmark = request.form.get("landmark", "").strip()
    try:
        pincode = int(request.form.get("pincode", ""))
    except ValueError:
        pincode = 0
    if not all((street, city, state)) or not 100000 <= pincode <= 999999:
        flash("Enter a complete address and a valid 6-digit PIN code.", "error")
        return redirect(url_for("account"))

    conn = get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM addresses WHERE customerID=?", (customer_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO addresses (addressID, customerID, street, city, state, pincode, landmark, isDefault)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ADDR-" + uuid.uuid4().hex[:10].upper(),
                customer_id,
                street,
                city,
                state,
                pincode,
                landmark,
                1 if count == 0 else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    flash("Address saved.", "success")
    return redirect(url_for("account"))


@app.route("/addresses/<address_id>/edit", methods=["GET", "POST"])
@customer_required
def edit_address(address_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        address = conn.execute(
            "SELECT * FROM addresses WHERE addressID=? AND customerID=?",
            (address_id, customer_id),
        ).fetchone()
        if not address:
            abort(404)
        if request.method == "POST":
            street = request.form.get("street", "").strip()
            city = request.form.get("city", "").strip()
            state = request.form.get("state", "").strip()
            landmark = request.form.get("landmark", "").strip()
            try:
                pincode = int(request.form.get("pincode", ""))
            except ValueError:
                pincode = 0
            if not all((street, city, state)) or not 100000 <= pincode <= 999999:
                flash("Enter a complete address and a valid 6-digit PIN code.", "error")
                return render_template("edit_address.html", address=address)
            conn.execute(
                """UPDATE addresses SET street=?, city=?, state=?, pincode=?, landmark=?
                   WHERE addressID=? AND customerID=?""",
                (street, city, state, pincode, landmark, address_id, customer_id),
            )
            conn.commit()
            flash("Address updated.", "success")
            return redirect(url_for("account"))
    finally:
        conn.close()
    return render_template("edit_address.html", address=address)


@app.route("/addresses/<address_id>/default", methods=["POST"])
@customer_required
def set_default_address(address_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        address = conn.execute(
            "SELECT addressID FROM addresses WHERE addressID=? AND customerID=?",
            (address_id, customer_id),
        ).fetchone()
        if not address:
            abort(404)
        conn.execute("UPDATE addresses SET isDefault=0 WHERE customerID=?", (customer_id,))
        conn.execute(
            "UPDATE addresses SET isDefault=1 WHERE addressID=? AND customerID=?",
            (address_id, customer_id),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("account"))


@app.route("/addresses/<address_id>/delete", methods=["POST"])
@customer_required
def delete_address(address_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM addresses WHERE addressID=? AND customerID=?",
            (address_id, customer_id),
        )
        conn.commit()
    finally:
        conn.close()
    flash("Address removed.", "success")
    return redirect(url_for("account"))


@app.route("/checkout", methods=["GET", "POST"])
@customer_required
def checkout():
    cart = get_cart()
    if not cart:
        flash("Your cart is empty.", "error")
        return redirect(url_for("products"))
    customer = get_customer()
    conn = get_connection()
    try:
        addresses = conn.execute(
            "SELECT * FROM addresses WHERE customerID=? ORDER BY isDefault DESC, addressID",
            (customer["customerID"],),
        ).fetchall()
        subtotal, discount, total, coupon = get_checkout_totals(cart, conn)
    finally:
        conn.close()

    if request.method == "GET":
        return render_template(
            "checkout.html",
            addresses=addresses,
            subtotal=subtotal,
            discount=discount,
            total=total,
            coupon=coupon,
            payment_methods=PAYMENT_METHODS,
        )

    address_id = request.form.get("address_id", "")
    if address_id == "new":
        street = request.form.get("street", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        landmark = request.form.get("landmark", "").strip()
        try:
            pincode = int(request.form.get("pincode", ""))
        except ValueError:
            pincode = 0
        if not all((street, city, state)) or not 100000 <= pincode <= 999999:
            flash("Enter a complete shipping address and a valid 6-digit PIN code.", "error")
            return redirect(url_for("checkout"))
        shipping_address = {
            "street": street,
            "city": city,
            "state": state,
            "pincode": pincode,
            "landmark": landmark,
        }
    else:
        conn = get_connection()
        try:
            address = conn.execute(
                "SELECT * FROM addresses WHERE addressID=? AND customerID=?",
                (address_id, customer["customerID"]),
            ).fetchone()
        finally:
            conn.close()
        if not address:
            flash("Select one of your saved addresses or enter a new one.", "error")
            return redirect(url_for("checkout"))
        shipping_address = dict(address)

    payment_method = request.form.get("payment_method", "")
    if payment_method not in PAYMENT_METHODS:
        flash("Select a valid payment method.", "error")
        return redirect(url_for("checkout"))

    conn = get_connection()
    try:
        refreshed_cart = []
        for item in cart:
            product = conn.execute(
                """SELECT p.productID, p.name, p.discountPrice AS price,
                          COALESCE(i.quantity, 0) AS stock
                   FROM products p LEFT JOIN inventory i ON i.productID=p.productID
                   WHERE p.productID=? AND p.isActive=1""",
                (item["productID"],),
            ).fetchone()
            if not product or item["quantity"] > product["stock"]:
                flash(f"{item['name']} is no longer available in that quantity.", "error")
                return redirect(url_for("view_cart"))
            refreshed_cart.append(
                {
                    "productID": product["productID"],
                    "name": product["name"],
                    "price": product["price"],
                    "quantity": item["quantity"],
                }
            )
        subtotal, discount, total, coupon = get_checkout_totals(refreshed_cart, conn)
        if session.get("coupon_code") and not coupon:
            session.pop("coupon_code", None)
            flash("Your coupon is no longer valid. Review the updated total.", "error")
            return redirect(url_for("checkout"))

        order_id = "ORD-" + uuid.uuid4().hex[:10].upper()
        payment_id = "PAY-" + uuid.uuid4().hex[:10].upper()
        now = datetime.datetime.now()
        order_status = "Processing" if payment_method == "Cash on Delivery" else "Confirmed"
        payment_status = (
            "Cash on Delivery" if payment_method == "Cash on Delivery"
            else "Demo successful (not charged)"
        )
        with conn:
            conn.execute(
                """INSERT INTO orders
                   (orderId, customerID, orderDate, status, totalAmount, discountAmount,
                    finalAmount, couponCode, paymentMethod, shippingAddressJSON)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    customer["customerID"],
                    now.isoformat(),
                    order_status,
                    subtotal,
                    discount,
                    total,
                    coupon["couponCode"] if coupon else None,
                    payment_method,
                    json.dumps(shipping_address),
                ),
            )
            for item in refreshed_cart:
                conn.execute(
                    """INSERT INTO order_items (orderId, productID, quantity, unitPrice, totalPrice)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        order_id,
                        item["productID"],
                        item["quantity"],
                        item["price"],
                        item["price"] * item["quantity"],
                    ),
                )
                conn.execute(
                    "UPDATE inventory SET quantity=quantity-? WHERE productID=?",
                    (item["quantity"], item["productID"]),
                )
            conn.execute(
                """INSERT INTO payments
                   (paymentId, orderId, method, amount, status, transactionId, paymentDate)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    payment_id,
                    order_id,
                    payment_method,
                    total,
                    payment_status,
                    "DEMO-" + uuid.uuid4().hex[:12].upper(),
                    now.isoformat(),
                ),
            )
            delivery_date = (now + datetime.timedelta(days=5)).date().isoformat()
            conn.execute(
                """INSERT INTO shipments
                   (shipmentID, orderId, trackingNumber, carrier, currentStatus,
                    shippedDate, estimatedDelivery, actualDelivery, currentLocation, historyJSON)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    "SHIP-" + uuid.uuid4().hex[:8].upper(),
                    order_id,
                    "BAS" + uuid.uuid4().hex[:9].upper(),
                    "BuyASAP Logistics (demo)",
                    order_status,
                    now.isoformat(),
                    delivery_date,
                    "Order processing center",
                    json.dumps(
                        [
                            {
                                "status": order_status,
                                "location": "Order processing center",
                                "timestamp": now.isoformat(),
                                "note": "Order received by BuyASAP.",
                            }
                        ]
                    ),
                ),
            )
            conn.execute(
                "DELETE FROM cart_items WHERE customerID=?",
                (customer["customerID"],),
            )
        session["last_order_id"] = order_id
    finally:
        conn.close()

    session.pop("cart", None)
    session.pop("coupon_code", None)
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/payment", methods=["GET", "POST"])
@customer_required
def payment():
    return redirect(url_for("checkout"))


@app.route("/orders")
@customer_required
def orders():
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT o.*, p.status AS paymentStatus, s.trackingNumber, s.currentStatus AS shipmentStatus
               FROM orders o LEFT JOIN payments p ON p.orderId=o.orderId
               LEFT JOIN shipments s ON s.orderId=o.orderId
               WHERE o.customerID=? ORDER BY o.orderDate DESC""",
            (customer_id,),
        ).fetchall()
    finally:
        conn.close()
    return render_template("orders.html", orders=rows)


@app.route("/orders/<order_id>")
@customer_required
def order_detail(order_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        order = conn.execute(
            """SELECT o.*, p.status AS paymentStatus, p.transactionId, p.paymentDate,
                      s.trackingNumber, s.carrier, s.currentStatus AS shipmentStatus,
                      s.estimatedDelivery, s.currentLocation, s.historyJSON
               FROM orders o LEFT JOIN payments p ON p.orderId=o.orderId
               LEFT JOIN shipments s ON s.orderId=o.orderId
               WHERE o.orderId=? AND o.customerID=?""",
            (order_id, customer_id),
        ).fetchone()
        if not order:
            abort(404)
        lines = conn.execute(
            """SELECT oi.*, p.name FROM order_items oi JOIN products p ON p.productID=oi.productID
               WHERE oi.orderId=?""",
            (order_id,),
        ).fetchall()
        return_request = conn.execute(
            "SELECT * FROM return_requests WHERE orderId=?", (order_id,)
        ).fetchone()
    finally:
        conn.close()
    return render_template(
        "order_detail.html",
        order=order,
        lines=lines,
        shipping_address=json.loads(order["shippingAddressJSON"]),
        tracking=json.loads(order["historyJSON"] or "[]"),
        return_request=return_request,
    )


@app.route("/orders/<order_id>/invoice")
@customer_required
def invoice(order_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        order = conn.execute(
            """SELECT o.*, p.status AS paymentStatus
               FROM orders o LEFT JOIN payments p ON p.orderId=o.orderId
               WHERE o.orderId=? AND o.customerID=?""",
            (order_id, customer_id),
        ).fetchone()
        if not order:
            abort(404)
        lines = conn.execute(
            """SELECT oi.*, p.name FROM order_items oi JOIN products p ON p.productID=oi.productID
               WHERE oi.orderId=?""",
            (order_id,),
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        "invoice.html",
        order=order,
        lines=lines,
        shipping_address=json.loads(order["shippingAddressJSON"]),
    )


@app.route("/orders/<order_id>/cancel", methods=["POST"])
@customer_required
def cancel_order(order_id):
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    try:
        order = conn.execute(
            "SELECT status FROM orders WHERE orderId=? AND customerID=?",
            (order_id, customer_id),
        ).fetchone()
        if not order:
            abort(404)
        if order["status"] not in ("Processing", "Confirmed"):
            flash("This order can no longer be cancelled.", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        with conn:
            conn.execute("UPDATE orders SET status='Cancelled' WHERE orderId=?", (order_id,))
            order_items = conn.execute(
                "SELECT productID, quantity FROM order_items WHERE orderId=?", (order_id,)
            ).fetchall()
            for item in order_items:
                conn.execute(
                    "UPDATE inventory SET quantity=quantity+? WHERE productID=?",
                    (item["quantity"], item["productID"]),
                )
            conn.execute(
                "UPDATE payments SET status='Refund requested (demo)' WHERE orderId=? AND method!='Cash on Delivery'",
                (order_id,),
            )
            shipment = conn.execute(
                "SELECT historyJSON FROM shipments WHERE orderId=?", (order_id,)
            ).fetchone()
            if shipment:
                history = json.loads(shipment["historyJSON"] or "[]")
                history.append(
                    {
                        "status": "Cancelled",
                        "location": "BuyASAP",
                        "timestamp": datetime.datetime.now().isoformat(),
                        "note": "Cancelled by customer.",
                    }
                )
                conn.execute(
                    """UPDATE shipments SET currentStatus='Cancelled', currentLocation='BuyASAP',
                       historyJSON=? WHERE orderId=?""",
                    (json.dumps(history), order_id),
                )
        flash("Your order was cancelled.", "success")
    finally:
        conn.close()
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<order_id>/return", methods=["POST"])
@customer_required
def request_return(order_id):
    customer_id = get_customer()["customerID"]
    reason = request.form.get("reason", "").strip()
    if len(reason) < 5:
        flash("Please describe the reason for your return.", "error")
        return redirect(url_for("order_detail", order_id=order_id))
    conn = get_connection()
    try:
        order = conn.execute(
            "SELECT status, finalAmount FROM orders WHERE orderId=? AND customerID=?",
            (order_id, customer_id),
        ).fetchone()
        if not order:
            abort(404)
        if order["status"] != "Delivered":
            flash("Only delivered orders can be returned.", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        if conn.execute(
            "SELECT 1 FROM return_requests WHERE orderId=?", (order_id,)
        ).fetchone():
            flash("A return request already exists for this order.", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        now = datetime.datetime.now().isoformat()
        with conn:
            conn.execute(
                """INSERT INTO return_requests
                   (returnId, orderId, customerID, reason, refundAmount, status, requestDate, resolutionNote)
                   VALUES (?, ?, ?, ?, ?, 'Requested', ?, ?)""",
                (
                    "RET-" + uuid.uuid4().hex[:10].upper(),
                    order_id,
                    customer_id,
                    reason,
                    order["finalAmount"],
                    now,
                    "Refund review is pending; this demo does not issue a real refund.",
                ),
            )
            conn.execute(
                """UPDATE payments SET status='Refund requested (demo)'
                   WHERE orderId=? AND method!='Cash on Delivery'""",
                (order_id,),
            )
        flash("Return request submitted for review, Friend.", "success")
    finally:
        conn.close()
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/products/<product_id>/reviews", methods=["POST"])
@customer_required
def add_review(product_id):
    customer = get_customer()
    try:
        rating = int(request.form.get("rating", "0"))
    except ValueError:
        rating = 0
    comment = request.form.get("comment", "").strip()
    if not 1 <= rating <= 5 or not comment:
        flash("Select a 1–5 star rating and write a short review.", "error")
        return redirect(url_for("product_detail", product_id=product_id))
    conn = get_connection()
    try:
        purchased = conn.execute(
            """SELECT 1 FROM order_items oi JOIN orders o ON o.orderId=oi.orderId
               WHERE oi.productID=? AND o.customerID=? AND o.status='Delivered' LIMIT 1""",
            (product_id, customer["customerID"]),
        ).fetchone()
        if not purchased:
            flash("You can review a product after it has been delivered to you.", "error")
            return redirect(url_for("product_detail", product_id=product_id))
        conn.execute(
            """INSERT INTO reviews
               (reviewId, productID, customerID, customerName, rating, comment, isVerifiedPurchase, reviewDate)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                "REV-" + uuid.uuid4().hex[:10].upper(),
                product_id,
                customer["customerID"],
                customer["name"],
                rating,
                comment,
                datetime.datetime.now().isoformat(),
            ),
        )
        conn.execute(
            """UPDATE products SET
               rating=(SELECT AVG(rating) FROM reviews WHERE productID=?),
               totalReviews=(SELECT COUNT(*) FROM reviews WHERE productID=?)
               WHERE productID=?""",
            (product_id, product_id, product_id),
        )
        conn.commit()
    finally:
        conn.close()
    flash("Thanks for sharing your review, Friend!", "success")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/support", methods=["GET", "POST"])
@customer_required
def support():
    customer_id = get_customer()["customerID"]
    conn = get_connection()
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        description = request.form.get("description", "").strip()
        if not subject or not description:
            flash("Enter a subject and describe how customer service can help.", "error")
        else:
            now = datetime.datetime.now().isoformat()
            conn.execute(
                """INSERT INTO support_tickets
                   (ticketId, customerID, subject, description, status, assignedTo, createdDate, commentsJSON)
                   VALUES (?, ?, ?, ?, 'Open', 'BuyASAP Customer Service', ?, ?)""",
                (
                    "TICK-" + uuid.uuid4().hex[:10].upper(),
                    customer_id,
                    subject,
                    description,
                    now,
                    json.dumps([{"author": "Friend", "message": description, "timestamp": now}]),
                ),
            )
            conn.commit()
            flash("Your customer-service request has been submitted.", "success")
            return redirect(url_for("support"))
    try:
        tickets = conn.execute(
            "SELECT * FROM support_tickets WHERE customerID=? ORDER BY createdDate DESC",
            (customer_id,),
        ).fetchall()
    finally:
        conn.close()
    return render_template("support.html", tickets=tickets)


# Rule-based support responses; escalates to a ticket when the user asks for a human
def chatbot_answer(message):
    text = message.lower()
    if any(word in text for word in ("order", "tracking", "track", "shipment", "delivery")):
        return (
            "Open My Orders, choose an order, and use the Tracking section for current status "
            "and estimated delivery. For a delivery issue, connect me to customer care."
        )
    if any(word in text for word in ("return", "refund", "cancel")):
        return (
            "Delivered orders can request a return or refund from the order details page. "
            "Processing or confirmed orders can be cancelled before shipment."
        )
    if any(word in text for word in ("coupon", "discount", "offer", "promo")):
        return (
            "Coupons can be entered from the Cart page. Try an available code and the cart "
            "will show whether it applies."
        )
    if any(word in text for word in ("address", "pin", "pincode")):
        return (
            "You can add, edit, remove or select a default address in Account Settings."
        )
    if any(word in text for word in ("payment", "upi", "card", "cod")):
        return (
            "BuyASAP demo checkout supports UPI, Card, Net Banking and Cash on Delivery. "
            "No real money is collected and no payment gateway is contacted."
        )
    if any(word in text for word in ("human", "agent", "care", "person", "support", "help")):
        return (
            "I can connect your query to BuyASAP Customer Care. A support request is recorded "
            "and a working-hours agent will respond within 24 working hours."
        )
    return (
        "I can help with orders, delivery, returns, refunds, coupons, addresses and payments. "
        "Tell me what you need, or ask to connect to customer care."
    )


@app.route("/support/chat", methods=["POST"])
@customer_required
def support_chat():
    message = request.form.get("message", "").strip()
    if not message and request.is_json:
        message = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not message:
        return jsonify({"reply": "Please type a question for BuyASAP support."}), 400

    wants_care = any(
        word in message.lower()
        for word in ("human", "agent", "customer care", "person", "connect", "support")
    )
    reply = chatbot_answer(message)
    if wants_care:
        customer_id = get_customer()["customerID"]
        now = datetime.datetime.now().isoformat()
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO support_tickets
                   (ticketId, customerID, subject, description, status, assignedTo, createdDate, commentsJSON)
                   VALUES (?, ?, 'AI chatbot handoff', ?, 'Open',
                           'BuyASAP Customer Care', ?, ?)""",
                (
                    "TICK-" + uuid.uuid4().hex[:10].upper(),
                    customer_id,
                    message,
                    now,
                    json.dumps([
                        {"author": "Friend", "message": message, "timestamp": now},
                        {
                            "author": "BuyASAP AI",
                            "message": "Request queued for customer care; response target: within 24 working hours.",
                            "timestamp": now,
                        },
                    ]),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        reply = (
            "Your request has been sent to BuyASAP Customer Care. "
            "A customer-care agent will respond within 24 working hours."
        )
    return jsonify({"reply": reply})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
