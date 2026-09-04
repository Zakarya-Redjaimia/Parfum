import sqlite3
import os
import sys
import time
import threading
import webbrowser
import hashlib
from io import BytesIO
from datetime import datetime

# Flask & PyWebIO Integration
from flask import Flask
from pywebio.platform.flask import webio_view

# PyWebIO UI Imports
import pywebio
from pywebio.input import input, select, input_group, TEXT, NUMBER, PASSWORD, file_upload
from pywebio.output import (
    put_text, put_markdown, put_table, put_buttons, put_button,
    put_html, popup, close_popup, clear, put_success, put_warning,
    put_error, put_info, put_image, download
)
from pywebio.session import set_env, local as session_local

# ReportLab Imports for PDF Generation
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# QR Code Generation
import qrcode

# ------------------------------------------------------------------------------
# ENVIRONMENT & PATH CONFIGURATION (RENDER-SAFE)
# ------------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", 8080))

# Store DB in persistent directory or /tmp for deployment compatibility
DATA_DIR = os.environ.get("RENDER_DATA_DIR", "/tmp")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.join(DATA_DIR, "store.db")

# ------------------------------------------------------------------------------
# DATABASE INITIALIZATION & THREAD-SAFE CONNECTIONS
# ------------------------------------------------------------------------------
def get_db():
    """Returns a thread-safe connection to SQLite with WAL mode enabled."""
    conn = sqlite3.connect(DB_NAME, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_db():
    """Initializes tables idempotently."""
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
    
    # Seed initial default admin user if table is empty
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        admin_pass_hash = hashlib.sha256("admin123".encode()).hexdigest()
        cursor.execute(
            'INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
            ('admin', admin_pass_hash, 'Admin')
        )
    
    conn.commit()
    conn.close()

# Auto-execute DB initialization at module load time
init_db()

# ------------------------------------------------------------------------------
# AUTHENTICATION & ACCOUNT REGISTRATION
# ------------------------------------------------------------------------------
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

def register_page():
    clear()
    set_env(title="Luxury Impact Parfume Rz - Register Account")
    
    put_markdown("# 💎 Luxury Impact Parfume Rz")
    put_markdown("### Create New Operational Account")
    
    data = input_group("User Registration", [
        input("Username", name="username", type=TEXT, required=True),
        input("Password", name="password", type=PASSWORD, required=True),
        select("Role", options=["Admin", "Manager", "Sales Specialist"], name="role")
    ])
    
    hashed_pass = hash_password(data['password'])
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
            (data['username'].strip(), hashed_pass, data['role'])
        )
        conn.commit()
        conn.close()
        put_success(f"Account for '{data['username']}' created successfully!")
        
        # If logged in, go back to main menu, else go to login page
        if getattr(session_local, 'username', None):
            put_button("Back to Dashboard", onclick=main_menu)
        else:
            put_button("Go to Login", onclick=login_page)
            
    except sqlite3.IntegrityError:
        put_error("Username already exists. Please choose a different name.")
        put_buttons([
            {"label": "Try Again", "value": "retry", "color": "warning"},
            {"label": "Back to Login", "value": "login", "color": "secondary"}
        ], onclick=lambda btn: register_page() if btn == "retry" else login_page())

def login_page():
    clear()
    set_env(title="Luxury Impact Parfume Rz - Login")
    
    put_markdown("# 💎 Luxury Impact Parfume Rz")
    put_markdown("### Management Portal Authentication")
    
    data = input_group("Login Credentials", [
        input("Username", name="username", type=TEXT, required=True),
        input("Password", name="password", type=PASSWORD, required=True)
    ])
    
    user = verify_user(data['username'], data['password'])
    if user:
        session_local.username = user['username']
        session_local.role = user['role']
        put_success(f"Welcome back, {user['username']}!")
        main_menu()
    else:
        put_error("Invalid username or password.")
        put_buttons([
            {"label": "Try Again", "value": "retry", "color": "primary"},
            {"label": "Create New Account", "value": "reg", "color": "success"}
        ], onclick=lambda btn: register_page() if btn == "reg" else login_page())

def logout():
    session_local.username = None
    session_local.role = None
    login_page()

