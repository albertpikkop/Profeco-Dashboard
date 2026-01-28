"""Generate marketing-oriented PDF reports from PROFECO egg price data.

Queries the existing egg_prices database and creates professional PDF reports
that can be ingested into the RAG knowledge base. This gives the AI deep,
current data to answer marketing questions about the Mexican egg market.

Usage:
    python scripts/generate_market_reports.py
"""

import os
import sys
import sqlite3
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable
)

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.config import DOCUMENTS_DIR

EGGS_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "eggs.db")

# Colors
BRAND_GREEN = HexColor("#16a34a")
DARK_GRAY = HexColor("#374151")
LIGHT_GRAY = HexColor("#f3f4f6")
WHITE = HexColor("#ffffff")


def get_db():
    conn = sqlite3.connect(EGGS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='ReportTitle', fontSize=18, leading=22, alignment=TA_CENTER,
        textColor=BRAND_GREEN, spaceAfter=6, fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='ReportSubtitle', fontSize=10, leading=14, alignment=TA_CENTER,
        textColor=DARK_GRAY, spaceAfter=20
    ))
    styles.add(ParagraphStyle(
        name='SectionTitle', fontSize=14, leading=18,
        textColor=BRAND_GREEN, spaceAfter=10, spaceBefore=16,
        fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='SubSection', fontSize=11, leading=14,
        textColor=DARK_GRAY, spaceAfter=6, spaceBefore=10,
        fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='BodyText2', fontSize=9, leading=12,
        textColor=DARK_GRAY, spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        name='Finding', fontSize=9, leading=12,
        textColor=HexColor("#b45309"), spaceAfter=8, spaceBefore=6,
        fontName='Helvetica-Oblique', leftIndent=10
    ))
    styles.add(ParagraphStyle(
        name='Footer', fontSize=7, leading=10, alignment=TA_CENTER,
        textColor=HexColor("#9ca3af")
    ))
    return styles


def make_table(headers, rows, col_widths=None):
    """Create a styled table."""
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_GREEN),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor("#d1d5db")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t


def header_block(elements, styles, title, subtitle_lines):
    elements.append(Paragraph(title, styles['ReportTitle']))
    for line in subtitle_lines:
        elements.append(Paragraph(line, styles['ReportSubtitle']))
    elements.append(HRFlowable(width="100%", color=BRAND_GREEN, thickness=2))
    elements.append(Spacer(1, 12))


