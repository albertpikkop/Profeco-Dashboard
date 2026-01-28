"""
Enhanced PROFECO Data Fetcher
Ingests QQP bulk CSV (eggs only, all 28 states) + SNIIM wholesale prices
into SQLite for the Egg Price Intelligence Dashboard.

Usage:
    python scripts/fetch_enhanced_data.py
"""

import os
import re
import csv
import io
import sys
import time
import sqlite3
import zipfile
import logging
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from datetime import datetime
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "eggs.db")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# ── Region mapping (state → region) ────────────────────────────────────
STATE_REGIONS = {
    "Aguascalientes": "Bajio", "Baja California": "Norte", "Baja California Sur": "Norte",
    "Campeche": "Sureste", "Chiapas": "Sureste", "Chihuahua": "Norte",
    "Ciudad de Mexico": "Centro", "Coahuila": "Norte", "Colima": "Bajio",
    "Durango": "Norte", "Estado de Mexico": "Centro", "Guanajuato": "Bajio",
    "Guerrero": "Centro", "Hidalgo": "Centro", "Jalisco": "Bajio",
    "Michoacan": "Bajio", "Morelos": "Centro", "Nayarit": "Bajio",
    "Nuevo Leon": "Norte", "Oaxaca": "Sureste", "Puebla": "Centro",
    "Queretaro": "Bajio", "Quintana Roo": "Sureste", "San Luis Potosi": "Centro",
    "Sinaloa": "Norte", "Sonora": "Norte", "Tabasco": "Sureste",
    "Tamaulipas": "Norte", "Tlaxcala": "Centro", "Veracruz": "Sureste",
    "Yucatan": "Sureste", "Zacatecas": "Norte",
    # Accented variants
    "Michoacán": "Bajio", "Nuevo León": "Norte", "Querétaro": "Bajio",
    "San Luis Potosí": "Centro", "México": "Centro", "Yucatán": "Sureste",
}

# Brand normalization (for bulk data where brand is already extracted)
BRAND_NORMALIZE = {
    "PrecÃ\xadssimo": "Precissimo", "Precíssimo": "Precissimo", "Precissimo": "Precissimo",
    "CrÃ\xado": "Crio", "Crío": "Crio", "Crio": "Crio",
    "TehuacÃ¡n": "Tehuacan", "Tehuacán": "Tehuacan", "Tehuacan": "Tehuacan",
    "MamÃ¡ Gallina": "Mama Gallina", "Mamá Gallina": "Mama Gallina",
    "AvÃ\xadcola": "Avicola", "Avícola": "Avicola",
    "S/m": "Sin Marca", "S/M": "Sin Marca", "s/m": "Sin Marca",
}

# Store type classification
STORE_TYPE_MAPPING = {
    'supermarket': ['WALMART', 'WAL-MART', 'BODEGA AURRERA', 'SORIANA', 'CHEDRAUI', 'LA COMER',
                    'SUPERISSSTE', 'HEB', 'S-MART', 'MEGA', 'COMERCIAL MEXICANA', 'ALSUPER',
                    'CITY MARKET', 'FRESKO', 'SUMESA', 'SUPERAMA', 'SMART & FINAL'],
    'wholesale': ['COSTCO', 'SAMS CLUB', "SAM'S CLUB", 'PRICESMART', 'CENTRAL DE ABASTOS'],
    'convenience': ['OXXO', '7 ELEVEN', '7-ELEVEN', 'EXTRA', 'CIRCLE K', 'GO MART', 'KIOSKO'],
    'market': ['MERCADO', 'TIANGUIS', 'ABARROTERO', 'ABARROTES'],
    'specialty': ['PANADERIA', 'TORTILLERIA', 'CARNICERIA', 'POLLERIA', 'CREMERIA', 'HUEVERIA', 'GRANJA'],
}

