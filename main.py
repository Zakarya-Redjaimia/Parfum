import sqlite3
import time
import os
import io
from io import BytesIO
import threading
import webbrowser
from flask import Flask, send_file
from pywebio.platform.flask import webio_view
from pywebio import start_server
from pywebio.input import input, input_group, select, file_upload, NUMBER, TEXT, actions
from pywebio.output import (
    clear, put_html, put_table, put_buttons, toast, 
    popup, close_popup, download
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
    "EUR (€)": {"rate": 1.0, "symbol": "€"},
    "USD ($)": {"rate": 1.08, "symbol": "$"},
    "DZD (د.ج)": {"rate": 220.0, "symbol": "د.ج"}
}

# Session State
current_user = None
selected_currency = "EUR (€)"

# --- Database On-Demand Creation ---

def get_db_connection():
    """Returns a connection to the SQLite database and ensures tables are created on demand."""
    conn = sqlite3.connect(DB_NAME)
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
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)
    conn.commit()
    return conn

# --- Helper Functions ---

def convert_price(amount, from_curr, to_curr):
    """Converts price accurately between configured currencies using EUR as base."""
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    # Convert from source currency to EUR base, then to target currency
    eur_amount = amount / CURRENCIES[from_curr]["rate"]
    return eur_amount * CURRENCIES[to_curr]["rate"]

def process_and_save_image(file_data):
    if not file_data:
        return ""
    import base64
    content = file_data['content']
    mime = file_data.get('mime_type', 'image/jpeg')
    b64_str = base64.b64encode(content).decode('utf-8')
    return f"data:{mime};base64,{b64_str}"

def get_image_source(img_path):
    if img_path and img_path.startswith("data:image"):
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

# --- Main Views ---

def main_menu():
    clear()
    render_header("المتجر الإلكتروني الرسمي للعطور الفاخرة")

    global current_user, selected_currency

    user_info_html = ""
    if current_user:
        user_info_html = f"<div style='text-align: center; margin-bottom: 15px;'><b>مرحباً بك:</b> {current_user['name']} ({current_user['role']})</div>"

    # Currency selection interface
    curr_select = select("اختر عملة العرض والتسوق:", list(CURRENCIES.keys()), value=selected_currency)
    if curr_select != selected_currency:
        selected_currency = curr_select
        toast(f"تم تغيير العملة إلى: {selected_currency}", color="info")

    put_html(f"""
        {user_info_html}
    """)

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
        current_user = None
        toast("تم تسجيل الخروج بنجاح.", color="info")
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

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (name, username, password, role) VALUES (?, ?, ?, ?)",
                       (data['name'], data['username'], data['password'], data['role']))
        conn.commit()
        toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
        login_page()
    except sqlite3.IntegrityError:
        toast("اسم المستخدم مستخدم بالفعل. اختر اسماً آخر.", color="error")
        register_page()
    finally:
        conn.close()

def login_page():
    clear()
    render_header("تسجيل الدخول")

    data = input_group("تسجيل الدخول", [
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=TEXT, required=True)
    ])

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, username, role FROM users WHERE username = ? AND password = ?", 
                   (data['username'], data['password']))
    user = cursor.fetchone()
    conn.close()

    if user:
        global current_user
        current_user = {'id': user[0], 'name': user[1], 'username': user[2], 'role': user[3]}
        toast(f"مرحباً بك {user[1]}!", color="success")
        main_menu()
    else:
        toast("بيانات الدخول غير صحيحة!", color="error")
        login_page()

# --- Store & Cart Views ---

def user_shop():
    clear()
    render_header("تصفح قائمة العطور")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()

    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 40px; border-radius: 12px; margin: 30px auto; text-align: center; max-width: 600px; font-weight: 900;'><h3>لا توجد عطور معروضة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "العطر", f"السعر الفردي ({curr_info['symbol']})", "الإجراء"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
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

def add_to_cart(product_id):
    global current_user
    if not current_user:
        toast("يجب تسجيل الدخول أولاً لإضافة منتجات إلى السلة!", color="warning")
        login_page()
        return

    # Select quantity when adding product
    qty_data = input_group("إضافة المنتج إلى السلة", [
        input("الكمية المطلوبة (1, 2, 3...):", name="qty", type=NUMBER, value=1, min=1, required=True)
    ])
    qty = int(qty_data['qty'])

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
    prod = cursor.fetchone()

    if prod:
        p_id, name, base_price, item_curr, img = prod

        cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ?", 
                       (current_user['id'], product_id))
        cart_item = cursor.fetchone()

        if cart_item:
            cursor.execute("UPDATE cart SET quantity = quantity + ? WHERE id = ?", (qty, cart_item[0]))
        else:
            cursor.execute("INSERT INTO cart (user_id, product_id, name, price, quantity, image) VALUES (?, ?, ?, ?, ?, ?)",
                           (current_user['id'], product_id, name, base_price, qty, img))

        conn.commit()
        toast(f"تمت إضافة ({qty}) قطعة من {name} إلى السلة بنجاح!", color="success")

    conn.close()

def view_cart():
    clear()
    render_header("سلة التسوق الخاصة بك")

    if not current_user:
        toast("يرجى تسجيل الدخول لعرض السلة.", color="warning")
        login_page()
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name, c.price, c.quantity, c.image, p.currency 
        FROM cart c 
        LEFT JOIN products p ON c.product_id = p.id 
        WHERE c.user_id = ?
    """, (current_user['id'],))
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
            c_id, name, base_price, quantity, img_path, orig_currency = item
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
    cursor.execute("""
        SELECT c.name, c.price, c.quantity, p.currency 
        FROM cart c 
        LEFT JOIN products p ON c.product_id = p.id 
        WHERE c.user_id = ?
    """, (current_user['id'],))
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
        name, base_price, qty, orig_curr = item
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

# --- Server & Flask WSGI Setup ---

app = Flask(__name__)
app.add_url_rule('/', 'webio_view', webio_view(main_menu), methods=['GET', 'POST', 'OPTIONS'])

flask_app = app

def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=True)