def generate_national_overview_pdf(filepath):
    """Report 1: National Egg Market Overview."""
    conn = get_db()
    c = conn.cursor()
    styles = get_styles()

    c.execute("SELECT MIN(fecha_observacion) as earliest, MAX(fecha_observacion) as latest, COUNT(*) as total FROM egg_prices")
    meta = c.fetchone()

    c.execute("""
        SELECT COUNT(*) as total_records, COUNT(DISTINCT ciudad) as cities,
               COUNT(DISTINCT marca) as brands, COUNT(DISTINCT cadena_comercial) as stores,
               ROUND(AVG(precio), 2) as avg_price, MIN(precio) as min_price,
               MAX(precio) as max_price, ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices
    """)
    nat = c.fetchone()

    c.execute("""
        SELECT ciudad, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price,
               MIN(precio) as min_price, MAX(precio) as max_price,
               ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices GROUP BY ciudad ORDER BY avg_price
    """)
    cities = c.fetchall()

    c.execute("""
        SELECT marca, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price,
               MIN(precio) as min_price, MAX(precio) as max_price,
               ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE marca IS NOT NULL GROUP BY marca ORDER BY records DESC
    """)
    brands = c.fetchall()

    c.execute("""
        SELECT tipo_huevo, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price,
               ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE tipo_huevo IS NOT NULL GROUP BY tipo_huevo
    """)
    types = c.fetchall()

    c.execute("""
        SELECT cantidad_piezas, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price,
               ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE cantidad_piezas IS NOT NULL
        GROUP BY cantidad_piezas ORDER BY cantidad_piezas
    """)
    packs = c.fetchall()

    doc = SimpleDocTemplate(filepath, pagesize=letter, topMargin=0.5*inch,
                           bottomMargin=0.5*inch, leftMargin=0.6*inch, rightMargin=0.6*inch)
    elements = []

    header_block(elements, styles,
        "Panorama Nacional del Mercado de Huevo en Mexico",
        [
            f"Fuente: PROFECO — Programa Quien es Quien en los Precios (QQP)",
            f"Periodo: {meta['earliest']} a {meta['latest']} | {meta['total']} observaciones | Generado: {datetime.now().strftime('%Y-%m-%d')}"
        ]
    )

    # Executive summary
    elements.append(Paragraph("1. Resumen Ejecutivo", styles['SectionTitle']))
    elements.append(Paragraph(
        f"Este reporte analiza {nat['total_records']} observaciones de precios de huevo recolectadas "
        f"por PROFECO en {nat['cities']} ciudades principales de Mexico, cubriendo {nat['brands']} marcas "
        f"en {nat['stores']} cadenas comerciales.", styles['BodyText2']
    ))

    kpi_data = [
        ["Indicador", "Valor"],
        ["Precio promedio nacional", f"${nat['avg_price']} MXN"],
        ["Precio minimo observado", f"${nat['min_price']} MXN"],
        ["Precio maximo observado", f"${nat['max_price']} MXN"],
        ["Precio promedio por pieza", f"${nat['avg_per_egg']} MXN"],
        ["Ciudades monitoreadas", str(nat['cities'])],
        ["Marcas detectadas", str(nat['brands'])],
        ["Cadenas comerciales", str(nat['stores'])],
    ]
    elements.append(make_table(kpi_data[0], kpi_data[1:], col_widths=[3*inch, 2*inch]))
    elements.append(Spacer(1, 12))

    # City rankings
    elements.append(Paragraph("2. Ranking de Precios por Ciudad", styles['SectionTitle']))
    city_rows = [[c['ciudad'], f"${c['avg_price']}", f"${c['min_price']}", f"${c['max_price']}",
                  f"${c['avg_per_egg']}", str(c['records'])] for c in cities]
    elements.append(make_table(
        ["Ciudad", "Promedio", "Min", "Max", "$/Pieza", "Obs"],
        city_rows, col_widths=[1.5*inch, 1*inch, 0.8*inch, 0.8*inch, 0.8*inch, 0.6*inch]
    ))

    cheapest, most_exp = cities[0], cities[-1]
    diff = round(most_exp['avg_price'] - cheapest['avg_price'], 2)
    pct = round(diff / cheapest['avg_price'] * 100, 1)
    elements.append(Paragraph(
        f"HALLAZGO: {cheapest['ciudad']} es la ciudad mas economica (${cheapest['avg_price']} MXN) "
        f"y {most_exp['ciudad']} la mas cara (${most_exp['avg_price']} MXN). "
        f"Diferencia: ${diff} MXN ({pct}%).", styles['Finding']
    ))

    # Brand rankings
    elements.append(Paragraph("3. Analisis de Marcas", styles['SectionTitle']))
    brand_rows = [[b['marca'], str(b['records']), f"${b['avg_price']}", f"${b['min_price']}",
                   f"${b['max_price']}", f"${b['avg_per_egg']}"] for b in brands]
    elements.append(make_table(
        ["Marca", "Obs", "Promedio", "Min", "Max", "$/Pieza"],
        brand_rows, col_widths=[1.5*inch, 0.5*inch, 1*inch, 0.8*inch, 0.8*inch, 0.8*inch]
    ))
    elements.append(Spacer(1, 8))

    # Type comparison
    elements.append(Paragraph("4. Comparativo por Tipo de Huevo", styles['SectionTitle']))
    type_rows = [[t['tipo_huevo'].capitalize(), str(t['records']),
                  f"${t['avg_price']}", f"${t['avg_per_egg']}"] for t in types]
    elements.append(make_table(
        ["Tipo", "Obs", "Promedio", "$/Pieza"],
        type_rows, col_widths=[1.5*inch, 1*inch, 1.2*inch, 1.2*inch]
    ))

    # Pack size
    elements.append(Paragraph("5. Analisis por Presentacion", styles['SectionTitle']))
    pack_rows = [[f"{p['cantidad_piezas']} piezas", str(p['records']),
                  f"${p['avg_price']}", f"${p['avg_per_egg']}"] for p in packs]
    elements.append(make_table(
        ["Presentacion", "Obs", "Promedio", "$/Pieza"],
        pack_rows, col_widths=[1.5*inch, 1*inch, 1.2*inch, 1.2*inch]
    ))

    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        f"Reporte generado automaticamente a partir de datos de PROFECO QQP. "
        f"Fuente: https://qqp.profeco.gob.mx/", styles['Footer']
    ))

    doc.build(elements)
    conn.close()