# ── SNIIM market codes → state mapping ─────────────────────────────────
SNIIM_MARKETS = {
    11: "Aguascalientes", 10: "Aguascalientes", 33: "Baja California",
    330: "Baja California Sur", 20: "Baja California Sur",
    40: "Campeche", 50: "Coahuila", 80: "Colima", 70: "Chiapas",
    61: "Chihuahua", 100: "Ciudad de Mexico", 102: "Durango", 101: "Durango",
    121: "Guerrero", 110: "Guanajuato", 111: "Guanajuato",
    130: "Hidalgo", 140: "Jalisco", 151: "Estado de Mexico", 150: "Estado de Mexico",
    160: "Michoacan", 170: "Morelos", 172: "Morelos",
    180: "Nayarit", 181: "Nayarit",
    191: "Nuevo Leon", 190: "Nuevo Leon", 192: "Nuevo Leon",
    200: "Oaxaca", 210: "Puebla",
    230: "Quintana Roo", 231: "Quintana Roo", 220: "Queretaro",
    250: "Sinaloa", 240: "San Luis Potosi",
    261: "Sonora", 260: "Sonora",
    270: "Tabasco", 280: "Tamaulipas",
    307: "Veracruz", 306: "Veracruz",
    310: "Yucatan", 320: "Zacatecas",
}


# ═══════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def classify_store_type(store_name):
    if not store_name:
        return 'otro'
    upper = store_name.upper()
    for stype, patterns in STORE_TYPE_MAPPING.items():
        for p in patterns:
            if p in upper:
                return stype
    return 'otro'


def normalize_brand(brand):
    if not brand:
        return "Sin Marca"
    return BRAND_NORMALIZE.get(brand, brand)


def extract_quantity(presentacion):
    """Extract egg count from presentation string like 'Paquete C/12 Blanco'."""
    if not presentacion:
        return None
    m = re.search(r'C/(\d+)', presentacion)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:pzas?|piezas?|pack)', presentacion, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if 'docena' in presentacion.lower() and 'media' not in presentacion.lower():
        return 12
    if 'media docena' in presentacion.lower():
        return 6
    return None


def extract_egg_type(presentacion):
    """Extract egg type from presentation string."""
    if not presentacion:
        return "No especificado"
    lower = presentacion.lower()
    if 'rojo' in lower:
        return "Rojo"
    if 'blanco' in lower:
        return "Blanco"
    if any(w in lower for w in ('organico', 'orgánico', 'libre pastoreo', 'free range')):
        return "Organico"
    return "No especificado"


def init_enhanced_tables():
    """Create or upgrade tables for enhanced data."""
    with get_db() as conn:
        c = conn.cursor()

        # Add lat/lng columns to egg_prices if they don't exist
        try:
            c.execute("ALTER TABLE egg_prices ADD COLUMN latitud REAL")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE egg_prices ADD COLUMN longitud REAL")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE egg_prices ADD COLUMN estado TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE egg_prices ADD COLUMN nombre_comercial TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE egg_prices ADD COLUMN catalogo TEXT")
        except sqlite3.OperationalError:
            pass

        # Create indexes on new columns
        try:
            c.execute("CREATE INDEX idx_estado ON egg_prices(estado)")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX idx_latitud ON egg_prices(latitud)")
        except sqlite3.OperationalError:
            pass

        # ── Wholesale prices table (SNIIM) ─────────────────────────────
        c.execute('''CREATE TABLE IF NOT EXISTS wholesale_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATE,
            producto TEXT,            -- 'Huevo blanco' or 'Huevo rojo'
            presentacion TEXT,        -- 'Mayoreo', 'Medio Mayoreo', 'Menudeo'
            precio_frecuente REAL,    -- frequent price $/kg
            precio_minimo REAL,       -- min price $/kg
            precio_maximo REAL,       -- max price $/kg
            mercado TEXT,             -- wholesale market name
            estado TEXT,              -- state name
            region TEXT,              -- region
            fuente TEXT DEFAULT 'SNIIM',
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fecha, producto, presentacion, mercado)
        )''')
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_ws_fecha ON wholesale_prices(fecha)")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_ws_producto ON wholesale_prices(producto)")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_ws_estado ON wholesale_prices(estado)")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_ws_presentacion ON wholesale_prices(presentacion)")
        except sqlite3.OperationalError:
            pass

        conn.commit()
    logger.info("Enhanced tables initialized")


# ═══════════════════════════════════════════════════════════════════════
# QQP BULK CSV INGESTION
# ═══════════════════════════════════════════════════════════════════════

