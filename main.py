import base64
import sqlite3
import threading
import time
import webbrowser
from io import BytesIO

from flask import Flask
from pywebio.input import PASSWORD, NUMBER, file_upload, input, input_group, select
from pywebio.output import (
    clear,
    download,
    put_buttons,
    put_html,
    put_table,
    toast,
)
from pywebio.platform.flask import webio_view
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash

# --- App Configuration ---
app = Flask(__name__)
DB_NAME = "store.db"
STORE_BRAND = "Luxury Impact Parfume RZ"
PORT = 8080

# Global session variables per execution thread
current_user = None  # Stores dict: {'id': int, 'name': str, 'username': str, 'email': str, 'is_admin': int}
selected_currency = "USD ($)"

CURRENCIES = {
    "USD ($)": {"symbol": "$", "rate": 1.0},
    "EUR (€)": {"symbol": "€", "rate": 0.92},
    "DZD (DA)": {"symbol": "DA", "rate": 134.5},
}


# --- Database Initialization ---
def init_db():
    """Initializes tables for users, products, and cart items."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            )
        """)
        
        # Products table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                image TEXT
            )
        """)
        
        # Cart table
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
        
        # Seed default Admin account if no users exist
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            admin_pw = generate_password_hash("admin123")
            cursor.execute("""
                INSERT INTO users (name, username, email, password, is_admin)
                VALUES (?, ?, ?, ?, 1)
            """, ("Administrator", "admin", "admin@luxuryimpact.com", admin_pw))
            
        conn.commit()


# --- Utilities ---
def convert_price(amount, from_curr, to_curr):
    """Converts amounts between supported currencies via base USD."""
    if from_curr == to_curr:
        return amount
    base_usd = amount / CURRENCIES[from_curr]["rate"]
    return base_usd * CURRENCIES[to_curr]["rate"]


def get_image_source(img_path):
    """Returns valid image string or a placeholder."""
    if img_path and (img_path.startswith("data:image") or img_path.startswith("http")):
        return img_path
    return "https://via.placeholder.com/150?text=Perfume"


def process_and_save_image(file_data):
    """Encodes uploaded images into base64 strings."""
    if file_data and "content" in file_data:
        encoded = base64.b64encode(file_data["content"]).decode("utf-8")
        mime = file_data.get("mime_type", "image/png")
        return f"data:{mime};base64,{encoded}"
    return ""


def render_header(title):
    """Renders main header with user context and currency indicator."""
    user_status = f"👤 {current_user['name']}" if current_user else "🔑 Not Logged In"
    put_html(f"""
        <div style="background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%); color: white; padding: 20px; border-radius: 12px; margin-bottom: 20px; text-align: center;">
            <div style="float: right; font-size: 12px; opacity: 0.8; font-weight: bold;">{user_status}</div>
            <h1 style="margin: 0; font-size: 26px; font-weight: 900;">{STORE_BRAND}</h1>
            <p style="margin: 5px 0 0 0; opacity: 0.8; font-size: 14px;">{title}</p>
        </div>
    """)


def render_footer():
    """Renders application footer."""
    put_html("""
        <footer style="margin-top: 40px; text-align: center; color: #718096; font-size: 13px;">
            <p>© 2026 Luxury Impact Parfume RZ. All rights reserved.</p>
        </footer>
    """)


def actions(label, buttons):
    """Utility helper for inline action button layouts."""
    return put_buttons(buttons, onclick=lambda v: v)


# --- Authentication Views ---
def auth_menu():
    """Account entry menu for registering or logging in."""
    clear()
    render_header("مرحباً بك - بوابة الحسابات")

    choice = actions("اختر الإجراء:", [
        {"label": "🔑 تسجيل الدخول (Login)", "value": "login", "color": "primary"},
        {"label": "📝 إنشاء حساب جديد (Register)", "value": "register", "color": "success"},
        {"label": "🛍️ التصفح كزائر", "value": "guest", "color": "secondary"}
    ])

    if choice == "login":
        login_page()
    elif choice == "register":
        register_page()
    elif choice == "guest":
        main_menu()


def register_page():
    """Handles new user registration."""
    clear()
    render_header("إنشاء حساب جديد")

    data = input_group("أدخل بيانات حسابك الجديد", [
        input("الاسم الكامل", name="name", required=True),
        input("اسم المستخدم (Username)", name="username", required=True),
        input("البريد الإلكتروني", name="email", type="email", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])

    hashed_pw = generate_password_hash(data["password"])

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (name, username, email, password, is_admin)
                VALUES (?, ?, ?, ?, 0)
            """, (data["name"], data["username"], data["email"], hashed_pw))
            conn.commit()

        toast("تم إنشاء الحساب بنجاح! يمكنك الآن تسجيل الدخول.", color="success")
        login_page()
    except sqlite3.IntegrityError:
        toast("اسم المستخدم أو البريد الإلكتروني مسجل بالفعل!", color="error")
        auth_menu()


