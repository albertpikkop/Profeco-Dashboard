"""
PROFECO Egg Price Data Fetcher
Fetches egg price data from PROFECO API for 8 Mexican cities
Enhanced extraction for precise product segmentation
"""

import requests
import sqlite3
import re
import time
from datetime import datetime
from contextlib import contextmanager

# Database path
DB_PATH = "data/eggs.db"

# PROFECO API
PROFECO_API = "https://qqp.profeco.gob.mx/api/precios"

# Target cities with region classification
CITIES = {
    "0901": {"name": "Ciudad de Mexico", "region": "Centro"},
    "1401": {"name": "Guadalajara", "region": "Bajio"},
    "1901": {"name": "Monterrey", "region": "Norte"},
    "0201": {"name": "Tijuana", "region": "Norte"},
    "2101": {"name": "Puebla", "region": "Centro"},
    "2301": {"name": "Cancun", "region": "Sureste"},
    "3101": {"name": "Merida", "region": "Sureste"},
    "1101": {"name": "Leon", "region": "Bajio"},
}

# Store type classification - expanded
STORE_TYPE_MAPPING = {
    'supermarket': ['WAL-MART', 'WALMART', 'BODEGA AURRERA', 'SORIANA', 'CHEDRAUI', 'LA COMER',
                    'SUPERISSSTE', 'SUMESA', 'SUPERAMA', 'CITY MARKET', 'FRESKO',
                    'HEB', 'S-MART', 'SMART & FINAL', 'MEGA', 'COMERCIAL MEXICANA', 'ALSUPER'],
    'wholesale': ['COSTCO', 'SAMS CLUB', 'SAM\'S CLUB', 'PRICESMART', 'CENTRAL DE ABASTOS'],
    'convenience': ['OXXO', '7 ELEVEN', '7-ELEVEN', 'EXTRA', 'CIRCLE K', 'GO MART', 'KIOSKO'],
    'market': ['MERCADO', 'TIANGUIS', 'ABARROTERO', 'ABARROTES'],
    'specialty': ['PANADERIA', 'TORTILLERIA', 'CARNICERIA', 'POLLERIA', 'CREMERIA', 'HUEVERIA', 'GRANJA'],
}

