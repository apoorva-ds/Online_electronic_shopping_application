import sqlite3
import json
import os
import datetime
import uuid
import hashlib

DB_PATH = os.path.join(os.path.dirname(__file__), "ecommerce.db")

def hash_pw(plain: str) -> str:
    return hashlib.sha256(plain.encode('utf-8')).hexdigest()

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("PRAGMA foreign_keys = ON;")

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        userID TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phoneNumber TEXT,
        password TEXT NOT NULL,
        emailVerified INTEGER NOT NULL DEFAULT 1,
        role TEXT NOT NULL,
        customerID TEXT,
        sellerID TEXT,
        department TEXT,
        isAvailable INTEGER DEFAULT 1,
        shopName TEXT,
        gstNo TEXT,
        sellerRating REAL DEFAULT 4.8,
        bankAccount TEXT,
        accessLevel TEXT DEFAULT 'ADMIN'
    );
    """)

    user_columns = {row["name"] for row in c.execute("PRAGMA table_info(users)")}
    if "emailVerified" not in user_columns:
        c.execute("ALTER TABLE users ADD COLUMN emailVerified INTEGER NOT NULL DEFAULT 1")

    c.execute("""
    CREATE TABLE IF NOT EXISTS addresses (
        addressID TEXT PRIMARY KEY,
        customerID TEXT NOT NULL,
        street TEXT NOT NULL,
        city TEXT NOT NULL,
        state TEXT NOT NULL,
        pincode INTEGER NOT NULL,
        landmark TEXT,
        isDefault INTEGER DEFAULT 0
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        categoryID TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        description TEXT
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        productID TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        price REAL NOT NULL,
        discountPrice REAL NOT NULL,
        image TEXT NOT NULL,
        brand TEXT NOT NULL,
        categoryID TEXT NOT NULL,
        sellerID TEXT NOT NULL,
        isActive INTEGER DEFAULT 1,
        totalReviews INTEGER DEFAULT 0,
        rating REAL DEFAULT 4.5,
        specifications TEXT DEFAULT '{}'
    );
    """)

    product_columns = {row["name"] for row in c.execute("PRAGMA table_info(products)")}
    if "specifications" not in product_columns:
        c.execute("ALTER TABLE products ADD COLUMN specifications TEXT DEFAULT '{}'")

    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        inventoryID TEXT PRIMARY KEY,
        productID TEXT UNIQUE NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 10,
        warehouseLocation TEXT NOT NULL DEFAULT 'Bengaluru Central Hub'
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS cart_items (
        cartID TEXT NOT NULL,
        customerID TEXT NOT NULL,
        productID TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        addDate TEXT NOT NULL,
        PRIMARY KEY (customerID, productID)
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS wishlist_items (
        wishlistID TEXT NOT NULL,
        customerID TEXT NOT NULL,
        productID TEXT NOT NULL,
        addDate TEXT NOT NULL,
        PRIMARY KEY (customerID, productID)
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS coupons (
        couponCode TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        discountType TEXT NOT NULL,
        discountValue REAL NOT NULL,
        expiryDate TEXT NOT NULL,
        usageLimit INTEGER NOT NULL DEFAULT 100,
        isActive INTEGER DEFAULT 1
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        orderId TEXT PRIMARY KEY,
        customerID TEXT NOT NULL,
        orderDate TEXT NOT NULL,
        status TEXT NOT NULL,
        totalAmount REAL NOT NULL,
        discountAmount REAL NOT NULL DEFAULT 0.0,
        finalAmount REAL NOT NULL,
        couponCode TEXT,
        paymentMethod TEXT NOT NULL,
        shippingAddressJSON TEXT NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        orderId TEXT NOT NULL,
        productID TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unitPrice REAL NOT NULL,
        totalPrice REAL NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        paymentId TEXT PRIMARY KEY,
        orderId TEXT UNIQUE NOT NULL,
        method TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL,
        transactionId TEXT,
        paymentDate TEXT NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS shipments (
        shipmentID TEXT PRIMARY KEY,
        orderId TEXT UNIQUE NOT NULL,
        trackingNumber TEXT NOT NULL,
        carrier TEXT NOT NULL,
        currentStatus TEXT NOT NULL,
        shippedDate TEXT NOT NULL,
        estimatedDelivery TEXT NOT NULL,
        actualDelivery TEXT,
        currentLocation TEXT NOT NULL,
        historyJSON TEXT NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS return_requests (
        returnId TEXT PRIMARY KEY,
        orderId TEXT UNIQUE NOT NULL,
        customerID TEXT NOT NULL,
        reason TEXT NOT NULL,
        refundAmount REAL NOT NULL,
        status TEXT NOT NULL,
        requestDate TEXT NOT NULL,
        resolutionNote TEXT
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        reviewId TEXT PRIMARY KEY,
        productID TEXT NOT NULL,
        customerID TEXT NOT NULL,
        customerName TEXT NOT NULL,
        rating INTEGER NOT NULL,
        comment TEXT NOT NULL,
        isVerifiedPurchase INTEGER DEFAULT 1,
        reviewDate TEXT NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS support_tickets (
        ticketId TEXT PRIMARY KEY,
        customerID TEXT NOT NULL,
        subject TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Open',
        assignedTo TEXT DEFAULT 'Customer Care Agent',
        createdDate TEXT NOT NULL,
        resolvedDate TEXT,
        commentsJSON TEXT NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        notificationID TEXT PRIMARY KEY,
        userID TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        type TEXT NOT NULL,
        isRead INTEGER DEFAULT 0,
        createdAt TEXT NOT NULL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS pending_registrations (
        email TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        phoneNumber TEXT,
        passwordHash TEXT NOT NULL,
        otpSalt TEXT NOT NULL,
        otpHash TEXT NOT NULL,
        expiresAt TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        createdAt TEXT NOT NULL
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS email_change_otps (
        userID TEXT PRIMARY KEY,
        newEmail TEXT UNIQUE NOT NULL,
        otpSalt TEXT NOT NULL,
        otpHash TEXT NOT NULL,
        expiresAt TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        createdAt TEXT NOT NULL
    );
    """)

    conn.commit()
    seed_initial_data(conn)
    seed_store_catalog(conn)
    conn.close()


