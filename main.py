"""
Egg Price BI Dashboard - FastAPI Backend
Dashboard de Inteligencia de Precios de Huevo con Datos PROFECO
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import os
import threading
from datetime import datetime
from typing import Optional, List
from contextlib import contextmanager

from fetcher import fetch_all_cities, CITIES, DB_PATH, init_database, ensure_database, classify_store_type

# Initialize FastAPI
app = FastAPI(
    title="Egg Price BI Dashboard",
    description="Dashboard de Inteligencia de Precios de Huevo - Datos PROFECO Mexico",
    version="2.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure directories exist
os.makedirs("data", exist_ok=True)
os.makedirs("static", exist_ok=True)


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ============================================
# Core API Endpoints
# ============================================

@app.get("/")
async def root():
    """Serve the main dashboard."""
    try:
        return HTMLResponse(content=open("static/index.html").read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Dashboard cargando...</h1><p>Ejecute la carga de datos primero.</p>")


@app.get("/api/summary")
async def get_summary():
    """Get KPIs and totals for executive overview."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Total records
        cursor.execute("SELECT COUNT(*) FROM egg_prices")
        total_records = cursor.fetchone()[0]

        if total_records == 0:
            return {"success": True, "data": None, "message": "No hay datos. Ejecute la carga primero."}

        # Price statistics
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                COUNT(DISTINCT ciudad_code) as cities,
                COUNT(DISTINCT cadena_comercial) as stores,
                COUNT(DISTINCT marca) as brands,
                ROUND(AVG(precio_por_pieza), 2) as avg_price_per_egg
            FROM egg_prices
            WHERE precio > 0 AND precio < 500
        """)
        stats = dict(cursor.fetchone())

        # Date range
        cursor.execute("""
            SELECT MIN(fecha_observacion), MAX(fecha_observacion)
            FROM egg_prices WHERE fecha_observacion IS NOT NULL
        """)
        dates = cursor.fetchone()

        # Cheapest and most expensive cities
        cursor.execute("""
            SELECT ciudad, ROUND(AVG(precio), 2) as avg
            FROM egg_prices WHERE precio > 0 AND precio < 500
            GROUP BY ciudad ORDER BY avg ASC LIMIT 1
        """)
        cheapest_city = cursor.fetchone()

        cursor.execute("""
            SELECT ciudad, ROUND(AVG(precio), 2) as avg
            FROM egg_prices WHERE precio > 0 AND precio < 500
            GROUP BY ciudad ORDER BY avg DESC LIMIT 1
        """)
        expensive_city = cursor.fetchone()

        # Data quality metrics
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE marca IS NOT NULL")
        with_brand = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE cantidad_piezas IS NOT NULL")
        with_qty = cursor.fetchone()[0]

        return {
            "success": True,
            "data": {
                **stats,
                "date_min": dates[0] if dates else None,
                "date_max": dates[1] if dates else None,
                "cheapest_city": {"name": cheapest_city[0], "avg": cheapest_city[1]} if cheapest_city else None,
                "expensive_city": {"name": expensive_city[0], "avg": expensive_city[1]} if expensive_city else None,
                "data_quality": {
                    "total": total_records,
                    "with_brand": with_brand,
                    "with_quantity": with_qty,
                    "brand_pct": round(with_brand / total_records * 100, 1) if total_records else 0,
                    "qty_pct": round(with_qty / total_records * 100, 1) if total_records else 0
                }
            }
        }


@app.get("/api/prices")
async def get_prices(
    city: Optional[str] = None,
    chain: Optional[str] = None,
    brand: Optional[str] = None,
    tipo_huevo: Optional[str] = None,
    cantidad: Optional[int] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = Query(default=1000, le=5000)
):
    """Get all prices with optional filters."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = "SELECT * FROM egg_prices WHERE precio > 0 AND precio < 500"
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)
        if chain:
            query += " AND cadena_comercial = ?"
            params.append(chain)
        if brand:
            query += " AND marca = ?"
            params.append(brand)
        if tipo_huevo:
            query += " AND tipo_huevo = ?"
            params.append(tipo_huevo)
        if cantidad:
            query += " AND cantidad_piezas = ?"
            params.append(cantidad)
        if min_price:
            query += " AND precio >= ?"
            params.append(min_price)
        if max_price:
            query += " AND precio <= ?"
            params.append(max_price)

        query += f" ORDER BY fecha_observacion DESC LIMIT {limit}"

        cursor.execute(query, params)
        prices = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "prices": prices, "total": len(prices)}


