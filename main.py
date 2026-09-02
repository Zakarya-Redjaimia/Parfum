import os
import sys
import json
import sqlite3
import datetime
import logging
from typing import Dict, Any, List, Optional, Tuple

# Third-party dependencies
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

import pywebio
from pywebio.input import input, select, textarea, checkbox, actions, input_group, NUMBER, PASSWORD, TEXT
from pywebio.output import (
    put_text, put_markdown, put_table, put_buttons, put_button,
    put_code, put_html, put_loading, put_row, put_column, clear, toast,
    popup, close_popup, use_scope, style
)
from pywebio.platform.flask import webio_view

# ==============================================================================
# SECTION 1: LOGGING & CONFIGURATION
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
)
logger = logging.getLogger("main_app")

DB_FILE = os.environ.get("DATABASE_URL", "app_database.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "default-dev-secret-key-change-in-prod")

# Initialize Flask Instance
flask_app = Flask(__name__)
flask_app.config["SECRET_KEY"] = SECRET_KEY

# ==============================================================================
# SECTION 2: DATABASE INITIALIZATION & ORM LAYER
# ==============================================================================

def get_db_connection():
    """Establish connection to SQLite database with Row factory."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes schema and seeds baseline data if empty."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Products / Catalog Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL,
            description TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Orders Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT DEFAULT 'Pending',
            items_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Audit Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            description TEXT NOT NULL,
            ip_address TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    
    # Seed Admin User if none exists
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        admin_pass = generate_password_hash("admin123")
        cursor.execute(
            "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("admin", "admin@system.local", admin_pass, "admin")
        )
        logger.info("Database initialized and default admin created.")
    
    # Seed Products if empty
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        sample_products = [
            ("SKU-1001", "Aromatic Essence Extract", "Ingredients", 45.00, 120, "High concentrated formulation oil."),
            ("SKU-1002", "Precision Digital Scale", "Hardware", 85.50, 30, "0.01g accuracy digital measuring scale."),
            ("SKU-1003", "Amber Glass Bottle 50ml", "Packaging", 2.50, 500, "UV-resistant glass container with dropper."),
            ("SKU-1004", "Stainless Steel Atomizer", "Packaging", 12.00, 200, "Fine mist spray nozzle set."),
            ("SKU-1005", "Automated Capper Tool", "Hardware", 340.00, 8, "Pneumatic bottle capping device.")
        ]
        cursor.executemany(
            "INSERT INTO products (sku, name, category, price, stock, description) VALUES (?, ?, ?, ?, ?, ?)",
            sample_products
        )
        conn.commit()
        logger.info("Database seeded with initial inventory items.")
        
    conn.close()

# Execute Database Initialization
init_db()

# ==============================================================================
# SECTION 3: DATABASE HELPER FUNCTIONS
# ==============================================================================

def log_event(event_type: str, description: str, ip_address: Optional[str] = None):
    """Inserts record into audit_logs table."""
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO audit_logs (event_type, description, ip_address) VALUES (?, ?, ?)",
            (event_type, description, ip_address or "Internal")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log event: {e}")

def db_fetch_all_products(category_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    if category_filter and category_filter != "All":
        rows = conn.execute("SELECT * FROM products WHERE category = ? ORDER BY id DESC", (category_filter,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_get_product(product_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def db_save_product(data: Dict[str, Any], product_id: Optional[int] = None) -> Tuple[bool, str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if product_id:
            cursor.execute("""
                UPDATE products 
                SET sku=?, name=?, category=?, price=?, stock=?, description=?, is_active=?
                WHERE id=?
            """, (data['sku'], data['name'], data['category'], data['price'], data['stock'], data['description'], data['is_active'], product_id))
            action = "updated"
        else:
            cursor.execute("""
                INSERT INTO products (sku, name, category, price, stock, description, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (data['sku'], data['name'], data['category'], data['price'], data['stock'], data['description'], data['is_active']))
            action = "created"
        conn.commit()
        conn.close()
        log_event("CATALOG_CHANGE", f"Product ID {product_id or cursor.lastrowid} {action}")
        return True, f"Product successfully {action}."
    except sqlite3.IntegrityError as e:
        conn.close()
        return False, f"Integrity error (e.g., duplicate SKU): {str(e)}"
    except Exception as e:
        conn.close()
        return False, f"Database error: {str(e)}"

def db_delete_product(product_id: int) -> bool:
    conn = get_db_connection()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    log_event("CATALOG_CHANGE", f"Product ID {product_id} deleted.")
    return True

def db_fetch_all_orders() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_create_order(customer_name: str, email: str, items: List[Dict[str, Any]]) -> Tuple[bool, str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    total_amount = 0.0
    for item in items:
        total_amount += item['price'] * item['quantity']
        
    order_num = f"ORD-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    try:
        cursor.execute("""
            INSERT INTO orders (order_number, customer_name, customer_email, total_amount, items_json)
            VALUES (?, ?, ?, ?, ?)
        """, (order_num, customer_name, email, total_amount, json.dumps(items)))
        
        # Deduct stock
        for item in items:
            cursor.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (item['quantity'], item['id']))
            
        conn.commit()
        conn.close()
        log_event("ORDER_CREATED", f"New Order {order_num} generated for {customer_name}")
        return True, order_num
    except Exception as e:
        conn.close()
        return False, str(e)

def db_fetch_logs() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_fetch_users() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT id, username, email, role, created_at FROM users ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_create_user(username: str, email: str, password: str, role: str = 'user') -> Tuple[bool, str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        pwd_hash = generate_password_hash(password)
        cursor.execute("INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                       (username, email, pwd_hash, role))
        conn.commit()
        conn.close()
        log_event("USER_MANAGEMENT", f"User account created: {username} ({role})")
        return True, "User account successfully created."
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Username or email already exists."
    except Exception as e:
        conn.close()
        return False, str(e)

# ==============================================================================
# SECTION 4: FLASK REST API ENDPOINTS
# ==============================================================================

@flask_app.route("/api/health", methods=["GET"])
def api_health_check():
    """Health status check endpoint for Render/uptime monitoring."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.datetime.now().isoformat(),
        "service": "Flask-PyWebIO Gateway"
    }), 200

@flask_app.route("/api/products", methods=["GET"])
def api_get_products():
    """REST endpoint to fetch products."""
    products = db_fetch_all_products()
    return jsonify({"success": True, "count": len(products), "data": products}), 200

@flask_app.route("/api/products/<int:pid>", methods=["GET"])
def api_get_product_by_id(pid: int):
    """REST endpoint for single product lookup."""
    product = db_get_product(pid)
    if not product:
        return jsonify({"success": False, "error": "Product not found"}), 404
    return jsonify({"success": True, "data": product}), 200

@flask_app.route("/api/orders", methods=["POST"])
def api_create_order():
    """REST endpoint to post a new order payload."""
    payload = request.get_json()
    if not payload or 'customer_name' not in payload or 'email' not in payload or 'items' not in payload:
        return jsonify({"success": False, "error": "Invalid payload format"}), 400
    
    success, result = db_create_order(payload['customer_name'], payload['email'], payload['items'])
    if success:
        return jsonify({"success": True, "order_number": result}), 201
    else:
        return jsonify({"success": False, "error": result}), 500

@flask_app.route("/api/logs", methods=["GET"])
def api_get_logs():
    """REST endpoint to access application audit history."""
    logs = db_fetch_logs()
    return jsonify({"success": True, "count": len(logs), "data": logs}), 200

# ==============================================================================
# SECTION 5: PYWEBIO UI COMPONENTS & VIEWS
# ==============================================================================

def ui_header_component():
    """Renders top navigation header across all PyWebIO views using HTML/Markdown."""
    put_html("""
        <div style="background-color: #1e293b; padding: 15px 25px; border-radius: 8px; margin-bottom: 20px; color: white; display: flex; justify-content: space-between; align-items: center;">
            <h2 style="margin:0; font-family: sans-serif;">Enterprise Portal</h2>
            <span style="font-size: 14px; background-color: #3b82f6; padding: 4px 12px; border-radius: 12px;">System Active</span>
        </div>
    """)

def render_dashboard_view():
    """Renders high-level inventory metrics and system status."""
    clear("main_content")
    with use_scope("main_content"):
        products = db_fetch_all_products()
        orders = db_fetch_all_orders()
        logs = db_fetch_logs()
        
        total_items = len(products)
        low_stock = len([p for p in products if p['stock'] < 10])
        total_revenue = sum(o['total_amount'] for o in orders)
        
        put_markdown("### Dashboard Analytics")
        
        # Stat cards
        put_row([
            put_column([
                put_html(f"<div style='border:1px solid #e2e8f0; padding:15px; border-radius:6px; text-align:center;'><h4>Total Products</h4><p style='font-size:24px; font-weight:bold; color:#2563eb;'>{total_items}</p></div>")
            ]),
            put_column([
                put_html(f"<div style='border:1px solid #e2e8f0; padding:15px; border-radius:6px; text-align:center;'><h4>Low Stock Warning</h4><p style='font-size:24px; font-weight:bold; color:#dc2626;'>{low_stock}</p></div>")
            ]),
            put_column([
                put_html(f"<div style='border:1px solid #e2e8f0; padding:15px; border-radius:6px; text-align:center;'><h4>Total Orders</h4><p style='font-size:24px; font-weight:bold; color:#16a34a;'>{len(orders)}</p></div>")
            ]),
            put_column([
                put_html(f"<div style='border:1px solid #e2e8f0; padding:15px; border-radius:6px; text-align:center;'><h4>Revenue</h4><p style='font-size:24px; font-weight:bold; color:#0d9488;'>${total_revenue:.2f}</p></div>")
            ])
        ], size="25% 25% 25% 25%")
        
        put_html("<br>")
        put_markdown("#### Recent Audit Activity")
        
        log_rows = []
        for log in logs[:8]:
            log_rows.append([log['id'], log['event_type'], log['description'], log['timestamp']])
            
        put_table(log_rows, header=["ID", "Event Type", "Description", "Timestamp"])

def render_catalog_view():
    """Renders inventory product management table with CRUD operations."""
    clear("main_content")
    with use_scope("main_content"):
        put_markdown("### Inventory & Catalog Management")
        
        col1 = put_button("Add New Product", onclick=lambda: show_product_form_popup(), color="success")
        put_row([col1], size="100%")
        put_html("<br>")
        
        products = db_fetch_all_products()
        
        table_data = []
        for p in products:
            actions_cell = put_buttons(
                [
                    {'label': 'Edit', 'value': f"edit_{p['id']}", 'color': 'warning'},
                    {'label': 'Delete', 'value': f"del_{p['id']}", 'color': 'danger'}
                ],
                onclick=lambda val, pid=p['id']: handle_catalog_action(val, pid)
            )
            
            status_tag = "Active" if p['is_active'] else "Inactive"
            table_data.append([
                p['id'],
                p['sku'],
                p['name'],
                p['category'],
                f"${p['price']:.2f}",
                p['stock'],
                status_tag,
                actions_cell
            ])
            
        put_table(table_data, header=["ID", "SKU", "Name", "Category", "Price", "Stock", "Status", "Actions"])

def handle_catalog_action(action_value: str, product_id: int):
    """Processes table button actions."""
    if action_value.startswith("edit_"):
        show_product_form_popup(product_id)
    elif action_value.startswith("del_"):
        confirm = actions(f"Confirm deletion of product ID {product_id}?", [
            {'label': 'Yes, Delete', 'value': True, 'color': 'danger'},
            {'label': 'Cancel', 'value': False, 'color': 'secondary'}
        ])
        if confirm:
            db_delete_product(product_id)
            toast("Product deleted successfully", color="info")
            render_catalog_view()

def show_product_form_popup(product_id: Optional[int] = None):
    """Displays modal form for creating or editing products."""
    existing_data = db_get_product(product_id) if product_id else {}
    
    def form_submission(data):
        close_popup()
        formatted_data = {
            'sku': data['sku'],
            'name': data['name'],
            'category': data['category'],
            'price': float(data['price']),
            'stock': int(data['stock']),
            'description': data['description'],
            'is_active': 1 if 'Active' in data['status'] else 0
        }
        success, msg = db_save_product(formatted_data, product_id)
        if success:
            toast(msg, color="success")
            render_catalog_view()
        else:
            toast(msg, color="error")

    popup("Product Configuration Form", [
        put_column([
            put_markdown(f"**{'Edit' if product_id else 'Create'} Product Record**"),
            put_button("Close", onclick=lambda: close_popup(), color="secondary")
        ])
    ])
    
    form_data = input_group("Enter Product Details", [
        input("SKU Code", name="sku", value=existing_data.get('sku', ''), required=True),
        input("Product Name", name="name", value=existing_data.get('name', ''), required=True),
        select("Category", name="category", options=["Ingredients", "Hardware", "Packaging", "General"], value=existing_data.get('category', 'General')),
        input("Price ($)", name="price", type=NUMBER, value=str(existing_data.get('price', 0.0)), required=True),
        input("Stock Quantity", name="stock", type=NUMBER, value=str(existing_data.get('stock', 0)), required=True),
        textarea("Description", name="description", value=existing_data.get('description', '')),
        checkbox("Status", name="status", options=["Active"], value=["Active"] if existing_data.get('is_active', 1) else [])
    ])
    
    form_submission(form_data)

def render_order_entry_view():
    """Renders order processing interface."""
    clear("main_content")
    with use_scope("main_content"):
        put_markdown("### Create New Customer Order")
        
        products = db_fetch_all_products()
        active_products = [p for p in products if p['is_active'] and p['stock'] > 0]
        
        if not active_products:
            put_text("No active products with available stock.")
            return

        order_form = input_group("Customer & Order Details", [
            input("Customer Name", name="cust_name", required=True),
            input("Customer Email", name="cust_email", required=True),
            select("Select Primary Item", name="product_id", options=[
                {'label': f"{p['name']} (${p['price']:.2f}) - Stock: {p['stock']}", 'value': p['id']} for p in active_products
            ]),
            input("Quantity", name="quantity", type=NUMBER, value="1", required=True)
        ])
        
        selected_prod = db_get_product(int(order_form['product_id']))
        qty = int(order_form['quantity'])
        
        if qty > selected_prod['stock']:
            toast("Selected quantity exceeds stock level!", color="error")
            return
            
        items = [{
            "id": selected_prod['id'],
            "name": selected_prod['name'],
            "price": selected_prod['price'],
            "quantity": qty
        }]
        
        success, result = db_create_order(order_form['cust_name'], order_form['cust_email'], items)
        if success:
            toast(f"Order created! Confirmation: {result}", color="success")
            render_orders_list_view()
        else:
            toast(f"Failed to process order: {result}", color="error")

def render_orders_list_view():
    """Renders table of past orders."""
    clear("main_content")
    with use_scope("main_content"):
        put_markdown("### Order History")
        
        orders = db_fetch_all_orders()
        table_rows = []
        
        for o in orders:
            items_summary = ""
            try:
                parsed = json.loads(o['items_json'])
                items_summary = ", ".join([f"{i['name']} (x{i['quantity']})" for i in parsed])
            except:
                items_summary = "Raw payload item"
                
            table_rows.append([
                o['id'],
                o['order_number'],
                o['customer_name'],
                o['customer_email'],
                f"${o['total_amount']:.2f}",
                o['status'],
                items_summary,
                o['created_at']
            ])
            
        put_table(table_rows, header=["ID", "Order #", "Customer", "Email", "Total", "Status", "Items", "Date"])

def render_system_logs_view():
    """Renders system audit history logs."""
    clear("main_content")
    with use_scope("main_content"):
        put_markdown("### System Audit Logs")
        logs = db_fetch_logs()
        
        rows = []
        for l in logs:
            rows.append([l['id'], l['event_type'], l['description'], l['ip_address'], l['timestamp']])
            
        put_table(rows, header=["Log ID", "Type", "Description", "IP Origin", "Timestamp"])

def render_users_view():
    """Renders user management view."""
    clear("main_content")
    with use_scope("main_content"):
        put_markdown("### User Management")
        put_button("Create New User", onclick=lambda: show_user_form_popup(), color="success")
        put_html("<br>")
        
        users = db_fetch_users()
        rows = [[u['id'], u['username'], u['email'], u['role'], u['created_at']] for u in users]
        put_table(rows, header=["User ID", "Username", "Email", "Role", "Created At"])

def show_user_form_popup():
    """Modal dialog for user creation."""
    popup("Create Account", [
        put_markdown("**New User Credentials**"),
        put_button("Close", onclick=lambda: close_popup(), color="secondary")
    ])
    
    data = input_group("User Details", [
        input("Username", name="username", required=True),
        input("Email", name="email", required=True),
        input("Password", name="password", type=PASSWORD, required=True),
        select("Role", name="role", options=["user", "admin", "manager"], value="user")
    ])
    
    close_popup()
    ok, msg = db_create_user(data['username'], data['email'], data['password'], data['role'])
    toast(msg, color="success" if ok else "error")
    render_users_view()

# ==============================================================================
# SECTION 6: PYWEBIO APPLICATION ROUTER & ENTRY POINT
# ==============================================================================

def pywebio_main_entry():
    """Main Web Application UI handler invoked by WSGI wrapper."""
    pywebio.config(title="Enterprise Control Panel", theme="default")
    ui_header_component()
    
    # Navigation Control Bar
    put_buttons(
        [
            {'label': 'Dashboard', 'value': 'dashboard', 'color': 'primary'},
            {'label': 'Catalog Management', 'value': 'catalog', 'color': 'secondary'},
            {'label': 'New Order', 'value': 'new_order', 'color': 'success'},
            {'label': 'Order History', 'value': 'orders', 'color': 'info'},
            {'label': 'User Management', 'value': 'users', 'color': 'warning'},
            {'label': 'Audit Logs', 'value': 'logs', 'color': 'dark'}
        ],
        onclick=lambda val: navigate_route(val)
    )
    
    put_html("<hr>")
    
    # Primary view container scope
    use_scope("main_content")
    render_dashboard_view()

def navigate_route(route_name: str):
    """Navigation dispatcher callback."""
    if route_name == 'dashboard':
        render_dashboard_view()
    elif route_name == 'catalog':
        render_catalog_view()
    elif route_name == 'new_order':
        render_order_entry_view()
    elif route_name == 'orders':
        render_orders_list_view()
    elif route_name == 'users':
        render_users_view()
    elif route_name == 'logs':
        render_system_logs_view()

# ==============================================================================
# SECTION 7: PYWEBIO & FLASK ROUTE INTEGRATION
# ==============================================================================

# Mount PyWebIO directly onto the Flask root route
flask_app.add_url_rule(
    '/', 
    endpoint='webio_main', 
    view_func=webio_view(pywebio_main_entry), 
    methods=['GET', 'POST', 'OPTIONS']
)

# Export app targets for Gunicorn
app = flask_app

# ==============================================================================
# SECTION 8: CLI LOCAL DEVELOPMENT EXECUTION
# ==============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting development server on port {port}...")
    
    pywebio.platform.start_server(
        pywebio_main_entry,
        port=port,
        debug=True
    )
