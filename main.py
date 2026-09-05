import os
import sqlite3
import io
import base64
import json
import logging
import qrcode
from pathlib import Path
from functools import wraps

from flask import Flask, send_file, request, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash

from pywebio import start_server, config
from pywebio.input import input, textarea, select, file_upload, input_group, ACTIONS, NUMBER, FLOAT
from pywebio.output import (
    put_text, put_markdown, put_buttons, put_table, put_image, 
    put_html, put_loading, put_row, put_column, put_widget, 
    clear, toast, popup, close_popup
)
from pywebio.session import run_js, eval_js, local

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# ==========================================
# 1. CONFIGURATION & DATABASE SETUP
# ==========================================

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = BASE_DIR / "perfume_shop.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DB_EXPORT_SECRET = os.environ.get("DB_EXPORT_SECRET", "super-secret-passphrase-rz")

CURRENCY_RATES = {
    "DZD": 1.0,
    "EUR": 0.0068,
    "USD": 0.0074
}

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'customer',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'DZD',
                category TEXT,
                top_notes TEXT,
                heart_notes TEXT,
                base_notes TEXT,
                image_path TEXT,
                stock INTEGER DEFAULT 10,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                total_amount REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders (id),
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        """)
        
        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            default_admin_hash = generate_password_hash("Admin123!")
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("admin", default_admin_hash, "admin")
            )
            logging.info("Default admin created (Username: admin, Password: Admin123!)")
            
        conn.commit()

init_db()

# ==========================================
# 2. HELPER FUNCTIONS & UTILITIES
# ==========================================

def generate_qr_svg_data_uri(content: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    
    import qrcode.image.svg
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    img.save(stream)
    svg_str = stream.getvalue().decode("utf-8")
    b64_svg = base64.b64encode(svg_str.encode("utf-8")).decode("utf-8")
    return f"data:image/svg+xml;base64,{b64_svg}"

def convert_currency(amount: float, from_curr: str, to_curr: str) -> float:
    if from_curr == to_curr:
        return amount
    in_dzd = amount / CURRENCY_RATES.get(from_curr, 1.0)
    return round(in_dzd * CURRENCY_RATES.get(to_curr, 1.0), 2)

def generate_pdf_invoice_bytes(order_id: int) -> bytes:
    with get_db_connection() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (order['user_id'],)).fetchone()
        items = conn.execute("""
            SELECT oi.*, p.name FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        """, (order_id,)).fetchall()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=12
    )
    
    story.append(Paragraph("Luxury Impact Parfum RZ", title_style))
    story.append(Paragraph(f"<b>Invoice #:</b> INV-{order['id']:05d}", styles['Normal']))
    story.append(Paragraph(f"<b>Customer:</b> {user['username']}", styles['Normal']))
    story.append(Paragraph(f"<b>Date:</b> {order['created_at']}", styles['Normal']))
    story.append(Spacer(1, 15))
    
    table_data = [["Product", "Quantity", "Unit Price", "Subtotal"]]
    for item in items:
        subtotal = item['quantity'] * item['unit_price']
        table_data.append([
            item['name'],
            str(item['quantity']),
            f"{item['unit_price']} {order['currency']}",
            f"{subtotal:.2f} {order['currency']}"
        ])
    
    table_data.append(["", "", "Total:", f"{order['total_amount']:.2f} {order['currency']}"])
    
    t = Table(table_data, colWidths=[200, 80, 100, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    
    story.append(t)
    doc.build(story)
    
    pdf_value = buffer.getvalue()
    buffer.close()
    return pdf_value

# ==========================================
# 3. SESSION MANAGEMENT
# ==========================================

def get_current_user():
    user_data = getattr(local, 'current_user', None)
    return user_data

def set_current_user(user_dict):
    local.current_user = user_dict
    if user_dict:
        run_js(f"window.localStorage.setItem('user_session_id', '{user_dict['id']}');")
    else:
        run_js("window.localStorage.removeItem('user_session_id');")

def restore_session_from_browser():
    try:
        stored_id = eval_js("window.localStorage.getItem('user_session_id');")
        if stored_id:
            with get_db_connection() as conn:
                user = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (stored_id,)).fetchone()
                if user:
                    local.current_user = dict(user)
                    return True
    except Exception as e:
        logging.error(f"Session restoration error: {e}")
    return False

def get_cart():
    if not hasattr(local, 'cart'):
        local.cart = {}
    return local.cart

def clear_cart():
    local.cart = {}

# ==========================================
# 4. PYWEBIO VIEWS & INTERFACE
# ==========================================

def render_header():
    user = get_current_user()
    curr = getattr(local, 'currency', 'DZD')
    
    user_info = f"👤 {user['username']} ({user['role']})" if user else "👤 Guest"
    cart_count = sum(get_cart().values())
    
    header_html = f"""
    <div style="background: #1A202C; color: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
        <div>
            <h2 style="margin: 0; color: #D69E2E;">Luxury Impact Parfum RZ</h2>
            <small style="color: #A0AEC0;">Haute Parfumerie Catalog</small>
        </div>
        <div>
            <span style="margin-right: 15px;">{user_info}</span>
            <span style="background: #2B6CB0; padding: 5px 10px; border-radius: 4px;">🛒 Cart: {cart_count}</span>
        </div>
    </div>
    """
    put_html(header_html)
    
    btn_group = [
        {"label": "🏠 Storefront", "value": "store"},
        {"label": f"💱 Currency [{curr}]", "value": "currency"},
        {"label": "🛒 View Cart", "value": "cart"}
    ]
    
    if user:
        btn_group.append({"label": "📦 My Orders", "value": "my_orders"})
        if user['role'] == 'admin':
            btn_group.append({"label": "⚙️ Admin Dashboard", "value": "admin"})
            btn_group.append({"label": "➕ Add Product", "value": "add_product"})
            btn_group.append({"label": "📝 Manage Products", "value": "manage_products"})
        btn_group.append({"label": "🚪 Logout", "value": "logout", "color": "danger"})
    else:
        btn_group.append({"label": "🔑 Login", "value": "login", "color": "success"})
        btn_group.append({"label": "📝 Register", "value": "register", "color": "primary"})
        
    put_buttons(btn_group, onclick=handle_nav)

def handle_nav(action):
    if action == "store":
        storefront_page()
    elif action == "currency":
        select_currency_popup()
    elif action == "cart":
        view_cart_page()
    elif action == "my_orders":
        my_orders_page()
    elif action == "admin":
        admin_dashboard_page()
    elif action == "add_product":
        add_product_page()
    elif action == "manage_products":
        list_products_page()
    elif action == "login":
        login_page()
    elif action == "register":
        register_page()
    elif action == "logout":
        set_current_user(None)
        clear_cart()
        toast("Logged out successfully.", color="info")
        storefront_page()

def select_currency_popup():
    curr = select("Select Preferred Currency", options=["DZD", "EUR", "USD"], value=getattr(local, 'currency', 'DZD'))
    local.currency = curr
    toast(f"Currency updated to {curr}")
    storefront_page()

def register_page():
    clear()
    render_header()
    put_markdown("### 📝 Register New Account")
    
    data = input_group("Account Registration", [
        input("Username", name="username", required=True),
        input("Password", name="password", type="password", required=True),
        input("Confirm Password", name="confirm_password", type="password", required=True)
    ])
    
    if data['password'] != data['confirm_password']:
        toast("Passwords do not match!", color="error")
        return register_page()
        
    with get_db_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'customer')",
                (data['username'].strip(), generate_password_hash(data['password']))
            )
            conn.commit()
            toast("Account created successfully! Please login.", color="success")
            login_page()
        except sqlite3.IntegrityError:
            toast("Username already exists.", color="error")
            register_page()

def login_page():
    clear()
    render_header()
    put_markdown("### 🔑 User Login")
    
    data = input_group("Sign In", [
        input("Username", name="username", required=True),
        input("Password", name="password", type="password", required=True)
    ])
    
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (data['username'].strip(),)).fetchone()
        
        if user and check_password_hash(user['password_hash'], data['password']):
            set_current_user(dict(user))
            toast(f"Welcome back, {user['username']}!", color="success")
            storefront_page()
        else:
            toast("Invalid username or password.", color="error")
            login_page()

def storefront_page():
    clear()
    restore_session_from_browser()
    render_header()
    
    active_curr = getattr(local, 'currency', 'DZD')
    
    with get_db_connection() as conn:
        products = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
        
    if not products:
        put_markdown("_No fragrances available in the catalog yet._")
        return

    cards = []
    for p in products:
        display_price = convert_currency(p['price'], p['currency'], active_curr)
        
        qr_uri = generate_qr_svg_data_uri(
            f"Luxury Impact Parfum RZ\nProduct: {p['name']}\nCategory: {p['category']}\nPrice: {display_price} {active_curr}"
        )
        
        img_src = f"/uploads/{Path(p['image_path']).name}" if p['image_path'] and os.path.exists(p['image_path']) else None
        
        card_content = [
            put_html(f"<h3 style='color: #2B6CB0; margin-bottom: 5px;'>{p['name']}</h3>"),
            put_html(f"<b>Category:</b> {p['category'] or 'N/A'}<br>"),
            put_html(f"<h4 style='color: #D69E2E;'>Price: {display_price:.2f} {active_curr}</h4>"),
            put_markdown(f"**Notes:**\n* Top: {p['top_notes'] or 'N/A'}\n* Heart: {p['heart_notes'] or 'N/A'}\n* Base: {p['base_notes'] or 'N/A'}"),
            put_html(f"<details><summary>View QR Accord</summary><img src='{qr_uri}' width='120'/></details>"),
            put_buttons([{"label": "🛒 Add to Cart", "value": p['id']}], onclick=lambda pid=p['id']: add_to_cart(pid))
        ]
        
        if img_src:
            card_content.insert(1, put_image(open(p['image_path'], 'rb').read(), width='100%'))
            
        cards.append(put_column(card_content).style("border: 1px solid #E2E8F0; padding: 15px; border-radius: 8px; background: white;"))

    # Render catalog grid
    put_grid([[cards[i] if i < len(cards) else put_text("") for i in range(j, j + 3)] for j in range(0, len(cards), 3)], cell_width="1fr", cell_height="auto")

def add_to_cart(product_id):
    cart = get_cart()
    cart[product_id] = cart.get(product_id, 0) + 1
    toast("Item added to cart!", color="success")
    storefront_page()

def view_cart_page():
    clear()
    render_header()
    put_markdown("### 🛒 Shopping Cart")
    
    cart = get_cart()
    if not cart:
        put_text("Your cart is empty.")
        return
        
    active_curr = getattr(local, 'currency', 'DZD')
    table_rows = []
    grand_total = 0.0
    
    with get_db_connection() as conn:
        for pid, qty in list(cart.items()):
            p = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
            if p:
                unit_p = convert_currency(p['price'], p['currency'], active_curr)
                subtotal = unit_p * qty
                grand_total += subtotal
                table_rows.append([
                    p['name'],
                    f"{unit_p:.2f} {active_curr}",
                    qty,
                    f"{subtotal:.2f} {active_curr}",
                    put_buttons([{"label": "❌ Remove", "value": pid, "color": "danger"}], onclick=lambda x=pid: remove_from_cart(x))
                ])

    put_table([["Product", "Unit Price", "Quantity", "Subtotal", "Action"]] + table_rows)
    put_markdown(f"### Grand Total: **{grand_total:.2f} {active_curr}**")
    
    put_buttons([
        {"label": "✅ Checkout & Place Order", "value": "checkout", "color": "success"},
        {"label": "🗑️ Clear Cart", "value": "clear", "color": "warning"}
    ], onclick=handle_cart_action)

def remove_from_cart(product_id):
    cart = get_cart()
    if product_id in cart:
        del cart[product_id]
        toast("Item removed.")
    view_cart_page()

def handle_cart_action(action):
    if action == "clear":
        clear_cart()
        view_cart_page()
    elif action == "checkout":
        user = get_current_user()
        if not user:
            toast("You must login prior to completing checkout.", color="error")
            login_page()
            return
            
        cart = get_cart()
        if not cart:
            return
            
        active_curr = getattr(local, 'currency', 'DZD')
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            grand_total = 0.0
            order_items_data = []
            
            for pid, qty in cart.items():
                p = cursor.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
                if p:
                    unit_p = convert_currency(p['price'], p['currency'], active_curr)
                    grand_total += unit_p * qty
                    order_items_data.append((pid, qty, unit_p))
            
            cursor.execute(
                "INSERT INTO orders (user_id, total_amount, currency) VALUES (?, ?, ?)",
                (user['id'], grand_total, active_curr)
            )
            order_id = cursor.lastrowid
            
            for item in order_items_data:
                cursor.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
                    (order_id, item[0], item[1], item[2])
                )
            
            conn.commit()
            
        clear_cart()
        toast("Order placed successfully!", color="success")
        my_orders_page()

def my_orders_page():
    clear()
    render_header()
    user = get_current_user()
    if not user:
        login_page()
        return
        
    put_markdown("### 📦 Order History")
    
    with get_db_connection() as conn:
        orders = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", 
            (user['id'],)
        ).fetchall()
        
    if not orders:
        put_text("No previous orders found.")
        return
        
    rows = []
    for o in orders:
        rows.append([
            f"INV-{o['id']:05d}",
            o['created_at'],
            f"{o['total_amount']:.2f} {o['currency']}",
            o['status'].upper(),
            put_buttons([{"label": "📄 Download Invoice", "value": o['id']}], onclick=lambda oid=o['id']: download_invoice(oid))
        ])
        
    put_table([["Order ID", "Date", "Total", "Status", "Action"]] + rows)

def download_invoice(order_id):
    pdf_bytes = generate_pdf_invoice_bytes(order_id)
    send_file(f"Invoice_INV-{order_id:05d}.pdf", pdf_bytes, mimetype="application/pdf")

def add_product_page():
    clear()
    render_header()
    user = get_current_user()
    if not user or user['role'] != 'admin':
        toast("Unauthorized access.", color="error")
        storefront_page()
        return
        
    put_markdown("### ➕ Add New Fragrance")
    
    data = input_group("Fragrance Specifications", [
        input("Name", name="name", required=True),
        input("Category", name="category", placeholder="e.g. Eau de Parfum, Oriental"),
        input("Base Price", name="price", type=FLOAT, required=True),
        select("Base Currency", options=["DZD", "EUR", "USD"], name="currency"),
        input("Top Notes", name="top_notes"),
        input("Heart Notes", name="heart_notes"),
        input("Base Notes", name="base_notes"),
        textarea("Description", name="description"),
        file_upload("Product Image", name="image", accept="image/*")
    ])
    
    image_path = None
    if data['image']:
        img_name = f"{data['name'].replace(' ', '_')}_{data['image']['filename']}"
        saved_file = UPLOAD_DIR / img_name
        with open(saved_file, 'wb') as f:
            f.write(data['image']['content'])
        image_path = str(saved_file)
        
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO products (name, description, price, currency, category, top_notes, heart_notes, base_notes, image_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['name'].strip(), data['description'], float(data['price']),
            data['currency'], data['category'], data['top_notes'],
            data['heart_notes'], data['base_notes'], image_path
        ))
        conn.commit()
        
    toast("Fragrance added to catalog!", color="success")
    storefront_page()

def list_products_page():
    clear()
    render_header()
    user = get_current_user()
    if not user or user['role'] != 'admin':
        toast("Unauthorized access.", color="error")
        storefront_page()
        return
        
    put_markdown("### 📝 Catalog Management")
    
    with get_db_connection() as conn:
        products = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
        
    rows = []
    for p in products:
        rows.append([
            p['id'],
            p['name'],
            f"{p['price']} {p['currency']}",
            p['category'] or "N/A",
            put_buttons([
                {"label": "✏️ Edit", "value": ("edit", p['id']), "color": "warning"},
                {"label": "❌ Delete", "value": ("delete", p['id']), "color": "danger"}
            ], onclick=handle_product_action)
        ])
        
    put_table([["ID", "Name", "Price", "Category", "Actions"]] + rows)

def handle_product_action(action_tuple):
    act, pid = action_tuple
    if act == "delete":
        with get_db_connection() as conn:
            conn.execute("DELETE FROM products WHERE id = ?", (pid,))
            conn.commit()
        toast("Product deleted.", color="info")
        list_products_page()
    elif act == "edit":
        edit_product_page(pid)

def edit_product_page(product_id):
    clear()
    render_header()
    
    with get_db_connection() as conn:
        p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        
    if not p:
        toast("Product not found.", color="error")
        list_products_page()
        return
        
    data = input_group("Edit Fragrance", [
        input("Name", name="name", value=p['name'], required=True),
        input("Category", name="category", value=p['category']),
        input("Base Price", name="price", type=FLOAT, value=p['price'], required=True),
        select("Base Currency", options=["DZD", "EUR", "USD"], value=p['currency'], name="currency"),
        input("Top Notes", name="top_notes", value=p['top_notes']),
        input("Heart Notes", name="heart_notes", value=p['heart_notes']),
        input("Base Notes", name="base_notes", value=p['base_notes']),
        textarea("Description", name="description", value=p['description']),
        file_upload("Replace Product Image (Optional)", name="image", accept="image/*")
    ])
    
    image_path = p['image_path']
    if data['image']:
        img_name = f"{data['name'].replace(' ', '_')}_{data['image']['filename']}"
        saved_file = UPLOAD_DIR / img_name
        with open(saved_file, 'wb') as f:
            f.write(data['image']['content'])
        image_path = str(saved_file)
        
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE products SET name = ?, description = ?, price = ?, currency = ?, category = ?, 
            top_notes = ?, heart_notes = ?, base_notes = ?, image_path = ? WHERE id = ?
        """, (
            data['name'].strip(), data['description'], float(data['price']),
            data['currency'], data['category'], data['top_notes'],
            data['heart_notes'], data['base_notes'], image_path, product_id
        ))
        conn.commit()
        
    toast("Product updated successfully!", color="success")
    list_products_page()