@app.get("/api/cities")
async def get_cities():
    """Get city list with statistics."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                ciudad_code,
                ciudad,
                region,
                COUNT(*) as record_count,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                ROUND(AVG(precio_por_pieza), 2) as avg_per_egg,
                COUNT(DISTINCT cadena_comercial) as store_count,
                COUNT(DISTINCT municipio) as municipio_count,
                COUNT(DISTINCT marca) as brand_count
            FROM egg_prices
            WHERE precio > 0 AND precio < 500
            GROUP BY ciudad_code, ciudad, region
            ORDER BY avg_price ASC
        """)

        cities = []
        rows = cursor.fetchall()
        cheapest_avg = rows[0][4] if rows else 0

        for i, row in enumerate(rows):
            city = dict(row)
            city["rank"] = i + 1
            city["diff_from_cheapest"] = round(city["avg_price"] - cheapest_avg, 2) if cheapest_avg else 0
            city["percent_more"] = round((city["diff_from_cheapest"] / cheapest_avg) * 100, 1) if cheapest_avg else 0
            cities.append(city)

        return {"success": True, "cities": cities}


@app.get("/api/chains")
async def get_chains(city: Optional[str] = None):
    """Get chain list with statistics."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT
                cadena_comercial,
                tipo_tienda,
                COUNT(*) as record_count,
                COUNT(DISTINCT marca) as brand_variety,
                COUNT(DISTINCT cantidad_piezas) as pack_variety,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                ROUND(AVG(precio_por_pieza), 2) as avg_per_egg,
                COUNT(DISTINCT municipio) as locations
            FROM egg_prices
            WHERE precio > 0 AND precio < 500 AND cadena_comercial IS NOT NULL
        """
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)

        query += " GROUP BY cadena_comercial, tipo_tienda HAVING record_count >= 3 ORDER BY avg_price ASC"

        cursor.execute(query, params)
        chains = [dict(row) for row in cursor.fetchall()]

        if chains:
            cheapest_avg = chains[0]["avg_price"]
            for i, chain in enumerate(chains):
                chain["rank"] = i + 1
                chain["diff_from_cheapest"] = round(chain["avg_price"] - cheapest_avg, 2)
                chain["percent_more"] = round((chain["diff_from_cheapest"] / cheapest_avg) * 100, 1) if cheapest_avg else 0

        return {"success": True, "chains": chains, "city": city}


@app.get("/api/brands")
async def get_brands(city: Optional[str] = None):
    """Get brand list with statistics."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT
                marca,
                COUNT(*) as record_count,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                ROUND(AVG(precio_por_pieza), 2) as avg_per_egg,
                COUNT(DISTINCT ciudad_code) as cities,
                COUNT(DISTINCT cadena_comercial) as stores,
                GROUP_CONCAT(DISTINCT cantidad_piezas) as pack_sizes
            FROM egg_prices
            WHERE precio > 0 AND precio < 500 AND marca IS NOT NULL
        """
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)

        query += " GROUP BY marca HAVING record_count >= 3 ORDER BY record_count DESC"

        cursor.execute(query, params)
        brands = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "brands": brands, "city": city}


