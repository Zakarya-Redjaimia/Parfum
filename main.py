import sqlite3
import time
import os
import io
import functools
import base64
from io import BytesIO
import threading
import webbrowser

from flask import Flask
from pywebio.platform.flask import webio_view
from pywebio import start_server
from pywebio.session import local as session_local
from pywebio.input import input, input_group, select, file_upload, NUMBER, TEXT, actions
from pywebio.output import (
    clear, put_html, put_table, put_buttons, toast, download
)
from reportlab.lib.pagesizes import A5
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

# --- Configuration & Constants ---

DB_NAME = 'shop_db.sqlite'
PORT = 8080
STORE_BRAND = "Luxury Impact Parfum RZ"

CURRENCIES = {
    "DA (د.ج)": {"rate": 220.0, "symbol": "DA"},
    "EUR (€)": {"rate": 1.0, "symbol": "€"},
    "USD ($)": {"rate": 1.08, "symbol": "$"}
}

# --- Database Security Initialization ---

def get_db_connection():
    """Returns a thread-safe connection to the SQLite database."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Ensures database schema exists before handling any web sessions."""
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

# --- Session Helpers & Security Decorators ---

def get_current_user():
    """Retrieves session-isolated user dict."""
    return getattr(session_local, 'user', None)

def set_current_user(user_dict):
    """Sets session-isolated user dict."""
    session_local.user = user_dict

def get_selected_currency():
    """Retrieves session-isolated selected currency."""
    return getattr(session_local, 'currency', "DA (د.ج)")

def set_selected_currency(currency_code):
    """Sets session-isolated currency."""
    session_local.currency = currency_code

def require_auth(func):
    """Decorator ensuring that only authenticated users access the route."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            toast("عذراً! يجب تسجيل الدخول بالاسم وكلمة المرور الصحيحة أولاً.", color="warning")
            login_page()
            return
        return func(*args, **kwargs)
    return wrapper

def require_admin(func):
    """Decorator ensuring that only users with 'admin' role access the route."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or user.get('role') != 'admin':
            toast("غير مصرح لك بالوصول إلى هذه الصفحة. صلاحيات مدير النظام مطلوبة.", color="error")
            main_menu()
            return
        return func(*args, **kwargs)
    return wrapper

# --- Conversion Helpers ---

def convert_price(amount, from_curr, to_curr):
    """Calculates rates accurately from EUR base rate."""
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    eur_amount = amount / CURRENCIES[from_curr]["rate"]
    return eur_amount * CURRENCIES[to_curr]["rate"]

def process_and_save_image(file_data):
    if not file_data or 'content' not in file_data:
        return ""
    content = file_data['content']
    mime = file_data.get('mime_type', 'image/jpeg')
    b64_str = base64.b64encode(content).decode('utf-8')
    return f"data:{mime};base64,{b64_str}"

def get_image_source(img_path):
    if img_path and str(img_path).startswith("data:image"):
        return img_path
    return "https://via.placeholder.com/150?text=No+Image"

def render_header(subtitle=""):
    put_html(f"""
        <div style="background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%); color: white; padding: 20px; border-radius: 12px; margin-bottom: 25px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <h1 style="margin: 0; font-size: 28px; font-weight: 900; letter-spacing: 1px;">✨ {STORE_BRAND} ✨</h1>
            <p style="margin: 5px 0 0 0; font-size: 14px; opacity: 0.8;">{subtitle}</p>
        </div>
    """)

def render_footer():
    put_html("""
        <div style="margin-top: 40px; text-align: center; color: #718096; font-size: 12px; padding: 15px; border-top: 1px solid #e2e8f0;">
            &copy; Luxury Impact Parfum RZ. All rights reserved.
        </div>
    """)

# --- Main Flow & Views ---

