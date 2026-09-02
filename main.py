import sqlite3
import base64
import os
import time
import threading
import webbrowser
from io import BytesIO

from pywebio import start_server
from pywebio.input import input, input_group, select, file_upload, NUMBER, PASSWORD
from pywebio.output import (
    put_html, put_table, put_buttons, clear, toast, popup, close_popup
)
from pywebio.session import run_js

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# --- Global Configurations & Constants ---
DB_NAME = "luxury_parfum.db"
PORT = 8080
STORE_BRAND = "Luxury Impact Parfum RZ"

CURRENCIES = {
    "USD ($)": {"rate": 1.0, "symbol": "$"},
    "EUR (€)": {"rate": 0.92, "symbol": "€"},
    "DZD (DA)": {"rate": 134.5, "symbol": "DA"},
    "GBP (£)": {"rate": 0.79, "symbol": "£"}
}

# App State
current_user = None
selected_currency = "EUR (€)"

# --- Database & Utility Setup ---

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'user'
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            image TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    
    # Create default admin account if none exists
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, name, role) VALUES (?, ?, ?, ?)",
                       ('admin', 'admin123', 'Administrator', 'admin'))
        
    conn.commit()
    conn.close()

def convert_price(amount, from_curr, to_curr):
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    usd_amount = amount / CURRENCIES[from_curr]["rate"]
    return usd_amount * CURRENCIES[to_curr]["rate"]

def process_and_save_image(file_data):
    if not file_data or not file_data.get('content'):
        return ""
    mime_type = file_data.get('mime_type', 'image/jpeg')
    encoded = base64.b64encode(file_data['content']).decode('utf-8')
    return f"data:{mime_type};base64,{encoded}"

def get_image_source(img_path):
    if not img_path:
        return "https://via.placeholder.com/150?text=No+Image"
    return img_path

def download(filename, data):
    b64_data = base64.b64encode(data).decode('utf-8')
    js_code = f"""
        var element = document.createElement('a');
        element.setAttribute('href', 'data:application/pdf;base64,{b64_data}');
        element.setAttribute('download', '{filename}');
        element.style.display = 'none';
        document.body.appendChild(element);
        element.click();
        document.body.removeChild(element);
    """
    run_js(js_code)

# --- Layout Components ---

def render_header(subtitle=""):
    user_status = f"👤 {current_user['name']}" if current_user else "🔑 غير مسجل"
    put_html(f"""
        <div style="background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%); color: #f7fafc; padding: 25px; border-radius: 12px; margin-bottom: 25px; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.15);">
            <h1 style="margin: 0; font-family: 'Georgia', serif; letter-spacing: 1px; font-size: 28px;">✨ {STORE_BRAND} ✨</h1>
            <p style="margin: 5px 0 0 0; color: #cbd5e0; font-size: 15px;">{subtitle}</p>
            <div style="margin-top: 10px; font-size: 13px; color: #a0aec0;">{user_status} | العملة الحالية: {selected_currency}</div>
        </div>
    """)

def render_footer():
    put_html("""
        <div style="text-align: center; color: #718096; padding: 20px 0; margin-top: 40px; border-top: 1px solid #e2e8f0; font-size: 13px;">
            &copy; Luxury Impact Parfum RZ — جميع الحقوق محفوظة
        </div>
    """)

def actions(title, choices):
    put_html(f"<p style='font-weight: bold; margin-top: 15px;'>{title}</p>")
    return put_buttons(choices, onclick=lambda v: v)

# --- Authentication Views ---

def login_page():
    global current_user
    clear()
    render_header("تسجيل الدخول")
    
    data = input_group("تسجيل الدخول", [
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, name, role FROM users WHERE username = ? AND password = ?", 
                   (data['username'], data['password']))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        current_user = {'id': user[0], 'username': user[1], 'name': user[2], 'role': user[3]}
        toast(f"مرحباً بك مجدداً {current_user['name']}!", color="success")
        main_menu()
    else:
        toast("اسم المستخدم أو كلمة المرور غير صحيحة", color="error")
        main_menu()

def register_page():
    clear()
    render_header("إنشاء حساب جديد")
    
    data = input_group("إنشاء حساب", [
        input("الاسم الكامل", name="name", required=True),
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, name, role) VALUES (?, ?, ?, 'user')",
                       (data['username'], data['password'], data['name']))
        conn.commit()
        toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
    except sqlite3.IntegrityError:
        toast("اسم المستخدم هذا مستخدم بالفعل.", color="error")
    finally:
        conn.close()
    
    main_menu()

def logout():
    global current_user
    current_user = None
    toast("تم تسجيل الخروج بنجاح.", color="info")
    main_menu()

# --- Shopping Views ---

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
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; text-align: center;'><h3>لا توجد عطور معروضة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "اسم العطر", f"السعر ({curr_info['symbol']})", "طلب"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)
            
            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'
            
            table_data.append([
                put_html(img_html),
                name,
                f"{disp_price:.2f} {curr_info['symbol']}",
                put_buttons([{'label': '🛒 إضافة للسلة', 'value': p_id, 'color': 'success'}], 
                            onclick=lambda p_id: prompt_add_to_cart(p_id))
            ])
            
        put_table(table_data)

    act = actions("", [
        {'label': '🛒 عرض سلة التسوق', 'value': 'cart', 'color': 'primary'},
        {'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])
    
    if act == 'cart': view_cart()
    elif act == 'home': main_menu()

def prompt_add_to_cart(product_id):
    if not current_user:
        toast("يرجى تسجيل الدخول أولاً!", color="warning")
        login_page()
        return
        
    qty = input("حدد الكمية المطلوب إضافتها:", type=NUMBER, value=1)
    if qty and qty > 0:
        add_to_cart(product_id, int(qty))

def add_to_cart(product_id, quantity):
    if not current_user:
        toast("يرجى تسجيل الدخول أولاً!", color="warning")
        login_page()
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

# --- Main Navigation ---

def set_currency():
    global selected_currency
    choice = select("اختر عملة العرض الفعالة:", list(CURRENCIES.keys()), value=selected_currency)
    selected_currency = choice
    toast(f"تم تغيير عملة العرض إلى {selected_currency}", color="info")
    main_menu()

def main_menu():
    clear()
    render_header("المتجر الإلكتروني الرئيسي")
    
    choices = [
        {'label': '🛍️ تصفح العطور', 'value': 'shop', 'color': 'primary'},
        {'label': '🛒 سلة التسوق', 'value': 'cart', 'color': 'success'},
        {'label': '💱 تغيير عملة العرض', 'value': 'currency', 'color': 'info'}
    ]
    
    if current_user:
        if current_user.get('role') == 'admin':
            choices.append({'label': '⚙️ لوحة الإدارة', 'value': 'admin', 'color': 'warning'})
        choices.append({'label': '🚪 تسجيل الخروج', 'value': 'logout', 'color': 'danger'})
    else:
        choices.append({'label': '🔑 تسجيل الدخول', 'value': 'login', 'color': 'dark'})
        choices.append({'label': '📝 حساب جديد', 'value': 'register', 'color': 'secondary'})

    act = actions("القائمة الرئيسية:", choices)
    
    if act == 'shop': user_shop()
    elif act == 'cart': view_cart()
    elif act == 'currency': set_currency()
    elif act == 'admin': admin_dashboard()
    elif act == 'login': login_page()
    elif act == 'register': register_page()
    elif act == 'logout': logout()

    render_footer()

def open_browser():
    """Opens default web browser for local execution."""
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    init_db()
    threading.Thread(target=open_browser, daemon=True).start()
    start_server(main_menu, port=PORT, debug=True)