@app.get("/api/packs")
async def get_pack_sizes(city: Optional[str] = None):
    """Get pack size analysis with price per egg."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT
                cantidad_piezas,
                COUNT(*) as record_count,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                ROUND(AVG(precio_por_pieza), 3) as avg_per_egg,
                ROUND(MIN(precio_por_pieza), 3) as min_per_egg,
                ROUND(MAX(precio_por_pieza), 3) as max_per_egg,
                COUNT(DISTINCT marca) as brands,
                COUNT(DISTINCT cadena_comercial) as stores
            FROM egg_prices
            WHERE precio > 0 AND precio < 500 AND cantidad_piezas IS NOT NULL
        """
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)

        query += " GROUP BY cantidad_piezas ORDER BY cantidad_piezas"

        cursor.execute(query, params)
        packs = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "packs": packs, "city": city}


@app.get("/api/egg-types")
async def get_egg_types(city: Optional[str] = None):
    """Get egg type (blanco/rojo) comparison."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT
                tipo_huevo,
                COUNT(*) as record_count,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(AVG(precio_por_pieza), 3) as avg_per_egg,
                COUNT(DISTINCT marca) as brands,
                COUNT(DISTINCT cadena_comercial) as stores
            FROM egg_prices
            WHERE precio > 0 AND precio < 500
        """
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)

        query += " GROUP BY tipo_huevo ORDER BY avg_price"

        cursor.execute(query, params)
        types = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "egg_types": types, "city": city}


@app.get("/api/regions")
async def get_regions():
    """Get regional aggregates."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                region,
                COUNT(*) as record_count,
                COUNT(DISTINCT ciudad_code) as city_count,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                ROUND(AVG(precio_por_pieza), 3) as avg_per_egg
            FROM egg_prices
            WHERE precio > 0 AND precio < 500
            GROUP BY region
            ORDER BY avg_price ASC
        """)

        regions = [dict(row) for row in cursor.fetchall()]
        return {"success": True, "regions": regions}


@app.get("/api/municipios")
async def get_municipios(city: str = Query(...)):
    """Get municipios/alcaldias for a city."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                municipio,
                COUNT(*) as record_count,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price,
                ROUND(AVG(precio_por_pieza), 3) as avg_per_egg,
                COUNT(DISTINCT cadena_comercial) as stores,
                COUNT(DISTINCT marca) as brands
            FROM egg_prices
            WHERE ciudad_code = ? AND precio > 0 AND precio < 500 AND municipio IS NOT NULL
            GROUP BY municipio
            ORDER BY avg_price ASC
        """, (city,))

        municipios = [dict(row) for row in cursor.fetchall()]

        # Add rankings
        if municipios:
            cheapest = municipios[0]["avg_price"]
            for i, m in enumerate(municipios):
                m["rank"] = i + 1
                m["diff_from_cheapest"] = round(m["avg_price"] - cheapest, 2)
                m["percent_more"] = round((m["diff_from_cheapest"] / cheapest) * 100, 1) if cheapest else 0

        return {"success": True, "municipios": municipios, "city": city}


@app.get("/api/best-deals")
async def get_best_deals(city: Optional[str] = None, limit: int = 20):
    """Get best deals (lowest price per egg)."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT
                producto_original,
                marca,
                cantidad_piezas,
                tipo_huevo,
                precio,
                precio_por_pieza,
                cadena_comercial,
                establecimiento,
                municipio,
                ciudad,
                fecha_observacion
            FROM egg_prices
            WHERE precio > 0 AND precio < 500 AND precio_por_pieza IS NOT NULL
        """
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)

        query += f" ORDER BY precio_por_pieza ASC LIMIT {limit}"

        cursor.execute(query, params)
        deals = [dict(row) for row in cursor.fetchall()]

        # Get average for comparison
        avg_query = "SELECT AVG(precio_por_pieza) FROM egg_prices WHERE precio_por_pieza IS NOT NULL"
        if city:
            avg_query += f" AND ciudad_code = '{city}'"
        cursor.execute(avg_query)
        avg_per_egg = cursor.fetchone()[0] or 0

        for deal in deals:
            if avg_per_egg:
                deal["savings_pct"] = round((avg_per_egg - deal["precio_por_pieza"]) / avg_per_egg * 100, 1)

        return {"success": True, "deals": deals, "avg_per_egg": round(avg_per_egg, 3)}