def main_menu():
    clear()
    render_header("المتجر الإلكتروني الرسمي للعطور الفاخرة")

    current_user = get_current_user()
    current_currency = get_selected_currency()

    user_info_html = ""
    if current_user:
        user_info_html = f"<div style='text-align: center; margin-bottom: 15px;'><b>مرحباً بك:</b> {current_user['name']} ({current_user['role']}) | <b>العملة الحالية:</b> {current_currency}</div>"

    put_html(user_info_html)

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

    if choice == 'shop': user_shop()
    elif choice == 'cart': view_cart()
    elif choice == 'admin': admin_dashboard()
    elif choice == 'login': login_page()
    elif choice == 'register': register_page()
    elif choice == 'logout':
        set_current_user(None)
        toast("تم تسجيل الخروج وتأمين الجلسة بنجاح.", color="info")
        main_menu()

# --- Auth System ---

def register_page():
    clear()
    render_header("إنشاء حساب جديد")

    data = input_group("تسجيل حساب جديد", [
        input("الاسم الكامل", name="name", required=True),
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=TEXT, required=True),
        select("نوع الحساب", [("مستخدم عادي", "user"), ("مدير النظام", "admin")], name="role")
    ])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (name, username, password, role) VALUES (?, ?, ?, ?)",
                (data['name'].strip(), data['username'].strip(), data['password'].strip(), data['role'])
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
        cursor.execute(
            "SELECT id, name, username, role FROM users WHERE username = ? AND password = ?", 
            (data['username'].strip(), data['password'].strip())
        )
        user = cursor.fetchone()

    if user:
        user_dict = {'id': user['id'], 'name': user['name'], 'username': user['username'], 'role': user['role']}
        set_current_user(user_dict)
        
        # Immediate post-login currency selection
        currency_choice = select("اختر عملة التسوق المفضلة لهذا المعرض:", list(CURRENCIES.keys()), value="DA (د.ج)")
        set_selected_currency(currency_choice)
        
        toast(f"مرحباً بك {user['name']}! تم توثيق دخولك واختيار العملة: {currency_choice}", color="success")
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
        put_html("<div style='background: white; padding: 40px; border-radius: 12px; margin: 30px auto; text-align: center; max-width: 600px; font-weight: 900;'><h3>لا توجد عطور معروضة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "العطر", f"السعر الفردي ({curr_info['symbol']})", "الإجراء"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod['id'], prod['name'], prod['price'], prod['currency'], prod['image']
            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)
            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'

            table_data.append([
                put_html(img_html),
                name,
                f"{disp_price:,.2f} {curr_info['symbol']}",
                put_buttons([{'label': '🛒 إضافة للسلة', 'value': p_id, 'color': 'success'}],
                            onclick=lambda val: add_to_cart(val))
            ])
        put_table(table_data)

    act = actions("", [{'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}])
    if act == 'home': main_menu()

@require_auth
def add_to_cart(product_id):
    current_user = get_current_user()

    qty_data = input_group("إضافة المنتج إلى السلة", [
        input("الكمية المطلوبة (1, 2, 3...):", name="qty", type=NUMBER, value=1, min=1, required=True)
    ])
    qty = int(qty_data['qty'])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
        prod = cursor.fetchone()

        if prod:
            p_id, name, base_price, item_curr, img = prod['id'], prod['name'], prod['price'], prod['currency'], prod['image']

            cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ?", 
                           (current_user['id'], product_id))
            cart_item = cursor.fetchone()

            if cart_item:
                cursor.execute("UPDATE cart SET quantity = quantity + ? WHERE id = ?", (qty, cart_item['id']))
            else:
                cursor.execute("INSERT INTO cart (user_id, product_id, name, price, quantity, image) VALUES (?, ?, ?, ?, ?, ?)",
                               (current_user['id'], product_id, name, base_price, qty, img))

            conn.commit()
            toast(f"تمت إضافة ({qty}) قطعة من {name} إلى السلة بنجاح!", color="success")

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
        put_html("""
            <div style="background: white; padding: 40px; border-radius: 12px; margin: 30px auto; text-align: center; max-width: 600px; font-weight: 900;">
                <h3>🛒 سلة التسوق فارغة حالياً.</h3>
            </div>
        """)
    else:
        table_data = [["الصورة", "العطر", "السعر الفردي", "الكمية", "الإجمالي"]]
        grand_total = 0.0

        for item in items:
            name, base_price, quantity, img_path, orig_currency = item['name'], item['price'], item['quantity'], item['image'], item['currency']
            item_currency = orig_currency if orig_currency else "EUR (€)"
            
            converted_unit_price = convert_price(base_price, item_currency, selected_currency)
            total_item_price = converted_unit_price * quantity
            grand_total += total_item_price
            img_src = get_image_source(img_path)

            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'
            table_data.append([
                put_html(img_html),
                name,
                f"{converted_unit_price:,.2f} {curr_info['symbol']}",
                str(quantity),
                f"{total_item_price:,.2f} {curr_info['symbol']}"
            ])

        put_table(table_data)

        put_html(f"""
            <div style="background: #ffffff; padding: 20px; border-radius: 12px; margin: 20px auto; max-width: 400px; box-shadow: 0 4px 10px rgba(0,0,0,0.08); text-align: center; font-weight: 900;">
                <h3 style="margin: 0; color: #1a202c; font-weight: 900; font-size: 22px;">المبلغ الإجمالي: <span style="color: #38a169;">{grand_total:,.2f} {curr_info['symbol']}</span></h3>
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

@require_auth
def empty_user_cart():
    current_user = get_current_user()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (current_user['id'],))
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

    title_style = ParagraphStyle(
        'TitleStyle', parent=styles['Heading1'], fontName='Helvetica-Bold',
        fontSize=18, alignment=1, textColor=colors.HexColor("#1a202c")
    )
    normal_style = ParagraphStyle(
        'NormalStyle', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=10, alignment=1, textColor=colors.HexColor("#4a5568")
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
        name, base_price, qty, orig_curr = item['name'], item['price'], item['quantity'], item['currency']
        item_curr = orig_curr if orig_curr else "EUR (€)"
        
        price = convert_price(base_price, item_curr, selected_currency)
        total = price * qty
        grand_total += total
        data.append([name, f"{price:,.2f} {curr_info['symbol']}", str(qty), f"{total:,.2f} {curr_info['symbol']}"])

    data.append(["Grand Total", "", "", f"{grand_total:,.2f} {curr_info['symbol']}"])

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

# --- Protected Admin Dashboard ---

@require_admin
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

    image_str = process_and_save_image(data['image'])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO products (name, price, currency, image) VALUES (?, ?, ?, ?)",
                       (data['name'].strip(), float(data['price']), data['currency'], image_str))
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
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; max-width: 600px; margin: 20px auto; font-weight: 900;'><h3>لا توجد عطور متوفرة للتعديل.</h3></div>")
    else:
        table_data = [["المعرف", "الصورة", "اسم العطر", f"السعر ({curr_info['symbol']})", "الإجراءات"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod['id'], prod['name'], prod['price'], prod['currency'], prod['image']

            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)

            img_html = f'<img src="{img_src}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px;">'

            table_data.append([
                str(p_id),
                put_html(img_html),
                name,
                f"{disp_price:,.2f} {curr_info['symbol']}",
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

@require_admin
def handle_product_action(action, p_id):
    if action == 'del':
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM products WHERE id = ?", (p_id,))
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

    p_name, p_price, p_currency, p_image = product['name'], product['price'], product['currency'], product['image']

    data = input_group("تعديل العطر", [
        input("اسم العطر", name="name", value=p_name, required=True),
        input("السعر", name="price", type=NUMBER, value=float(p_price), required=True),
        select("عملة السعر المسجلة", list(CURRENCIES.keys()), name="currency", value=p_currency),
        file_upload("تحديث صورة العطر (اختياري)", name="image", accept="image/*")
    ])

    image_str = p_image
    if data['image'] and data['image'].get('content'):
        image_str = process_and_save_image(data['image'])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE products SET name = ?, price = ?, currency = ?, image = ? WHERE id = ?",
            (data['name'].strip(), float(data['price']), data['currency'], image_str, product_id)
        )
        conn.commit()

    toast("تم تحديث بيانات العطر بنجاح!", color="success")
    list_products_page()

# --- WSGI App Setup ---

app = Flask(__name__)
app.add_url_rule('/', 'webio_view', webio_view(main_menu), methods=['GET', 'POST', 'OPTIONS'])

flask_app = app

def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=True)