# ------------------------------------------------------------------------------
# INVENTORY, PRODUCT UPLOAD & CATALOG CONTROL
# ------------------------------------------------------------------------------
def add_perfume_form():
    clear()
    put_markdown("## 🧪 Product Upload: New Fragrance Entry")
    
    data = input_group("Fragrance Specifications", [
        input("Perfume / Fragrance Name", name="name", type=TEXT, required=True),
        select("Olfactory Family / Category", options=["Oriental", "Woody", "Floral", "Fresh", "Citrus", "Gourmand", "Leather", "Chypre"], name="category"),
        input("Top Notes", name="top_notes", type=TEXT, placeholder="e.g. Bergamot, Saffron, Cardamom"),
        input("Heart Notes", name="heart_notes", type=TEXT, placeholder="e.g. Bulgarian Rose, Jasmine, Cedar"),
        input("Base Notes", name="base_notes", type=TEXT, placeholder="e.g. Ambergris, Oud, Vanilla, Musk"),
        input("Initial Stock Quantity", name="stock_qty", type=NUMBER, value=10),
        input("Purchase Unit Price (DZD)", name="purchase_price", type=NUMBER, value=1000.0),
        input("Selling Unit Price (DZD)", name="selling_price", type=NUMBER, value=2000.0),
        input("Minimum Stock Alert Threshold", name="min_stock_alert", type=NUMBER, value=5)
    ])
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO perfumes (name, category, top_notes, heart_notes, base_notes, stock_qty, purchase_price, selling_price, min_stock_alert)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['name'].strip(), data['category'], data['top_notes'],
            data['heart_notes'], data['base_notes'], int(data['stock_qty']),
            float(data['purchase_price']), float(data['selling_price']),
            int(data['min_stock_alert'])
        ))
        conn.commit()
        conn.close()
        put_success(f"Product '{data['name']}' uploaded to catalog successfully!")
    except sqlite3.IntegrityError:
        put_error(f"Error: Product with name '{data['name']}' already exists.")
    
    put_buttons([
        {"label": "+ Upload Another Product", "value": "add_more", "color": "success"},
        {"label": "📦 View Inventory Catalog", "value": "catalog", "color": "primary"},
        {"label": "📊 Main Menu", "value": "menu", "color": "secondary"}
    ], onclick=lambda btn: add_perfume_form() if btn == "add_more" else (inventory_view() if btn == "catalog" else main_menu()))

