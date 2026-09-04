import sqlite3
import os
import sys
import hashlib
from io import BytesIO
from datetime import datetime

# PyWebIO Imports
import pywebio
from pywebio import start_server
from pywebio.input import input, select, input_group, ACTIONS, TEXT, NUMBER, PASSWORD
from pywebio.output import (
    put_text, put_markdown, put_table, put_buttons, put_button,
    put_html, put_code, popup, close_popup, clear, put_success,
    put_warning, put_error, put_loading, put_image
)
from pywebio.session import run_js, set_env

# ReportLab Imports for PDF Generation
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# QR Code Generation
import qrcode

# ------------------------------------------------------------------------------
# DATABASE INITIALIZATION & CONFIGURATION
# ------------------------------------------------------------------------------
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store.db")

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    ''')
    
    # Perfumes / Inventory table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS perfumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            top_notes TEXT,
            heart_notes TEXT,
            base_notes TEXT,
            stock_qty INTEGER DEFAULT 0,
            purchase_price REAL DEFAULT 0.0,
            selling_price REAL DEFAULT 0.0,
            min_stock_alert INTEGER DEFAULT 5
        )
    ''')
    
    # Sales transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            perfume_id INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            total_price REAL NOT NULL,
            sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seller_username TEXT,
            FOREIGN KEY (perfume_id) REFERENCES perfumes (id)
        )
    ''')
    
    # Expenses table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT
        )
    ''')
    
    # Create default admin if no users exist
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        admin_pass_hash = hashlib.sha256("admin123".encode()).hexdigest()
        cursor.execute(
            'INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
            ('admin', admin_pass_hash, 'Admin')
        )
    
    conn.commit()
    conn.close()

# Auto-initialize database on import/boot
init_db()

# ------------------------------------------------------------------------------
# AUTHENTICATION & SESSION MANAGEMENT
# ------------------------------------------------------------------------------
CURRENT_USER = {"username": None, "role": None}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_user(username, password):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT username, password, role FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    
    if user and user['password'] == hash_password(password):
        return {"username": user['username'], "role": user['role']}
    return None

def login_page():
    clear()
    set_env(title="Luxury Impact Parfume Rz - Login")
    
    put_markdown("# 💎 Luxury Impact Parfume Rz")
    put_markdown("### Management Portal Authentication")
    
    data = input_group("Login", [
        input("Username", name="username", type=TEXT, required=True),
        input("Password", name="password", type=PASSWORD, required=True)
    ])
    
    user = verify_user(data['username'], data['password'])
    if user:
        CURRENT_USER['username'] = user['username']
        CURRENT_USER['role'] = user['role']
        put_success(f"Welcome back, {user['username']}!")
        main_dashboard()
    else:
        put_error("Invalid username or password.")
        put_button("Try Again", onclick=login_page)

def logout():
    CURRENT_USER['username'] = None
    CURRENT_USER['role'] = None
    login_page()

# ------------------------------------------------------------------------------
# PERFUME & INVENTORY MANAGEMENT
# ------------------------------------------------------------------------------
def add_perfume_form():
    clear()
    put_markdown("## 🧪 Add New Perfume Entry")
    
    data = input_group("Perfume Details", [
        input("Perfume Name", name="name", type=TEXT, required=True),
        select("Category", options=["Oriental", "Woody", "Floral", "Fresh", "Citrus", "Gourmand"], name="category"),
        input("Top Notes (comma separated)", name="top_notes", type=TEXT),
        input("Heart Notes (comma separated)", name="heart_notes", type=TEXT),
        input("Base Notes (comma separated)", name="base_notes", type=TEXT),
        input("Initial Stock Quantity", name="stock_qty", type=NUMBER, value=10),
        input("Purchase Price (DZD)", name="purchase_price", type=NUMBER, value=1000.0),
        input("Selling Price (DZD)", name="selling_price", type=NUMBER, value=2000.0),
        input("Minimum Stock Alert Threshold", name="min_stock_alert", type=NUMBER, value=5)
    ])
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO perfumes (name, category, top_notes, heart_notes, base_notes, stock_qty, purchase_price, selling_price, min_stock_alert)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['name'], data['category'], data['top_notes'],
            data['heart_notes'], data['base_notes'], int(data['stock_qty']),
            float(data['purchase_price']), float(data['selling_price']),
            int(data['min_stock_alert'])
        ))
        conn.commit()
        conn.close()
        put_success(f"Perfume '{data['name']}' added successfully!")
    except sqlite3.IntegrityError:
        put_error(f"Error: Perfume name '{data['name']}' already exists.")
    
    put_button("Back to Inventory", onclick=inventory_view)

