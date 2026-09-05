import sqlite3
import time
import os
import functools
import urllib.parse
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import qrcode
import qrcode.image.svg

from flask import Flask, send_from_directory, jsonify
from pywebio.platform.flask import webio_view
from pywebio.session import local as session_local
from pywebio.input import input, input_group, select, file_upload, NUMBER, TEXT, actions
from pywebio.output import (
    clear, put_html, put_table, put_buttons, toast, download
)
from reportlab.lib.pagesizes import A5
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from io import BytesIO

# --- Environment & Configuration ---

PORT = int(os.environ.get("PORT", 8080))
STORE_BRAND = "Luxury Impact Parfum RZ"
STORE_PHONE = "0542932846"
STORE_EMAIL = "contact@luxuryimpactparfum.com"
STORE_WEBSITE = "https://www.luxuryimpactparfum.com"

DATA_DIR = Path(os.environ.get("RENDER_DISK_PATH", "."))
DB_NAME = DATA_DIR / "shop_db.sqlite"
UPLOAD_DIR = DATA_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CURRENCIES = {
    "DA (د.ج)": {"rate": 220.0, "symbol": "DA"},
    "EUR (€)": {"rate": 1.0, "symbol": "€"},
    "USD ($)": {"rate": 1.08, "symbol": "$"}
}

# --- Database Initialization ---

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT DEFAULT 'user'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                image TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                image TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

init_db()

# --- Utility Functions ---

def get_ingredient_search_url(product_name):
    """Generates the Google Search URL for perfume ingredients."""
    search_query = f"{product_name} perfume ingredients notes"
    encoded_query = urllib.parse.quote_plus(search_query)
    return f"https://www.google.com/search?q={encoded_query}"

def generate_product_qr_svg(product_name):
    """Generates an SVG QR code encoding brand, perfume, phone, email, and ingredient search URL."""
    search_url = get_ingredient_search_url(product_name)
    
    qr_payload = (
        f"Brand: {STORE_BRAND}\n"
        f"Perfume: {product_name}\n"
        f"Phone: {STORE_PHONE}\n"
        f"Admin Email: {STORE_EMAIL}\n"
        f"Ingredients Link: {search_url}"
    )
    
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=1)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    stream = BytesIO()
    img.save(stream)
    return stream.getvalue().decode('utf-8')

def save_uploaded_file(file_data):
    if not file_data or 'content' not in file_data:
        return ""
    
    original_name = file_data.get('filename', 'image.jpg')
    ext = Path(original_name).suffix or '.jpg'
    filename = secure_filename(f"{int(time.time())}_{os.urandom(4).hex()}{ext}")
    file_path = UPLOAD_DIR / filename
    
    with open(file_path, "wb") as f:
        f.write(file_data['content'])
        
    return f"/static/uploads/{filename}"

def get_image_source(img_path):
    if img_path and str(img_path).startswith("/static/uploads/"):
        filename = os.path.basename(img_path)
        if (UPLOAD_DIR / filename).exists():
            return img_path
    if img_path and str(img_path).startswith("data:image"):
        return img_path
    return "https://via.placeholder.com/150?text=No+Image"

