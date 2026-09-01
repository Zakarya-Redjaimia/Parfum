import os
import sqlite3
import base64
import threading
from io import BytesIO

# PyWebIO imports
from pywebio import start_server
from pywebio.input import input, input_group, select, file_upload, NUMBER, actions
from pywebio.output import (
    put_html, put_table, put_buttons, toast, clear, download
)
from pywebio.platform.wsgi import wsgi_app

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import A5
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Kivy imports (optional in server environment)
try:
    from kivy.app import App
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.label import Label
    KIVY_AVAILABLE = True
except ImportError:
    KIVY_AVAILABLE = False

# --- Constants & Global Configuration ---
DB_NAME = "shop.db"
PORT = int(os.environ.get("PORT", 8080))
STORE_BRAND = "Luxury Impact Parfum RZ"

CURRENCIES = {
    "EUR (€)": {"symbol": "€", "rate_to_usd": 1.08},
    "USD ($)": {"symbol": "$", "rate_to_usd": 1.0},
    "DZD (DA)": {"symbol": "DA", "rate_to_usd": 0.0074}
}

selected_currency = "EUR (€)"
current_user = None

# --- Helper & Utility Functions ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Products Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            image TEXT
        )
    """)
    
    # Cart Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT,
            quantity INTEGER NOT NULL
        )
    """)
    
    # Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        )
    """)
    
    # Insert default admin if not existing
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, is_admin) VALUES ('admin', 'admin123', 1)")
        
    conn.commit()
    conn.close()

# Initialize DB on script load
init_db()

def convert_price(amount, from_curr, to_curr):
    if from_curr not in CURRENCIES or to_curr not in CURRENCIES:
        return amount
    usd_amount = amount * CURRENCIES[from_curr]["rate_to_usd"]
    return usd_amount / CURRENCIES[to_curr]["rate_to_usd"]

def process_and_save_image(img_file):
    if not img_file or not img_file.get('content'):
        return ""
    encoded_b64 = base64.b64encode(img_file['content']).decode('utf-8')
    mime_type = img_file.get('mime_type', 'image/png')
    return f"data:{mime_type};base64,{encoded_b64}"

def get_image_source(img_str):
    if img_str and img_str.startswith("data:image"):
        return img_str
    return "https://via.placeholder.com/150"

def render_header(title=""):
    put_html(f"""
        <div style="background-color: #1a202c; color: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; text-align: center;">
            <h1 style="margin: 0; font-size: 24px;">{STORE_BRAND}</h1>
            <p style="margin: 5px 0 0 0; color: #cbd5e0;">{title}</p>
        </div>
    """)

def render_footer():
    put_html("""
        <footer style="margin-top: 40px; text-align: center; color: #718096; font-size: 12px;">
            <p>© Luxury Impact Parfum RZ. All rights reserved.</p>
        </footer>
    """)

# --- View & Page Flow ---
def main_menu():
    clear()
    render_header("المتجر الرئيسي")
    
    options = [
        {'label': '🛍️ تصفح العطور', 'value': 'shop', 'color': 'primary'},
        {'label': '🛒 سلة التسوق', 'value': 'cart', 'color': 'info'}
    ]
    
    if current_user and current_user.get('is_admin'):
        options.append({'label': '⚙️ لوحة التحكم (Admin)', 'value': 'admin', 'color': 'warning'})
        
    if not current_user:
        options.append({'label': '🔑 تسجيل الدخول', 'value': 'login', 'color': 'success'})
    else:
        options.append({'label': '🚪 تسجيل الخروج', 'value': 'logout', 'color': 'danger'})

    act = actions("القائمة الرئيسية:", options)
    
    if act == 'shop': user_shop()
    elif act == 'cart': view_cart()
    elif act == 'admin': admin_dashboard()
    elif act == 'login': login_page()
    elif act == 'logout': logout_action()

def login_page():
    clear()
    render_header("تسجيل الدخول")
    
    data = input_group("دخول المستخدم", [
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type="password", required=True)
    ])
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, is_admin FROM users WHERE username = ? AND password = ?", 
                   (data['username'], data['password']))
    user = cursor.fetchone()
    conn.close()
    
    global current_user
    if user:
        current_user = {'id': user[0], 'name': user[1], 'is_admin': bool(user[2])}
        toast(f"مرحباً بك {user[1]}!", color="success")
        main_menu()
    else:
        toast("خطأ في بيانات الدخول!", color="error")
        main_menu()

def logout_action():
    global current_user
    current_user = None
    toast("تم تسجيل الخروج بنجاح.", color="info")
    main_menu()

def user_shop():
    clear()
    render_header("كتالوج العطور")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, currency, image FROM products")
    products = cursor.fetchall()
    conn.close()

    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; max-width: 600px; margin: 20px auto; text-align: center; font-weight: 900;'><h3>لا توجد عطور متاحة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "اسم العطر", f"السعر ({curr_info['symbol']})", "الإجراءات"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            img_src = get_image_source(img_path)
            disp_price = convert_price(base_price, item_currency, selected_currency)
            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'
            
            table_data.append([
                put_html(img_html),
                name,
                f"{disp_price:.2f} {curr_info['symbol']}",
                put_buttons([{'label': '➕ إضافة للسلة', 'value': p_id, 'color': 'success'}],
                            onclick=lambda val: add_to_cart(val))
            ])
            
        put_table(table_data)

    act = actions("", [{'label': '🔙 القائمة الرئيسية', 'value': 'home', 'color': 'secondary'}])
    if act == 'home':
        main_menu()

def add_to_cart(product_id):
    if not current_user:
        toast("يرجى تسجيل الدخول أولاً لإضافة المنتجات!", color="warning")
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
            new_qty = existing_item[1] + 1
            cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (new_qty, existing_item[0]))
        else:
            cursor.execute("INSERT INTO cart (user_id, name, price, image, quantity) VALUES (?, ?, ?, ?, ?)",
                           (current_user['id'], name, base_usd_price, image, 1))
                           
        conn.commit()
        toast(f"تم إضافة '{name}' إلى السلة بنجاح!", color="success")
    
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

    if act == 'pdf': generate_pdf_invoice()
    elif act == 'clear_cart': empty_user_cart()
    elif act == 'shop': user_shop()
    elif act == 'home': main_menu()

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

def admin_dashboard():
    clear()
    render_header("لوحة التحكم وإدارة العطور")
    
    put_html("<h2 style='color: #1a202c; text-align: center; font-weight: 900; font-size: 24px;'>⚙️ لوحة إدارة المتجر</h2>")
    
    choice = actions("اختر العملية المطلوبة:", [
        {'label': '➕ إضافة عطر جديد', 'value': 'add', 'color': 'success'},
        {'label': '📋 عرض وتعديل قائمة العطور', 'value': 'list', 'color': 'primary'},
        {'label': '🔙 العودة للقائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])
    
    if choice == 'add': add_product_page()
    elif choice == 'list': list_products_page()
    elif choice == 'home': main_menu()

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

# --- WSGI App Export for Render / Gunicorn ---
app = wsgi_app(main_menu)

# Local Development / Kivy Entry Point
if __name__ == '__main__':
    if KIVY_AVAILABLE:
        def run_server():
            start_server(main_menu, port=PORT, auto_open_webbrowser=False)

        class ZakiShopApp(App):
            def build(self):
                threading.Thread(target=run_server, daemon=True).start()
                layout = BoxLayout(orientation='vertical')
                try:
                    from kivy.uix.webview import WebView
                    wb = WebView(url=f"http://127.0.0.1:{PORT}")
                    layout.add_widget(wb)
                except Exception:
                    layout.add_widget(Label(text=f"Server running at http://127.0.0.1:{PORT}"))
                return layout

        ZakiShopApp().run()
    else:
        start_server(main_menu, port=PORT, debug=True)