def inventory_view():
    clear()
    put_markdown("## 📦 Inventory Catalog & Fragrance Accords")
    
    put_buttons([
        {"label": "🧪 Upload New Product", "value": "add", "color": "success"},
        {"label": "📊 Main Dashboard", "value": "menu", "color": "secondary"}
    ], onclick=lambda btn: add_perfume_form() if btn == "add" else main_menu())
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM perfumes ORDER BY name ASC')
    perfumes = cursor.fetchall()
    conn.close()
    
    if not perfumes:
        put_info("No products currently in database catalog.")
        return
        
    table_data = [["ID", "Perfume Name", "Category", "Accord Pyramids", "Stock", "Purchase Price", "Selling Price", "Actions"]]
    
    for p in perfumes:
        notes = f"Top: {p['top_notes'] or 'N/A'}\nHeart: {p['heart_notes'] or 'N/A'}\nBase: {p['base_notes'] or 'N/A'}"
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
        put_error("Fragrance record not found.")
        return

    clear()
    put_markdown(f"## ✏️ Edit Product Entry: {p['name']}")
    
    data = input_group("Update Specifications", [
        input("Perfume Name", name="name", type=TEXT, value=p['name'], required=True),
        select("Category", options=["Oriental", "Woody", "Floral", "Fresh", "Citrus", "Gourmand", "Leather", "Chypre"], name="category", value=p['category']),
        input("Top Notes", name="top_notes", type=TEXT, value=p['top_notes']),
        input("Heart Notes", name="heart_notes", type=TEXT, value=p['heart_notes']),
        input("Base Notes", name="base_notes", type=TEXT, value=p['base_notes']),
        input("Stock Quantity", name="stock_qty", type=NUMBER, value=p['stock_qty']),
        input("Purchase Price (DZD)", name="purchase_price", type=NUMBER, value=p['purchase_price']),
        input("Selling Price (DZD)", name="selling_price", type=NUMBER, value=p['selling_price']),
        input("Min Stock Alert Threshold", name="min_stock_alert", type=NUMBER, value=p['min_stock_alert'])
    ])
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE perfumes
        SET name=?, category=?, top_notes=?, heart_notes=?, base_notes=?, stock_qty=?, purchase_price=?, selling_price=?, min_stock_alert=?
        WHERE id=?
    ''', (
        data['name'].strip(), data['category'], data['top_notes'], data['heart_notes'],
        data['base_notes'], int(data['stock_qty']), float(data['purchase_price']),
        float(data['selling_price']), int(data['min_stock_alert']), perfume_id
    ))
    conn.commit()
    conn.close()
    
    put_success("Product record updated successfully!")
    inventory_view()

def delete_perfume(perfume_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM perfumes WHERE id = ?', (perfume_id,))
    conn.commit()
    conn.close()
    put_success("Product deleted from database.")
    inventory_view()

# ------------------------------------------------------------------------------
# DYNAMIC QR CODE GENERATOR
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
        f"Brand: Luxury Impact Parfume Rz\n"
        f"Name: {p['name']}\n"
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
    
    popup(f"QR Accord Tag - {p['name']}", [
        put_html(f"<h3>{p['name']} Accord Specifications</h3>"),
        put_image(img_bytes, title=f"{p['name']} QR Code"),
        put_markdown(f"**Category:** {p['category']}"),
        put_markdown(f"**Top Notes:** {p['top_notes']}"),
        put_markdown(f"**Heart Notes:** {p['heart_notes']}"),
        put_markdown(f"**Base Notes:** {p['base_notes']}"),
        put_button("Close", onclick=close_popup)
    ])

# ------------------------------------------------------------------------------
# POINT OF SALE (POS) TERMINAL
# ------------------------------------------------------------------------------
def process_sale_view():
    clear()
    put_markdown("## 🛒 Point of Sale Terminal")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, selling_price, stock_qty FROM perfumes WHERE stock_qty > 0 ORDER BY name ASC')
    items = cursor.fetchall()
    conn.close()
    
    if not items:
        put_warning("No available items in stock to process sale!")
        put_button("Back to Dashboard", onclick=main_menu)
        return
        
    item_options = [{"label": f"{i['name']} (Stock: {i['stock_qty']}) - {i['selling_price']} DZD", "value": i['id']} for i in items]
    
    sale_data = input_group("New Sale Entry", [
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
        put_error(f"Insufficient stock balance! Requested: {qty}, In Stock: {perfume['stock_qty']}")
        conn.close()
        put_button("Retry Transaction", onclick=process_sale_view)
        return
        
    unit_price = perfume['selling_price']
    total_price = unit_price * qty
    new_stock = perfume['stock_qty'] - qty
    
    current_username = getattr(session_local, 'username', 'admin')
    
    cursor.execute('UPDATE perfumes SET stock_qty = ? WHERE id = ?', (new_stock, perfume_id))
    cursor.execute('''
        INSERT INTO sales (perfume_id, qty, unit_price, total_price, seller_username)
        VALUES (?, ?, ?, ?, ?)
    ''', (perfume_id, qty, unit_price, total_price, current_username))
    
    conn.commit()
    conn.close()
    
    put_success(f"Sale Executed! Total Amount: {total_price:.2f} DZD")
    
    put_buttons([
        {"label": "📄 Export PDF Receipt", "value": "pdf", "color": "primary"},
        {"label": "New Sale", "value": "new", "color": "success"},
        {"label": "Main Dashboard", "value": "dashboard", "color": "secondary"}
    ], onclick=lambda btn: generate_sales_pdf(perfume['name'], qty, unit_price, total_price) if btn == "pdf" else (process_sale_view() if btn == "new" else main_menu()))

# ------------------------------------------------------------------------------
# FINANCIAL STATEMENTS & EXPENSE AUDITING
# ------------------------------------------------------------------------------
def add_expense():
    clear()
    put_markdown("## 💸 Log Operating Expense")
    
    data = input_group("Expense Audit Entry", [
        input("Expense Description", name="description", type=TEXT, required=True),
        input("Amount (DZD)", name="amount", type=NUMBER, required=True)
    ])
    
    current_username = getattr(session_local, 'username', 'admin')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO expenses (description, amount, created_by)
        VALUES (?, ?, ?)
    ''', (data['description'], float(data['amount']), current_username))
    conn.commit()
    conn.close()
    
    put_success("Expense item logged successfully.")
    financial_overview()