def login_page():
    """Handles user authentication."""
    global current_user
    clear()
    render_header("تسجيل الدخول")

    data = input_group("أدخل بيانات الدخول", [
        input("اسم المستخدم", name="username", required=True),
        input("كلمة المرور", name="password", type=PASSWORD, required=True)
    ])

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, username, email, password, is_admin FROM users WHERE username = ?", (data["username"],))
        user = cursor.fetchone()

    if user and check_password_hash(user[4], data["password"]):
        current_user = {
            "id": user[0],
            "name": user[1],
            "username": user[2],
            "email": user[3],
            "is_admin": user[5]
        }
        toast(f"أهلاً بك مجدداً {current_user['name']}!", color="success")
        main_menu()
    else:
        toast("اسم المستخدم أو كلمة المرور غير صحيحة!", color="error")
        auth_menu()


def logout():
    """Clears active session."""
    global current_user
    current_user = None
    toast("تم تسجيل الخروج بنجاح.", color="info")
    auth_menu()


# --- Primary Application Views ---
def main_menu():
    """Main dashboard menu."""
    clear()
    render_header("الصفحة الرئيسية")

    btn_list = [
        {"label": "🛍️ تصفح العطور", "value": "shop", "color": "primary"},
        {"label": "🛒 سلة التسوق", "value": "cart", "color": "success"},
    ]

    if current_user and current_user["is_admin"] == 1:
        btn_list.append({"label": "⚙️ لوحة التحكم (Admin)", "value": "admin", "color": "dark"})

    if current_user:
        btn_list.append({"label": "🚪 تسجيل الخروج", "value": "logout", "color": "danger"})
    else:
        btn_list.append({"label": "🔑 تسجيل الدخول", "value": "login", "color": "info"})

    act = actions("القائمة الرئيسية:", btn_list)

    if act == "shop":
        user_shop()
    elif act == "cart":
        view_cart()
    elif act == "admin":
        admin_dashboard()
    elif act == "logout":
        logout()
    elif act == "login":
        auth_menu()

    render_footer()


def user_shop():
    """Shop catalogue display."""
    clear()
    render_header("تصفح قائمة العطور")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products")
        products = cursor.fetchall()

    curr_info = CURRENCIES[selected_currency]

    if not products:
        put_html("<div style='background: white; padding: 30px; border-radius: 12px; max-width: 600px; margin: 20px auto; text-align: center;'><h3>لا توجد عطور متوفرة حالياً.</h3></div>")
    else:
        table_data = [["الصورة", "اسم العطر", f"السعر ({curr_info['symbol']})", "الشراء"]]
        for prod in products:
            p_id, name, base_price, item_currency, img_path = prod
            disp_price = convert_price(base_price, item_currency, selected_currency)
            img_src = get_image_source(img_path)

            img_html = f'<img src="{img_src}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;">'

            table_data.append([
                put_html(img_html),
                name,
                f"{disp_price:.2f} {curr_info['symbol']}",
                put_buttons([{"label": "➕ إضافة للسلة", "value": p_id, "color": "success"}],
                            onclick=lambda p=p_id: add_to_cart(p, 1))
            ])

        put_table(table_data)

    act = actions("", [
        {"label": "🛒 الانتقال للسلة", "value": "view_cart", "color": "primary"},
        {"label": "🔙 القائمة الرئيسية", "value": "home", "color": "secondary"}
    ])
    if act == "view_cart":
        view_cart()
    elif act == "home":
        main_menu()

    render_footer()


def add_to_cart(product_id, quantity=1):
    """Adds item to cart in database for active user."""
    if not current_user:
        toast("يرجى تسجيل الدخول أولاً لتتمكن من الإضافة للسلة!", color="warning")
        auth_menu()
        return

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if product:
            p_id, name, price_val, prod_currency, image = product
            base_usd_price = convert_price(price_val, prod_currency, "USD ($)")

            cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND name = ?", (current_user["id"], name))
            existing_item = cursor.fetchone()

            if existing_item:
                new_qty = existing_item[1] + quantity
                cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (new_qty, existing_item[0]))
            else:
                cursor.execute(
                    "INSERT INTO cart (user_id, name, price, image, quantity) VALUES (?, ?, ?, ?, ?)",
                    (current_user["id"], name, base_usd_price, image, quantity)
                )

            conn.commit()
            toast(f"تم إضافة {quantity} من '{name}' إلى السلة بنجاح!", color="success")

    view_cart()