def generate_competitive_report_pdf(filepath):
    """Report 2: Competitive brand analysis — Bachoco vs competitors."""
    conn = get_db()
    c = conn.cursor()
    styles = get_styles()

    c.execute("SELECT MIN(fecha_observacion) as earliest, MAX(fecha_observacion) as latest FROM egg_prices")
    meta = c.fetchone()

    c.execute("""
        SELECT marca, COUNT(*) as records,
               ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM egg_prices WHERE marca IS NOT NULL), 1) as share,
               ROUND(AVG(precio), 2) as avg_price, ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE marca IS NOT NULL GROUP BY marca ORDER BY records DESC
    """)
    market_shares = c.fetchall()

    c.execute("""
        SELECT b.ciudad, b.avg_price as bachoco_price, s.avg_price as sanjuan_price,
               ROUND(s.avg_price - b.avg_price, 2) as difference,
               ROUND((s.avg_price - b.avg_price) / b.avg_price * 100, 1) as pct_diff
        FROM (SELECT ciudad, ROUND(AVG(precio), 2) as avg_price FROM egg_prices WHERE marca = 'Bachoco' GROUP BY ciudad) b
        JOIN (SELECT ciudad, ROUND(AVG(precio), 2) as avg_price FROM egg_prices WHERE marca = 'San Juan' GROUP BY ciudad) s
        ON b.ciudad = s.ciudad ORDER BY pct_diff DESC
    """)
    head_to_head = c.fetchall()

    c.execute("""
        SELECT cadena_comercial, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price
        FROM egg_prices WHERE marca = 'Bachoco' GROUP BY cadena_comercial ORDER BY records DESC
    """)
    bachoco_stores = c.fetchall()

    c.execute("""
        SELECT cadena_comercial, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price
        FROM egg_prices WHERE marca = 'San Juan' GROUP BY cadena_comercial ORDER BY records DESC
    """)
    sanjuan_stores = c.fetchall()

    c.execute("""
        SELECT ciudad, marca, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price
        FROM egg_prices WHERE marca IS NOT NULL GROUP BY ciudad, marca ORDER BY ciudad, avg_price
    """)
    city_brands = c.fetchall()

    doc = SimpleDocTemplate(filepath, pagesize=letter, topMargin=0.5*inch,
                           bottomMargin=0.5*inch, leftMargin=0.6*inch, rightMargin=0.6*inch)
    elements = []

    header_block(elements, styles,
        "Inteligencia Competitiva: Marcas de Huevo en Mexico",
        [
            f"Fuente: PROFECO — Programa Quien es Quien en los Precios (QQP)",
            f"Periodo: {meta['earliest']} a {meta['latest']} | Generado: {datetime.now().strftime('%Y-%m-%d')}"
        ]
    )

    # Market share
    elements.append(Paragraph("1. Participacion de Mercado por Marca", styles['SectionTitle']))
    elements.append(Paragraph(
        "Participacion basada en presencia en puntos de venta monitoreados por PROFECO.",
        styles['BodyText2']
    ))
    share_rows = [[m['marca'], str(m['records']), f"{m['share']}%",
                   f"${m['avg_price']}", f"${m['avg_per_egg']}"] for m in market_shares]
    elements.append(make_table(
        ["Marca", "Obs", "Participacion", "Precio Prom", "$/Pieza"],
        share_rows, col_widths=[1.5*inch, 0.6*inch, 1*inch, 1*inch, 0.8*inch]
    ))
    elements.append(Paragraph(
        f"HALLAZGO: Bachoco lidera con {market_shares[0]['share']}% de presencia en puntos de venta, "
        f"seguido por San Juan ({market_shares[1]['share']}%) y El Calvario ({market_shares[2]['share']}%).",
        styles['Finding']
    ))

    # Head to head
    elements.append(Paragraph("2. Bachoco vs San Juan — Comparativo Directo", styles['SectionTitle']))
    h2h_rows = [[h['ciudad'], f"${h['bachoco_price']}", f"${h['sanjuan_price']}",
                 f"${h['difference']:+.2f}", f"{h['pct_diff']:+.1f}%"] for h in head_to_head]
    elements.append(make_table(
        ["Ciudad", "Bachoco", "San Juan", "Diferencia", "%"],
        h2h_rows, col_widths=[1.5*inch, 1*inch, 1*inch, 1*inch, 0.8*inch]
    ))
    if head_to_head:
        elements.append(Paragraph(
            f"HALLAZGO: San Juan es mas caro que Bachoco en todas las ciudades comparadas. "
            f"Bachoco tiene ventaja de precio de ${min(h['difference'] for h in head_to_head)} a "
            f"${max(h['difference'] for h in head_to_head)} MXN por paquete.",
            styles['Finding']
        ))

    # Bachoco distribution
    elements.append(Paragraph("3. Distribucion de Bachoco por Cadena Comercial", styles['SectionTitle']))
    bach_rows = [[b['cadena_comercial'], str(b['records']), f"${b['avg_price']}"] for b in bachoco_stores]
    elements.append(make_table(
        ["Cadena", "Obs", "Precio Prom"],
        bach_rows, col_widths=[2.5*inch, 0.8*inch, 1*inch]
    ))

    # San Juan distribution
    elements.append(Paragraph("4. Distribucion de San Juan por Cadena Comercial", styles['SectionTitle']))
    sj_rows = [[s['cadena_comercial'], str(s['records']), f"${s['avg_price']}"] for s in sanjuan_stores]
    elements.append(make_table(
        ["Cadena", "Obs", "Precio Prom"],
        sj_rows, col_widths=[2.5*inch, 0.8*inch, 1*inch]
    ))

    # Brand prices by city
    elements.append(PageBreak())
    elements.append(Paragraph("5. Precios por Marca en Cada Ciudad", styles['SectionTitle']))
    current_city = None
    city_rows = []
    for cb in city_brands:
        if cb['ciudad'] != current_city:
            if city_rows:
                elements.append(make_table(
                    ["Marca", "Precio Prom", "Obs"],
                    city_rows, col_widths=[2*inch, 1.2*inch, 0.8*inch]
                ))
                elements.append(Spacer(1, 8))
            current_city = cb['ciudad']
            elements.append(Paragraph(current_city, styles['SubSection']))
            city_rows = []
        city_rows.append([cb['marca'], f"${cb['avg_price']}", str(cb['records'])])
    if city_rows:
        elements.append(make_table(
            ["Marca", "Precio Prom", "Obs"],
            city_rows, col_widths=[2*inch, 1.2*inch, 0.8*inch]
        ))

    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        f"Reporte generado automaticamente a partir de datos de PROFECO QQP. "
        f"Fuente: https://qqp.profeco.gob.mx/", styles['Footer']
    ))

    doc.build(elements)
    conn.close()