@app.get("/api/price-comparison")
async def get_price_comparison(
    marca: Optional[str] = None,
    cantidad: Optional[int] = None
):
    """Compare same product across stores."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = """
            SELECT
                cadena_comercial,
                ciudad,
                COUNT(*) as records,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(MIN(precio), 2) as min_price,
                ROUND(MAX(precio), 2) as max_price
            FROM egg_prices
            WHERE precio > 0 AND precio < 500
        """
        params = []

        if marca:
            query += " AND marca = ?"
            params.append(marca)
        if cantidad:
            query += " AND cantidad_piezas = ?"
            params.append(cantidad)

        query += " GROUP BY cadena_comercial, ciudad HAVING records >= 2 ORDER BY avg_price ASC"

        cursor.execute(query, params)
        comparison = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "comparison": comparison, "marca": marca, "cantidad": cantidad}


@app.get("/api/data-quality")
async def get_data_quality():
    """Get detailed data quality metrics."""
    with get_db() as conn:
        cursor = conn.cursor()

        metrics = {}

        cursor.execute("SELECT COUNT(*) FROM egg_prices")
        total = cursor.fetchone()[0]
        metrics["total_records"] = total

        if total == 0:
            return {"success": True, "metrics": metrics}

        # Brand coverage
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE marca IS NOT NULL")
        metrics["with_brand"] = cursor.fetchone()[0]
        metrics["brand_pct"] = round(metrics["with_brand"] / total * 100, 1)

        # Quantity coverage
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE cantidad_piezas IS NOT NULL")
        metrics["with_quantity"] = cursor.fetchone()[0]
        metrics["quantity_pct"] = round(metrics["with_quantity"] / total * 100, 1)

        # Price per piece coverage
        cursor.execute("SELECT COUNT(*) FROM egg_prices WHERE precio_por_pieza IS NOT NULL")
        metrics["with_price_per_egg"] = cursor.fetchone()[0]
        metrics["price_per_egg_pct"] = round(metrics["with_price_per_egg"] / total * 100, 1)

        # Unique values
        cursor.execute("SELECT COUNT(DISTINCT marca) FROM egg_prices WHERE marca IS NOT NULL")
        metrics["unique_brands"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT cadena_comercial) FROM egg_prices")
        metrics["unique_chains"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT ciudad_code) FROM egg_prices")
        metrics["unique_cities"] = cursor.fetchone()[0]

        return {"success": True, "metrics": metrics}


@app.get("/api/stats/distribution")
async def get_distribution(city: Optional[str] = None, field: str = "precio"):
    """Get price distribution data for histograms."""
    with get_db() as conn:
        cursor = conn.cursor()

        valid_fields = ["precio", "precio_por_pieza"]
        if field not in valid_fields:
            field = "precio"

        query = f"SELECT {field} FROM egg_prices WHERE {field} IS NOT NULL AND {field} > 0 AND {field} < 500"
        params = []

        if city:
            query += " AND ciudad_code = ?"
            params.append(city)

        cursor.execute(query, params)
        values = [row[0] for row in cursor.fetchall()]

        if not values:
            return {"success": True, "distribution": None}

        # Calculate histogram bins
        min_val = min(values)
        max_val = max(values)
        num_bins = 20
        bin_width = (max_val - min_val) / num_bins

        bins = []
        counts = []
        for i in range(num_bins):
            bin_start = min_val + i * bin_width
            bin_end = bin_start + bin_width
            count = sum(1 for v in values if bin_start <= v < bin_end)
            if i == num_bins - 1:
                count = sum(1 for v in values if bin_start <= v <= bin_end)
            bins.append(round((bin_start + bin_end) / 2, 2))
            counts.append(count)

        # Statistics
        n = len(values)
        mean = sum(values) / n
        sorted_vals = sorted(values)
        median = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        variance = sum((v - mean) ** 2 for v in values) / n
        std_dev = variance ** 0.5

        return {
            "success": True,
            "distribution": {
                "bins": bins,
                "counts": counts,
                "bin_width": round(bin_width, 2)
            },
            "statistics": {
                "count": n,
                "mean": round(mean, 2),
                "median": round(median, 2),
                "std_dev": round(std_dev, 2),
                "min": round(min_val, 2),
                "max": round(max_val, 2)
            },
            "field": field,
            "city": city
        }


@app.get("/api/map-data")
async def get_map_data():
    """Get data for Mexico map visualization."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                ciudad_code,
                ciudad,
                region,
                ROUND(AVG(precio), 2) as avg_price,
                ROUND(AVG(precio_por_pieza), 3) as avg_per_egg,
                COUNT(*) as record_count
            FROM egg_prices
            WHERE precio > 0 AND precio < 500
            GROUP BY ciudad_code, ciudad, region
        """)

        # City coordinates
        coords = {
            "0901": {"lat": 19.4326, "lon": -99.1332},
            "1401": {"lat": 20.6597, "lon": -103.3496},
            "1901": {"lat": 25.6866, "lon": -100.3161},
            "0201": {"lat": 32.5149, "lon": -117.0382},
            "2101": {"lat": 19.0414, "lon": -98.2063},
            "2301": {"lat": 21.1619, "lon": -86.8515},
            "3101": {"lat": 20.9674, "lon": -89.5926},
            "1101": {"lat": 21.1250, "lon": -101.6860},
        }

        map_data = []
        for row in cursor.fetchall():
            data = dict(row)
            if data["ciudad_code"] in coords:
                data.update(coords[data["ciudad_code"]])
            map_data.append(data)

        return {"success": True, "map_data": map_data}


@app.get("/api/data-status")
async def get_data_status():
    """Get current data status."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM egg_prices")
        total = cursor.fetchone()[0]

        cursor.execute("""
            SELECT MIN(fecha_observacion), MAX(fecha_observacion)
            FROM egg_prices WHERE fecha_observacion IS NOT NULL
        """)
        dates = cursor.fetchone()

        cursor.execute("SELECT MAX(fetched_at) FROM egg_prices")
        last_fetch = cursor.fetchone()[0]

        return {
            "success": True,
            "status": {
                "total_records": total,
                "date_min": dates[0] if dates else None,
                "date_max": dates[1] if dates else None,
                "date_range": f"{dates[0]} a {dates[1]}" if dates[0] else "Sin datos",
                "last_fetch": last_fetch,
                "cities": list(CITIES.keys())
            }
        }


