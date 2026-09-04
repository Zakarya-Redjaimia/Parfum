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
from flask import Flask

from reportlab.lib.pagesizes import A5
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from pywebio.platform.flask import webio_view
from pywebio import start_server
from pywebio.input import input, PASSWORD, file_upload, input_group, actions, NUMBER, select
from pywebio.output import put_html, put_table, put_buttons, clear, toast, download

# Deployment & Storage Configuration
DATA_DIR = os.environ.get("RENDER_DISK_PATH", os.path.join(os.getcwd(), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.abspath(os.path.join(DATA_DIR, "shop_db.sqlite"))
UPLOAD_DIR = os.path.abspath(os.path.join(DATA_DIR, "uploads"))
PORT = int(os.environ.get("PORT", 8080))

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

def get_db_connection():
    """Returns a thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=15)
    return conn

def init_db():
    """Initializes SQLite tables and applies necessary schema migrations."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = get_db_connection()
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
    """Saves uploaded image file to disk and constructs base64 string."""
    if not file_data or not file_data.get('content'):
        return "https://via.placeholder.com/260x180?text=No+Image"
    
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"img_{int(time.time())}_{file_data['filename']}"
    save_path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    
    try:
        with open(save_path, "wb") as f:
            f.write(file_data['content'])
    except Exception as e:
        print(f"File write error: {e}")
        
    encoded = base64.b64encode(file_data['content']).decode('utf-8')
    mime_type = file_data.get('mime_type', 'image/jpeg')
    return f"data:{mime_type};base64,{encoded}"

def get_image_source(img_str):
    """Normalizes image source strings for PyWebIO display."""
    if not img_str:
        return "https://via.placeholder.com/260x180?text=No+Image"
    if img_str.startswith("data:image/") or img_str.startswith("http"):
        return img_str
    
    abs_path = os.path.abspath(img_str)
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                ext = os.path.splitext(abs_path)[1].replace('.', '')
                return f"data:image/{ext};base64,{encoded_string}"
        except Exception as e:
            print(f"Error reading image path: {e}")
            
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
    
    if not data or not data.get('email'):
        main_menu()
        return

    hashed_pwd = md5_hash(data['password'])

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", (data['email'],))
        if cursor.fetchone():
            toast("البريد الإلكتروني مسجل بالفعل!", color="error")
            conn.close()
            register_page()
            return
            
        cursor.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                       (data['name'], data['email'], hashed_pwd))
        conn.commit()
        conn.close()
        
        toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
        login_page()
    except Exception as e:
        toast(f"حدث خطأ أثناء إنشاء الحساب: {str(e)}", color="error")
        main_menu()

def login_page():
    clear()
    render_header("تسجيل الدخول إلى حسابك")
    
    data = input_group("تسجيل الدخول", [
        input("البريد الإلكتروني", name="email", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    if not data or not data.get('email'):
        main_menu()
        return

    hashed_pwd = md5_hash(data['password'])
    conn = get_db_connection()
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
    
    conn = get_db_connection()
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
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    
    if product:
        p_id, name, price_val, prod_currency, image = product
        base_usd_price = convert_price(price_val, prod_currency, "USD ($)")
        
        cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND name = ?", (current_user['id'], name))
        existing_item = cursor.fetchone()
        
        if existing_item:
            new_qty = existing_item[1] + quantity
            cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (new_qty, existing_item[0]))
        else:
            cursor.execute("INSERT INTO cart (user_id, name, price, image, quantity) VALUES (?, ?, ?, ?, ?)",
                           (current_user['id'], name, base_usd_price, image, quantity))
                           
        conn.commit()
        toast(f"تم إضافة {quantity} من '{name}' إلى السلة بنجاح!", color="success")
    
    conn.close()
    view_cart()

def view_cart():
    clear()
    render_header("سلة التسوق الخاصة بك")
    
    if not current_user:
        toast("يرجى تسجيل الدخول لعرض سلة التسوق.", color="warning")
        login_page()
        return

    conn = get_db_connection()
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
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (current_user['id'],))
        conn.commit()
        conn.close()
        toast("تم تفريغ سلة التسوق بنجاح.", color="info")
    view_cart()

def generate_pdf_invoice():
    if not current_user:
        return
        
    conn = get_db_connection()
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
    
    if not data or not data.get('name'):
        admin_dashboard()
        return

    image_str = process_and_save_image(data['image'])
        
    conn = get_db_connection()
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
    
    conn = get_db_connection()
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
        conn = get_db_connection()
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
    
    conn = get_db_connection()
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
    
    if not data:
        list_products_page()
        return

    image_str = p_image
    if data['image'] and data['image'].get('content'):
        image_str = process_and_save_image(data['image'])

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET name = ?, price = ?, currency = ?, image = ? WHERE id = ?",
                   (data['name'], float(data['price']), data['currency'], image_str, product_id))
    conn.commit()
    conn.close()
    
    toast("تم تحديث بيانات العطر بنجاح!", color="success")
    list_products_page()

# --- Flask Server Mounting for Render Deployment ---

app = Flask(__name__)

with app.app_context():
    init_db()

app.add_url_rule('/', endpoint='webio_view', view_func=webio_view(main_menu), methods=['GET', 'POST', 'OPTIONS'])

# Gunicorn Attribute Alias
flask_app = app

def open_browser():
    """Opens default web browser for local execution."""
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=True)