def seed_initial_data(conn):
    c = conn.cursor()

    # Check if already seeded
    c.execute("SELECT COUNT(*) FROM users;")
    if c.fetchone()[0] > 0:
        return

    now = datetime.datetime.now().isoformat()

    # Default users (customer, seller, admin, support)
    cust_uid = "USR-CUST-001"
    cust_id = "CUST-APOORVA"
    c.execute("""
        INSERT INTO users (userID, name, email, phoneNumber, password, role, customerID)
        VALUES (?, ?, ?, ?, ?, 'Customer', ?)
    """, (cust_uid, "Apoorva S M", "apoorva@gmail.com", "9876543210", hash_pw("pass123"), cust_id))

    seller_uid = "USR-SEL-001"
    seller_id = "SEL-ELECTRO"
    c.execute("""
        INSERT INTO users (userID, name, email, phoneNumber, password, role, sellerID, shopName, gstNo, sellerRating, bankAccount)
        VALUES (?, ?, ?, ?, ?, 'Seller', ?, ?, ?, 4.9, ?)
    """, (seller_uid, "ElectroTech Solutions", "techhub@sellers.in", "9123456789", hash_pw("pass123"), seller_id, "ElectroTech Official", "29AABCU9603R1ZX", "HDFC0001234 - 50100234901"))

    admin_uid = "USR-ADM-001"
    c.execute("""
        INSERT INTO users (userID, name, email, phoneNumber, password, role, accessLevel)
        VALUES (?, ?, ?, ?, ?, 'Admin', 'SUPER_ADMIN')
    """, (admin_uid, "System Administrator", "admin@electronicshop.com", "9000000001", hash_pw("admin123")))

    care_uid = "USR-CARE-001"
    c.execute("""
        INSERT INTO users (userID, name, email, phoneNumber, password, role, department, isAvailable)
        VALUES (?, ?, ?, ?, ?, 'CustomerCare', 'Priority Customer Assistance', 1)
    """, (care_uid, "Sheethal (Customer Care)", "support@electronicshop.com", "9000000002", hash_pw("care123")))

    # Seed Customer Address
    addr_id = "ADDR-DEFAULT-01"
    c.execute("""
        INSERT INTO addresses (addressID, customerID, street, city, state, pincode, landmark, isDefault)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (addr_id, cust_id, "Flat 402, Green Glen Layout, Bellandur", "Bengaluru", "Karnataka", 560103, "Opposite RUAS Campus"))

    # Seed Categories
    categories = [
        ("CAT-PHONES", "Smartphones", "Next-generation 5G flagship and mid-range mobile phones"),
        ("CAT-LAPTOPS", "Laptops & Computing", "High performance ultrabooks, creator workstations, and gaming laptops"),
        ("CAT-AUDIO", "Audio & Headphones", "Audiophile active noise cancelling headphones and premium earbuds"),
        ("CAT-WATCHES", "Smartwatches & Wearables", "Fitness tracking, health monitoring, and cellular smartwatches"),
        ("CAT-CAMERAS", "Cameras & Photography", "Full-frame mirrorless digital cameras and professional optics"),
        ("CAT-ACCESSORIES", "Accessories & Peripherals", "Ergonomic mice, mechanical keyboards, chargers, and cables")
    ]
    for cid, cname, cdesc in categories:
        c.execute("INSERT INTO categories (categoryID, name, description) VALUES (?, ?, ?)", (cid, cname, cdesc))

    # Seed Products
    products = [
        ("PROD-IP16PM", "iPhone 16 Pro Max 256GB - Desert Titanium",
         "Apple A18 Pro chip, 48MP Fusion camera system with 5x Telephoto, 6.9-inch Super Retina XDR display with ProMotion and Titanium finish.",
         144900.0, 134900.0, "https://images.unsplash.com/photo-1695048133142-1a20484d2569?w=800&auto=format&fit=crop&q=80",
         "Apple", "CAT-PHONES", seller_id, 1, 142, 4.9, 25),

        ("PROD-S25ULTRA", "Samsung Galaxy S25 Ultra 5G - Titanium Silver",
         "Snapdragon 8 Elite Mobile Platform, 200MP Quad Tele camera, built-in S-Pen, Galaxy AI live translate, and 5000mAh battery.",
         134999.0, 124999.0, "https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?w=800&auto=format&fit=crop&q=80",
         "Samsung", "CAT-PHONES", seller_id, 1, 98, 4.8, 18),

        ("PROD-MBP16M3", "MacBook Pro 16\" M3 Max (36GB / 1TB SSD)",
         "Liquid Retina XDR display, up to 22 hours battery life, 16-core CPU, 40-core GPU, Space Black aluminum unibody.",
         349900.0, 319900.0, "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=800&auto=format&fit=crop&q=80",
         "Apple", "CAT-LAPTOPS", seller_id, 1, 64, 4.9, 12),

        ("PROD-DELLXPS15", "Dell XPS 15 9530 OLED Touch Laptop",
         "13th Gen Intel Core i9-13900H, NVIDIA GeForce RTX 4070 8GB, 3.5K OLED InfinityEdge touch display, 32GB DDR5, 1TB SSD.",
         224990.0, 199990.0, "https://images.unsplash.com/photo-1593642632823-8f785ba67e45?w=800&auto=format&fit=crop&q=80",
         "Dell", "CAT-LAPTOPS", seller_id, 1, 45, 4.7, 15),

        ("PROD-SONYXM5", "Sony WH-1000XM5 Wireless ANC Headphones",
         "Industry-leading noise cancellation with two processors and 8 microphones, Auto NC Optimizer, 30-hour battery life, Multipoint connection.",
         34990.0, 26990.0, "https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=800&auto=format&fit=crop&q=80",
         "Sony", "CAT-AUDIO", seller_id, 1, 230, 4.8, 40),

        ("PROD-AIRPODSPRO", "Apple AirPods Pro (2nd Generation, USB-C)",
         "Up to 2x more Active Noise Cancellation, Adaptive Audio, Transparency mode, Personalized Spatial Audio with dynamic head tracking.",
         24900.0, 20900.0, "https://images.unsplash.com/photo-1600294037681-c80b4cb5b434?w=800&auto=format&fit=crop&q=80",
         "Apple", "CAT-AUDIO", seller_id, 1, 310, 4.9, 50),

        ("PROD-AWATCH10", "Apple Watch Series 10 GPS + Cellular 46mm",
         "Thinnest Apple Watch ever with wide-angle OLED display, sleep apnea notifications, ECG app, and water depth/temperature gauge.",
         54900.0, 49900.0, "https://images.unsplash.com/photo-1508685096489-7aacd43bd3b1?w=800&auto=format&fit=crop&q=80",
         "Apple", "CAT-WATCHES", seller_id, 1, 77, 4.7, 22),

        ("PROD-GWATCHULTRA", "Samsung Galaxy Watch Ultra 47mm LTE",
         "Grade 4 Titanium cushion frame, 100m water resistance, dual-frequency GPS, up to 100h battery life in Power Saving mode.",
         59999.0, 52999.0, "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=800&auto=format&fit=crop&q=80",
         "Samsung", "CAT-WATCHES", seller_id, 1, 52, 4.6, 16),

        ("PROD-SONYA7IV", "Sony Alpha A7 IV Full-Frame Mirrorless Camera",
         "33MP Exmor R back-illuminated CMOS sensor, BIONZ XR processing engine, 4K 60p 10-bit 4:2:2 video recording, 759-point AF system.",
         209990.0, 189990.0, "https://images.unsplash.com/photo-1516035069371-29a1b244cc32?w=800&auto=format&fit=crop&q=80",
         "Sony", "CAT-CAMERAS", seller_id, 1, 38, 4.9, 10),

        ("PROD-MXMASTER3S", "Logitech MX Master 3S Wireless Performance Mouse",
         "8K DPI any-surface tracking, quiet clicks, MagSpeed electromagnetic scrolling, USB-C fast charging, cross-computer Flow control.",
         10995.0, 8995.0, "https://images.unsplash.com/photo-1615663245857-ac93bb7c39e7?w=800&auto=format&fit=crop&q=80",
         "Logitech", "CAT-ACCESSORIES", seller_id, 1, 180, 4.8, 60),

        ("PROD-BOSEQCULTRA", "Bose QuietComfort Ultra Noise Cancelling Headphones",
         "World-class noise cancellation, breakthrough spatialized audio with Bose Immersive Audio, custom acoustic architecture, up to 24-hr battery.",
         35900.0, 29900.0, "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=800&auto=format&fit=crop&q=80",
         "Bose", "CAT-AUDIO", seller_id, 1, 95, 4.8, 30),

        ("PROD-ROGZEPHYRUS", "ASUS ROG Zephyrus G16 (OLED, RTX 4080)",
         "Intel Core Ultra 9 185H processor, 2.5K 240Hz OLED ROG Nebula display, NVIDIA RTX 4080 12GB, ultra-slim CNC aluminum chassis.",
         279990.0, 249990.0, "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=800&auto=format&fit=crop&q=80",
         "ASUS", "CAT-LAPTOPS", seller_id, 1, 29, 4.9, 8)
    ]

    for pid, pname, pdesc, price, disc_price, img, brand, cid, sid, is_act, rev_count, rating, stock in products:
        c.execute("""
            INSERT INTO products (productID, name, description, price, discountPrice, image, brand, categoryID, sellerID, isActive, totalReviews, rating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, pname, pdesc, price, disc_price, img, brand, cid, sid, is_act, rev_count, rating))

        inv_id = f"INV-{pid}"
        c.execute("""
            INSERT INTO inventory (inventoryID, productID, quantity, warehouseLocation)
            VALUES (?, ?, ?, 'Bengaluru Central Distribution Hub')
        """, (inv_id, pid, stock))

    # Seed Coupons
    coupons = [
        ("TECH10", "Get 10% instant discount on any electronic purchase", "percentage", 10.0, "2027-12-31", 200, 1),
        ("RUAS20", "Exclusive 20% discount for University students and staff", "percentage", 20.0, "2027-12-31", 100, 1),
        ("SAVE500", "Flat ₹500 off on orders above ₹2000", "fixed", 500.0, "2027-12-31", 150, 1),
        ("WELCOME50", "Welcome festive 15% discount for new customers", "percentage", 15.0, "2027-12-31", 50, 1)
    ]
    for code, desc, dtype, val, exp, limit, act in coupons:
        c.execute("""
            INSERT INTO coupons (couponCode, description, discountType, discountValue, expiryDate, usageLimit, isActive)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (code, desc, dtype, val, exp, limit, act))

    # Sample reviews
    c.execute("""
        INSERT INTO reviews (reviewId, productID, customerID, customerName, rating, comment, isVerifiedPurchase, reviewDate)
        VALUES
        (?, 'PROD-SONYXM5', ?, 'Apoorva S M', 5, 'Exceptional active noise cancellation and crystal clear audio fidelity. Highly recommended!', 1, ?),
        (?, 'PROD-IP16PM', ?, 'Apoorva S M', 5, 'The camera button and battery life on the 16 Pro Max are stellar. Delivery was on time!', 1, ?)
    """, ("REV-001", cust_id, now, "REV-002", cust_id, now))

    # Initial delivered order for order history and return/refund testing
    ord_id = "ORD-202611-001"
    shipping_addr = {
        "addressID": addr_id,
        "street": "Flat 402, Green Glen Layout, Bellandur",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": 560103,
        "landmark": "Opposite RUAS Campus"
    }
    c.execute("""
        INSERT INTO orders (orderId, customerID, orderDate, status, totalAmount, discountAmount, finalAmount, couponCode, paymentMethod, shippingAddressJSON)
        VALUES (?, ?, ?, 'Delivered', 26990.0, 2699.0, 24291.0, 'TECH10', 'UPI', ?)
    """, (ord_id, cust_id, (datetime.datetime.now() - datetime.timedelta(days=4)).isoformat(), json.dumps(shipping_addr)))

    c.execute("""
        INSERT INTO order_items (orderId, productID, quantity, unitPrice, totalPrice)
        VALUES (?, 'PROD-SONYXM5', 1, 26990.0, 26990.0)
    """, (ord_id,))

    c.execute("""
        INSERT INTO payments (paymentId, orderId, method, amount, status, transactionId, paymentDate)
        VALUES (?, ?, 'UPI', 24291.0, 'Success', 'TXN-UPI-9823419082', ?)
    """, (f"PAY-{ord_id}", ord_id, (datetime.datetime.now() - datetime.timedelta(days=4)).isoformat()))

    ship_history = [
        {"status": "Confirmed", "location": "Warehouse Hub, Bengaluru", "timestamp": (datetime.datetime.now() - datetime.timedelta(days=4)).isoformat(), "note": "Order packed and invoice generated"},
        {"status": "Shipped", "location": "Bangalore Central Sorting Facility", "timestamp": (datetime.datetime.now() - datetime.timedelta(days=3)).isoformat(), "note": "Dispatched via BlueDart Express"},
        {"status": "Out for Delivery", "location": "Bellandur Delivery Station", "timestamp": (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat(), "note": "Assigned to courier agent"},
        {"status": "Delivered", "location": "Customer Address, Bellandur", "timestamp": datetime.datetime.now().isoformat(), "note": "Delivered to customer. Signature verified."}
    ]
    c.execute("""
        INSERT INTO shipments (shipmentID, orderId, trackingNumber, carrier, currentStatus, shippedDate, estimatedDelivery, actualDelivery, currentLocation, historyJSON)
        VALUES (?, ?, 'BD9982401', 'BlueDart Express', 'Delivered', ?, ?, ?, 'Customer Address, Bellandur', ?)
    """, (f"SHIP-{ord_id}", ord_id, (datetime.datetime.now() - datetime.timedelta(days=3)).isoformat(),
          (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
          datetime.datetime.now().isoformat(), json.dumps(ship_history)))

    # Seed Support Ticket
    ticket_history = [
        {"author": "System", "message": "Ticket created and dispatched to Customer Care queue.", "timestamp": now},
        {"author": "Sheethal (Customer Care)", "message": "Hello Apoorva, your delivery invoice has been emailed to you.", "timestamp": now}
    ]
    c.execute("""
        INSERT INTO support_tickets (ticketId, customerID, subject, description, status, assignedTo, createdDate, commentsJSON)
        VALUES (?, ?, 'Invoice download inquiry', 'Need duplicate tax invoice for my Sony WH-1000XM5 purchase.', 'Resolved', 'Sheethal (Customer Care)', ?, ?)
    """, ("TICK-101", cust_id, now, json.dumps(ticket_history)))

    # Seed Notification
    c.execute("""
        INSERT INTO notifications (notificationID, userID, title, message, type, isRead, createdAt)
        VALUES
        (?, ?, 'Order Delivered Successfully!', 'Your order ORD-202611-001 has been delivered. Enjoy your Sony WH-1000XM5!', 'order', 0, ?),
        (?, ?, 'Exclusive Student Coupon Added', 'Use code RUAS20 at checkout for 20% off all electronics!', 'promo', 0, ?)
    """, (f"NOTIF-{uuid.uuid4().hex[:6]}", cust_uid, now, f"NOTIF-{uuid.uuid4().hex[:6]}", cust_uid, now))

    conn.commit()


STORE_CATEGORIES = (
    ("CAT-TABLETS", "Tablets", "Popular tablets and 2-in-1 devices sold in India"),
    ("CAT-INDIAN-PHONES", "Phones", "Top-rated smartphones sold in India"),
    ("CAT-EARPHONES", "Earphones", "Wireless and wired audio rated above 4 stars"),
    ("CAT-INDIAN-LAPTOPS", "Laptops", "Highly rated laptops sold in India"),
    ("CAT-SPEAKERS", "Speakers", "Top-rated portable and home speakers sold in India"),
    ("CAT-KITCHEN-ELECTRONICS", "Kitchen Electronics", "Highly rated kitchen appliances sold in India"),
    ("CAT-TVS", "TV", "Top-rated televisions sold in India"),
    ("CAT-SMARTWATCHES", "Smart Watches", "Popular smartwatches and fitness trackers sold in India"),
)

STORE_PRODUCTS = {
    "CAT-TABLETS": [
        ("Samsung", "Galaxy Tab S9 FE", 44999, 4.6),
        ("Apple", "iPad 10th Generation", 34900, 4.7),
        ("Lenovo", "Tab P12", 39999, 4.5),
        ("Xiaomi", "Pad 6", 29999, 4.6),
        ("Realme", "Pad 2", 14999, 4.4),
        ("OnePlus", "Pad Go", 19999, 4.5),
        ("Honor", "Pad 9", 25999, 4.4),
        ("Motorola", "Moto Pad 60 Pro", 24999, 4.3),
        ("Lava", "Agni Tab 8", 12999, 4.2),
        ("Acer", "Iconia X12", 27999, 4.3),
    ],
    "CAT-INDIAN-PHONES": [
        ("Samsung", "Galaxy S24 FE 5G", 49999, 4.6),
        ("Apple", "iPhone 15", 69900, 4.7),
        ("Xiaomi", "Xiaomi 14", 54999, 4.5),
        ("Redmi", "Note 13 Pro+ 5G", 29999, 4.4),
        ("Vivo", "V40 5G", 35999, 4.5),
        ("OPPO", "Reno 12 5G", 32999, 4.4),
        ("Realme", "GT 6 5G", 40999, 4.5),
        ("OnePlus", "Nord 4", 29999, 4.6),
        ("Motorola", "Edge 50 Pro", 28999, 4.4),
        ("iQOO", "Neo 9 Pro 5G", 35999, 4.5),
    ],
    "CAT-EARPHONES": [
        ("boAt", "Airdopes 141 ANC", 1999, 4.3),
        ("Noise", "Buds VS104", 1799, 4.2),
        ("Boult", "Z40 Pro", 1499, 4.3),
        ("Samsung", "Galaxy Buds FE", 6999, 4.5),
        ("Apple", "AirPods 3rd Generation", 19900, 4.6),
        ("Sony", "WF-C700N", 8990, 4.5),
        ("OnePlus", "Nord Buds 3 Pro", 3299, 4.4),
        ("Realme", "Buds T310", 1999, 4.2),
        ("JBL", "Live Pro 2 TWS", 9999, 4.5),
        ("Nothing", "Ear (a)", 7999, 4.4),
    ],
    "CAT-INDIAN-LAPTOPS": [
        ("HP", "Victus 15 Gaming Laptop", 67990, 4.5),
        ("Dell", "Inspiron 14", 52990, 4.5),
        ("Lenovo", "IdeaPad Slim 5", 61990, 4.6),
        ("ASUS", "Vivobook 15 OLED", 65990, 4.5),
        ("Acer", "Aspire 7", 57990, 4.4),
        ("Apple", "MacBook Air M2", 96900, 4.7),
        ("Samsung", "Galaxy Book4", 74990, 4.5),
        ("MSI", "Modern 14", 49990, 4.3),
        ("Infinix", "ZEROBOOK 13", 44990, 4.2),
        ("LG", "Gram 14", 89990, 4.6),
    ],
    "CAT-SPEAKERS": [
        ("boAt", "Stone 1200", 4999, 4.4),
        ("JBL", "Flip 6", 9999, 4.6),
        ("Sony", "SRS-XB100", 4490, 4.5),
        ("Marshall", "Emberton II", 13999, 4.6),
        ("Bose", "SoundLink Flex", 16900, 4.7),
        ("Samsung", "Sound Tower MX-T40", 24990, 4.5),
        ("Zebronics", "Zeb-County", 1499, 4.2),
        ("Harman Kardon", "Onyx Studio 8", 24999, 4.6),
        ("Mivi", "Fort S16 Soundbar", 2999, 4.3),
        ("Portronics", "SoundDrum P", 2499, 4.2),
    ],
    "CAT-KITCHEN-ELECTRONICS": [
        ("Prestige", "Endura Mixer Grinder", 4999, 4.4),
        ("Philips", "HD9252 Air Fryer", 8999, 4.6),
        ("Bajaj", "GX 8 Mixer Grinder", 3499, 4.3),
        ("Havells", "Prolife Digi Air Fryer", 7999, 4.5),
        ("Panasonic", "MX-AC400 Mixer Grinder", 5499, 4.4),
        ("Croma", "4.5L Air Fryer", 6999, 4.2),
        ("Samsung", "MG23J5133AG Microwave", 10990, 4.5),
        ("Bosch", "TrueMixx Mixer Grinder", 6499, 4.5),
        ("Morphy Richards", "Nutrimix Juicer", 5799, 4.3),
        ("Usha", "Instachef Induction Cooktop", 3299, 4.4),
    ],
    "CAT-TVS": [
        ("Samsung", "Crystal 4K 55-inch UHD", 52990, 4.6),
        ("LG", "OLED Evo 55-inch 4K", 139990, 4.7),
        ("Sony", "Bravia 55-inch 4K Google TV", 72990, 4.6),
        ("Xiaomi", "X Pro 55-inch QLED", 47999, 4.5),
        ("OnePlus", "Q1 Pro 55-inch QLED", 49999, 4.5),
        ("TCL", "C655 55-inch QLED", 44990, 4.4),
        ("Hisense", "A7N 55-inch 4K", 46990, 4.3),
        ("VU", "GloLED 50-inch 4K", 35999, 4.4),
        ("Haier", "S800 55-inch 4K", 46990, 4.4),
        ("Panasonic", "LX Series 43-inch 4K", 35999, 4.5),
    ],
    "CAT-SMARTWATCHES": [
        ("Apple", "Watch Series 10 GPS 46mm", 44900, 4.7),
        ("Samsung", "Galaxy Watch Ultra 47mm", 59999, 4.6),
        ("Noise", "ColorFit Pro 5 Max", 4999, 4.3),
        ("boAt", "Lunar Oasis", 2999, 4.2),
        ("Fire-Boltt", "Phoenix Ultra", 3499, 4.3),
        ("Amazfit", "GTR 4 Smartwatch", 14999, 4.5),
        ("OnePlus", "Watch 2", 24999, 4.4),
        ("Titan", "Talk S with AMOLED Display", 8999, 4.4),
        ("Fastrack", "Revoltt FS1 Pro", 3999, 4.2),
        ("Realme", "Watch S2", 4999, 4.3),
    ],
}


def build_product_details(category_id, brand, model, price, rating):
    details = {
        "CAT-TABLETS": {
            "Display": "10.1-inch 2K LCD / LED display",
            "Processor": "Octa-core processor",
            "Memory": "8 GB RAM",
            "Storage": "128 GB internal storage",
            "Operating System": "Android with regular updates",
            "Camera": "13 MP rear camera, 8 MP front camera",
            "Battery": "Up to 12 hours of mixed usage",
            "Connectivity": "Wi-Fi, Bluetooth, USB-C",
            "In the Box": "Tablet, USB-C cable, charger and quick-start guide",
        },
        "CAT-INDIAN-PHONES": {
            "Display": "6.7-inch AMOLED, 120 Hz refresh rate",
            "Processor": "Fast 5G octa-core processor",
            "Memory": "8 GB RAM",
            "Storage": "256 GB UFS storage",
            "Rear Camera": "50 MP primary + wide/portrait cameras",
            "Front Camera": "32 MP selfie camera",
            "Battery": "5000 mAh battery",
            "Charging": "Fast charging with compatible charger",
            "Connectivity": "5G, 4G, Wi-Fi 6, Bluetooth 5.3, NFC",
            "Operating System": "Android with brand UI",
        },
        "CAT-EARPHONES": {
            "Type": "True wireless earbuds",
            "Driver": "10 mm dynamic audio driver",
            "Playback": "Up to 30 hours with charging case",
            "Microphone": "Built-in call noise reduction",
            "Connectivity": "Bluetooth 5.3, instant pairing",
            "Controls": "Touch controls for calls and music",
            "Protection": "IPX4 sweat and splash resistance",
            "Charging": "USB-C charging",
            "In the Box": "Earbuds, charging case, USB-C cable and ear tips",
        },
        "CAT-INDIAN-LAPTOPS": {
            "Display": "15.6-inch Full HD IPS / OLED panel",
            "Processor": "Latest 8-core Intel or AMD processor",
            "Memory": "16 GB DDR5 RAM",
            "Storage": "512 GB PCIe NVMe SSD",
            "Graphics": "Integrated or dedicated graphics",
            "Battery": "Up to 8 hours of everyday usage",
            "Operating System": "Windows 11",
            "Ports": "USB-C, USB-A, HDMI and audio jack",
            "Connectivity": "Wi-Fi 6, Bluetooth",
            "Weight": "Approximately 1.5–2.0 kg",
        },
        "CAT-SPEAKERS": {
            "Type": "Portable wireless speaker",
            "Output": "30 W stereo / spatial sound output",
            "Connectivity": "Bluetooth 5.3, USB-C and AUX",
            "Battery": "Up to 16 hours of playback",
            "Protection": "Water and dust resistant",
            "Controls": "On-device volume, playback and voice controls",
            "Microphone": "Hands-free calling support",
            "In the Box": "Speaker, charging cable and quick-start guide",
        },
        "CAT-KITCHEN-ELECTRONICS": {
            "Material": "Food-grade metal and heat-resistant plastic",
            "Power": "750–1500 W",
            "Capacity": "Suitable for everyday family cooking",
            "Controls": "Easy digital or mechanical controls",
            "Safety": "Overheat protection and automatic shut-off",
            "Mounting": "Countertop design",
            "Cleaning": "Detachable, easy-clean parts",
            "Warranty": "Brand warranty with authorized India service",
            "In the Box": "Main unit, attachments and user manual",
        },
        "CAT-TVS": {
            "Display": "55-inch 4K Ultra HD panel",
            "Panel Technology": "LED / QLED / OLED, model dependent",
            "Resolution": "3840 × 2160 pixels",
            "Refresh Rate": "60 Hz",
            "Smart Platform": "Brand smart OS with popular streaming apps",
            "Audio": "20 W to 40 W speaker output",
            "HDR": "HDR10 and HDR10+ / Dolby Vision, model dependent",
            "Connectivity": "HDMI, USB, Ethernet, Wi-Fi and Bluetooth",
            "Power": "AC 100–240 V",
            "Warranty": "1-year brand warranty",
        },
        "CAT-SMARTWATCHES": {
            "Display": "1.43-inch AMOLED Always-On Display",
            "Health Tracking": "Heart rate, SpO2, sleep and stress monitoring",
            "Sports Modes": "100+ workout and fitness tracking modes",
            "Battery": "Up to 7–14 days of typical usage",
            "Connectivity": "Bluetooth calling, notifications and app sync",
            "Water Resistance": "IP68 / 5 ATM water resistant",
            "Compatibility": "Android and iOS smartphones",
            "In the Box": "Smartwatch, magnetic charging cable and user manual",
        },
    }
    highlights = {
        "CAT-TABLETS": ["Portable high-resolution display", "Large storage", "All-day learning and entertainment"],
        "CAT-INDIAN-PHONES": ["Premium 5G experience", "High-resolution cameras", "Large battery"],
        "CAT-EARPHONES": ["Immersive wireless sound", "Active or passive noise control", "Comfortable everyday fit"],
        "CAT-INDIAN-LAPTOPS": ["Fast SSD storage", "High-resolution display", "Built for work and entertainment"],
        "CAT-SPEAKERS": ["Wireless portability", "Clear room-filling sound", "Long battery life"],
        "CAT-KITCHEN-ELECTRONICS": ["Powerful everyday performance", "Easy controls", "Safety and warranty support"],
        "CAT-TVS": ["4K picture quality", "Smart streaming platform", "Multiple HDMI and USB ports"],
        "CAT-SMARTWATCHES": ["AMOLED touchscreen display", "24/7 health and fitness tracking", "Bluetooth calling and multi-day battery"],
    }
    return {
        "Brand": brand,
        "Model": model,
        "MRP": f"₹{price:,}",
        "Rating": f"{rating:.1f} out of 5",
        "Highlights": " • ".join(highlights[category_id]),
        "Specifications": details[category_id],
    }


def seed_store_catalog(conn):
    """Sync the store categories and product catalog on startup."""
    seller = conn.execute(
        "SELECT sellerID FROM users WHERE role='Seller' AND sellerID IS NOT NULL LIMIT 1"
    ).fetchone()
    seller_id = seller["sellerID"] if seller else "SEL-BUYASAP"
    category_names = {category_id: name for category_id, name, _ in STORE_CATEGORIES}
    image_by_category = {
        "CAT-TABLETS": (
            "photo-1544244015-0df4b3ffc6b0", "photo-1561154464-82e9adf32764",
            "photo-1589739900243-4b52cd9b104e",
        ),
        "CAT-INDIAN-PHONES": (
            "photo-1511707171634-5f897ff02aa9", "photo-1598327105666-5b89351aff97",
            "photo-1592899677977-9c10ca588bbd",
        ),
        "CAT-EARPHONES": (
            "photo-1590658268037-6bf12165a8df", "photo-1606220945770-b5b6c2c55bf1",
            "photo-1600086827875-a63b01f1335c",
        ),
        "CAT-INDIAN-LAPTOPS": (
            "photo-1496181133206-80ce9b88a853", "photo-1588872657578-7efd1f1555ed",
            "photo-1498050108023-c5249f4df085",
        ),
        "CAT-SPEAKERS": (
            "photo-1608043152269-423dbba4e7e1", "photo-1608539733292-190446f3d9f5",
            "photo-1589003077984-894e133dabab",
        ),
        "CAT-KITCHEN-ELECTRONICS": (
            "photo-1556911220-e15b29be8c8f", "photo-1556911220-bff31c812dba",
            "photo-1556909114-f6e7ad7d3136",
        ),
        "CAT-TVS": (
            "photo-1593359677879-a4bb92f829d1", "photo-1593784991095-a205069470b6",
            "photo-1574717024653-61fd2cf4d44d",
        ),
        "CAT-SMARTWATCHES": (
            "photo-1523275335684-37898b6baf30", "photo-1579586337278-3befd40fd17a",
            "photo-1546868871-7041f2a55e12",
        ),
    }

    with conn:
        # Deactivate legacy listings while keeping existing order_items intact
        conn.execute("UPDATE products SET isActive=0")

        for category_id, name, description in STORE_CATEGORIES:
            conn.execute(
                """INSERT INTO categories (categoryID, name, description) VALUES (?, ?, ?)
                   ON CONFLICT(categoryID) DO UPDATE SET name=excluded.name, description=excluded.description""",
                (category_id, name, description),
            )

        for category_id, listings in STORE_PRODUCTS.items():
            for index, (brand, model, price, rating) in enumerate(listings, start=1):
                product_id = f"PROD-BUYASAP-{category_id.removeprefix('CAT-')}-{index:02d}"
                title = f"{brand} {model}"
                description = (
                    f"{title} brings together {brand} quality, dependable everyday performance "
                    f"and a strong customer rating. Explore the full {category_names[category_id]} "
                    "specifications, offer price and delivery information below."
                )
                details = build_product_details(category_id, brand, model, price, rating)
                conn.execute(
                    """INSERT INTO products
                       (productID, name, description, price, discountPrice, image, brand,
                        categoryID, sellerID, isActive, totalReviews, rating, specifications)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                       ON CONFLICT(productID) DO UPDATE SET
                           name=excluded.name,
                           description=excluded.description,
                           price=excluded.price,
                           discountPrice=excluded.discountPrice,
                           image=excluded.image,
                           brand=excluded.brand,
                           categoryID=excluded.categoryID,
                           isActive=1,
                           rating=excluded.rating,
                           totalReviews=excluded.totalReviews,
                           specifications=excluded.specifications""",
                    (
                        product_id,
                        title,
                        description,
                        price,
                        round(price * 0.9, 2),
                        (
                            "https://images.unsplash.com/"
                            f"{image_by_category[category_id][(index - 1) % len(image_by_category[category_id])]}"
                            "?w=800&auto=format&fit=crop&q=80"
                        ),
                        brand,
                        category_id,
                        seller_id,
                        120 + index * 37,
                        rating,
                        json.dumps(details),
                    ),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO inventory
                       (inventoryID, productID, quantity, warehouseLocation)
                       VALUES (?, ?, 10, 'BuyASAP Bengaluru Hub')""",
                    (f"INV-{product_id}", product_id),
                )