# Brand patterns - COMPREHENSIVE list based on actual PROFECO data
BRAND_PATTERNS = [
    # Major national brands
    (r'\bBACHOCO\b', 'Bachoco'),
    (r'\bSAN JUAN\b', 'San Juan'),
    (r'\bEL CALVARIO\b', 'El Calvario'),
    (r'\bPILGRIM', 'Pilgrim\'s'),
    # Store brands
    (r'\bGREAT VALUE\b', 'Great Value'),
    (r'\bKIRKLAND\b', 'Kirkland'),
    (r'\bMEMBERS? MARK\b', 'Member\'s Mark'),
    (r'\bAURRERA\b', 'Aurrera'),
    (r'\bSORIANA\b', 'Soriana'),
    (r'\bHEB\b', 'HEB'),
    (r'\bHILL COUNTRY FARE\b', 'Hill Country Fare'),
    # Regional brands
    (r'\bTEHUACAN\b', 'Tehuacan'),
    (r'\bCRIO\b', 'Crio'),
    (r'\bD[\'`]?ORO\b', 'D\'Oro'),
    (r'\bGUADALUPE\b', 'Guadalupe'),
    (r'\bJEVSA\b', 'Jevsa'),
    (r'\bMAM[AÁ] GALLINA\b', 'Mama Gallina'),
    (r'\bRANCHO VIEJO\b', 'Rancho Viejo'),
    (r'\bLA ASTURIANA\b', 'La Asturiana'),
    (r'\bSANTA LUCIA\b', 'Santa Lucia'),
    (r'\bSANTA CLARA\b', 'Santa Clara'),
    (r'\bLA GRANJA\b', 'La Granja'),
    (r'\bEL CORRAL\b', 'El Corral'),
    (r'\bEL RANCHO\b', 'El Rancho'),
    (r'\bDON JOSE\b', 'Don Jose'),
    (r'\bSAN PEDRO\b', 'San Pedro'),
    (r'\bSAN PABLO\b', 'San Pablo'),
    (r'\bLA HACIENDA\b', 'La Hacienda'),
    (r'\bEL DORADO\b', 'El Dorado'),
    (r'\bPROTEINA\b', 'Proteina'),
    (r'\bNUTRIHUEVO\b', 'Nutrihuevo'),
    (r'\bRANCHERA\b', 'Ranchera'),
    (r'\bCAMPO REAL\b', 'Campo Real'),
]


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_database():
    """Initialize the database schema with egg-specific fields."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Drop and recreate for clean schema
        cursor.execute('DROP TABLE IF EXISTS egg_prices')

        # Main egg prices table with comprehensive fields
        cursor.execute('''
            CREATE TABLE egg_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Raw PROFECO data
                producto_original TEXT,
                presentacion_profeco TEXT,

                -- Extracted/Parsed fields
                marca TEXT,
                cantidad_piezas INTEGER,
                tipo_huevo TEXT,
                tamaño TEXT,

                -- Calculated fields
                precio REAL,
                precio_por_pieza REAL,

                -- Location data
                ciudad_code TEXT,
                ciudad TEXT,
                region TEXT,
                municipio TEXT,

                -- Store data
                cadena_comercial TEXT,
                tipo_tienda TEXT,
                establecimiento TEXT,
                direccion TEXT,

                -- Metadata
                fecha_observacion DATE,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(producto_original, establecimiento, fecha_observacion, precio)
            )
        ''')

        # Cities reference table
        cursor.execute('DROP TABLE IF EXISTS cities')
        cursor.execute('''
            CREATE TABLE cities (
                codigo TEXT PRIMARY KEY,
                nombre TEXT,
                region TEXT
            )
        ''')

        # Insert cities
        for code, info in CITIES.items():
            cursor.execute(
                "INSERT OR REPLACE INTO cities (codigo, nombre, region) VALUES (?, ?, ?)",
                (code, info["name"], info["region"])
            )

        # Create indexes for fast queries
        cursor.execute('CREATE INDEX idx_ciudad ON egg_prices(ciudad_code)')
        cursor.execute('CREATE INDEX idx_cadena ON egg_prices(cadena_comercial)')
        cursor.execute('CREATE INDEX idx_fecha ON egg_prices(fecha_observacion)')
        cursor.execute('CREATE INDEX idx_tipo_huevo ON egg_prices(tipo_huevo)')
        cursor.execute('CREATE INDEX idx_marca ON egg_prices(marca)')
        cursor.execute('CREATE INDEX idx_tipo_tienda ON egg_prices(tipo_tienda)')
        cursor.execute('CREATE INDEX idx_region ON egg_prices(region)')
        cursor.execute('CREATE INDEX idx_cantidad ON egg_prices(cantidad_piezas)')

        conn.commit()
    print("Database initialized successfully")


def classify_store_type(store_name):
    """Classify store into type based on name patterns."""
    if not store_name:
        return 'otro'
    store_upper = store_name.upper()
    for store_type, patterns in STORE_TYPE_MAPPING.items():
        for pattern in patterns:
            if pattern in store_upper:
                return store_type
    return 'otro'


def extract_brand(product_name):
    """Extract brand name from product description."""
    if not product_name:
        return None
    product_upper = product_name.upper()
    for pattern, brand in BRAND_PATTERNS:
        if re.search(pattern, product_upper, re.IGNORECASE):
            return brand

    # Try to extract brand from "HUEVO, BRANDNAME, PAQUETE..." format
    match = re.match(r'HUEVO,\s*([^,]+),', product_upper)
    if match:
        potential_brand = match.group(1).strip()
        # Exclude common non-brand words
        non_brands = ['BLANCO', 'ROJO', 'MARRON', 'PAQUETE', 'CON', 'DOCENA', 'KG', 'KILO']
        if potential_brand not in non_brands and len(potential_brand) > 2:
            return potential_brand.title()

    return None


def extract_egg_type(product_name):
    """Determine egg type (blanco/rojo) from product name."""
    if not product_name:
        return 'no especificado'
    product_upper = product_name.upper()

    if 'ROJO' in product_upper or 'MARRON' in product_upper or 'BROWN' in product_upper:
        return 'rojo'
    elif 'BLANCO' in product_upper or 'WHITE' in product_upper:
        return 'blanco'

    return 'no especificado'


def extract_size(product_name):
    """Extract egg size if specified."""
    if not product_name:
        return None
    product_upper = product_name.upper()

    sizes = {
        'JUMBO': 'jumbo',
        'EXTRA GRANDE': 'extra grande',
        'EXTRAGRANDE': 'extra grande',
        'GRANDE': 'grande',
        'MEDIANO': 'mediano',
        'CHICO': 'chico',
        'PEQUEÑO': 'pequeño',
    }

    for pattern, size in sizes.items():
        if pattern in product_upper:
            return size

    return None


def extract_quantity(product_name):
    """Extract number of eggs from product description - IMPROVED."""
    if not product_name:
        return None

    product_upper = product_name.upper()

    # Pattern 1: "PAQUETE CON 12", "PAQUETE C/12", "PAQUETE CON 18", etc.
    patterns = [
        r'PAQUETE\s*(?:CON|C/?)\s*(\d+)',
        r'PAQUETE\s+(\d+)',
        r'(?:CON|C/?)\s*(\d+)\s*(?:PIEZAS?|PZS?|HUEVOS?)?',
        r'(\d+)\s*(?:PIEZAS?|PZS?|HUEVOS?)',
        r'(\d+)\s*(?:PACK|PAQUETE)',
    ]

    for pattern in patterns:
        match = re.search(pattern, product_upper)
        if match:
            qty = int(match.group(1))
            # Validate reasonable quantities
            if qty in [4, 6, 8, 10, 12, 15, 18, 20, 24, 30, 36, 60, 90, 180, 360]:
                return qty

    # Check for "DOCENA" = 12
    if re.search(r'\bDOCENA\b', product_upper):
        if re.search(r'\bMEDIA\s+DOCENA\b', product_upper):
            return 6
        return 12

    # Check for kg (estimate ~16 eggs per kg for calculation display)
    if re.search(r'\b1\s*(?:KG|KILO)\b', product_upper):
        return None  # Don't estimate, mark as kg-based

    return None


def calculate_price_per_egg(price, quantity):
    """Calculate price per individual egg."""
    if price and quantity and quantity > 0:
        return round(price / quantity, 2)
    return None


def parse_product_description(producto):
    """
    Comprehensive product description parser.
    Returns dict with all extracted fields.
    """
    result = {
        'marca': extract_brand(producto),
        'cantidad_piezas': extract_quantity(producto),
        'tipo_huevo': extract_egg_type(producto),
        'tamaño': extract_size(producto),
    }
    return result


def fetch_city_eggs(city_code):
    """Fetch egg prices for a single city."""
    city_info = CITIES.get(city_code, {"name": city_code, "region": "Unknown"})

    try:
        params = {"clave_ciudad": city_code, "busqueda": "huevo"}
        response = requests.get(PROFECO_API, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            print(f"  API returned success=false for {city_info['name']}")
            return {"city": city_info["name"], "fetched": 0, "new": 0}

        products = data.get("data", {}).get("productos", [])
        new_count = 0

        with get_db() as conn:
            cursor = conn.cursor()

            for p in products:
                try:
                    producto = p.get("producto", "")
                    precio = p.get("precio")

                    # Skip if no valid price
                    if not precio or precio <= 0:
                        continue

                    # Parse product description
                    parsed = parse_product_description(producto)

                    # Calculate price per piece
                    precio_por_pieza = calculate_price_per_egg(precio, parsed['cantidad_piezas'])

                    # Classify store
                    cadena = p.get("cadena_comercial", "")
                    tipo_tienda = classify_store_type(cadena)

                    cursor.execute('''
                        INSERT OR IGNORE INTO egg_prices
                        (producto_original, presentacion_profeco, marca, cantidad_piezas,
                         tipo_huevo, tamaño, precio, precio_por_pieza,
                         ciudad_code, ciudad, region, municipio,
                         cadena_comercial, tipo_tienda, establecimiento, direccion,
                         fecha_observacion)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        producto,
                        p.get("tipo_producto"),
                        parsed['marca'],
                        parsed['cantidad_piezas'],
                        parsed['tipo_huevo'],
                        parsed['tamaño'],
                        precio,
                        precio_por_pieza,
                        city_code,
                        city_info["name"],
                        city_info["region"],
                        p.get("municipio"),
                        cadena,
                        tipo_tienda,
                        p.get("establecimiento"),
                        p.get("direccion"),
                        p.get("fecha_observacion")
                    ))

                    if cursor.rowcount > 0:
                        new_count += 1

                except Exception as e:
                    print(f"    Error processing record: {e}")
                    continue

            conn.commit()

        return {"city": city_info["name"], "fetched": len(products), "new": new_count}

    except requests.exceptions.RequestException as e:
        print(f"  Network error for {city_info['name']}: {e}")
        return {"city": city_info["name"], "fetched": 0, "new": 0, "error": str(e)}
    except Exception as e:
        print(f"  Error for {city_info['name']}: {e}")
        return {"city": city_info["name"], "fetched": 0, "new": 0, "error": str(e)}


def fetch_all_cities():
    """Fetch egg prices from all target cities."""
    print("\n" + "=" * 60)
    print("PROFECO Egg Price Fetcher - Starting data collection")
    print("=" * 60)
    print(f"Fetching from {len(CITIES)} cities...\n")

    # Initialize database
    init_database()

    results = []
    total_fetched = 0
    total_new = 0

    for city_code in CITIES:
        print(f"Fetching: {CITIES[city_code]['name']}...", end=" ")
        result = fetch_city_eggs(city_code)
        results.append(result)
        total_fetched += result.get("fetched", 0)
        total_new += result.get("new", 0)
        print(f"Got {result.get('fetched', 0)} records, {result.get('new', 0)} new")

        # Rate limiting
        time.sleep(0.5)

    print("\n" + "=" * 60)
    print(f"COMPLETE: {total_fetched} total records, {total_new} new records")
    print("=" * 60)

    return {
        "success": True,
        "results": results,
        "total_fetched": total_fetched,
        "total_new": total_new,
        "timestamp": datetime.now().isoformat()
    }


def get_data_summary():
    """Get detailed summary of data in database."""
    with get_db() as conn:
        cursor = conn.cursor()

        summary = {}

        cursor.execute("SELECT COUNT(*) FROM egg_prices")
        summary['total_records'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT ciudad_code) FROM egg_prices")
        summary['cities'] = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(fecha_observacion), MAX(fecha_observacion) FROM egg_prices")
        dates = cursor.fetchone()
        summary['date_range'] = f"{dates[0]} to {dates[1]}" if dates[0] else "No data"

        cursor.execute("SELECT COUNT(DISTINCT cadena_comercial) FROM egg_prices")
        summary['chains'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT marca) FROM egg_prices WHERE marca IS NOT NULL")
        summary['brands'] = cursor.fetchone()[0]

        # Data quality metrics
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE marca IS NOT NULL")
        summary['with_brand'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE cantidad_piezas IS NOT NULL")
        summary['with_quantity'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE precio_por_pieza IS NOT NULL")
        summary['with_price_per_piece'] = cursor.fetchone()[0]

        return summary


def print_data_quality_report():
    """Print detailed data quality report."""
    with get_db() as conn:
        cursor = conn.cursor()

        print("\n" + "=" * 70)
        print("DATA QUALITY REPORT")
        print("=" * 70)

        cursor.execute("SELECT COUNT(*) FROM egg_prices")
        total = cursor.fetchone()[0]
        print(f"\nTotal Records: {total}")

        # Brand extraction
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE marca IS NOT NULL")
        with_brand = cursor.fetchone()[0]
        print(f"\nBrand Extracted: {with_brand}/{total} ({with_brand/total*100:.1f}%)")

        cursor.execute("""
            SELECT marca, COUNT(*) as cnt
            FROM egg_prices
            GROUP BY marca
            ORDER BY cnt DESC
            LIMIT 15
        """)
        print("  Top Brands:")
        for row in cursor.fetchall():
            print(f"    {row[0] or 'NULL'}: {row[1]}")

        # Quantity extraction
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE cantidad_piezas IS NOT NULL")
        with_qty = cursor.fetchone()[0]
        print(f"\nQuantity Extracted: {with_qty}/{total} ({with_qty/total*100:.1f}%)")

        cursor.execute("""
            SELECT cantidad_piezas, COUNT(*) as cnt
            FROM egg_prices
            GROUP BY cantidad_piezas
            ORDER BY cnt DESC
        """)
        print("  Pack Sizes:")
        for row in cursor.fetchall():
            print(f"    {row[0] or 'NULL'} pcs: {row[1]}")

        # Egg type
        cursor.execute("""
            SELECT tipo_huevo, COUNT(*) as cnt
            FROM egg_prices
            GROUP BY tipo_huevo
            ORDER BY cnt DESC
        """)
        print("\nEgg Types:")
        for row in cursor.fetchall():
            print(f"    {row[0]}: {row[1]}")

        # Store types
        cursor.execute("""
            SELECT tipo_tienda, COUNT(*) as cnt
            FROM egg_prices
            GROUP BY tipo_tienda
            ORDER BY cnt DESC
        """)
        print("\nStore Types:")
        for row in cursor.fetchall():
            print(f"    {row[0]}: {row[1]}")

        # Sample products without quantity (for debugging)
        cursor.execute("""
            SELECT DISTINCT producto_original
            FROM egg_prices
            WHERE cantidad_piezas IS NULL
            LIMIT 10
        """)
        print("\nSample products without quantity (needs pattern fix):")
        for row in cursor.fetchall():
            print(f"    {row[0]}")


if __name__ == "__main__":
    # Run fetcher
    result = fetch_all_cities()

    # Print quality report
    print_data_quality_report()

    # Print summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    summary = get_data_summary()
    for key, value in summary.items():
        print(f"  {key}: {value}")