def ingest_qqp_bulk(zip_path=None, max_files=None):
    """Ingest egg data from QQP bulk CSV zip into egg_prices.

    Downloads the 2025 zip if not found locally.
    Filters for category == 'Huevo' only.
    """
    if zip_path is None:
        zip_path = os.path.join(DATA_DIR, "qqp_2025.csv")

    if not os.path.exists(zip_path):
        logger.info("QQP 2025 bulk file not found. Downloading (~290 MB)...")
        url = "https://datos.profeco.gob.mx/datos_abiertos/file.php?t=2de3505b2d37e7db72557a09262d95c4"
        urllib.request.urlretrieve(url, zip_path)
        logger.info(f"Downloaded to {zip_path}")

    z = zipfile.ZipFile(zip_path)
    csv_files = sorted([f for f in z.namelist() if f.endswith('.csv')])
    if max_files:
        csv_files = csv_files[:max_files]

    total_inserted = 0

    for csv_file in csv_files:
        logger.info(f"Processing {csv_file}...")
        with z.open(csv_file) as f:
            text = io.TextIOWrapper(f, encoding='latin-1')
            reader = csv.DictReader(text)

            batch = []
            for row in reader:
                # Filter: only egg products
                if row.get('categoria', '').strip() != 'Huevo':
                    continue

                presentacion = row.get('presentacion', '')
                brand = normalize_brand(row.get('marca', ''))
                precio = float(row.get('precio', 0) or 0)
                if precio <= 0:
                    continue

                qty = extract_quantity(presentacion)
                egg_type = extract_egg_type(presentacion)
                precio_por_pieza = round(precio / qty, 2) if qty and qty > 0 else None
                estado = row.get('estado', '').strip()
                region = STATE_REGIONS.get(estado, 'Otro')
                cadena = row.get('cadena_comercial', '').strip()
                tipo_tienda = classify_store_type(cadena)

                lat = float(row.get('latitud', 0) or 0) or None
                lng = float(row.get('longitud', 0) or 0) or None

                # Use state as ciudad for bulk data (QQP bulk doesn't have ciudad_code)
                ciudad = estado

                batch.append((
                    row.get('\ufeffproducto', row.get('producto', '')).strip(),  # BOM-safe
                    presentacion,
                    brand,
                    qty,
                    egg_type,
                    None,  # tamaño
                    precio,
                    precio_por_pieza,
                    None,  # ciudad_code
                    ciudad,
                    region,
                    row.get('municipio', '').strip(),
                    cadena,
                    tipo_tienda,
                    row.get('nombre_comercial', '').strip(),
                    row.get('direccion', '').strip(),
                    row.get('fecha_registro', '').strip().replace('/', '-'),
                    lat,
                    lng,
                    estado,
                    row.get('nombre_comercial', '').strip(),
                    row.get('catalogo', '').strip(),
                ))

                if len(batch) >= 500:
                    total_inserted += _insert_bulk_batch(batch)
                    batch = []

            if batch:
                total_inserted += _insert_bulk_batch(batch)

        logger.info(f"  → cumulative egg rows inserted: {total_inserted:,}")

    logger.info(f"QQP bulk ingestion complete. Total egg rows: {total_inserted:,}")
    return total_inserted