def financial_overview():
    clear()
    put_markdown("## 📈 Executive Financial Analysis & Ledger")
    
    put_buttons([
        {"label": "+ Add Operating Expense", "value": "exp", "color": "warning"},
        {"label": "📊 Main Menu", "value": "menu", "color": "secondary"}
    ], onclick=lambda btn: add_expense() if btn == "exp" else main_menu())
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT SUM(total_price) FROM sales')
    total_revenue = cursor.fetchone()[0] or 0.0
    
    cursor.execute('SELECT SUM(amount) FROM expenses')
    total_expenses = cursor.fetchone()[0] or 0.0
    
    cursor.execute('SELECT SUM(stock_qty * purchase_price) FROM perfumes')
    inventory_cogs = cursor.fetchone()[0] or 0.0
    
    net_profit = total_revenue - total_expenses
    conn.close()
    
    put_html(f"""
    <div style="display: flex; flex-wrap: wrap; gap: 15px; margin-top: 20px;">
        <div style="background-color: #28a745; color: white; padding: 20px; border-radius: 8px; flex: 1; min-width: 200px;">
            <h3>Total Sales Revenue</h3>
            <h2>{total_revenue:,.2f} DZD</h2>
        </div>
        <div style="background-color: #dc3545; color: white; padding: 20px; border-radius: 8px; flex: 1; min-width: 200px;">
            <h3>Total Operating Expenses</h3>
            <h2>{total_expenses:,.2f} DZD</h2>
        </div>
        <div style="background-color: #17a2b8; color: white; padding: 20px; border-radius: 8px; flex: 1; min-width: 200px;">
            <h3>Net Profit Balance</h3>
            <h2>{net_profit:,.2f} DZD</h2>
        </div>
        <div style="background-color: #6c757d; color: white; padding: 20px; border-radius: 8px; flex: 1; min-width: 200px;">
            <h3>Stock Valuation (COGS)</h3>
            <h2>{inventory_cogs:,.2f} DZD</h2>
        </div>
    </div>
    """)

# ------------------------------------------------------------------------------
# REPORTLAB PDF INVOICE & RECEIPT ENGINE
# ------------------------------------------------------------------------------
def generate_sales_pdf(item_name, qty, unit_price, total_price):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    
    story = []
    
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor("#1A1A1A"),
        spaceAfter=12
    )
    story.append(Paragraph("LUXURY IMPACT PARFUME RZ", title_style))
    story.append(Paragraph(f"Official Sales Receipt - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 20))
    
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
    
    download("receipt.pdf", pdf_data)
    put_success("PDF Receipt Generated and Downloaded!")

# ------------------------------------------------------------------------------
# MAIN SYSTEM ROUTER & NAVIGATION DASHBOARD
# ------------------------------------------------------------------------------
def main_menu():
    username = getattr(session_local, 'username', None)
    role = getattr(session_local, 'role', None)
    
    if not username:
        login_page()
        return

    clear()
    set_env(title="Luxury Impact Parfume Rz - Operational Portal")
    
    put_markdown("# 💎 Luxury Impact Parfume Rz Portal")
    put_markdown(f"**Active Session:** `{username}` | **Role:** `{role}`")
    
    put_buttons([
        {"label": "📦 Inventory Catalog", "value": "inventory", "color": "primary"},
        {"label": "🧪 Upload Product", "value": "upload", "color": "success"},
        {"label": "🛒 Point of Sale (POS)", "value": "pos", "color": "info"},
        {"label": "📈 Financial Reports", "value": "finance", "color": "warning"},
        {"label": "➕ Register User Account", "value": "register", "color": "secondary"},
        {"label": "🔒 Logout", "value": "logout", "color": "danger"}
    ], onclick=handle_dashboard_action)

def handle_dashboard_action(action):
    if action == "inventory":
        inventory_view()
    elif action == "upload":
        add_perfume_form()
    elif action == "pos":
        process_sale_view()
    elif action == "finance":
        financial_overview()
    elif action == "register":
        register_page()
    elif action == "logout":
        logout()

# ------------------------------------------------------------------------------
# FLASK APPLICATION & WSGI ROUTING (GUNICORN / RENDER)
# ------------------------------------------------------------------------------
app = Flask(__name__)

# Bind PyWebIO root URL handler
app.add_url_rule(
    '/',
    endpoint='webio_view',
    view_func=webio_view(main_menu),
    methods=['GET', 'POST', 'OPTIONS']
)

# Gunicorn Attribute Alias (Crucial for Gunicorn main:flask_app invocation)
flask_app = app

# Helper for local development testing
def open_browser():
    """Opens local browser when executed directly."""
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == '__main__':
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=True)
