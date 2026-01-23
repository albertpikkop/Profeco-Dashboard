"""
Analytics Module for Egg Price Dashboard
Statistical calculations and data analysis
"""

import sqlite3
import math
from contextlib import contextmanager
from typing import List, Dict, Any, Optional

DB_PATH = "data/eggs.db"


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def calculate_statistics(prices: List[float]) -> Dict[str, Any]:
    """Calculate comprehensive statistics for a list of prices."""
    if not prices:
        return {}

    n = len(prices)
    sorted_prices = sorted(prices)

    # Mean
    mean = sum(prices) / n

    # Median
    if n % 2 == 0:
        median = (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) / 2
    else:
        median = sorted_prices[n // 2]

    # Mode (most common value, rounded to 2 decimals)
    rounded = [round(p, 2) for p in prices]
    mode = max(set(rounded), key=rounded.count)

    # Standard deviation
    variance = sum((p - mean) ** 2 for p in prices) / n
    std_dev = math.sqrt(variance)

    # Percentiles
    def percentile(data, p):
        k = (len(data) - 1) * p / 100
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return data[int(k)]
        return data[f] * (c - k) + data[c] * (k - f)

    return {
        "count": n,
        "mean": round(mean, 2),
        "median": round(median, 2),
        "mode": mode,
        "min": round(sorted_prices[0], 2),
        "max": round(sorted_prices[-1], 2),
        "range": round(sorted_prices[-1] - sorted_prices[0], 2),
        "std_dev": round(std_dev, 2),
        "variance": round(variance, 2),
        "p10": round(percentile(sorted_prices, 10), 2),
        "p25": round(percentile(sorted_prices, 25), 2),
        "p75": round(percentile(sorted_prices, 75), 2),
        "p90": round(percentile(sorted_prices, 90), 2),
        "iqr": round(percentile(sorted_prices, 75) - percentile(sorted_prices, 25), 2)
    }


def detect_outliers(prices: List[float], z_threshold: float = 2.5) -> List[Dict]:
    """Detect outliers using Z-score method."""
    if len(prices) < 3:
        return []

    mean = sum(prices) / len(prices)
    std_dev = math.sqrt(sum((p - mean) ** 2 for p in prices) / len(prices))

    if std_dev == 0:
        return []

    outliers = []
    for price in prices:
        z_score = (price - mean) / std_dev
        if abs(z_score) > z_threshold:
            outliers.append({
                "price": round(price, 2),
                "z_score": round(z_score, 2),
                "type": "high" if z_score > 0 else "low",
                "deviation_percent": round(abs(price - mean) / mean * 100, 1)
            })

    return sorted(outliers, key=lambda x: abs(x["z_score"]), reverse=True)


def calculate_histogram_bins(prices: List[float], num_bins: int = 15) -> Dict:
    """Calculate histogram bin data for price distribution."""
    if not prices:
        return {"bins": [], "counts": []}

    min_price = min(prices)
    max_price = max(prices)
    bin_width = (max_price - min_price) / num_bins

    bins = []
    counts = []

    for i in range(num_bins):
        bin_start = min_price + i * bin_width
        bin_end = bin_start + bin_width
        bin_center = (bin_start + bin_end) / 2

        count = sum(1 for p in prices if bin_start <= p < bin_end)
        if i == num_bins - 1:
            count = sum(1 for p in prices if bin_start <= p <= bin_end)

        bins.append(round(bin_center, 2))
        counts.append(count)

    return {
        "bins": bins,
        "counts": counts,
        "bin_width": round(bin_width, 2),
        "min": round(min_price, 2),
        "max": round(max_price, 2)
    }


def calculate_savings(cheapest_avg: float, expensive_avg: float, items_per_month: int = 30) -> Dict:
    """Calculate potential monthly savings."""
    if not cheapest_avg or not expensive_avg:
        return {}

    diff_per_item = expensive_avg - cheapest_avg
    monthly_savings = diff_per_item * items_per_month
    annual_savings = monthly_savings * 12
    percent_savings = (diff_per_item / expensive_avg) * 100

    return {
        "diff_per_item": round(diff_per_item, 2),
        "monthly_savings": round(monthly_savings, 2),
        "annual_savings": round(annual_savings, 2),
        "percent_savings": round(percent_savings, 1),
        "items_per_month": items_per_month
    }


def get_price_percentile(price: float, all_prices: List[float]) -> int:
    """Get percentile rank of a price within all prices."""
    if not all_prices:
        return 50

    below = sum(1 for p in all_prices if p < price)
    return round(below / len(all_prices) * 100)


def rank_stores_multiaxis(stores_data: List[Dict]) -> List[Dict]:
    """
    Rank stores across multiple axes:
    - Price (lower is better)
    - Variety (more product types is better)
    - Coverage (more locations is better)
    - Consistency (lower std dev is better)
    """
    if not stores_data:
        return []

    # Normalize scores (0-100, higher is better)
    def normalize(values, reverse=False):
        if not values:
            return []
        min_v, max_v = min(values), max(values)
        if max_v == min_v:
            return [50] * len(values)
        if reverse:
            return [round((max_v - v) / (max_v - min_v) * 100) for v in values]
        return [round((v - min_v) / (max_v - min_v) * 100) for v in values]

    prices = [s.get("avg_price", 0) for s in stores_data]
    varieties = [s.get("product_variety", 0) for s in stores_data]
    coverages = [s.get("locations", 0) for s in stores_data]
    consistencies = [s.get("std_dev", 0) for s in stores_data]

    price_scores = normalize(prices, reverse=True)  # Lower price = higher score
    variety_scores = normalize(varieties)
    coverage_scores = normalize(coverages)
    consistency_scores = normalize(consistencies, reverse=True)  # Lower std dev = higher score

    results = []
    for i, store in enumerate(stores_data):
        overall_score = (
            price_scores[i] * 0.4 +  # Price is most important
            variety_scores[i] * 0.25 +
            coverage_scores[i] * 0.2 +
            consistency_scores[i] * 0.15
        )

        results.append({
            **store,
            "scores": {
                "price": price_scores[i],
                "variety": variety_scores[i],
                "coverage": coverage_scores[i],
                "consistency": consistency_scores[i],
                "overall": round(overall_score)
            }
        })

    return sorted(results, key=lambda x: x["scores"]["overall"], reverse=True)


def build_sankey_data(city_chain_data: List[Dict]) -> Dict:
    """
    Build Sankey diagram data showing flow:
    Region -> City -> Chain -> Price Range
    """
    regions = set()
    cities = set()
    chains = set()
    price_ranges = ["$0-30", "$30-45", "$45-60", "$60+"]

    # Collect unique nodes
    for item in city_chain_data:
        regions.add(item.get("region", "Unknown"))
        cities.add(item.get("city", "Unknown"))
        chains.add(item.get("chain", "Unknown"))

    # Build node list
    all_nodes = list(regions) + list(cities) + list(chains) + price_ranges
    node_map = {node: i for i, node in enumerate(all_nodes)}

    # Build links
    links = {"source": [], "target": [], "value": []}

    # Region -> City flows
    region_city_flows = {}
    for item in city_chain_data:
        key = (item.get("region"), item.get("city"))
        region_city_flows[key] = region_city_flows.get(key, 0) + item.get("count", 1)

    for (region, city), value in region_city_flows.items():
        links["source"].append(node_map[region])
        links["target"].append(node_map[city])
        links["value"].append(value)

    # City -> Chain flows
    city_chain_flows = {}
    for item in city_chain_data:
        key = (item.get("city"), item.get("chain"))
        city_chain_flows[key] = city_chain_flows.get(key, 0) + item.get("count", 1)

    for (city, chain), value in city_chain_flows.items():
        links["source"].append(node_map[city])
        links["target"].append(node_map[chain])
        links["value"].append(value)

    # Chain -> Price Range flows
    chain_price_flows = {}
    for item in city_chain_data:
        avg_price = item.get("avg_price", 45)
        if avg_price < 30:
            price_range = "$0-30"
        elif avg_price < 45:
            price_range = "$30-45"
        elif avg_price < 60:
            price_range = "$45-60"
        else:
            price_range = "$60+"
        key = (item.get("chain"), price_range)
        chain_price_flows[key] = chain_price_flows.get(key, 0) + item.get("count", 1)

    for (chain, price_range), value in chain_price_flows.items():
        links["source"].append(node_map[chain])
        links["target"].append(node_map[price_range])
        links["value"].append(value)

    return {
        "nodes": all_nodes,
        "links": links
    }


def get_best_value_by_pack(prices_with_qty: List[Dict]) -> List[Dict]:
    """Find best value (lowest price per egg) by pack size."""
    pack_sizes = {}

    for item in prices_with_qty:
        qty = item.get("cantidad_piezas")
        price_per_egg = item.get("precio_por_pieza")

        if not qty or not price_per_egg:
            continue

        if qty not in pack_sizes:
            pack_sizes[qty] = {
                "pack_size": qty,
                "prices": [],
                "best_price": float('inf'),
                "best_store": None,
                "best_product": None
            }

        pack_sizes[qty]["prices"].append(price_per_egg)

        if price_per_egg < pack_sizes[qty]["best_price"]:
            pack_sizes[qty]["best_price"] = price_per_egg
            pack_sizes[qty]["best_store"] = item.get("cadena_comercial")
            pack_sizes[qty]["best_product"] = item.get("producto")

    results = []
    for qty, data in sorted(pack_sizes.items()):
        if data["prices"]:
            stats = calculate_statistics(data["prices"])
            results.append({
                "pack_size": qty,
                "avg_price_per_egg": stats["mean"],
                "min_price_per_egg": stats["min"],
                "max_price_per_egg": stats["max"],
                "sample_count": stats["count"],
                "best_store": data["best_store"],
                "best_product": data["best_product"]
            })

    return results


def compare_brands(brand_data: List[Dict]) -> List[Dict]:
    """Compare brand performance across multiple metrics."""
    brand_stats = {}

    for item in brand_data:
        brand = item.get("marca") or "Sin Marca"
        price = item.get("precio", 0)

        if brand not in brand_stats:
            brand_stats[brand] = {
                "brand": brand,
                "prices": [],
                "cities": set(),
                "stores": set()
            }

        brand_stats[brand]["prices"].append(price)
        brand_stats[brand]["cities"].add(item.get("ciudad_code"))
        brand_stats[brand]["stores"].add(item.get("cadena_comercial"))

    results = []
    for brand, data in brand_stats.items():
        if data["prices"]:
            stats = calculate_statistics(data["prices"])
            results.append({
                "brand": brand,
                "avg_price": stats["mean"],
                "min_price": stats["min"],
                "max_price": stats["max"],
                "price_range": stats["range"],
                "sample_count": stats["count"],
                "cities_available": len(data["cities"]),
                "stores_available": len(data["stores"])
            })

    return sorted(results, key=lambda x: x["avg_price"])