def _insert_bulk_batch(batch):
    """Insert a batch of rows into egg_prices."""
    inserted = 0
    with get_db() as conn:
        c = conn.cursor()
        for row in batch:
            try:
                c.execute('''INSERT OR IGNORE INTO egg_prices
                    (producto_original, presentacion_profeco, marca, cantidad_piezas,
                     tipo_huevo, tamaño, precio, precio_por_pieza,
                     ciudad_code, ciudad, region, municipio,
                     cadena_comercial, tipo_tienda, establecimiento, direccion,
                     fecha_observacion, latitud, longitud, estado, nombre_comercial, catalogo)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', row)
                inserted += c.rowcount
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    return inserted


# ═══════════════════════════════════════════════════════════════════════
# SNIIM WHOLESALE PRICES SCRAPER
# ═══════════════════════════════════════════════════════════════════════

class SNIIMParser(HTMLParser):
    """Parse SNIIM HTML table for wholesale egg prices."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.current_market = None
        self.in_td = False
        self.in_header = False
        self.current_row = []
        self.current_data = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get('class', '')
        if tag == 'td':
            if 'encabDES' in cls:
                self.in_header = True
                self.current_data = ""
            elif 'Datos' in cls:
                self.in_td = True
                self.current_data = ""

    def handle_endtag(self, tag):
        if tag == 'td':
            if self.in_header:
                self.current_market = self.current_data.strip()
                self.in_header = False
            elif self.in_td:
                self.current_row.append(self.current_data.strip())
                self.in_td = False
        if tag == 'tr' and len(self.current_row) == 6:
            # fecha, producto, presentacion, precio_freq, precio_min, precio_max
            self.rows.append({
                'mercado': self.current_market,
                'fecha': self.current_row[0],
                'producto': self.current_row[1],
                'presentacion': self.current_row[2],
                'precio_frecuente': self.current_row[3],
                'precio_minimo': self.current_row[4],
                'precio_maximo': self.current_row[5],
            })
            self.current_row = []
        elif tag == 'tr':
            self.current_row = []

    def handle_data(self, data):
        if self.in_td or self.in_header:
            self.current_data += data


def parse_sniim_price(price_str):
    """Parse '$34.50' → 34.50"""
    if not price_str:
        return None
    clean = price_str.replace('$', '').replace(',', '').strip()
    try:
        return float(clean)
    except ValueError:
        return None


def parse_sniim_date(date_str):
    """Parse '21/01/2026' → '2026-01-21'"""
    try:
        dt = datetime.strptime(date_str.strip(), '%d/%m/%Y')
        return dt.strftime('%Y-%m-%d')
    except ValueError:
        return None


def fetch_sniim_wholesale(year=2026, months=None, weeks=None):
    """Fetch wholesale egg prices from SNIIM.

    Parameters:
        year: Year to fetch
        months: List of month numbers (1-12), defaults to all
        weeks: List of week numbers (1-5), defaults to all
    """
    if months is None:
        current_month = datetime.now().month if year == datetime.now().year else 12
        months = list(range(1, current_month + 1))
    if weeks is None:
        weeks = [1, 2, 3, 4, 5]

    url = "http://www.economia-sniim.gob.mx/SNIIM-Pecuarios-Nacionales/Hue.asp"
    total_inserted = 0

    for month in months:
        for week in weeks:
            logger.info(f"Fetching SNIIM: {year}-{month:02d} week {week}...")

            data = urllib.parse.urlencode({
                'prod': '0',       # All products (blanco + rojo)
                'destino': '0',    # All markets
                'sem': str(week),
                'mes': f'{month:02d}',
                'anio': str(year),
                'RegPag': '1000',  # Max results per page
            }).encode()

            req = urllib.request.Request(url, data=data)
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                html = resp.read().decode('latin-1')
            except Exception as e:
                logger.warning(f"  SNIIM fetch failed: {e}")
                time.sleep(2)
                continue

            parser = SNIIMParser()
            parser.feed(html)

            if not parser.rows:
                continue

            batch_inserted = _insert_sniim_batch(parser.rows)
            total_inserted += batch_inserted
            logger.info(f"  → {len(parser.rows)} rows parsed, {batch_inserted} new inserted")
            time.sleep(1)  # Rate limiting

    logger.info(f"SNIIM ingestion complete. Total rows: {total_inserted:,}")
    return total_inserted


def _insert_sniim_batch(rows):
    """Insert SNIIM rows into wholesale_prices."""
    inserted = 0
    with get_db() as conn:
        c = conn.cursor()
        for row in rows:
            fecha = parse_sniim_date(row['fecha'])
            if not fecha:
                continue

            pf = parse_sniim_price(row['precio_frecuente'])
            pm = parse_sniim_price(row['precio_minimo'])
            px = parse_sniim_price(row['precio_maximo'])
            if pf is None:
                continue

            mercado = row.get('mercado', '').strip()
            # Map market to state
            estado = None
            for code, st in SNIIM_MARKETS.items():
                if st.upper() in mercado.upper() or mercado.upper().startswith(st.upper()[:4]):
                    estado = st
                    break
            # Try extracting state from market name prefix (e.g., "AGS:", "BC:", "CAMP:")
            if not estado:
                prefix = mercado.split(':')[0].strip().upper() if ':' in mercado else ''
                state_prefix_map = {
                    'AGS': 'Aguascalientes', 'BC': 'Baja California', 'BCS': 'Baja California Sur',
                    'CAMP': 'Campeche', 'COAH': 'Coahuila', 'COL': 'Colima',
                    'CHIAPAS': 'Chiapas', 'CHIHUAHUA': 'Chihuahua',
                    'DF': 'Ciudad de Mexico', 'DGO': 'Durango',
                    'GRO': 'Guerrero', 'GTO': 'Guanajuato', 'HGO': 'Hidalgo',
                    'JAL': 'Jalisco', 'MEX': 'Estado de Mexico', 'MICH': 'Michoacan',
                    'MOR': 'Morelos', 'NAY': 'Nayarit', 'NL': 'Nuevo Leon',
                    'OAX': 'Oaxaca', 'PUE': 'Puebla', 'Q.R': 'Quintana Roo', 'Q.R.': 'Quintana Roo',
                    'QUERETARO': 'Queretaro', 'SIN': 'Sinaloa', 'SLP': 'San Luis Potosi',
                    'SON': 'Sonora', 'TAB': 'Tabasco', 'TAMPS': 'Tamaulipas',
                    'VER': 'Veracruz', 'YUC': 'Yucatan', 'ZAC': 'Zacatecas',
                }
                estado = state_prefix_map.get(prefix)

            region = STATE_REGIONS.get(estado, 'Otro') if estado else 'Otro'

            try:
                c.execute('''INSERT OR IGNORE INTO wholesale_prices
                    (fecha, producto, presentacion, precio_frecuente, precio_minimo,
                     precio_maximo, mercado, estado, region)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (fecha, row['producto'], row['presentacion'], pf, pm, px,
                     mercado, estado, region))
                inserted += c.rowcount
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    return inserted


