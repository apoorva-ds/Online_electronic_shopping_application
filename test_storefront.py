import sqlite3

import pytest

import database
import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    database_path = tmp_path / "storefront.db"
    monkeypatch.setattr(database, "DB_PATH", str(database_path))
    database.init_db()
    server.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with server.app.test_client() as test_client:
        yield test_client


def sign_in(client):
    response = client.post(
        "/login",
        data={"email": "apoorva@gmail.com", "password": "pass123"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    return response


def test_login_and_authenticated_root_redirect_to_dashboard(client):
    assert client.get("/").headers["Location"].endswith("/login")
    sign_in(client)
    assert client.get("/login").headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    assert client.get("/").headers["Location"].endswith("/login")


def rows(query, params=()):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def test_catalog_has_ten_products_for_every_requested_category(client):
    placeholders = ", ".join("?" for _ in server.STORE_CATEGORY_IDS)
    categories = rows(
        f"""SELECT c.categoryID, c.name, COUNT(p.productID) AS product_count
           FROM categories c JOIN products p ON p.categoryID=c.categoryID
           WHERE c.categoryID IN ({placeholders})
           GROUP BY c.categoryID ORDER BY c.name""",
        server.STORE_CATEGORY_IDS,
    )
    assert len(categories) == len(server.STORE_CATEGORY_IDS)
    assert {row["product_count"] for row in categories} == {10}
    low_rated = rows(
        """SELECT COUNT(*) AS total FROM products
           WHERE isActive=1 AND rating < 4.1"""
    )[0]["total"]
    assert low_rated == 0
    old_active = rows(
        """SELECT COUNT(*) AS total FROM products
           WHERE isActive=1 AND productID NOT LIKE 'PROD-BUYASAP-%'"""
    )[0]["total"]
    assert old_active == 0

    sign_in(client)
    for category_id, _, _ in database.STORE_CATEGORIES:
        response = client.get(f"/products?category={category_id}")
        assert response.status_code == 200
        assert b"10 products" in response.data


def test_gmail_registration_requires_and_accepts_email_otp(client, monkeypatch):
    sent = {}

    def fake_deliver(recipient, otp, purpose):
        sent.update(recipient=recipient, otp=otp, purpose=purpose)
        return True

    monkeypatch.setattr(server, "deliver_otp", fake_deliver)
    response = client.post(
        "/register",
        data={
            "name": "New Friend",
            "email": "NewFriend@GMAIL.com",
            "phone": "9876543210",
            "password": "secret123",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/verify-email"
    assert sent["recipient"] == "newfriend@gmail.com"
    assert rows("SELECT 1 FROM users WHERE lower(email)=?", ("newfriend@gmail.com",)) == []

    response = client.post(
        "/verify-email",
        data={"otp": sent["otp"]},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"
    dashboard = client.get(response.headers["Location"])
    assert b"Welcome" in dashboard.data
    created = rows(
        "SELECT emailVerified FROM users WHERE lower(email)=?",
        ("newfriend@gmail.com",),
    )
    assert len(created) == 1
    assert created[0]["emailVerified"] == 1
    assert client.post(
        "/login",
        data={"email": "newfriend@gmail.com", "password": "secret123"},
        follow_redirects=True,
    ).status_code == 200


def test_product_detail_shows_unique_offer_description_and_specifications(client):
    sign_in(client)
    product_id = "PROD-BUYASAP-INDIAN-PHONES-02"
    response = client.get(f"/products/{product_id}")
    assert response.status_code == 200
    assert b"Apple iPhone 15" in response.data
    assert b"Specifications" in response.data
    assert b"6.7-inch AMOLED" in response.data
    assert b"5000 mAh battery" in response.data
    assert b"discount" in response.data
    assert b"Customer ratings" in response.data

    laptop_id = "PROD-BUYASAP-INDIAN-LAPTOPS-01"
    laptop = client.get(f"/products/{laptop_id}")
    assert laptop.status_code == 200
    assert b"HP Victus 15 Gaming Laptop" in laptop.data
    assert b"512 GB PCIe NVMe SSD" in laptop.data
    assert b"Windows 11" in laptop.data


def test_account_edits_address_and_email_only_after_otp(client, monkeypatch):
    sign_in(client)
    response = client.post(
        "/account",
        data={"name": "Friend Updated", "phone": "9000000000"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Friend Updated" in response.data

    address_id = "ADDR-DEFAULT-01"
    response = client.post(
        f"/addresses/{address_id}/edit",
        data={
            "street": "Updated Flat 12, Test Road",
            "city": "Mysuru",
            "state": "Karnataka",
            "pincode": "570001",
            "landmark": "Near station",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Updated Flat 12" in response.data
    saved_address = rows(
        "SELECT street, city, pincode FROM addresses WHERE addressID=?",
        (address_id,),
    )[0]
    assert saved_address["city"] == "Mysuru"

    sent = {}
    def fake_deliver(recipient, otp, purpose):
        sent.update(recipient=recipient, otp=otp, purpose=purpose)
        return True

    monkeypatch.setattr(server, "deliver_otp", fake_deliver)
    response = client.post(
        "/account/email",
        data={"email": "friend.changed@gmail.com"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/account/verify-email"
    assert rows(
        "SELECT email FROM users WHERE userID='USR-CUST-001'"
    )[0]["email"] == "apoorva@gmail.com"
    assert client.get("/account/verify-email").status_code == 200
    response = client.post(
        "/account/verify-email",
        data={"otp": sent["otp"]},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"friend.changed@gmail.com" in response.data
    assert rows(
        "SELECT email FROM users WHERE userID='USR-CUST-001'"
    )[0]["email"] == "friend.changed@gmail.com"


def test_registration_reports_unconfigured_gmail_sender(client, monkeypatch):
    def no_sender(*_args):
        raise RuntimeError("Email delivery is not configured.")

    monkeypatch.setattr(server, "deliver_otp", no_sender)
    response = client.post(
        "/register",
        data={
            "name": "Friend",
            "email": "friend@gmail.com",
            "password": "secret123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Email delivery is not configured." in response.data
    assert rows("SELECT 1 FROM users WHERE email='friend@gmail.com'") == []


def test_entry_flow_dashboard_copy_and_support_chat_handoff(client):
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 302
    assert home.headers["Location"] == "/login"

    sign_in(client)
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"The Indian tech edit" not in dashboard.data
    assert b"Meet standout electronics" not in dashboard.data
    assert b"Discover smart electronics" in dashboard.data

    answer = client.post(
        "/support/chat",
        json={"message": "I need help tracking my order"},
    )
    assert answer.status_code == 200
    assert b"Tracking" in answer.data or b"tracking" in answer.data

    handoff = client.post(
        "/support/chat",
        json={"message": "Please connect me to customer care"},
    )
    assert handoff.status_code == 200
    assert b"24 working hours" in handoff.data
    tickets = rows(
        "SELECT subject, description FROM support_tickets "
        "WHERE subject='AI chatbot handoff'"
    )
    assert len(tickets) == 1