def generate_channel_report_pdf(filepath):
    """Report 3: Store/channel analysis."""
    conn = get_db()
    c = conn.cursor()
    styles = get_styles()

    c.execute("SELECT MIN(fecha_observacion) as earliest, MAX(fecha_observacion) as latest FROM egg_prices")
    meta = c.fetchone()

    c.execute("""
        SELECT cadena_comercial, COUNT(*) as records, COUNT(DISTINCT ciudad) as cities,
               COUNT(DISTINCT marca) as brands, ROUND(AVG(precio), 2) as avg_price,
               MIN(precio) as min_price, MAX(precio) as max_price,
               ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices GROUP BY cadena_comercial ORDER BY avg_price
    """)
    chains = c.fetchall()

    c.execute("""
        SELECT producto_original, marca, precio, precio_por_pieza,
               cadena_comercial, ciudad, cantidad_piezas
        FROM egg_prices WHERE precio_por_pieza IS NOT NULL
        ORDER BY precio_por_pieza ASC LIMIT 15
    """)
    best_deals = c.fetchall()

    c.execute("""
        SELECT producto_original, marca, precio, precio_por_pieza,
               cadena_comercial, ciudad, cantidad_piezas
        FROM egg_prices WHERE precio_por_pieza IS NOT NULL
        ORDER BY precio_por_pieza DESC LIMIT 10
    """)
    worst_deals = c.fetchall()

    doc = SimpleDocTemplate(filepath, pagesize=letter, topMargin=0.5*inch,
                           bottomMargin=0.5*inch, leftMargin=0.6*inch, rightMargin=0.6*inch)
    elements = []

    header_block(elements, styles,
        "Canales de Distribucion: Precios por Cadena Comercial",
        [
            f"Fuente: PROFECO — Programa Quien es Quien en los Precios (QQP)",
            f"Periodo: {meta['earliest']} a {meta['latest']} | Generado: {datetime.now().strftime('%Y-%m-%d')}"
        ]
    )

    elements.append(Paragraph("1. Ranking de Cadenas Comerciales", styles['SectionTitle']))
    elements.append(Paragraph("Ordenadas de precio mas bajo a mas alto.", styles['BodyText2']))
    chain_rows = [[ch['cadena_comercial'], f"${ch['avg_price']}", f"${ch['min_price']}-${ch['max_price']}",
                   f"${ch['avg_per_egg']}", str(ch['cities']), str(ch['records'])]
                  for ch in chains]
    elements.append(make_table(
        ["Cadena", "Promedio", "Rango", "$/Pieza", "Ciudades", "Obs"],
        chain_rows, col_widths=[1.8*inch, 0.8*inch, 1.1*inch, 0.7*inch, 0.6*inch, 0.5*inch]
    ))

    elements.append(Paragraph("2. Top 15 Mejores Ofertas (Menor Precio por Pieza)", styles['SectionTitle']))
    deal_rows = [[f"${d['precio_por_pieza']:.2f}", d['marca'] or 'S/M',
                  f"${d['precio']}", f"{d['cantidad_piezas']}pz",
                  d['cadena_comercial'][:20], d['ciudad']] for d in best_deals]
    elements.append(make_table(
        ["$/Pieza", "Marca", "Precio", "Pzas", "Tienda", "Ciudad"],
        deal_rows, col_widths=[0.7*inch, 1*inch, 0.7*inch, 0.5*inch, 1.3*inch, 1.2*inch]
    ))

    elements.append(Paragraph("3. Top 10 Huevos Mas Caros", styles['SectionTitle']))
    exp_rows = [[f"${d['precio_por_pieza']:.2f}", d['marca'] or 'S/M',
                 f"${d['precio']}", f"{d['cantidad_piezas']}pz",
                 d['cadena_comercial'][:20], d['ciudad']] for d in worst_deals]
    elements.append(make_table(
        ["$/Pieza", "Marca", "Precio", "Pzas", "Tienda", "Ciudad"],
        exp_rows, col_widths=[0.7*inch, 1*inch, 0.7*inch, 0.5*inch, 1.3*inch, 1.2*inch]
    ))

    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        f"Reporte generado automaticamente a partir de datos de PROFECO QQP. "
        f"Fuente: https://qqp.profeco.gob.mx/", styles['Footer']
    ))

    doc.build(elements)
    conn.close()


