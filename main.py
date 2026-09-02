import hashlib
import sqlite3
import os
import base64
import threading
import time
import webbrowser
from io import BytesIO

# Third-party libraries
import qrcode

from reportlab.lib.pagesizes import A5
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from flask import Flask
from pywebio.platform.flask import wsgi_app
from pywebio import start_server
from pywebio.input import input, PASSWORD, file_upload, input_group, actions, NUMBER, select
from pywebio.output import put_html, put_table, put_buttons, clear, toast, download

# Global Configuration
DB_NAME = "shop_db.sqlite"
PORT = 8080
UPLOAD_DIR = "uploads"

# Store Details
STORE_BRAND = "Luxury Impact Parfum RZ"
STORE_PHONE = "0542932846"
STORE_EMAIL = "siokop04@gmail.com"
PARFUM_INGREDIENTS = "Alcohol Denat, Parfum (Fragrance), Aqua, Limonene, Linalool, Citronellol, Coumarin, Citral, Geraniol."

# Currency Conversion Rates (Base USD)
CURRENCIES = {
    "DZD (DA)": {"symbol": "DA", "rate": 134.5},
    "EUR (€)": {"symbol": "€", "rate": 0.92},
    "USD ($)": {"symbol": "$", "rate": 1.0}
}

# Runtime Session State
selected_currency = "DZD (DA)"
current_user = None

