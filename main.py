import os
import sys
import time
import base64
import sqlite3
import threading
import webbrowser
from io import BytesIO

from flask import Flask
from pywebio import start_server, config
from pywebio.input import input, select, file_upload, input_group, NUMBER, PASSWORD, TEXT
from pywebio.output import (
    put_html, put_table, put_buttons, clear, toast, 
    download, popup, close_popup, put_text
)
from pywebio.platform.flask import webio_view

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# --- Configuration & Global Constants ---

STORE_BRAND = "Luxury Impact Parfum RZ"
STORE_PHONE = "+213 550 00 00 00"
STORE_EMAIL = "contact@luxuryimpactparfum.com"
PORT = int(os.environ.get("PORT", 8080))
DB_FILE = "store.db"

CURRENCIES = {
    "EUR (€)": {"symbol": "€", "rate_to_usd": 1.08},
    "USD ($)": {"symbol": "$", "rate_to_usd": 1.00},
    "DZD (DA)": {"symbol": "DA", "rate_to_usd": 0.0074}
}

# Session State
current_user = None  # None or dict: {'id': int, 'name': str, 'email': str, 'role': str}
selected_currency = "EUR (€)"

# --- Database & Utility Functions ---

def ensure_db_ready():
    """Initializes SQLite tables if they do not exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user'
        )
    """)
    
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
    
    # Cart table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # Create default admin account if none exists
    cursor.execute("SELECT id FROM users WHERE role = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
            ("Admin RZ", "admin@luxury.com", "admin123", "admin")
        )
    
    conn.commit()
    return conn

def convert_price(amount, from_curr, to_curr):
    """Converts amounts between supported currencies via USD base rate."""
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    usd_amount = amount * CURRENCIES[from_curr]["rate_to_usd"]
    return usd_amount / CURRENCIES[to_curr]["rate_to_usd"]

def process_and_save_image(file_data):
    """Converts uploaded file buffer into a Base64 image string for database storage."""
    if not file_data or not file_data.get('content'):
        return ""
    encoded = base64.b64encode(file_data['content']).decode('utf-8')
    mime_type = file_data.get('mime_type', 'image/jpeg')
    return f"data:{mime_type};base64,{encoded}"

def get_image_source(img_path_or_b64):
    """Ensures fallback display image if valid image string is missing."""
    if img_path_or_b64 and (img_path_or_b64.startswith("data:image") or os.path.exists(img_path_or_b64)):
        return img_path_or_b64
    return "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100'><rect width='100%' height='100%' fill='%23edf2f7'/><text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' fill='%23a0aec0'>Parfum</text></svg>"

# --- UI Components Layout ---

def render_header(title="المتجر الرسمي"):
    """Renders persistent global navigation header."""
    user_status = f"👤 مرحبا، {current_user['name']}" if current_user else "🔑 زائر"
    
    put_html(f"""
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: white; padding: 25px; border-radius: 12px; margin-bottom: 25px; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3); text-align: center;">
            <h1 style="margin: 0; font-weight: 900; font-size: 32px; letter-spacing: 1px; color: #f8fafc;">✨ {STORE_BRAND} ✨</h1>
            <p style="margin-top: 8px; color: #94a3b8; font-size: 16px;">{title}</p>
            <div style="margin-top: 15px; font-size: 14px; color: #cbd5e1; font-weight: 700;">
                <span>{user_status}</span> | <span>العملة الحالية: {selected_currency}</span>
            </div>
        </div>
    """)

def render_footer():
    """Renders persistent footer."""
    put_html(f"""
        <div style="margin-top: 50px; padding: 20px; text-align: center; color: #64748b; border-top: 1px solid #e2e8f0; font-size: 13px; font-weight: 600;">
            © 2026 {STORE_BRAND}. جميع الحقوق محفوظة. | التواصل: {STORE_PHONE}
        </div>
    """)

# --- Primary Routing & Navigation ---

@config(theme='mint')
def main_menu():
    clear()
    render_header("الصفحة الرئيسية")
    
    buttons = [
        {'label': '🛍️ تصفح العطور والطلب', 'value': 'shop', 'color': 'primary'},
        {'label': '🛒 عرض سلة التسوق', 'value': 'cart', 'color': 'info'},
        {'label': '💱 تغيير العملة', 'value': 'currency', 'color': 'warning'},
    ]
    
    if not current_user:
        buttons.append({'label': '🔑 تسجيل الدخول', 'value': 'login', 'color': 'success'})
        buttons.append({'label': '📝 إنشاء حساب جديد', 'value': 'register', 'color': 'secondary'})
    else:
        if current_user['role'] == 'admin':
            buttons.append({'label': '⚙️ لوحة الإدارة', 'value': 'admin', 'color': 'danger'})
        buttons.append({'label': '🚪 تسجيل الخروج', 'value': 'logout', 'color': 'dark'})
        
    choice = actions("القائمة الرئيسية - اختر الوجهة:", buttons)
    
    if choice == 'shop': user_shop()
    elif choice == 'cart': view_cart()
    elif choice == 'currency': change_currency_page()
    elif choice == 'login': login_page()
    elif choice == 'register': register_page()
    elif choice == 'admin': admin_dashboard()
    elif choice == 'logout': logout_action()