def inventory_view():
    clear()
    put_markdown("## 📦 Inventory Catalog & Stock Control")
    
    put_buttons([
        {"label": "+ Add New Perfume", "value": "add", "color": "success"},
        {"label": "📊 Main Menu", "value": "menu", "color": "secondary"}
    ], onclick=lambda btn: add_perfume_form() if btn == "add" else main_dashboard())
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM perfumes ORDER BY name ASC')
    perfumes = cursor.fetchall()
    conn.close()
    
    if not perfumes:
        put_info("No perfume items currently in stock.")
        return
        
    table_data = [["ID", "Name", "Category", "Accords / Notes", "Stock", "Purchase Price", "Selling Price", "Actions"]]
    
    for p in perfumes:
        notes = f"Top: {p['top_notes'] or 'N/A'} | Heart: {p['heart_notes'] or 'N/A'} | Base: {p['base_notes'] or 'N/A'}"
        stock_status = f"⚠️ {p['stock_qty']}" if p['stock_qty'] <= p['min_stock_alert'] else str(p['stock_qty'])
        
        actions = [
            put_button("QR Code", onclick=lambda pid=p['id']: generate_perfume_qr(pid), color="info"),
            put_button("Edit", onclick=lambda pid=p['id']: edit_perfume(pid), color="warning"),
            put_button("Delete", onclick=lambda pid=p['id']: delete_perfume(pid), color="danger")
        ]
        
        table_data.append([
            p['id'], p['name'], p['category'], notes,
            stock_status, f"{p['purchase_price']:.2f} DZD", f"{p['selling_price']:.2f} DZD",
            actions
        ])
        
    put_table(table_data)

