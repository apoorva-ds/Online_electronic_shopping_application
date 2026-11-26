# BuyASAP

BuyASAP is a Flask and SQLite electronics storefront featuring 10 products across 8 categories: Tablets, Phones, Earphones, Laptops, Speakers, Kitchen Electronics, TVs, and Smart Watches.

## Prerequisites
- Python 3.10+
- Git

## Setup & Run
```powershell
# Clone the project and open the folder
git clone <https://github.com/apoorva-ds/electronic_shopping_application.git> electronic_shopping_app
cd electronic_shopping_app

# Create and activate a local virtual environment so dependencies stay isolated
python -m venv .venv
.\.venv\Scripts\Activate.ps1 # just ignore the text and return to the terminal

# Install Flask and pytest
pip install -r requirements.txt

# Start the app (creates ecommerce.db and seeds the catalog automatically on first run)
python server.py
```

Once the server is running, open **http://127.0.0.1:5000** in your browser.

### Default Login
Use this built-in customer account to explore the storefront right away:

| Role | Email | Password |
|------|-------|----------|
| Customer | `apoorva@gmail.com` | `pass123` |

## Email OTP Verification
New registrations and email changes require a 6-digit OTP before the change is saved.

- **Local development (default):** If Gmail credentials aren't set, the app falls back to displaying the OTP on screen and logging it in the terminal so you can still test the flow.
- **Real email delivery:** Use a Gmail account with 2-Step Verification enabled and generate an App Password, then set these environment variables in the same PowerShell session before running the server:

```powershell
# Sender Gmail address and 16-character App Password (not your regular Gmail password)
$env:BUYASAP_GMAIL_ADDRESS = "your-sender@gmail.com"
$env:BUYASAP_GMAIL_APP_PASSWORD = "your-16-character-app-password"

# Restart the server so it picks up the environment variables
python server.py
```

OTPs expire after 10 minutes and allow up to 5 wrong attempts before requiring a new code.

## Project Structure
```
├─ server.py               # Flask app, routes, cart/checkout flow, and OTP handling
├─ database.py             # SQLite schema setup and initial catalog/user seeding
├─ requirements.txt        # Python dependencies (Flask, pytest)
├─ test_storefront.py      # End-to-end Flask test suite
├─ ecommerce.db            # SQLite database file (auto-created on first run)
└─ templates/              # Jinja2 HTML templates for storefront pages
```

## Running Tests
```powershell
# Runs all storefront, OTP, account, and support chat tests against a temp DB
pytest
```

## Refreshing the Catalog
If you edit product listings in `database.py`, run this to sync the catalog without wiping existing customer orders:
```powershell
python -c "import database; database.init_db()"
```