def view_cart():
    """Displays items stored in user's cart."""
    clear()
    render_header("سلة التسوق")

    if not current_user:
        toast("يرجى تسجيل الدخول لعرض سلة التسوق.", color="warning")
        auth_menu()
        return

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, quantity, image FROM cart WHERE user_id = ?", (current_user["id"],))
        items = cursor.fetchall()

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
        {"label": "📄 تحميل الفاتورة (PDF)", "value": "pdf", "color": "success"},
        {"label": "🗑️ تفريغ السلة", "value": "clear_cart", "color": "danger"},
        {"label": "🛍️ مواصلة التسوق", "value": "shop", "color": "primary"},
        {"label": "🔙 القائمة الرئيسية", "value": "home", "color": "secondary"}
    ])

    if act == "pdf":
        generate_pdf_invoice()
    elif act == "clear_cart":
        empty_user_cart()
    elif act == "shop":
        user_shop()
    elif act == "home":
        main_menu()

    render_footer()


def empty_user_cart():
    """Clears active user cart."""
    if current_user:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cart WHERE user_id = ?", (current_user["id"],))
            conn.commit()
        toast("تم تفريغ سلة التسوق بنجاح.", color="info")
    view_cart()


def generate_pdf_invoice():
    """Generates PDF invoice download via ReportLab."""
    if not current_user:
        return

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, price, quantity FROM cart WHERE user_id = ?", (current_user["id"],))
        items = cursor.fetchall()

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

    customer_info = f"Customer: {current_user['name']} | Email: {current_user['email']}"
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
    """Admin management portal."""
    if not current_user or current_user["is_admin"] != 1:
        toast("غير مصرح لك بدخول هذه الصفحة!", color="error")
        main_menu()
        return

    clear()
    render_header("لوحة التحكم وإدارة العطور")

    put_html("<h2 style='color: #1a202c; text-align: center; font-weight: 900; font-size: 24px;'>⚙️ لوحة إدارة المتجر</h2>")

    choice = actions("اختر العملية المطلوبة:", [
        {'label': '➕ إضافة عطر جديد', 'value': 'add', 'color': 'success'},
        {'label': '📋 عرض وتعديل قائمة العطور', 'value': 'list', 'color': 'primary'},
        {'label': '🔙 العودة للقائمة الرئيسية', 'value': 'home', 'color': 'secondary'}
    ])

    if choice == 'add':
        add_product_page()
    elif choice == 'list':
        list_products_page()
    elif choice == 'home':
        main_menu()


def add_product_page():
    """Adds a new perfume product to database."""
    clear()
    render_header("إضافة عطر جديد")

    data = input_group("إضافة عطر جديد", [
        input("اسم العطر", name="name", required=True),
        input("السعر", name="price", type=NUMBER, required=True),
        select("عملة السعر الإدخالي", list(CURRENCIES.keys()), name="currency", value="EUR (€)"),
        file_upload("صورة العطر", name="image", accept="image/*", required=True)
    ])

    image_str = process_and_save_image(data['image'])

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO products (name, price, currency, image) VALUES (?, ?, ?, ?)",
            (data['name'], float(data['price']), data['currency'], image_str)
        )
        conn.commit()

    toast("تمت إضافة العطر بنجاح!", color="success")
    admin_dashboard()


def list_products_page():
    """Lists registered products with edit and delete buttons."""
    clear()
    render_header("إدارة وتعديل العطور المسجلة")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products")
        products = cursor.fetchall()

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
    """Processes deletion or edit execution for selected products."""
    if action == 'del':
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM products WHERE id = ?", (p_id,))
            conn.commit()
        toast("تم حذف العطر بنجاح.", color="info")
        list_products_page()
    elif action == 'edit':
        edit_product_page(p_id)


def edit_product_page(product_id):
    """Edits specific perfume data."""
    clear()
    render_header("تعديل بيانات العطر")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, currency, image FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

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

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE products SET name = ?, price = ?, currency = ?, image = ? WHERE id = ?",
            (data['name'], float(data['price']), data['currency'], image_str, product_id)
        )
        conn.commit()

    toast("تم تحديث بيانات العطر بنجاح!", color="success")
    list_products_page()


# --- Application Binding & Startup ---
app.add_url_rule("/", "webio_view", webio_view(auth_menu), methods=["GET", "POST", "OPTIONS"])


def open_browser():
    """Opens browser local session."""
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    init_db()
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=True)