def edit_perfume(perfume_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM perfumes WHERE id = ?', (perfume_id,))
    p = cursor.fetchone()
    conn.close()
    
    if not p:
        put_error("Perfume record not found.")
        return

    clear()
    put_markdown(f"## ✏️ Edit Perfume: {p['name']}")
    
    data = input_group("Update Details", [
        input("Perfume Name", name="name", type=TEXT, value=p['name'], required=True),
        select("Category", options=["Oriental", "Woody", "Floral", "Fresh", "Citrus", "Gourmand"], name="category", value=p['category']),
        input("Top Notes", name="top_notes", type=TEXT, value=p['top_notes']),
        input("Heart Notes", name="heart_notes", type=TEXT, value=p['heart_notes']),
        input("Base Notes", name="base_notes", type=TEXT, value=p['base_notes']),
        input("Stock Quantity", name="stock_qty", type=NUMBER, value=p['stock_qty']),
        input("Purchase Price (DZD)", name="purchase_price", type=NUMBER, value=p['purchase_price']),
        input("Selling Price (DZD)", name="selling_price", type=NUMBER, value=p['selling_price']),
        input("Min Stock Alert", name="min_stock_alert", type=NUMBER, value=p['min_stock_alert'])
    ])
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE perfumes
        SET name=?, category=?, top_notes=?, heart_notes=?, base_notes=?, stock_qty=?, purchase_price=?, selling_price=?, min_stock_alert=?
        WHERE id=?
    ''', (
        data['name'], data['category'], data['top_notes'], data['heart_notes'],
        data['base_notes'], int(data['stock_qty']), float(data['purchase_price']),
        float(data['selling_price']), int(data['min_stock_alert']), perfume_id
    ))
    conn.commit()
    conn.close()
    
    put_success("Perfume updated successfully!")
    inventory_view()

def delete_perfume(perfume_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM perfumes WHERE id = ?', (perfume_id,))
    conn.commit()
    conn.close()
    put_success("Item deleted.")
    inventory_view()

# ------------------------------------------------------------------------------
# QR CODE GENERATION & DISCOVERY TOOL
# ------------------------------------------------------------------------------
def generate_perfume_qr(perfume_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM perfumes WHERE id = ?', (perfume_id,))
    p = cursor.fetchone()
    conn.close()
    
    if not p:
        return
        
    qr_data = (
        f"Luxury Impact Parfume Rz\n"
        f"Fragrance: {p['name']}\n"
        f"Category: {p['category']}\n"
        f"Top Notes: {p['top_notes']}\n"
        f"Heart Notes: {p['heart_notes']}\n"
        f"Base Notes: {p['base_notes']}\n"
        f"Price: {p['selling_price']} DZD"
    )
    
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    
    popup(f"QR Code - {p['name']}", [
        put_html(f"<h3>{p['name']} Accord Specifications</h3>"),
        put_image(img_bytes, title=f"{p['name']} QR Code"),
        put_markdown(f"**Category:** {p['category']}"),
        put_markdown(f"**Top:** {p['top_notes']} | **Heart:** {p['heart_notes']} | **Base:** {p['base_notes']}"),
        put_button("Close", onclick=close_popup)
    ])

# ------------------------------------------------------------------------------
# POS / SALES MANAGEMENT
# ------------------------------------------------------------------------------
def process_sale_view():
    clear()
    put_markdown("## 🛒 Point of Sale & Terminal")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, selling_price, stock_qty FROM perfumes WHERE stock_qty > 0 ORDER BY name ASC')
    items = cursor.fetchall()
    conn.close()
    
    if not items:
        put_warning("No items in stock available for sale!")
        put_button("Back to Dashboard", onclick=main_dashboard)
        return
        
    item_options = [{"label": f"{i['name']} (In Stock: {i['stock_qty']}) - {i['selling_price']} DZD", "value": i['id']} for i in items]
    
    sale_data = input_group("New Transaction", [
        select("Select Perfume", options=item_options, name="perfume_id"),
        input("Quantity", name="qty", type=NUMBER, value=1, min=1)
    ])
    
    perfume_id = sale_data['perfume_id']
    qty = int(sale_data['qty'])
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM perfumes WHERE id = ?', (perfume_id,))
    perfume = cursor.fetchone()
    
    if qty > perfume['stock_qty']:
        put_error(f"Insufficient stock! Requested: {qty}, Available: {perfume['stock_qty']}")
        conn.close()
        put_button("Retry Transaction", onclick=process_sale_view)
        return
        
    unit_price = perfume['selling_price']
    total_price = unit_price * qty
    new_stock = perfume['stock_qty'] - qty
    
    cursor.execute('UPDATE perfumes SET stock_qty = ? WHERE id = ?', (new_stock, perfume_id))
    cursor.execute('''
        INSERT INTO sales (perfume_id, qty, unit_price, total_price, seller_username)
        VALUES (?, ?, ?, ?, ?)
    ''', (perfume_id, qty, unit_price, total_price, CURRENT_USER['username']))
    
    conn.commit()
    conn.close()
    
    put_success(f"Sale Processed! Total: {total_price:.2f} DZD")
    
    put_buttons([
        {"label": "📄 Print PDF Invoice", "value": "pdf", "color": "primary"},
        {"label": "New Sale", "value": "new", "color": "success"},
        {"label": "Main Dashboard", "value": "dashboard", "color": "secondary"}
    ], onclick=lambda btn: generate_sales_pdf(perfume['name'], qty, unit_price, total_price) if btn == "pdf" else (process_sale_view() if btn == "new" else main_dashboard()))

# ------------------------------------------------------------------------------
# FINANCIALS, EXPENSES & REPORTING
# ------------------------------------------------------------------------------
def add_expense():
    clear()
    put_markdown("## 💸 Record Operating Expense")
    
    data = input_group("Expense Log", [
        input("Expense Description / Title", name="description", type=TEXT, required=True),
        input("Amount (DZD)", name="amount", type=NUMBER, required=True)
    ])
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO expenses (description, amount, created_by)
        VALUES (?, ?, ?)
    ''', (data['description'], float(data['amount']), CURRENT_USER['username']))
    conn.commit()
    conn.close()
    
    put_success("Expense successfully recorded.")
    financial_overview()