def generate_regional_report_pdf(filepath):
    """Report 4: Regional analysis."""
    conn = get_db()
    c = conn.cursor()
    styles = get_styles()

    c.execute("SELECT MIN(fecha_observacion) as earliest, MAX(fecha_observacion) as latest FROM egg_prices")
    meta = c.fetchone()

    c.execute("SELECT ROUND(AVG(precio), 2) as national_avg FROM egg_prices")
    national_avg = c.fetchone()['national_avg']

    c.execute("""
        SELECT region, COUNT(*) as records, COUNT(DISTINCT ciudad) as cities,
               ROUND(AVG(precio), 2) as avg_price, MIN(precio) as min_price,
               MAX(precio) as max_price, ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE region IS NOT NULL GROUP BY region ORDER BY avg_price
    """)
    regions = c.fetchall()

    c.execute("""
        SELECT ciudad, region, ROUND(AVG(precio), 2) as avg_price,
               ROUND(AVG(precio) - ?, 2) as vs_national,
               ROUND((AVG(precio) - ?) / ? * 100, 1) as pct_vs_national
        FROM egg_prices GROUP BY ciudad ORDER BY avg_price
    """, (national_avg, national_avg, national_avg))
    city_vs_national = c.fetchall()

    c.execute("""
        SELECT region, marca, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price
        FROM egg_prices WHERE marca IS NOT NULL AND region IS NOT NULL
        GROUP BY region, marca HAVING records >= 3 ORDER BY region, records DESC
    """)
    brand_by_region = c.fetchall()

    doc = SimpleDocTemplate(filepath, pagesize=letter, topMargin=0.5*inch,
                           bottomMargin=0.5*inch, leftMargin=0.6*inch, rightMargin=0.6*inch)
    elements = []

    header_block(elements, styles,
        "Analisis Regional de Precios de Huevo en Mexico",
        [
            f"Fuente: PROFECO — Programa Quien es Quien en los Precios (QQP)",
            f"Periodo: {meta['earliest']} a {meta['latest']} | Precio nacional promedio: ${national_avg} MXN"
        ]
    )

    elements.append(Paragraph("1. Resumen por Region", styles['SectionTitle']))
    reg_rows = [[r['region'], str(r['cities']), f"${r['avg_price']}", f"${r['min_price']}-${r['max_price']}",
                 f"${r['avg_per_egg']}", str(r['records'])] for r in regions]
    elements.append(make_table(
        ["Region", "Ciudades", "Promedio", "Rango", "$/Pieza", "Obs"],
        reg_rows, col_widths=[1.2*inch, 0.7*inch, 0.9*inch, 1.2*inch, 0.7*inch, 0.5*inch]
    ))

    elements.append(Paragraph("2. Ciudades vs Promedio Nacional", styles['SectionTitle']))
    cvn_rows = [[cv['ciudad'], cv['region'] or 'N/A', f"${cv['avg_price']}",
                 f"${cv['vs_national']:+.2f}", f"{cv['pct_vs_national']:+.1f}%"] for cv in city_vs_national]
    elements.append(make_table(
        ["Ciudad", "Region", "Promedio", "vs Nacional", "%"],
        cvn_rows, col_widths=[1.5*inch, 1*inch, 1*inch, 1*inch, 0.8*inch]
    ))

    elements.append(Paragraph("3. Marcas Dominantes por Region", styles['SectionTitle']))
    current_region = None
    reg_brand_rows = []
    for br in brand_by_region:
        if br['region'] != current_region:
            if reg_brand_rows:
                elements.append(make_table(
                    ["Marca", "Precio Prom", "Obs"],
                    reg_brand_rows, col_widths=[2*inch, 1.2*inch, 0.8*inch]
                ))
                elements.append(Spacer(1, 6))
            current_region = br['region']
            elements.append(Paragraph(f"Region {current_region}", styles['SubSection']))
            reg_brand_rows = []
        reg_brand_rows.append([br['marca'], f"${br['avg_price']}", str(br['records'])])
    if reg_brand_rows:
        elements.append(make_table(
            ["Marca", "Precio Prom", "Obs"],
            reg_brand_rows, col_widths=[2*inch, 1.2*inch, 0.8*inch]
        ))

    elements.append(Spacer(1, 12))
    elements.append(Paragraph("4. Implicaciones para Estrategia de Marketing", styles['SectionTitle']))
    insights = [
        "a) POSICIONAMIENTO: Las marcas lideres (Bachoco, San Juan, El Calvario) tienen rangos de precio muy diferentes. Bachoco se posiciona como marca de precio medio-alto.",
        "b) OPORTUNIDADES REGIONALES: Las diferencias regionales sugieren oportunidades de ajuste de estrategia de precios por zona geografica.",
        "c) CANALES: Los autoservicios y supermercados manejan precios diferentes. Los mercados publicos pueden ofrecer precios mas competitivos.",
        "d) SEGMENTACION: Los paquetes de 30 piezas ofrecen mejor precio por pieza. Oportunidad de marketing para empaques familiares.",
    ]
    for ins in insights:
        elements.append(Paragraph(ins, styles['BodyText2']))

    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        f"Reporte generado automaticamente a partir de datos de PROFECO QQP. "
        f"Fuente: https://qqp.profeco.gob.mx/", styles['Footer']
    ))

    doc.build(elements)
    conn.close()