def logout_action():
    global current_user
    current_user = None
    toast("تم تسجيل الخروج بنجاح.", color="info")
    main_menu()

def change_currency_page():
    global selected_currency
    clear()
    render_header("إعدادات العملة")
    
    new_curr = select("اختر العملة المفضلة لعرض الأسعار:", list(CURRENCIES.keys()), value=selected_currency)
    if new_curr:
        selected_currency = new_curr
        toast(f"تم تغيير العملة إلى {selected_currency}", color="success")
    main_menu()

# --- Authentication Views ---

def login_page():
    global current_user
    clear()
    render_header("تسجيل الدخول")
    
    data = input_group("أدخل بيانات حسابك", [
        input("البريد الإلكتروني", name="email", type=TEXT, required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    if not data:
        main_menu()
        return

    conn = ensure_db_ready()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, role FROM users WHERE email = ? AND password = ?", (data['email'], data['password']))
    user = cursor.fetchone()
    conn.close()

    if user:
        current_user = {'id': user[0], 'name': user[1], 'email': user[2], 'role': user[3]}
        toast(f"أهلاً بك مجدداً {current_user['name']}!", color="success")
        main_menu()
    else:
        toast("بيانات الدخول غير صحيحة، يرجى المحاولة مجدداً.", color="error")
        login_page()

def register_page():
    clear()
    render_header("إنشاء حساب جديد")
    
    data = input_group("بيانات التسجيل", [
        input("الاسم الكامل", name="name", required=True),
        input("البريد الإلكتروني", name="email", type=TEXT, required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    if not data:
        main_menu()
        return

    conn = ensure_db_ready()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, 'user')",
                       (data['name'], data['email'], data['password']))
        conn.commit()
        toast("تم إنشاء حسابك بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
        login_page()
    except sqlite3.IntegrityError:
        toast("البريد الإلكتروني مسجل بالفعل!", color="error")
        register_page()
    finally:
        conn.close()

# --- Storefront & Cart Functions ---

def user_shop():
    clear()
    render_header("قائمة العطور المتاحة")
    
    conn = ensure_db_ready()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()

    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; max-width: 600px; margin: 20px auto; font-weight: 900; text-align: center;'><h3>لا توجد عطور معروضة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "العطر", f"السعر ({curr_info['symbol']})"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)
            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'
            
            table_data.append([
                put_html(img_html),
                name,
                f"{disp_price:.2f} {curr_info['symbol']}"
            ])
        
        put_table(table_data)

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
        
    conn = ensure_db_ready()
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

    conn = ensure_db_ready()
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
        conn = ensure_db_ready()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (current_user['id'],))
        conn.commit()
        conn.close()
        toast("تم تفريغ سلة التسوق بنجاح.", color="info")
    view_cart()

def generate_pdf_invoice():
    if not current_user:
        return
        
    conn = ensure_db_ready()
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

    conn = ensure_db_ready()
    image_str = process_and_save_image(data['image'])
        
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
    
    conn = ensure_db_ready()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()
    
    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; max-width: 600px; margin: 20px auto; font-weight: 900; text-align: center;'><h3>لا توجد عطور متوفرة للتعديل.</h3></div>")
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
        conn = ensure_db_ready()
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
    
    conn = ensure_db_ready()
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

    conn = ensure_db_ready()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET name = ?, price = ?, currency = ?, image = ? WHERE id = ?",
                   (data['name'], float(data['price']), data['currency'], image_str, product_id))
    conn.commit()
    conn.close()
    
    toast("تم تحديث بيانات العطر بنجاح!", color="success")
    list_products_page()

# --- Server Execution & Deployment Hooks ---

app = Flask(__name__)

with app.app_context():
    conn_context = ensure_db_ready()
    conn_context.close()

app.add_url_rule('/', endpoint='webio_view', view_func=webio_view(main_menu), methods=['GET', 'POST', 'OPTIONS'])

# Gunicorn WSGI Entry Point
flask_app = app

def open_browser():
    """Opens default web browser for local environment execution."""
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    if os.environ.get("RENDER") is None:
        threading.Thread(target=open_browser, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=True)