@app.post("/api/fetch-fresh")
async def fetch_fresh_data(background: bool = True):
    """Fetch fresh data from PROFECO API."""
    if background:
        thread = threading.Thread(target=fetch_all_cities, daemon=True)
        thread.start()
        return {"success": True, "message": "Carga de datos iniciada en segundo plano"}
    else:
        result = fetch_all_cities()
        return {"success": True, "result": result}


# ============================================
# Static Files & Startup
# ============================================

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup_event():
    """Initialize on startup and auto-fetch if database is empty."""
    print("=" * 50)
    print("Dashboard de Precios de Huevo - Iniciando...")
    print("=" * 50)

    # Ensure database exists
    if not os.path.exists(DB_PATH):
        print("Base de datos no encontrada, inicializando...")
        init_database()

    # Check if we have data
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM egg_prices")
            count = cursor.fetchone()[0]
            print(f"Registros en base de datos: {count}")

            if count < 100:
                print("\nPocos datos detectados. Iniciando carga automatica...")
                # Fetch in background thread so server starts immediately
                thread = threading.Thread(target=fetch_all_cities, daemon=True)
                thread.start()
                print("Carga de datos iniciada en segundo plano...")
            else:
                print(f"Base de datos lista con {count} registros")
    except Exception as e:
        print(f"Error verificando base de datos: {e}")
        print("Inicializando base de datos nueva...")
        init_database()
        thread = threading.Thread(target=fetch_all_cities, daemon=True)
        thread.start()

    print(f"\nServidor listo en http://0.0.0.0:8000")
    print("=" * 50)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