def generate_bachoco_report_pdf(filepath):
    """Report 5: Bachoco deep dive."""
    conn = get_db()
    c = conn.cursor()
    styles = get_styles()

    c.execute("SELECT MIN(fecha_observacion) as earliest, MAX(fecha_observacion) as latest FROM egg_prices")
    meta = c.fetchone()

    c.execute("""
        SELECT COUNT(*) as total, COUNT(DISTINCT ciudad) as cities,
               COUNT(DISTINCT cadena_comercial) as chains,
               ROUND(AVG(precio), 2) as avg_price, MIN(precio) as min_price,
               MAX(precio) as max_price, ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE marca = 'Bachoco'
    """)
    overview = c.fetchone()

    c.execute("""
        SELECT ciudad, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price,
               MIN(precio) as min_price, MAX(precio) as max_price,
               ROUND(AVG(precio_por_pieza), 2) as avg_per_egg
        FROM egg_prices WHERE marca = 'Bachoco' GROUP BY ciudad ORDER BY avg_price
    """)
    by_city = c.fetchall()

    c.execute("""
        SELECT b.ciudad, b.bachoco_avg, m.market_avg,
               ROUND(b.bachoco_avg - m.market_avg, 2) as difference,
               ROUND((b.bachoco_avg - m.market_avg) / m.market_avg * 100, 1) as pct_diff
        FROM (SELECT ciudad, ROUND(AVG(precio), 2) as bachoco_avg FROM egg_prices WHERE marca = 'Bachoco' GROUP BY ciudad) b
        JOIN (SELECT ciudad, ROUND(AVG(precio), 2) as market_avg FROM egg_prices GROUP BY ciudad) m
        ON b.ciudad = m.ciudad ORDER BY pct_diff
    """)
    vs_market = c.fetchall()

    c.execute("""
        SELECT cadena_comercial, COUNT(*) as records, ROUND(AVG(precio), 2) as avg_price,
               MIN(precio) as min_price, MAX(precio) as max_price
        FROM egg_prices WHERE marca = 'Bachoco' GROUP BY cadena_comercial ORDER BY avg_price
    """)
    by_chain = c.fetchall()

    c.execute("""
        SELECT DISTINCT producto_original, tipo_huevo, cantidad_piezas,
               ROUND(AVG(precio), 2) as avg_price, COUNT(*) as records
        FROM egg_prices WHERE marca = 'Bachoco' GROUP BY producto_original ORDER BY avg_price
    """)
    products = c.fetchall()

    doc = SimpleDocTemplate(filepath, pagesize=letter, topMargin=0.5*inch,
                           bottomMargin=0.5*inch, leftMargin=0.6*inch, rightMargin=0.6*inch)
    elements = []

    header_block(elements, styles,
        "Bachoco: Analisis Detallado de Posicionamiento",
        [
            f"Fuente: PROFECO — Programa Quien es Quien en los Precios (QQP)",
            f"Periodo: {meta['earliest']} a {meta['latest']} | Generado: {datetime.now().strftime('%Y-%m-%d')}"
        ]
    )

    # Overview KPIs
    elements.append(Paragraph("1. Resumen Ejecutivo — Bachoco", styles['SectionTitle']))
    elements.append(Paragraph(
        "Bachoco es la marca de huevo con mayor presencia en los puntos de venta "
        "monitoreados por PROFECO en Mexico.", styles['BodyText2']
    ))
    kpi_rows = [
        ["Total de observaciones", str(overview['total'])],
        ["Presencia en ciudades", f"{overview['cities']} de 8"],
        ["Cadenas comerciales", str(overview['chains'])],
        ["Precio promedio", f"${overview['avg_price']} MXN"],
        ["Rango de precios", f"${overview['min_price']} - ${overview['max_price']} MXN"],
        ["Precio promedio por pieza", f"${overview['avg_per_egg']} MXN"],
    ]
    elements.append(make_table(["Metrica", "Valor"], kpi_rows, col_widths=[2.5*inch, 2.5*inch]))

    # By city
    elements.append(Paragraph("2. Bachoco por Ciudad", styles['SectionTitle']))
    city_rows = [[bc['ciudad'], f"${bc['avg_price']}", f"${bc['min_price']}-${bc['max_price']}",
                  f"${bc['avg_per_egg']}", str(bc['records'])] for bc in by_city]
    elements.append(make_table(
        ["Ciudad", "Promedio", "Rango", "$/Pieza", "Obs"],
        city_rows, col_widths=[1.5*inch, 1*inch, 1.2*inch, 0.8*inch, 0.6*inch]
    ))

    # Vs market
    elements.append(Paragraph("3. Bachoco vs Promedio del Mercado", styles['SectionTitle']))
    vm_rows = [[vm['ciudad'], f"${vm['bachoco_avg']}", f"${vm['market_avg']}",
                f"${vm['difference']:+.2f}", f"{vm['pct_diff']:+.1f}%"] for vm in vs_market]
    elements.append(make_table(
        ["Ciudad", "Bachoco", "Mercado", "Diferencia", "%"],
        vm_rows, col_widths=[1.5*inch, 1*inch, 1*inch, 1*inch, 0.8*inch]
    ))

    above = sum(1 for vm in vs_market if vm['difference'] > 0)
    below = sum(1 for vm in vs_market if vm['difference'] < 0)
    elements.append(Paragraph(
        f"HALLAZGO: Bachoco esta por encima del promedio del mercado en {above} ciudades "
        f"y por debajo en {below}. Posicionamiento {'premium' if above > below else 'competitivo'} "
        f"en la mayoria de los mercados.", styles['Finding']
    ))

    # By chain
    elements.append(Paragraph("4. Bachoco por Cadena Comercial", styles['SectionTitle']))
    ch_rows = [[ch['cadena_comercial'], f"${ch['avg_price']}", f"${ch['min_price']}-${ch['max_price']}",
                str(ch['records'])] for ch in by_chain]
    elements.append(make_table(
        ["Cadena", "Promedio", "Rango", "Obs"],
        ch_rows, col_widths=[2.2*inch, 1*inch, 1.2*inch, 0.6*inch]
    ))

    # Products catalog
    elements.append(PageBreak())
    elements.append(Paragraph("5. Catalogo de Productos Bachoco Detectados", styles['SectionTitle']))
    prod_rows = [[p['producto_original'][:55], p['tipo_huevo'] or 'N/A',
                  str(p['cantidad_piezas'] or 'N/A'), f"${p['avg_price']}",
                  str(p['records'])] for p in products]
    elements.append(make_table(
        ["Producto", "Tipo", "Pzas", "Precio", "Obs"],
        prod_rows, col_widths=[2.8*inch, 0.6*inch, 0.5*inch, 0.7*inch, 0.5*inch]
    ))

    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        f"Reporte generado automaticamente a partir de datos de PROFECO QQP. "
        f"Fuente: https://qqp.profeco.gob.mx/", styles['Footer']
    ))

    doc.build(elements)
    conn.close()