# ═══════════════════════════════════════════════════════════════════════
# STATS DISPLAY
# ═══════════════════════════════════════════════════════════════════════

def print_stats():
    with get_db() as conn:
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM egg_prices")
        egg_count = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT estado) FROM egg_prices WHERE estado IS NOT NULL")
        state_count = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT marca) FROM egg_prices")
        brand_count = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT cadena_comercial) FROM egg_prices")
        chain_count = c.fetchone()[0]
        c.execute("SELECT MIN(fecha_observacion), MAX(fecha_observacion) FROM egg_prices")
        dates = c.fetchone()

        print("\n" + "="*60)
        print("   EGG PRICE DATABASE STATS")
        print("="*60)
        print(f"  Retail prices (egg_prices):  {egg_count:>10,} rows")
        print(f"  States:                      {state_count:>10}")
        print(f"  Brands:                      {brand_count:>10}")
        print(f"  Store chains:                {chain_count:>10}")
        print(f"  Date range:                  {dates[0]} → {dates[1]}")

        try:
            c.execute("SELECT COUNT(*) FROM wholesale_prices")
            ws_count = c.fetchone()[0]
            c.execute("SELECT COUNT(DISTINCT mercado) FROM wholesale_prices")
            ws_markets = c.fetchone()[0]
            c.execute("SELECT MIN(fecha), MAX(fecha) FROM wholesale_prices")
            ws_dates = c.fetchone()
            print(f"\n  Wholesale prices:            {ws_count:>10,} rows")
            print(f"  Wholesale markets:           {ws_markets:>10}")
            print(f"  Wholesale date range:        {ws_dates[0]} → {ws_dates[1]}")
        except sqlite3.OperationalError:
            print("\n  Wholesale prices:            (table not yet created)")

        print("="*60 + "\n")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    logger.info("Initializing enhanced tables...")
    init_enhanced_tables()

    # 1. Ingest QQP bulk data (eggs only)
    zip_path = os.path.join(DATA_DIR, "qqp_2025.csv")
    if os.path.exists(zip_path):
        logger.info("Found QQP 2025 bulk file. Ingesting egg data...")
        # Process first 4 files for POC (Jan-Feb 2025) to keep it manageable
        ingest_qqp_bulk(zip_path, max_files=4)
    else:
        logger.info("QQP 2025 bulk file not found at data/qqp_2025.csv")
        logger.info("Download it from: https://datos.profeco.gob.mx/datos_abiertos/qqp.php")
        logger.info("Or run with: python scripts/fetch_enhanced_data.py --download")
        if '--download' in sys.argv:
            ingest_qqp_bulk(zip_path, max_files=4)

    # 2. Fetch SNIIM wholesale prices
    logger.info("Fetching SNIIM wholesale egg prices...")
    current_year = datetime.now().year
    current_month = datetime.now().month
    fetch_sniim_wholesale(
        year=current_year,
        months=list(range(1, current_month + 1)),
        weeks=[1, 2, 3, 4, 5]
    )

    # Also fetch 2025 data for historical context
    if current_year > 2025:
        logger.info("Fetching 2025 SNIIM data for historical context...")
        fetch_sniim_wholesale(year=2025, months=list(range(1, 13)), weeks=[1, 2, 3, 4])

    # Print stats
    print_stats()


if __name__ == "__main__":
    main()
