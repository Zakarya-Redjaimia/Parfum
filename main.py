import os
import io
import qrcode
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

import pywebio
from pywebio import start_server
from pywebio.input import input, textarea, select, file_upload, input_group, actions
from pywebio.output import put_text, put_markdown, put_buttons, put_file, put_html, put_table, clear, toast

# ----------------------------------------------------------------------
# Application Configuration & In-Memory Store
# ----------------------------------------------------------------------
PERFUME_CATALOG = []

# ----------------------------------------------------------------------
# Helper Functions: PDF & QR Code Generation
# ----------------------------------------------------------------------
def generate_qr_code(data_string: str) -> io.BytesIO:
    """Generates a QR code image as an in-memory BytesIO stream."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_perfume_pdf(perfume: dict) -> bytes:
    """Generates a PDF datasheet for a perfume and returns raw bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1A365D"),
        alignment=1,
    )
    header_style = ParagraphStyle(
        'HeaderStyle',
        parent=styles['Heading2'],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2B6CB0"),
    )
    body_style = styles['Normal']

    # Document Header
    story.append(Paragraph("Luxury Impact Parfume Rz", title_style))
    story.append(Spacer(1, 15))

    # Details Table
    table_data = [
        [Paragraph("<b>Perfume Name</b>", body_style), Paragraph(perfume.get('name', ''), body_style)],
        [Paragraph("<b>Category / Gender</b>", body_style), Paragraph(perfume.get('category', ''), body_style)],
        [Paragraph("<b>Top Notes</b>", body_style), Paragraph(perfume.get('top_notes', ''), body_style)],
        [Paragraph("<b>Heart Notes</b>", body_style), Paragraph(perfume.get('heart_notes', ''), body_style)],
        [Paragraph("<b>Base Notes</b>", body_style), Paragraph(perfume.get('base_notes', ''), body_style)],
        [Paragraph("<b>Base Price</b>", body_style), Paragraph(f"${perfume.get('price', 0):.2f}", body_style)],
    ]

    table = Table(table_data, colWidths=[150, 350])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor("#2D3748")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
    ]))
    story.append(table)
    story.append(Spacer(1, 20))

    # QR Code Section
    qr_data_str = f"Brand: Luxury Impact Parfume Rz | Name: {perfume.get('name')} | Notes: {perfume.get('top_notes')}"
    qr_buffer = generate_qr_code(qr_data_str)
    story.append(Paragraph("Scan QR Code for Specifications:", header_style))
    story.append(Spacer(1, 10))
    story.append(Image(qr_buffer, width=120, height=120))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# ----------------------------------------------------------------------
# PyWebIO Interface Logic
# ----------------------------------------------------------------------
def add_perfume_flow():
    clear()
    put_markdown("### ➕ Add New Fragrance Entry")

    data = input_group("Perfume Specifications", [
        input("Perfume Name", name="name", type="text", required=True),
        select("Category", name="category", options=["Unisex", "For Men", "For Women", "Private Blend"]),
        input("Top Notes", name="top_notes", placeholder="e.g. Bergamot, Pink Pepper", required=True),
        input("Heart Notes", name="heart_notes", placeholder="e.g. Rose, Jasmine, Cedar", required=True),
        input("Base Notes", name="base_notes", placeholder="e.g. Amber, Vanilla, Oud", required=True),
        input("Base Price ($)", name="price", type="float", value="100.0", required=True),
    ])

    PERFUME_CATALOG.append(data)
    toast(f"Added {data['name']} successfully!", color="success")
    main_menu()


def list_perfumes_flow():
    clear()
    put_markdown("### 🧴 Fragrance Catalog & Documentation Generator")

    if not PERFUME_CATALOG:
        put_text("No perfumes currently in inventory.")
        put_buttons(["Back to Main Menu"], onclick=lambda _: main_menu())
        return

    table_rows = []
    for idx, item in enumerate(PERFUME_CATALOG):
        table_rows.append([
            idx + 1,
            item['name'],
            item['category'],
            item['top_notes'],
            f"${float(item['price']):.2f}"
        ])

    put_table(
        table_rows,
        header=["#", "Name", "Category", "Top Notes", "Price"]
    )

    action = actions(
        label="Select action:",
        buttons=[
            {"label": "Download Datasheet (PDF)", "value": "export_pdf"},
            {"label": "Back to Main Menu", "value": "menu"}
        ]
    )

    if action == "export_pdf":
        perfume_idx = input("Enter item # to export PDF:", type="number", required=True)
        target_idx = int(perfume_idx) - 1

        if 0 <= target_idx < len(PERFUME_CATALOG):
            perfume = PERFUME_CATALOG[target_idx]
            pdf_bytes = generate_perfume_pdf(perfume)
            clean_filename = secure_filename(f"{perfume['name']}_datasheet.pdf")
            put_file(clean_filename, pdf_bytes, label=f"Click here to download {clean_filename}")
        else:
            toast("Invalid selection number.", color="error")
        
        put_buttons(["Return to Catalog"], onclick=lambda _: list_perfumes_flow())

    elif action == "menu":
        main_menu()


def main_menu():
    clear()
    put_markdown("# Luxury Impact Parfume Rz Management")
    put_text("Internal Catalog & Documentation System")
    put_html("<hr>")

    choice = actions(
        label="Choose an operation:",
        buttons=[
            {"label": "View Catalog", "value": "view"},
            {"label": "Add New Fragrance", "value": "add"},
        ]
    )

    if choice == "view":
        list_perfumes_flow()
    elif choice == "add":
        add_perfume_flow()


# ----------------------------------------------------------------------
# Application Entry Point
# ----------------------------------------------------------------------
# Instantiate WSGI app for Gunicorn context
app = pywebio.platform.flask.webio_exec(main_menu)

if __name__ == "__main__":
    # Local debugging execution
    port = int(os.environ.get("PORT", 8080))
    start_server(main_menu, port=port, debug=True)