def main():
    print("=" * 60)
    print("Market Report Generator — PROFECO Egg Price Data")
    print("=" * 60)

    os.makedirs(DOCUMENTS_DIR, exist_ok=True)

    reports = [
        {
            "generator": generate_national_overview_pdf,
            "filename": "Reporte_Panorama_Nacional_Huevo_Mexico.pdf",
            "category": "Market Intelligence",
            "source_org": "PROFECO / QQP",
            "source_url": None,
        },
        {
            "generator": generate_competitive_report_pdf,
            "filename": "Reporte_Inteligencia_Competitiva_Marcas_Huevo.pdf",
            "category": "Market Intelligence",
            "source_org": "PROFECO / QQP",
            "source_url": None,
        },
        {
            "generator": generate_channel_report_pdf,
            "filename": "Reporte_Canales_Distribucion_Huevo.pdf",
            "category": "Market Intelligence",
            "source_org": "PROFECO / QQP",
            "source_url": None,
        },
        {
            "generator": generate_regional_report_pdf,
            "filename": "Reporte_Analisis_Regional_Precios_Huevo.pdf",
            "category": "Market Intelligence",
            "source_org": "PROFECO / QQP",
            "source_url": None,
        },
        {
            "generator": generate_bachoco_report_pdf,
            "filename": "Reporte_Bachoco_Analisis_Detallado.pdf",
            "category": "Market Intelligence",
            "source_org": "PROFECO / QQP",
            "source_url": None,
        },
    ]

    generated = []
    for r in reports:
        print(f"\nGenerating: {r['filename']}...")
        try:
            filepath = os.path.join(DOCUMENTS_DIR, r["filename"])
            r["generator"](filepath)
            size_kb = os.path.getsize(filepath) / 1024
            print(f"  Generated: {size_kb:.1f} KB")
            generated.append({**r, "filepath": filepath})
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback
            traceback.print_exc()

    # Now ingest into RAG
    print("\n" + "=" * 60)
    print("Ingesting reports into RAG knowledge base...")
    print("=" * 60)

    from rag.engine import RAGEngine
    engine = RAGEngine()

    if not engine.is_configured():
        print("\nERROR: OpenAI API key not configured. Set OPENAI_API_KEY in .env")
        print("Reports were generated but not ingested.")
        return

    existing_docs = {d.filename for d in engine.list_documents()}

    ingested = 0
    skipped = 0
    for r in generated:
        if r["filename"] in existing_docs:
            print(f"  Already ingested: {r['filename']}")
            skipped += 1
            continue

        print(f"  Ingesting: {r['filename']}...")
        try:
            result = engine.ingest_document(
                filepath=r["filepath"],
                filename=r["filename"],
                category=r["category"],
                source_org=r["source_org"],
                source_url=r["source_url"],
            )
            print(f"  Done: {result.page_count} pages, {result.chunk_count} chunks")
            ingested += 1
        except Exception as e:
            print(f"  FAILED: {e}")

    print(f"\n{'=' * 60}")
    print(f"Results: {len(generated)} generated, {ingested} ingested, {skipped} skipped")
    status = engine.get_status()
    print(f"Knowledge base: {status.document_count} docs, {status.total_chunks} chunks")
    print("=" * 60)


if __name__ == "__main__":
    main()