def admin_exists():
    """Checks if an admin user already exists in the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
        return cursor.fetchone() is not None

# --- Session Helpers & Security Decorators ---

def get_current_user():
    return getattr(session_local, 'user', None)

def set_current_user(user_dict):
    session_local.user = user_dict

def get_selected_currency():
    return getattr(session_local, 'currency', "DA (د.ج)")

def set_selected_currency(currency_code):
    session_local.currency = currency_code

def require_auth(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            toast("عذراً! يجب تسجيل الدخول بالاسم وكلمة المرور الصحيحة أولاً.", color="warning")
            login_page()
            return
        return func(*args, **kwargs)
    return wrapper

def require_admin(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or user.get('role') != 'admin':
            toast("غير مصرح لك بالوصول إلى هذه الصفحة. صلاحيات مدير النظام مطلوبة.", color="error")
            main_menu()
            return
        return func(*args, **kwargs)
    return wrapper

# --- Conversion & UI Helpers ---

def convert_price(amount, from_curr, to_curr):
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    eur_amount = amount / CURRENCIES[from_curr]["rate"]
    return eur_amount * CURRENCIES[to_curr]["rate"]

def render_header(subtitle=""):
    put_html(f"""
        <style>
            .ingredient-link {{
                color: #3182ce;
                font-weight: bold;
                text-decoration: underline;
                transition: color 0.2s ease-in-out;
            }}
            .ingredient-link:hover {{ color: #2b6cb0; }}
            .ingredient-link:active {{ color: #e53e3e; }}
            .ingredient-link:visited {{ color: #805ad5; }}
        </style>
        <div style="background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%); color: white; padding: 20px; border-radius: 12px; margin-bottom: 25px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <h1 style="margin: 0; font-size: 28px; font-weight: 900; letter-spacing: 1px;">✨ {STORE_BRAND} ✨</h1>
            <p style="margin: 5px 0 0 0; font-size: 14px; opacity: 0.8;">{subtitle}</p>
        </div>
    """)

def render_footer():
    """Renders the website link footer strictly once at the absolute bottom."""
    put_html(f"""
        <div style="margin-top: 40px; padding: 15px; text-align: center; border-top: 1px solid #e2e8f0; font-size: 14px; color: #4a5568;">
            🌐 زيارة موقعنا الرسمي: <a href="{STORE_WEBSITE}" target="_blank" style="color: #3182ce; font-weight: bold; text-decoration: none;">{STORE_WEBSITE}</a>
        </div>
    """)

# --- Main Flow & Views ---

def main_menu():
    clear()
    render_header("المتجر الإلكتروني الرسمي للعطور الفاخرة")

    current_user = get_current_user()
    current_currency = get_selected_currency()

    if current_user:
        put_html(f"<div style='text-align: center; margin-bottom: 15px;'><b>مرحباً بك:</b> {current_user['name']} ({current_user['role']}) | <b>العملة الحالية:</b> {current_currency}</div>")

    options = [{'label': '🛍️ تصفح المتجر', 'value': 'shop', 'color': 'primary'}]

    if current_user:
        options.append({'label': '🛒 سلة التسوق', 'value': 'cart', 'color': 'success'})
        if current_user['role'] == 'admin':
            options.append({'label': '⚙️ لوحة التحكم', 'value': 'admin', 'color': 'warning'})
        options.append({'label': '🚪 تسجيل الخروج', 'value': 'logout', 'color': 'danger'})
    else:
        options.append({'label': '🔑 تسجيل الدخول', 'value': 'login', 'color': 'info'})
        options.append({'label': '📝 إنشاء حساب جديد', 'value': 'register', 'color': 'secondary'})

    choice = actions("القائمة الرئيسية:", options)

    if choice == 'shop':
        user_shop()
    elif choice == 'cart':
        view_cart()
    elif choice == 'admin':
        admin_dashboard()
    elif choice == 'login':
        login_page()
    elif choice == 'register':
        register_page()
    elif choice == 'logout':
        set_current_user(None)
        toast("تم تسجيل الخروج وتأمين الجلسة بنجاح.", color="info")
        main_menu()

# --- Auth System ---

def register_page():
    clear()
    render_header("إنشاء حساب جديد")

    role_options = [("مستخدم عادي", "user")]
    if not admin_exists():
        role_options.append(("مدير النظام (حساب واحد فقط متاح)", "admin"))

    data = input_group("تسجيل حساب جديد", [
        input("الاسم الكامل", name="name", required=True),
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=TEXT, required=True),
        select("نوع الحساب", role_options, name="role")
    ])

    selected_role = data['role']
    if selected_role == 'admin' and admin_exists():
        toast("خطأ: يوجد مدير نظام مسجل بالفعل! تم تحويل حسابك إلى مستخدم عادي.", color="warning")
        selected_role = 'user'

    hashed_pw = generate_password_hash(data['password'].strip())

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (name, username, password, role) VALUES (?, ?, ?, ?)",
                (data['name'].strip(), data['username'].strip(), hashed_pw, selected_role)
            )
            conn.commit()
            toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
            login_page()
        except sqlite3.IntegrityError:
            toast("اسم المستخدم هذا مستخدم بالفعل. يرجى اختيار اسم آخر.", color="error")
            register_page()

def login_page():
    clear()
    render_header("تسجيل الدخول الآمن")

    data = input_group("أدخل بيانات الاعتماد", [
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=TEXT, required=True)
    ])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, username, password, role FROM users WHERE username = ?", (data['username'].strip(),))
        user = cursor.fetchone()

    if user and check_password_hash(user['password'], data['password'].strip()):
        user_dict = {'id': user['id'], 'name': user['name'], 'username': user['username'], 'role': user['role']}
        set_current_user(user_dict)
        
        currency_choice = select("اختر عملة التسوق المفضلة لهذا المعرض:", list(CURRENCIES.keys()), value="DA (د.ج)")
        set_selected_currency(currency_choice)
        
        toast(f"مرحباً بك {user['name']}!", color="success")
        main_menu()
    else:
        toast("خطأ: اسم المستخدم أو كلمة المرور غير صحيحة!", color="error")
        login_page()

# --- Protected Store & Cart Views ---

def user_shop():
    clear()
    render_header("تصفح قائمة العطور")

    selected_currency = get_selected_currency()
    curr_info = CURRENCIES[selected_currency]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products")
        products = cursor.fetchall()

    if not products:
        put_html("<div style='background: white; padding: 40px; border-radius: 12px; margin: 30px auto; text-align: center;'><h3>لا توجد عطور معروضة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "العطر والمعلومات", "رمز QR", f"السعر ({curr_info['symbol']})", "الإجراء"]]
        for prod in products:
            p_id, name, base_price, item_currency = prod['id'], prod['name'], prod['price'], prod['currency']
            img_path = prod['image']
            
            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)
            img_html = f'<img src="{img_src}" style="width: 80px; height: 80px; object-fit: cover; border-radius: 8px;">'

            qr_svg = generate_product_qr_svg(name)
            qr_html = f'<div style="width: 80px; height: 80px; margin: auto;">{qr_svg}</div>'

            search_url = get_ingredient_search_url(name)

            details_html = f"""
                <div style="text-align: right; line-height: 1.5;">
                    <b style="font-size: 15px;">{name}</b><br/>
                    <small style="color: #718096;"><b>الهاتف:</b> {STORE_PHONE}</small><br/>
                    <a href="{search_url}" target="_blank" class="ingredient-link">المكونات</a>
                </div>
            """

            table_data.append([
                put_html(img_html),
                put_html(details_html),
                put_html(qr_html),
                f"{disp_price:,.2f} {curr_info['symbol']}",
                put_buttons([{'label': '🛒 إضافة للسلة', 'value': p_id, 'color': 'success'}], onclick=lambda val: add_to_cart(val))
            ])
        
        put_table(table_data)

    render_footer()
    act = actions("", [{'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}])
    if act == 'home': main_menu()

@require_auth
def add_to_cart(product_id):
    current_user = get_current_user()

    qty_data = input_group("إضافة المنتج إلى السلة", [
        input("الكمية المطلوبة:", name="qty", type=NUMBER, value=1, min=1, required=True)
    ])
    qty = int(qty_data['qty'])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
        prod = cursor.fetchone()

        if prod:
            cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ?", (current_user['id'], product_id))
            cart_item = cursor.fetchone()

            if cart_item:
                cursor.execute("UPDATE cart SET quantity = quantity + ? WHERE id = ?", (qty, cart_item['id']))
            else:
                cursor.execute(
                    "INSERT INTO cart (user_id, product_id, name, price, quantity, image) VALUES (?, ?, ?, ?, ?, ?)",
                    (current_user['id'], product_id, prod['name'], prod['price'], qty, prod['image'])
                )
            conn.commit()
            toast(f"تمت إضافة ({qty}) قطعة من {prod['name']} إلى السلة بنجاح!", color="success")

@require_auth
def view_cart():
    clear()
    render_header("سلة التسوق الخاصة بك")

    current_user = get_current_user()
    selected_currency = get_selected_currency()
    curr_info = CURRENCIES[selected_currency]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name, c.price, c.quantity, c.image, p.currency 
            FROM cart c 
            LEFT JOIN products p ON c.product_id = p.id 
            WHERE c.user_id = ?
        """, (current_user['id'],))
        items = cursor.fetchall()

    if not items:
        put_html("<div style='background: white; padding: 40px; border-radius: 12px; margin: 30px auto; text-align: center;'><h3>🛒 سلة التسوق فارغة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "العطر", "السعر الفردي", "الكمية", "الإجمالي"]]
        grand_total = 0.0

        for item in items:
            item_curr = item['currency'] if item['currency'] else "EUR (€)"
            converted_unit_price = convert_price(item['price'], item_curr, selected_currency)
            total_item_price = converted_unit_price * item['quantity']
            grand_total += total_item_price

            img_html = f'<img src="{get_image_source(item["image"])}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'
            table_data.append([
                put_html(img_html),
                item['name'],
                f"{converted_unit_price:,.2f} {curr_info['symbol']}",
                str(item['quantity']),
                f"{total_item_price:,.2f} {curr_info['symbol']}"
            ])

        put_table(table_data)
        put_html(f"""
            <div style="background: #ffffff; padding: 20px; border-radius: 12px; margin: 20px auto; max-width: 400px; text-align: center;">
                <h3 style="margin: 0;">المبلغ الإجمالي: <span style="color: #38a169;">{grand_total:,.2f} {curr_info['symbol']}</span></h3>
            </div>
        """)

    render_footer()
    act = actions("الخيارات المتاحة:", [
        {'label': '📄 تحميل الفاتورة (PDF)', 'value': 'pdf', 'color': 'success'},
        {'label': '🗑️ تفريغ السلة', 'value': 'clear_cart', 'color': 'danger'},
        {'label': '🛍️ مواصلة التسوق', 'value': 'shop', 'color': 'primary'},
        {'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])

    if act == 'pdf': generate_pdf_invoice()
    elif act == 'clear_cart': empty_user_cart()
    elif act == 'shop': user_shop()
    elif act == 'home': main_menu()

@require_auth
def empty_user_cart():
    current_user = get_current_user()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM cart WHERE user_id = ?", (current_user['id'],))
        conn.commit()
    toast("تم تفريغ سلة التسوق بنجاح.", color="info")
    view_cart()

@require_auth
def generate_pdf_invoice():
    current_user = get_current_user()
    selected_currency = get_selected_currency()
    curr_info = CURRENCIES[selected_currency]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.name, c.price, c.quantity, p.currency 
            FROM cart c 
            LEFT JOIN products p ON c.product_id = p.id 
            WHERE c.user_id = ?
        """, (current_user['id'],))
        items = cursor.fetchall()

    if not items:
        toast("السلة فارغة، لا يمكن إنتاج فاتورة!", color="warning")
        return

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A5, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=16, alignment=1)
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=10, alignment=1)

    story.append(Paragraph(f"<b>{STORE_BRAND}</b>", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("OFFICIAL INVOICE / RECEIPT", normal_style))
    story.append(Spacer(1, 10))

    data = [["Item Description", "Price", "Qty", "Total"]]
    grand_total = 0.0

    for item in items:
        item_curr = item['currency'] if item['currency'] else "EUR (€)"
        price = convert_price(item['price'], item_curr, selected_currency)
        total = price * item['quantity']
        grand_total += total
        data.append([item['name'], f"{price:,.2f} {curr_info['symbol']}", str(item['quantity']), f"{total:,.2f} {curr_info['symbol']}"])

    data.append(["Grand Total", "", "", f"{grand_total:,.2f} {curr_info['symbol']}"])

    t = Table(data, colWidths=[140, 70, 30, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a202c")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e0")),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
    ]))

    story.append(t)
    doc.build(story)
    download("Invoice_Parfum_RZ.pdf", buffer.getvalue())
    toast("تم تحميل الفاتورة بنجاح!", color="success")

# --- Protected Admin Dashboard ---

@require_admin
def admin_dashboard():
    clear()
    render_header("لوحة التحكم وإدارة العطور")

    choice = actions("اختر العملية المطلوبة:", [
        {'label': '➕ إضافة عطر جديد', 'value': 'add', 'color': 'success'},
        {'label': '📋 عرض وتعديل قائمة العطور', 'value': 'list', 'color': 'primary'},
        {'label': '🔙 العودة للقائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])

    if choice == 'add': add_product_page()
    elif choice == 'list': list_products_page()
    elif choice == 'home': main_menu()

    render_footer()

@require_admin
def add_product_page():
    clear()
    render_header("إضافة عطر جديد إلى المتجر")

    data = input_group("إضافة عطر جديد", [
        input("اسم العطر", name="name", required=True),
        input("السعر", name="price", type=NUMBER, required=True),
        select("عملة السعر الإدخالي", list(CURRENCIES.keys()), name="currency", value="DA (د.ج)"),
        file_upload("صورة العطر", name="image", accept="image/*", required=True)
    ])

    image_path = save_uploaded_file(data['image'])

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO products (name, price, currency, image) VALUES (?, ?, ?, ?)",
            (data['name'].strip(), float(data['price']), data['currency'], image_path)
        )
        conn.commit()

    toast("تمت إضافة العطر بنجاح!", color="success")
    admin_dashboard()

@require_admin
def list_products_page():
    clear()
    render_header("إدارة وتعديل العطور المسجلة")

    selected_currency = get_selected_currency()
    curr_info = CURRENCIES[selected_currency]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products")
        products = cursor.fetchall()

    if not products:
        put_html("<div style='background: white; padding: 30px; margin: 20px auto; text-align: center;'><h3>لا توجد عطور متوفرة للتعديل.</h3></div>")
    else:
        table_data = [["المعرف", "الصورة", "اسم العطر", f"السعر ({curr_info['symbol']})", "الإجراءات"]]
        for prod in products:
            p_id, name, base_price, item_currency = prod['id'], prod['name'], prod['price'], prod['currency']
            img_path = prod['image']
            disp_price = convert_price(base_price, item_currency, selected_currency)

            img_html = f'<img src="{get_image_source(img_path)}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px;">'

            table_data.append([
                str(p_id),
                put_html(img_html),
                name,
                f"{disp_price:,.2f} {curr_info['symbol']}",
                put_buttons([
                    {'label': '✏️ تعديل', 'value': f'edit_{p_id}', 'color': 'warning'},
                    {'label': '🗑️ حذف', 'value': f'del_{p_id}', 'color': 'danger'}
                ], onclick=lambda btn: handle_action(btn))
            ])

        put_table(table_data)

    render_footer()
    act = actions("", [{'label': '🔙 العودة للوحة التحكم', 'value': 'admin', 'color': 'secondary'}])
    if act == 'admin': admin_dashboard()

def handle_action(action_value):
    action, p_id = action_value.split('_')
    p_id = int(p_id)
    
    if action == 'del':
        with get_db_connection() as conn:
            conn.execute("DELETE FROM products WHERE id = ?", (p_id,))
            conn.commit()
        toast("تم حذف العطر بنجاح.", color="info")
        list_products_page()
    elif action == 'edit':
        edit_product_page(p_id)

@require_admin
def edit_product_page(product_id):
    clear()
    render_header("تعديل بيانات العطر")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

    if not product:
        toast("العطر غير موجود!", color="error")
        list_products_page()
        return

    data = input_group("تعديل العطر", [
        input("اسم العطر", name="name", value=product['name'], required=True),
        input("السعر", name="price", type=NUMBER, value=float(product['price']), required=True),
        select("عملة السعر المسجلة", list(CURRENCIES.keys()), name="currency", value=product['currency']),
        file_upload("تحديث صورة العطر (اختياري)", name="image", accept="image/*")
    ])

    image_path = product['image']
    if data['image'] and data['image'].get('content'):
        image_path = save_uploaded_file(data['image'])

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE products SET name = ?, price = ?, currency = ?, image = ? WHERE id = ?",
            (data['name'].strip(), float(data['price']), data['currency'], image_path, product_id)
        )
        conn.commit()

    toast("تم تحديث بيانات العطر بنجاح!", color="success")
    list_products_page()

# --- WSGI App Definition & Health Route ---

app = Flask(__name__)
app.add_url_rule('/', 'webio_view', webio_view(main_menu), methods=['GET', 'POST', 'OPTIONS'])

@app.route('/healthz', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "timestamp": time.time()}), 200

@app.route('/static/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