def financial_overview():
    clear()
    put_markdown("## 📈 Financial Statements & Performance Analysis")
    
    put_buttons([
        {"label": "+ Add Expense", "value": "exp", "color": "warning"},
        {"label": "📊 Main Menu", "value": "menu", "color": "secondary"}
    ], onclick=lambda btn: add_expense() if btn == "exp" else main_dashboard())
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Calculate Total Revenue
    cursor.execute('SELECT SUM(total_price) FROM sales')
    total_revenue = cursor.fetchone()[0] or 0.0
    
    # Calculate Total Expenses
    cursor.execute('SELECT SUM(amount) FROM expenses')
    total_expenses = cursor.fetchone()[0] or 0.0
    
    # Calculate Inventory COGS Valuation
    cursor.execute('SELECT SUM(stock_qty * purchase_price) FROM perfumes')
    inventory_cogs = cursor.fetchone()[0] or 0.0
    
    net_profit = total_revenue - total_expenses
    
    conn.close()
    
    put_html(f"""
    <div style="display: flex; gap: 20px; margin-top: 20px;">
        <div style="background-color: #28a745; color: white; padding: 20px; border-radius: 8px; flex: 1;">
            <h3>Total Sales Revenue</h3>
            <h2>{total_revenue:,.2f} DZD</h2>
        </div>
        <div style="background-color: #dc3545; color: white; padding: 20px; border-radius: 8px; flex: 1;">
            <h3>Operating Expenses</h3>
            <h2>{total_expenses:,.2f} DZD</h2>
        </div>
        <div style="background-color: #17a2b8; color: white; padding: 20px; border-radius: 8px; flex: 1;">
            <h3>Net Profit Balance</h3>
            <h2>{net_profit:,.2f} DZD</h2>
        </div>
        <div style="background-color: #6c757d; color: white; padding: 20px; border-radius: 8px; flex: 1;">
            <h3>Stock Asset Valuation</h3>
            <h2>{inventory_cogs:,.2f} DZD</h2>
        </div>
    </div>
    """)

# ------------------------------------------------------------------------------
# REPORTLAB PDF GENERATION ENGINE
# ------------------------------------------------------------------------------
def generate_sales_pdf(item_name, qty, unit_price, total_price):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    
    story = []
    
    # PDF Header
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor("#1A1A1A"),
        spaceAfter=12
    )
    story.append(Paragraph("LUXURY IMPACT PARFUME RZ", title_style))
    story.append(Paragraph(f"Official Transaction Receipt - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 20))
    
    # Invoice Data Table
    table_data = [
        ["Description / Item", "Quantity", "Unit Price", "Total Amount"],
        [item_name, str(qty), f"{unit_price:.2f} DZD", f"{total_price:.2f} DZD"]
    ]
    
    pdf_table = Table(table_data, colWidths=[200, 80, 100, 120])
    pdf_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#333333")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
    ]))
    
    story.append(pdf_table)
    story.append(Spacer(1, 30))
    story.append(Paragraph("Thank you for choosing Luxury Impact Parfume Rz!", styles['Italic']))
    
    doc.build(story)
    pdf_data = buffer.getvalue()
    
    import pywebio.output as pyo
    pyo.download("receipt.pdf", pdf_data)
    put_success("PDF Receipt Generated and Download triggered!")

# ------------------------------------------------------------------------------
# MAIN DASHBOARD / WORKFLOW ROUTER
# ------------------------------------------------------------------------------
def main_dashboard():
    clear()
    set_env(title="Luxury Impact Parfume Rz - Operational Control")
    
    put_markdown(f"# 💎 Luxury Impact Parfume Rz Portal")
    put_markdown(f"**Active Session:** `{CURRENT_USER['username']}` | **Role:** `{CURRENT_USER['role']}`")
    
    put_buttons([
        {"label": "📦 Inventory & Fragrances", "value": "inventory", "color": "primary"},
        {"label": "🛒 Point of Sale (POS)", "value": "pos", "color": "success"},
        {"label": "📈 Financial Reports", "value": "finance", "color": "info"},
        {"label": "🔒 Logout", "value": "logout", "color": "danger"}
    ], onclick=handle_dashboard_action)

def handle_dashboard_action(action):
    if action == "inventory":
        inventory_view()
    elif action == "pos":
        process_sale_view()
    elif action == "finance":
        financial_overview()
    elif action == "logout":
        logout()

def auth_menu():
    if CURRENT_USER['username'] is None:
        login_page()
    else:
        main_dashboard()

# ------------------------------------------------------------------------------
# APPLICATION ENTRYPOINT FOR RENDER DEPLOYMENT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Ensure database is provisioned on boot
    init_db()
    
    # Read dynamic host port provided by Render environment
    port = int(os.environ.get("PORT", 8080))
    
    # Run natively through PyWebIO server engine to maintain session events/buttons
    start_server(auth_menu, port=port, host="0.0.0.0", debug=False)