def init_db():
    """Initializes SQLite tables and applies necessary schema migrations."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE,
                        password TEXT NOT NULL)''')
    
    # Products Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS products (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        price REAL NOT NULL,
                        currency TEXT NOT NULL DEFAULT 'EUR (€)',
                        image TEXT NOT NULL)''')
                        
    # Schema Migration Check for currency column in products
    cursor.execute("PRAGMA table_info(products)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'currency' not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN currency TEXT NOT NULL DEFAULT 'EUR (€)'")

    # Shopping Cart Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS cart (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        price REAL NOT NULL,
                        image TEXT NOT NULL,
                        quantity INTEGER NOT NULL)''')
                        
    conn.commit()
    conn.close()

init_db()

# --- Helper Utilities ---

def md5_hash(text):
    """Returns MD5 hash for passwords."""
    return hashlib.md5(text.encode()).hexdigest()

def process_and_save_image(file_data):
    """Saves uploaded image file to disk and returns the local file path to keep DB lightweight."""
    if not file_data or not file_data.get('content'):
        return "https://via.placeholder.com/260x180?text=No+Image"
    
    filename = f"img_{int(time.time())}_{file_data['filename']}"
    save_path = os.path.join(UPLOAD_DIR, filename)
    
    with open(save_path, "wb") as f:
        f.write(file_data['content'])
        
    return save_path

def get_image_source(img_str):
    """Normalizes image source strings for PyWebIO display."""
    if not img_str:
        return "https://via.placeholder.com/260x180?text=No+Image"
    if img_str.startswith("data:image/") or img_str.startswith("http"):
        return img_str
    if os.path.exists(img_str):
        with open(img_str, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            ext = os.path.splitext(img_str)[1].replace('.', '')
            return f"data:image/{ext};base64,{encoded_string}"
    return "https://via.placeholder.com/260x180?text=No+Image"

def convert_price(base_price, from_curr, to_curr):
    """Converts price values across supported currencies."""
    from_rate = CURRENCIES.get(from_curr, CURRENCIES["EUR (€)"])["rate"]
    to_rate = CURRENCIES.get(to_curr, CURRENCIES["DZD (DA)"])["rate"]
    price_in_usd = float(base_price) / from_rate
    return price_in_usd * to_rate

def obfuscate_contact_info(phone, email):
    encoded_phone = base64.b64encode(phone.encode()).decode()
    encoded_email = base64.b64encode(email.encode()).decode()
    return f"SECURE-CONTACT-KEY:[P:{encoded_phone}|E:{encoded_email}]"

def generate_product_qr_base64(product_name, price, item_currency):
    """Generates product QR code as a base64-encoded PNG image."""
    hidden_contact = obfuscate_contact_info(STORE_PHONE, STORE_EMAIL)
    curr_info = CURRENCIES.get(item_currency, CURRENCIES["DZD (DA)"])
    
    qr_data = (
        f"Brand: {STORE_BRAND}\n"
        f"Product: {product_name}\n"
        f"Price: {price:.2f} {curr_info['symbol']}\n"
        f"Contact_Token: {hidden_contact}\n"
        f"Ingredients: {PARFUM_INGREDIENTS}"
    )
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="#1a202c", back_color="#ffffff")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{img_str}"

# --- UI Styling & Layouts ---

def inject_global_centered_styles():
    """Injects RTL, Tajawal font, and centered flex layouts."""
    put_html("""
        <head>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@800;900&display=swap" rel="stylesheet">
            <style>
                * {
                    font-family: 'Tajawal', sans-serif !important;
                    font-weight: 900 !important;
                    box-sizing: border-box;
                }
                body, .container, .pywebio-wrapper, .pywebio {
                    text-align: center !important;
                    margin: 0 auto !important;
                    max-width: 1100px;
                    background-color: #f8fafc;
                    font-weight: 900 !important;
                }
                h1, h2, h3, h4, h5, h6, p, span, label, input, button, table, td, th {
                    font-weight: 900 !important;
                    -webkit-font-smoothing: antialiased;
                }
                .pywebio-wrapper {
                    display: flex !important;
                    flex-direction: column !important;
                    align-items: center !important;
                    justify-content: center !important;
                    clear: both !important;
                }
                .pywebio-actions, .btn-group, div.btn-group {
                    justify-content: center !important;
                    display: flex !important;
                    flex-wrap: wrap !important;
                    gap: 12px !important;
                    margin: 20px auto !important;
                    padding: 5px !important;
                }
                .btn, button, .pywebio-actions button {
                    border-radius: 8px !important;
                    font-weight: 900 !important;
                    padding: 14px 28px !important;
                    margin: 4px !important;
                    font-size: 16px !important;
                    letter-spacing: 0.5px;
                    transition: all 0.2s ease-in-out !important;
                }
                .btn:hover, button:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
                }
                table {
                    margin: 25px auto !important;
                    width: 100% !important;
                    max-width: 950px !important;
                    background: white;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.08);
                    border-collapse: collapse !important;
                    position: relative !important;
                    z-index: 1 !important;
                }
                th {
                    background-color: #1a202c !important;
                    color: white !important;
                    padding: 18px !important;
                    text-align: center !important;
                    font-size: 17px !important;
                    font-weight: 900 !important;
                }
                td {
                    padding: 16px !important;
                    vertical-align: middle !important;
                    text-align: center !important;
                    font-weight: 900 !important;
                    font-size: 16px !important;
                }
                form, .form-group, .input-group, .ws-form {
                    max-width: 480px !important;
                    margin: 20px auto !important;
                    padding: 24px;
                    background: #ffffff;
                    border-radius: 12px;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
                    text-align: center !important;
                }
                input, select, textarea {
                    text-align: center !important;
                    border-radius: 6px !important;
                    border: 3px solid #cbd5e0 !important;
                    padding: 12px !important;
                    width: 100% !important;
                    font-weight: 900 !important;
                    font-size: 16px !important;
                }
                label {
                    font-weight: 900 !important;
                    font-size: 16px !important;
                    margin-bottom: 8px !important;
                    display: block !important;
                }
            </style>
        </head>
    """)

def render_header(subtitle_text=None):
    inject_global_centered_styles()
    user_badge = ""
    if current_user:
        user_badge = f"""
            <div style="background: #edf2f7; color: #1a202c; padding: 10px 20px; border-radius: 20px; font-size: 16px; font-weight: 900;">
                👤 {current_user['name']}
            </div>
        """
    
    put_html(f"""
        <div style="background: #ffffff; padding: 20px 30px; border-radius: 16px; box-shadow: 0 4px 15px rgba(0,0,0,0.08); margin: 20px auto; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; direction: rtl; max-width: 1000px;">
            <div style="text-align: right;">
                <h1 style="margin: 0; color: #1a202c; font-size: 30px; font-weight: 900;">👑 {STORE_BRAND}</h1>
                <p style="margin: 4px 0 0 0; color: #4a5568; font-size: 16px; font-weight: 900;">{subtitle_text or 'عطور فاخرة أصيلة وتجربة تسوق راقية'}</p>
            </div>
            {user_badge}
        </div>
    """)

def render_footer():
    put_html(f"""
        <footer style="
            margin-top: 60px;
            background: #1a202c;
            color: #edf2f7;
            padding: 40px 20px 20px 20px;
            border-radius: 20px 20px 0 0;
            text-align: center;
            direction: rtl;
            position: relative;
            z-index: 2;
            font-weight: 900;
        ">
            <div style="max-width: 1000px; margin: 0 auto; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 20px;">
                <div style="text-align: center; flex: 1; min-width: 250px;">
                    <h3 style="margin: 0 0 10px 0; color: #d69e2e; font-size: 24px; font-weight: 900;">✨ {STORE_BRAND}</h3>
                    <p style="margin: 0; color: #cbd5e0; font-size: 15px; font-weight: 900;">👑 تجربة العطور الفاخرة بجودة عالية ولمسة ملكية.</p>
                </div>
                <div style="text-align: center; flex: 1; min-width: 250px;">
                    <h4 style="margin: 0 0 10px 0; color: #ffffff; font-size: 18px; font-weight: 900;">📞 تواصل معنا مباشرة</h4>
                    <p style="margin: 2px 0; font-size: 15px; color: #cbd5e0; font-weight: 900;">📱 الهاتف: <a href="tel:{STORE_PHONE}" style="color: #63b3ed; text-decoration: none; font-weight: 900;">{STORE_PHONE}</a></p>
                    <p style="margin: 2px 0; font-size: 15px; color: #cbd5e0; font-weight: 900;">✉️ البريد: <a href="mailto:{STORE_EMAIL}" style="color: #63b3ed; text-decoration: none; font-weight: 900;">{STORE_EMAIL}</a></p>
                </div>
                <div style="text-align: center; flex: 1; min-width: 250px;">
                    <h4 style="margin: 0 0 10px 0; color: #ffffff; font-size: 18px; font-weight: 900;">🌐 تابعنا</h4>
                    <div style="display: flex; justify-content: center; gap: 10px;">
                        <a href="https://facebook.com" target="_blank" style="background: #3b5998; color: white; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-size: 14px; font-weight: 900;">Facebook</a>
                        <a href="https://instagram.com" target="_blank" style="background: #e1306c; color: white; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-size: 14px; font-weight: 900;">Instagram</a>
                        <a href="https://tiktok.com" target="_blank" style="background: #000000; color: white; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-size: 14px; font-weight: 900; border: 1px solid #4a5568;">TikTok</a>
                    </div>
                </div>
            </div>
            <hr style="border: 0; border-top: 1px solid #2d3748; margin: 25px 0 15px 0;"/>
            <p style="margin: 0; font-size: 14px; color: #a0aec0; font-weight: 900;">&copy; 2026 {STORE_BRAND}. جميع الحقوق محفوظة.</p>
        </footer>
    """)

# --- Primary Application Views ---

def select_currency_page():
    global selected_currency
    clear()
    render_header("تحديد العملة المفضلة")
    
    choice = select("اختر العملة للعرض (DA, EUR, USD):", list(CURRENCIES.keys()), value=selected_currency)
    selected_currency = choice
    toast(f"تم تغيير العملة إلى: {selected_currency}", color="info")
    main_menu()

def main_menu():
    clear()
    render_header()
    
    put_html(f"""
        <div style="margin: 10px auto; background: #edf2f7; padding: 10px 24px; border-radius: 20px; display: inline-block; font-weight: 900; font-size: 18px;">
            💱 العملة الحالية: <b>{selected_currency}</b>
        </div>
    """)
    
    if current_user:
        choice = actions("اختر الإجراء المطلوب من القائمة التالية:", [
            {'label': '🛍️ تصفح المتجر', 'value': 'shop', 'color': 'primary'},
            {'label': '🛒 عربة التسوق', 'value': 'cart', 'color': 'success'},
            {'label': '💱 تغيير العملة', 'value': 'currency', 'color': 'warning'},
            {'label': '⚙️ لوحة التحكم', 'value': 'admin', 'color': 'secondary'},
            {'label': '🚪 تسجيل الخروج', 'value': 'logout', 'color': 'danger'}
        ])
        if choice == 'shop': user_shop(); return
        elif choice == 'cart': view_cart(); return
        elif choice == 'currency': select_currency_page(); return
        elif choice == 'admin': admin_dashboard(); return
        elif choice == 'logout': logout_user(); return
    else:
        choice = actions("مرحباً بك! اختر كيفية المتابعة:", [
            {'label': '🔑 تسجيل الدخول', 'value': 'login', 'color': 'primary'},
            {'label': '📝 إنشاء حساب جديد', 'value': 'register', 'color': 'success'},
            {'label': '🛍️ تصفح المتجر', 'value': 'shop', 'color': 'info'},
            {'label': '💱 تغيير العملة', 'value': 'currency', 'color': 'warning'}
        ])
        if choice == 'login': login_page(); return
        elif choice == 'register': register_page(); return
        elif choice == 'shop': user_shop(); return
        elif choice == 'currency': select_currency_page(); return
        
    render_footer()

def register_page():
    clear()
    render_header("إنشاء حساب جديد للاستفادة من كامل المزايا")
    
    data = input_group("تسجيل حساب جديد", [
        input("اسم المستخدم الكامل", name="name", required=True),
        input("البريد الإلكتروني", name="email", type="email", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (data['email'],))
    if cursor.fetchone():
        toast("البريد الإلكتروني مسجل بالفعل!", color="error")
        conn.close()
        register_page()
        return
        
    hashed_pwd = md5_hash(data['password'])
    cursor.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                   (data['name'], data['email'], hashed_pwd))
    conn.commit()
    conn.close()
    
    toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
    login_page()

def login_page():
    clear()
    render_header("تسجيل الدخول إلى حسابك")
    
    data = input_group("تسجيل الدخول", [
        input("البريد الإلكتروني", name="email", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    hashed_pwd = md5_hash(data['password'])
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email FROM users WHERE email = ? AND password = ?", (data['email'], hashed_pwd))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        global current_user
        current_user = {'id': user[0], 'name': user[1], 'email': user[2]}
        toast(f"مرحباً بعودتك، {user[1]}!", color="success")
        main_menu()
    else:
        toast("بيانات الدخول غير صحيحة، يرجى المحاولة مرة أخرى.", color="error")
        login_page()

def logout_user():
    global current_user
    current_user = None
    toast("تم تسجيل الخروج بنجاح.", color="info")
    main_menu()

def user_shop():
    clear()
    render_header("تصفح مجموعة العطور الملكية والمتميزة")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()
    
    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 40px; border-radius: 12px; margin: 20px 0; font-weight: 900;'><h3>لا توجد عطور متوفرة حالياً في المتجر.</h3></div>")
    else:
        cards_html = "<div style='display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 25px; margin: 30px auto; direction: rtl; max-width: 1000px;'>"
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            
            img_src = get_image_source(img_path)
            display_price = convert_price(base_price, item_currency, selected_currency)
            qr_base64 = generate_product_qr_base64(name, display_price, selected_currency)
            
            cards_html += f"""
                <div style="background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 6px 18px rgba(0,0,0,0.08); border: 2px solid #edf2f7; display: flex; flex-direction: column;">
                    <div style="width: 100%; height: 220px; background: #fafafa; display: flex; align-items: center; justify-content: center; overflow: hidden;">
                        <img src="{img_src}" style="width: 100%; height: 100%; object-fit: cover;" alt="{name}">
                    </div>
                    <div style="padding: 20px; text-align: center; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between;">
                        <div>
                            <h3 style="margin: 0 0 8px 0; color: #1a202c; font-size: 22px; font-weight: 900;">{name}</h3>
                            <p style="margin: 0 0 15px 0; color: #d69e2e; font-size: 24px; font-weight: 900;">{display_price:.2f} {curr_info['symbol']}</p>
                        </div>
                        <div style="background: #f7fafc; border: 2px dashed #cbd5e0; padding: 10px; border-radius: 8px; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-around;">
                            <img src="{qr_base64}" style="width: 65px; height: 65px;" alt="QR Code">
                            <span style="font-size: 13px; color: #4a5568; max-width: 140px; text-align: center; font-weight: 900;">امسح رمز QR للتحقق من أصل المنتج</span>
                        </div>
                    </div>
                </div>
            """
        cards_html += "</div>"
        put_html(cards_html)
        
        if current_user:
            put_html("<h3 style='margin-top: 30px; font-weight: 900; font-size: 22px;'>🛒 إضافة عطر إلى السلة</h3>")
            prod_options = []
            for p in products:
                calc_price = convert_price(p[2], p[3], selected_currency)
                prod_options.append({
                    "label": f"{p[1]} - ({calc_price:.2f} {curr_info['symbol']})", 
                    "value": p[0]
                })
            selected_prod_id = actions("اختر العطر للشراء:", prod_options)
            
            qty = input("أدخل الكمية المطلوبة", type=NUMBER, value=1)
            if qty and qty > 0:
                add_to_cart(selected_prod_id, qty)
                return
        else:
            put_html("""
                <div style='background: #ebf8ff; border-right: 6px solid #3182ce; padding: 18px; margin: 20px auto; border-radius: 6px; text-align: center; max-width: 600px; font-weight: 900; font-size: 16px;'>
                    💡 قم بتسجيل الدخول لتتمكن من إضافة العطور إلى سلة الشراء وإتمام الطلب.
                </div>
            """)

    act = actions("", [
        {'label': '🔙 العودة للقائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])
    if act == 'home':
        main_menu()
        return

    render_footer()

def add_to_cart(product_id, quantity):
    if not current_user:
        toast("يرجى تسجيل الدخول أولاً!", color="warning")
        login_page()
 import sqlite3
import os
import base64
import time
import threading
import webbrowser
from io import BytesIO

from flask import Flask
from pywebio import start_server, config
from pywebio.input import input, select, file_upload, NUMBER, PASSWORD, input_group
from pywebio.output import (
    put_html, put_table, put_buttons, clear, toast, download, 
    actions, put_text, put_row, put_column
)
from pywebio.platform.flask import wsgi_app

from reportlab.lib.pagesizes import A5
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# --- Application Configurations & Constants ---

DB_NAME = "parfum_rz.db"
STORE_BRAND = "Luxury Impact Parfum RZ"
PORT = 8080
IMAGE_DIR = "static/images"

os.makedirs(IMAGE_DIR, exist_ok=True)

CURRENCIES = {
    "USD ($)": {"symbol": "$", "rate": 1.0},
    "EUR (€)": {"symbol": "€", "rate": 0.92},
    "DZD (DA)": {"symbol": "DA", "rate": 134.50},
    "GBP (£)": {"symbol": "£", "rate": 0.79}
}

current_user = None  # Global session state: {"id": int, "name": str, "role": str}
selected_currency = "EUR (€)"

# --- Database & Helper Utilities ---

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Products table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            image TEXT NOT NULL
        )
    """)
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        )
    """)
    
    # Cart table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    
    # Seed default admin account if none exists
    cursor.execute("SELECT * FROM users WHERE role = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (name, username, password, role) VALUES (?, ?, ?, ?)",
                       ("Administrator", "admin", "admin123", "admin"))
    
    conn.commit()
    conn.close()

init_db()

def convert_price(amount, from_curr, to_curr):
    """Converts price values between supported currencies via USD base rate."""
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    usd_amount = amount / CURRENCIES[from_curr]['rate']
    return usd_amount * CURRENCIES[to_curr]['rate']

def process_and_save_image(file_obj):
    """Saves uploaded images locally or converts to base64 string."""
    if not file_obj or not file_obj.get('content'):
        return "placeholder.png"
    
    filename = f"{int(time.time())}_{file_obj['filename']}"
    filepath = os.path.join(IMAGE_DIR, filename)
    
    with open(filepath, 'wb') as f:
        f.write(file_obj['content'])
        
    return filepath

def get_image_source(img_path):
    """Returns valid image source path or fallback base64 string for HTML rendering."""
    if os.path.exists(img_path):
        with open(img_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode('utf-8')
            return f"data:image/jpeg;base64,{encoded}"
    # Default visual fallback SVG
    return "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='70' height='70' viewBox='0 0 70 70'><rect width='70' height='70' fill='%23edf2f7'/><text x='50%' y='50%' fill='%23a0aec0' dominant-baseline='middle' text-anchor='middle' font-family='sans-serif' font-size='10'>No Image</text></svg>"

# --- UI Header & Footer Layouts ---

def render_header(subtitle=""):
    user_status = f"👤 {current_user['name']} ({current_user['role'].upper()})" if current_user else "🔑 غير مسجل"
    
    put_html(f"""
        <div style="background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%); padding: 25px; border-radius: 12px; margin-bottom: 25px; text-align: center; color: white; box-shadow: 0 4px 15px rgba(0,0,0,0.15);">
            <h1 style="margin: 0; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 900; letter-spacing: 1px; color: #f7fafc; font-size: 32px;">{STORE_BRAND}</h1>
            <p style="margin: 5px 0 0 0; color: #cbd5e0; font-weight: 500; font-size: 16px;">{subtitle}</p>
            <div style="margin-top: 15px; font-size: 14px; background: rgba(255,255,255,0.1); display: inline-block; padding: 6px 16px; border-radius: 20px;">
                {user_status} | 🌐 العملة الحالية: <b>{selected_currency}</b>
            </div>
        </div>
    """)

def render_footer():
    put_html("""
        <hr style="border: 0; height: 1px; background: #e2e8f0; margin: 40px 0 20px 0;">
        <div style="text-align: center; color: #718096; font-size: 13px; font-weight: 600; padding-bottom: 20px;">
            &copy; Luxury Impact Parfum RZ. جميع الحقوق محفوظة.
        </div>
    """)

# --- Authentication & Main Views ---

def main_menu():
    clear()
    render_header("الصفحة الرئيسية والخدمات")
    
    put_html("<h2 style='text-align: center; color: #2d3748; font-weight: 900;'>مرحباً بكم في متجرنا الرقمي</h2>")
    
    opts = [
        {'label': '🛍️ تصفح العطور', 'value': 'shop', 'color': 'primary'},
        {'label': '🛒 عرض سلة التسوق', 'value': 'cart', 'color': 'info'},
        {'label': '💱 تغيير العملة', 'value': 'currency', 'color': 'warning'},
    ]
    
    if current_user and current_user['role'] == 'admin':
        opts.append({'label': '⚙️ لوحة التحكم (Admin)', 'value': 'admin', 'color': 'danger'})
        
    if current_user:
        opts.append({'label': '🚪 تسجيل الخروج', 'value': 'logout', 'color': 'secondary'})
    else:
        opts.append({'label': '🔑 تسجيل الدخول / إنشاء حساب', 'value': 'login', 'color': 'success'})

    choice = actions("اختر وجهتك:", opts)
    
    if choice == 'shop': user_shop()
    elif choice == 'cart': view_cart()
    elif choice == 'currency': change_currency_page()
    elif choice == 'admin': admin_dashboard()
    elif choice == 'login': login_page()
    elif choice == 'logout': 
        global current_user
        current_user = None
        toast("تم تسجيل الخروج بنجاح.", color="info")
        main_menu()

def login_page():
    clear()
    render_header("تسجيل الدخول أو حساب جديد")
    
    mode = actions("يرجى تحديد الخيار:", [
        {'label': 'تسجيل الدخول', 'value': 'login', 'color': 'primary'},
        {'label': 'إنشاء حساب جديد', 'value': 'register', 'color': 'success'},
        {'label': 'العودة', 'value': 'back', 'color': 'secondary'}
    ])
    
    if mode == 'back': main_menu(); return
    
    if mode == 'login':
        data = input_group("تسجيل الدخول", [
            input("اسم المستخدم", name="username", required=True),
            input("كلمة المرور", name="password", type=PASSWORD, required=True)
        ])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, role FROM users WHERE username = ? AND password = ?", 
                       (data['username'], data['password']))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            global current_user
            current_user = {'id': user[0], 'name': user[1], 'role': user[2]}
            toast(f"مرحباً بعودتك، {current_user['name']}!", color="success")
            main_menu()
        else:
            toast("بيانات الدخول غير صحيحة!", color="error")
            login_page()
            
    elif mode == 'register':
        data = input_group("إنشاء حساب جديد", [
            input("الاسم الكامل", name="name", required=True),
            input("اسم المستخدم", name="username", required=True),
            input("كلمة المرور", name="password", type=PASSWORD, required=True)
        ])
        
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (name, username, password, role) VALUES (?, ?, ?, 'user')",
                           (data['name'], data['username'], data['password']))
            conn.commit()
            conn.close()
            toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
            login_page()
        except sqlite3.IntegrityError:
            toast("اسم المستخدم مستخدم بالفعل. اختر اسماً آخر.", color="error")
            login_page()

def change_currency_page():
    global selected_currency
    clear()
    render_header("إعدادات العملة")
    
    curr = select("اختر العملة المفضلة لعرض الأسعار:", list(CURRENCIES.keys()), value=selected_currency)
    selected_currency = curr
    toast(f"تم تغيير العملة إلى {selected_currency}", color="success")
    main_menu()

# --- Storefront & Cart Management ---

def user_shop():
    clear()
    render_header("كتالوج العطور الفاخرة")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()
    
    curr_info = CURRENCIES[selected_currency]
    
    if not products:
        put_html("<div style='background: white; padding: 40px; border-radius: 12px; margin: 20px auto; text-align: center; max-width: 600px; font-weight: 900;'><h3>لا توجد عطور متوفرة حالياً في المتجر.</h3></div>")
    else:
        cards = []
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            disp_price = convert_price(base_price, item_currency, selected_currency)
            img_src = get_image_source(img_path)
            
            card_html = f"""
                <div style="background: white; border-radius: 12px; padding: 15px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); text-align: center; border: 1px solid #e2e8f0;">
                    <img src="{img_src}" style="width: 100%; height: 180px; object-fit: cover; border-radius: 8px;">
                    <h3 style="margin: 10px 0 5px 0; color: #1a202c; font-size: 18px; font-weight: 800;">{name}</h3>
                    <p style="color: #38a169; font-weight: 900; font-size: 16px; margin: 0 0 10px 0;">{disp_price:.2f} {curr_info['symbol']}</p>
                </div>
            """
            
            btn = put_buttons([{'label': '🛒 إضافة للسلة', 'value': p_id, 'color': 'success'}], 
                              onclick=lambda pid=p_id: add_to_cart_action(pid))
            cards.append(put_column([put_html(card_html), btn]))
            
        put_row(cards, size='280px 20px')
        
    act = actions("", [{'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}])
    if act == 'home': main_menu()

def add_to_cart_action(product_id):
    if not current_user:
        toast("يرجى تسجيل الدخول أولاً لإضافة العطور إلى سلتك.", color="warning")
        login_page()
        return
        
    qty = input("الكمية المطلوب إضافتها:", type=NUMBER, value=1)
    if not qty or qty < 1:
        toast("الكمية غير صحيحة!", color="error")
        return
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    
    if product:
        p_id, name, price_val, prod_currency, image = product
        base_usd_price = convert_price(price_val, prod_currency, "USD ($)")
        
        cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND name = ?", (current_user['id'], name))
        existing_item = cursor.fetchone()
        
        if existing_item:
            new_qty = existing_item[1] + qty
            cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (new_qty, existing_item[0]))
        else:
            cursor.execute("INSERT INTO cart (user_id, name, price, image, quantity) VALUES (?, ?, ?, ?, ?)",
                           (current_user['id'], name, base_usd_price, image, qty))
                           
        conn.commit()
        toast(f"تم إضافة {qty} من '{name}' إلى السلة بنجاح!", color="success")
    
    conn.close()
    view_cart()

def view_cart():
    clear()
    render_header("سلة التسوق الخاصة بك")
    
    if not current_user:
        toast("يرجى تسجيل الدخول لعرض سلة التسوق.", color="warning")
        login_page()
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, quantity, image FROM cart WHERE user_id = ?", (current_user['id'],))
    items = cursor.fetchall()
    conn.close()

    curr_info = CURRENCIES[selected_currency]

    if not items:
        put_html("""
            <div style="background: white; padding: 40px; border-radius: 12px; margin: 30px auto; text-align: center; max-width: 600px; font-weight: 900;">
                <h3>🛒 سلة التسوق فارغة حالياً.</h3>
            </div>
        """)
    else:
        table_data = [["الصورة", "العطر", "السعر الفردي", "الكمية", "الإجمالي"]]
        grand_total = 0.0

        for item in items:
            c_id, name, base_usd_price, quantity, img_path = item
            converted_price = convert_price(base_usd_price, "USD ($)", selected_currency)
            total = converted_price * quantity
            grand_total += total
            img_src = get_image_source(img_path)
            
            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'
            table_data.append([
                put_html(img_html),
                name,
                f"{converted_price:.2f} {curr_info['symbol']}",
                str(quantity),
                f"{total:.2f} {curr_info['symbol']}"
            ])

        put_table(table_data)
        
        put_html(f"""
            <div style="background: #ffffff; padding: 20px; border-radius: 12px; margin: 20px auto; max-width: 400px; box-shadow: 0 4px 10px rgba(0,0,0,0.08); text-align: center; font-weight: 900;">
                <h3 style="margin: 0; color: #1a202c; font-weight: 900; font-size: 22px;">المبلغ الإجمالي: <span style="color: #38a169;">{grand_total:.2f} {curr_info['symbol']}</span></h3>
            </div>
        """)

    act = actions("الخيارات المتاحة:", [
        {'label': '📄 تحميل الفاتورة (PDF)', 'value': 'pdf', 'color': 'success'},
        {'label': '🗑️ تفريغ السلة', 'value': 'clear_cart', 'color': 'danger'},
        {'label': '🛍️ مواصلة التسوق', 'value': 'shop', 'color': 'primary'},
        {'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])

    if act == 'pdf': generate_pdf_invoice(); return
    elif act == 'clear_cart': empty_user_cart(); return
    elif act == 'shop': user_shop(); return
    elif act == 'home': main_menu(); return

    render_footer()

def empty_user_cart():
    if current_user:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (current_user['id'],))
        conn.commit()
        conn.close()
        toast("تم تفريغ سلة التسوق بنجاح.", color="info")
    view_cart()

def generate_pdf_invoice():
    if not current_user:
        return
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, quantity FROM cart WHERE user_id = ?", (current_user['id'],))
    items = cursor.fetchall()
    conn.close()

    if not items:
        toast("السلة فارغة، لا يمكن إنتاج فاتورة!", color="warning")
        return

    curr_info = CURRENCIES[selected_currency]

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A5, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        alignment=1,
        textColor=colors.HexColor("#1a202c")
    )
    
    normal_style = ParagraphStyle(
        'NormalStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        alignment=1,
        textColor=colors.HexColor("#4a5568")
    )

    story.append(Paragraph(f"<b>{STORE_BRAND}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("OFFICIAL INVOICE / RECEIPT", normal_style))
    story.append(Spacer(1, 15))

    customer_info = f"Customer: {current_user['name']} | Currency: {selected_currency}"
    story.append(Paragraph(customer_info, normal_style))
    story.append(Spacer(1, 15))

    data = [["Item Description", "Price", "Qty", "Total"]]
    grand_total = 0.0

    for item in items:
        name, base_usd_price, qty = item
        price = convert_price(base_usd_price, "USD ($)", selected_currency)
        total = price * qty
        grand_total += total
        data.append([name, f"{price:.2f} {curr_info['symbol']}", str(qty), f"{total:.2f} {curr_info['symbol']}"])

    data.append(["Grand Total", "", "", f"{grand_total:.2f} {curr_info['symbol']}"])

    t = Table(data, colWidths=[140, 70, 30, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a202c")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('BACKGROUND', (0,1), (-1,-2), colors.HexColor("#f7fafc")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e0")),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#edf2f7")),
    ]))

    story.append(t)
    story.append(Spacer(1, 20))
    story.append(Paragraph("Thank you for choosing Luxury Impact Parfum RZ!", normal_style))

    doc.build(story)
    pdf_data = buffer.getvalue()
    buffer.close()

    download("Invoice_Parfum_RZ.pdf", pdf_data)
    toast("تم تحميل الفاتورة بنجاح!", color="success")

# --- Administration Views ---

def admin_dashboard():
    clear()
    render_header("لوحة التحكم وإدارة العطور")
    
    put_html("<h2 style='color: #1a202c; text-align: center; font-weight: 900; font-size: 24px;'>⚙️ لوحة إدارة المتجر</h2>")
    
    choice = actions("اختر العملية المطلوبة:", [
        {'label': '➕ إضافة عطر جديد', 'value': 'add', 'color': 'success'},
        {'label': '📋 عرض وتعديل قائمة العطور', 'value': 'list', 'color': 'primary'},
        {'label': '🔙 العودة للقائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])
    
    if choice == 'add': add_product_page(); return
    elif choice == 'list': list_products_page(); return
    elif choice == 'home': main_menu(); return

def add_product_page():
    clear()
    render_header("إضافة عطر جديد إلى المتجر")
    
    data = input_group("إضافة عطر جديد", [
        input("اسم العطر", name="name", required=True),
        input("السعر", name="price", type=NUMBER, required=True),
        select("عملة السعر الإدخالي", list(CURRENCIES.keys()), name="currency", value="EUR (€)"),
        file_upload("صورة العطر", name="image", accept="image/*", required=True)
    ])
    
    image_str = process_and_save_image(data['image'])
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (name, price, currency, image) VALUES (?, ?, ?, ?)",
                   (data['name'], float(data['price']), data['currency'], image_str))
    conn.commit()
    conn.close()
    
    toast("تمت إضافة العطر بنجاح!", color="success")
    admin_dashboard()

def list_products_page():
    clear()
    render_header("إدارة وتعديل العطور المسجلة")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()
    
    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; max-width: 600px; margin: 20px auto; font-weight: 900;'><h3>لا توجد عطور متوفرة للتعديل.</h3></div>")
    else:
        table_data = [["المعرف", "الصورة", "اسم العطر", f"السعر ({curr_info['symbol']})", "الإجراءات"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            
            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)
            
            img_html = f'<img src="{img_src}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px;">'
            
            table_data.append([
                str(p_id),
                put_html(img_html),
                name,
                f"{disp_price:.2f} {curr_info['symbol']}",
                put_buttons([
                    {'label': '✏️ تعديل', 'value': 'edit', 'color': 'warning'},
                    {'label': '🗑️ حذف', 'value': 'del', 'color': 'danger'}
                ], onclick=lambda btn, item_id=p_id: handle_product_action(btn, item_id))
            ])
            
        put_table(table_data)

    act = actions("", [
        {'label': '🔙 العودة للوحة التحكم', 'value': 'admin', 'color': 'secondary'}
    ])
    if act == 'admin':
        admin_dashboard()
        return

    render_footer()

def handle_product_action(action, p_id):
    if action == 'del':
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (p_id,))
        conn.commit()
        conn.close()
        toast("تم حذف العطر بنجاح.", color="info")
        list_products_page()
    elif action == 'edit':
        edit_product_page(p_id)

def edit_product_page(product_id):
    clear()
    render_header("تعديل بيانات العطر")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    
    if not product:
        toast("العطر غير موجود!", color="error")
        list_products_page()
        return

    _, p_name, p_price, p_currency, p_image = product

    data = input_group("تعديل العطر", [
        input("اسم العطر", name="name", value=p_name, required=True),
        input("السعر", name="price", type=NUMBER, value=float(p_price), required=True),
        select("عملة السعر المسجلة", list(CURRENCIES.keys()), name="currency", value=p_currency),
        file_upload("تحديث صورة العطر (اختياري)", name="image", accept="image/*")
    ])
    
    image_str = p_image
    if data['image'] and data['image'].get('content'):
        image_str = process_and_save_image(data['image'])

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET name = ?, price = ?, currency = ?, image = ? WHERE id = ?",
                   (data['name'], float(data['price']), data['currency'], image_str, product_id))
    conn.commit()
    conn.close()
    
    toast("تم تحديث بيانات العطر بنجاح!", color="success")
    list_products_page()

# --- Flask & WSGI Application Bindings ---

flask_app = Flask(__name__)
flask_app.add_url_rule('/', 'webio_view', wsgi_app(main_menu), methods=['GET', 'POST', 'OPTIONS'])

# WSGI callable for production web servers (e.g., Gunicorn/uWSGI)
app = wsgi_app(main_menu)

def open_browser():
    """Opens default web browser for local execution."""
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    threading.Thread(target=open_browser, daemon=True).start()
    start_server(main_menu, port=PORT, debug=True)