def admin_dashboard_page():
    clear()
    render_header()
    user = get_current_user()
    if not user or user['role'] != 'admin':
        toast("Unauthorized access.", color="error")
        storefront_page()
        return
        
    put_markdown("### ⚙️ Administrative Dashboard")
    
    with get_db_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        recent_orders = conn.execute("""
            SELECT o.*, u.username FROM orders o 
            JOIN users u ON o.user_id = u.id 
            ORDER BY o.created_at DESC LIMIT 5
        """).fetchall()

    put_row([
        put_column([put_html("<h4>Total Users</h4>"), put_html(f"<h2>{total_users}</h2>")]).style("border:1px solid #CBD5E0; padding:15px; border-radius:5px; text-align:center;"),
        put_column([put_html("<h4>Products</h4>"), put_html(f"<h2>{total_products}</h2>")]).style("border:1px solid #CBD5E0; padding:15px; border-radius:5px; text-align:center;"),
        put_column([put_html("<h4>Total Orders</h4>"), put_html(f"<h2>{total_orders}</h2>")]).style("border:1px solid #CBD5E0; padding:15px; border-radius:5px; text-align:center;")
    ])
    
    put_markdown("#### Recent System Orders")
    order_rows = []
    for ro in recent_orders:
        order_rows.append([
            f"INV-{ro['id']:05d}",
            ro['username'],
            f"{ro['total_amount']:.2f} {ro['currency']}",
            ro['status'],
            ro['created_at']
        ])
    put_table([["Order ID", "Customer", "Amount", "Status", "Date"]] + order_rows)

def main():
    config(title="Luxury Impact Parfum RZ", theme="mint")
    restore_session_from_browser()
    storefront_page()

# ==========================================
# 5. FLASK APPARATUS & ENDPOINTS
# ==========================================

app = Flask(__name__)

@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_file(UPLOAD_DIR / filename)

@app.route('/admin/export-db/<secret_key>', methods=['GET'])
def export_database(secret_key):
    if secret_key != DB_EXPORT_SECRET:
        logging.warning("Unauthorized database export attempt.")
        return jsonify({"error": "Unauthorized access"}), 403
    
    if DB_NAME.exists():
        return send_file(
            DB_NAME,
            as_attachment=True,
            download_name="perfume_shop_export.db",
            mimetype="application/x-sqlite3"
        )
    return jsonify({"error": "Database file unavailable"}), 404

# Integrate PyWebIO with Flask backend
app.add_url_rule('/', 'webio_game', start_server(main, debug=True, standalone=False), methods=['GET', 'POST', 'OPTIONS'])

if __name__ == '__main__':
    logging.info("Starting Luxury Impact Parfum RZ Server on http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, debug=True)
