"""Building blocks for the scenario builder: industries and regions.

Industry profiles describe what flows through a supply chain (products, bills of
materials, order sizes, transport habits). Region profiles provide real cities
with coordinates for sites, customers and suppliers, plus typical overseas
sourcing hubs. All figures are plausible defaults for test data, not market data.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    category: str
    value: float
    weight_kg: float
    components: dict[str, float] = field(default_factory=dict)
    """Bill of materials (component code -> quantity) for produced items."""


@dataclass(frozen=True)
class Industry:
    name: str
    label: str
    description: str
    site_type: str  # "warehouse" or "plant"
    site_label: str
    customer_label: str
    products: tuple[Product, ...]
    components: tuple[Product, ...] = ()
    order_quantity: float = 10
    orders_per_customer_day: float = 1.0
    requested_days: float = 3
    handling_days: float = 0.25
    transit_cv: float = 0.2
    overseas_mode: str = "sea"  # how overseas suppliers ship: sea or air
    domestic_mode: str = "road"
    overseas_share: float = 0.5  # share of supplied items sourced overseas

    @property
    def produces(self) -> bool:
        return bool(self.components)


def _p(code: str, name: str, category: str, value: float, kg: float, **bom: float) -> Product:
    return Product(code, name, category, value, kg, dict(bom))


INDUSTRIES: dict[str, Industry] = {
    i.name: i
    for i in (
        Industry(
            "consumer_electronics", "Consumer electronics",
            "Distribution of small electronics sourced mostly from Asia by sea.",
            "warehouse", "DC", "retailer",
            (
                _p("HEADSET", "Wireless headset", "audio", 59, 0.4),
                _p("SPEAKER", "Bluetooth speaker", "audio", 89, 0.9),
                _p("CHARGER", "USB-C charger", "accessories", 19, 0.2),
                _p("TABLET", "10-inch tablet", "computing", 249, 0.6),
                _p("WATCH", "Smart watch", "wearables", 179, 0.2),
                _p("CAMERA", "Action camera", "imaging", 299, 0.3),
                _p("KEYBOARD", "Wireless keyboard", "computing", 49, 0.7),
                _p("ROUTER", "Wi-Fi router", "networking", 99, 0.8),
            ),
            order_quantity=12, orders_per_customer_day=1.2, overseas_share=0.75,
        ),
        Industry(
            "fashion_retail", "Fashion retail",
            "Apparel and footwear for stores and online shops, sourced from Asia.",
            "warehouse", "DC", "store",
            (
                _p("TSHIRT", "Cotton T-shirt", "tops", 15, 0.2),
                _p("JEANS", "Denim jeans", "bottoms", 49, 0.7),
                _p("JACKET", "Rain jacket", "outerwear", 89, 0.9),
                _p("SNEAKER", "Running sneaker", "footwear", 79, 0.9),
                _p("DRESS", "Summer dress", "dresses", 39, 0.3),
                _p("HOODIE", "Hoodie", "tops", 45, 0.6),
                _p("BAG", "Backpack", "accessories", 35, 0.8),
                _p("SOCKS", "Socks 3-pack", "basics", 9, 0.1),
            ),
            order_quantity=30, orders_per_customer_day=1.0, requested_days=4,
            transit_cv=0.25, overseas_share=0.8,
        ),
        Industry(
            "food_beverage", "Food & beverage",
            "Fast-moving groceries from regional producers, short lead times by road.",
            "warehouse", "DC", "supermarket",
            (
                _p("PASTA", "Pasta 500 g (case)", "dry goods", 18, 6),
                _p("COFFEE", "Coffee beans 1 kg (case)", "hot drinks", 95, 6.5),
                _p("OLIVEOIL", "Olive oil 1 l (case)", "oils", 60, 7),
                _p("JUICE", "Orange juice 1 l (case)", "beverages", 14, 6.5),
                _p("WATER", "Mineral water (pack)", "beverages", 4, 9),
                _p("CEREAL", "Breakfast cereal (case)", "dry goods", 22, 4),
                _p("CHOC", "Chocolate bars (case)", "snacks", 30, 3),
                _p("TOMATO", "Canned tomatoes (case)", "canned", 12, 10),
            ),
            order_quantity=40, orders_per_customer_day=2.0, requested_days=1.5,
            handling_days=0.2, transit_cv=0.15, overseas_share=0.2,
        ),
        Industry(
            "pharma", "Pharmaceuticals",
            "High-value medicines with low variability; overseas supply by air.",
            "warehouse", "distribution centre", "pharmacy wholesaler",
            (
                _p("ANALGESIC", "Analgesic tablets (carton)", "OTC", 120, 2),
                _p("ANTIBIOTIC", "Antibiotic capsules (carton)", "Rx", 340, 1.5),
                _p("INSULIN", "Insulin pens (cold chain)", "Rx cold", 900, 1.2),
                _p("VACCINE", "Vaccine vials (cold chain)", "Rx cold", 1500, 1),
                _p("SYRINGE", "Syringes (box)", "medical devices", 45, 2.5),
                _p("INHALER", "Inhaler (carton)", "Rx", 260, 1.4),
            ),
            order_quantity=6, orders_per_customer_day=1.5, requested_days=1,
            transit_cv=0.1, overseas_mode="air", overseas_share=0.4,
        ),
        Industry(
            "automotive_parts", "Automotive parts",
            "Plants assemble brake and steering modules from sourced components.",
            "plant", "plant", "OEM assembly plant",
            (
                _p("BRAKEMOD", "Brake module", "chassis", 420, 22, PAD=2, DISC=2, CALIPER=2),
                _p("STEERMOD", "Steering module", "chassis", 680, 18, RACK=1, MOTOR=1, ECU=1),
            ),
            (
                _p("PAD", "Brake pad set", "component", 28, 1.5),
                _p("DISC", "Brake disc", "component", 45, 7),
                _p("CALIPER", "Brake caliper", "component", 70, 3),
                _p("RACK", "Steering rack", "component", 210, 9),
                _p("MOTOR", "Assist motor", "component", 160, 4),
                _p("ECU", "Control unit", "electronics", 120, 0.5),
            ),
            order_quantity=40, orders_per_customer_day=1.5, requested_days=2,
            handling_days=0.3, transit_cv=0.15, overseas_mode="sea", overseas_share=0.35,
        ),
        Industry(
            "industrial_machinery", "Industrial machinery",
            "Make-to-stock pumps and compressors built from motors, housings and electronics.",
            "plant", "plant", "industrial customer",
            (
                _p("PUMP", "Centrifugal pump", "pumps", 1450, 38, MOTOR=1, HOUSING=1, BOARD=2),
                _p("COMPRESSOR", "Air compressor", "compressors", 2300, 55, MOTOR=1, TANK=1,
                   BOARD=1),
            ),
            (
                _p("MOTOR", "Electric motor", "component", 310, 14),
                _p("HOUSING", "Pump housing", "component", 120, 18),
                _p("TANK", "Pressure tank", "component", 190, 25),
                _p("BOARD", "Control board", "electronics", 85, 0.3),
            ),
            order_quantity=8, orders_per_customer_day=0.8, requested_days=5,
            handling_days=0.5, transit_cv=0.2, overseas_mode="air", overseas_share=0.3,
        ),
    )
}  # fmt: skip


@dataclass(frozen=True)
class City:
    name: str
    country: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Region:
    name: str
    label: str
    hubs: tuple[City, ...]
    """Cities suited for DCs and plants."""
    customers: tuple[City, ...]
    suppliers: tuple[City, ...]
    """Domestic / regional supplier cities."""
    overseas: tuple[City, ...]
    """Overseas sourcing cities (another continent)."""


def _c(name: str, country: str, lat: float, lon: float) -> City:
    return City(name, country, lat, lon)


_ASIA_SOURCING = (
    _c("Shenzhen", "CN", 22.54, 114.06), _c("Shanghai", "CN", 31.23, 121.47),
    _c("Ho Chi Minh City", "VN", 10.82, 106.63), _c("Hsinchu", "TW", 24.80, 120.97),
    _c("Dhaka", "BD", 23.81, 90.41), _c("Mumbai", "IN", 19.08, 72.88),
)  # fmt: skip
_EUROPE_SOURCING = (
    _c("Hamburg", "DE", 53.55, 9.99), _c("Milan", "IT", 45.46, 9.19),
    _c("Lyon", "FR", 45.76, 4.84), _c("Barcelona", "ES", 41.39, 2.17),
)  # fmt: skip

REGIONS: dict[str, Region] = {
    r.name: r
    for r in (
        Region(
            "europe", "Europe",
            (_c("Hamburg", "DE", 53.55, 9.99), _c("Venlo", "NL", 51.37, 6.17),
             _c("Lyon", "FR", 45.76, 4.84), _c("Poznan", "PL", 52.41, 16.93),
             _c("Zaragoza", "ES", 41.65, -0.88)),
            (_c("Berlin", "DE", 52.52, 13.40), _c("Munich", "DE", 48.14, 11.58),
             _c("Paris", "FR", 48.86, 2.35), _c("Amsterdam", "NL", 52.37, 4.90),
             _c("Vienna", "AT", 48.21, 16.37), _c("Copenhagen", "DK", 55.68, 12.57),
             _c("Milan", "IT", 45.46, 9.19), _c("Madrid", "ES", 40.42, -3.70),
             _c("Warsaw", "PL", 52.23, 21.01), _c("Prague", "CZ", 50.08, 14.44),
             _c("Brussels", "BE", 50.85, 4.35), _c("Zurich", "CH", 47.38, 8.54),
             _c("Stockholm", "SE", 59.33, 18.07), _c("Lisbon", "PT", 38.72, -9.14)),
            (_c("Gyor", "HU", 47.69, 17.63), _c("Plzen", "CZ", 49.74, 13.38),
             _c("Turin", "IT", 45.07, 7.69), _c("Wroclaw", "PL", 51.11, 17.04),
             _c("Porto", "PT", 41.15, -8.61), _c("Bursa", "TR", 40.19, 29.06)),
            _ASIA_SOURCING,
        ),
        Region(
            "north_america", "North America",
            (_c("Memphis", "US", 35.15, -90.05), _c("Columbus", "US", 39.96, -83.00),
             _c("Dallas", "US", 32.78, -96.80), _c("Reno", "US", 39.53, -119.81),
             _c("Toronto", "CA", 43.65, -79.38)),
            (_c("New York", "US", 40.71, -74.01), _c("Chicago", "US", 41.88, -87.63),
             _c("Atlanta", "US", 33.75, -84.39), _c("Los Angeles", "US", 34.05, -118.24),
             _c("Seattle", "US", 47.61, -122.33), _c("Denver", "US", 39.74, -104.99),
             _c("Houston", "US", 29.76, -95.37), _c("Miami", "US", 25.76, -80.19),
             _c("Boston", "US", 42.36, -71.06), _c("Phoenix", "US", 33.45, -112.07),
             _c("Montreal", "CA", 45.50, -73.57), _c("Vancouver", "CA", 49.28, -123.12),
             _c("Mexico City", "MX", 19.43, -99.13), _c("Minneapolis", "US", 44.98, -93.27)),
            (_c("Monterrey", "MX", 25.69, -100.32), _c("Detroit", "US", 42.33, -83.05),
             _c("Charlotte", "US", 35.23, -80.84), _c("Querétaro", "MX", 20.59, -100.39),
             _c("Indianapolis", "US", 39.77, -86.16), _c("Hamilton", "CA", 43.26, -79.87)),
            _ASIA_SOURCING,
        ),
        Region(
            "asia_pacific", "Asia-Pacific",
            (_c("Singapore", "SG", 1.35, 103.82), _c("Shanghai", "CN", 31.23, 121.47),
             _c("Tokyo", "JP", 35.68, 139.69), _c("Sydney", "AU", -33.87, 151.21),
             _c("Bangkok", "TH", 13.76, 100.50)),
            (_c("Seoul", "KR", 37.57, 126.98), _c("Osaka", "JP", 34.69, 135.50),
             _c("Hong Kong", "HK", 22.32, 114.17), _c("Jakarta", "ID", -6.21, 106.85),
             _c("Kuala Lumpur", "MY", 3.14, 101.69), _c("Manila", "PH", 14.60, 120.98),
             _c("Melbourne", "AU", -37.81, 144.96), _c("Beijing", "CN", 39.90, 116.40),
             _c("Taipei", "TW", 25.03, 121.57), _c("Hanoi", "VN", 21.03, 105.85),
             _c("Auckland", "NZ", -36.85, 174.76), _c("Bengaluru", "IN", 12.97, 77.59),
             _c("Delhi", "IN", 28.70, 77.10), _c("Guangzhou", "CN", 23.13, 113.26)),
            (_c("Shenzhen", "CN", 22.54, 114.06), _c("Hsinchu", "TW", 24.80, 120.97),
             _c("Penang", "MY", 5.41, 100.33), _c("Ho Chi Minh City", "VN", 10.82, 106.63),
             _c("Chennai", "IN", 13.08, 80.27), _c("Nagoya", "JP", 35.18, 136.91)),
            _EUROPE_SOURCING,
        ),
        Region(
            "latin_america", "Latin America",
            (_c("São Paulo", "BR", -23.55, -46.63), _c("Mexico City", "MX", 19.43, -99.13),
             _c("Bogotá", "CO", 4.71, -74.07), _c("Santiago", "CL", -33.45, -70.67),
             _c("Panama City", "PA", 8.98, -79.52)),
            (_c("Rio de Janeiro", "BR", -22.91, -43.17), _c("Buenos Aires", "AR", -34.60, -58.38),
             _c("Lima", "PE", -12.05, -77.04), _c("Guadalajara", "MX", 20.66, -103.35),
             _c("Monterrey", "MX", 25.69, -100.32), _c("Medellín", "CO", 6.24, -75.58),
             _c("Quito", "EC", -0.18, -78.47), _c("Montevideo", "UY", -34.90, -56.16),
             _c("Belo Horizonte", "BR", -19.92, -43.94), _c("Curitiba", "BR", -25.43, -49.27),
             _c("San José", "CR", 9.93, -84.08), _c("Guatemala City", "GT", 14.63, -90.51),
             _c("Santo Domingo", "DO", 18.49, -69.93), _c("Córdoba", "AR", -31.42, -64.18)),
            (_c("Manaus", "BR", -3.12, -60.02), _c("Querétaro", "MX", 20.59, -100.39),
             _c("Rosario", "AR", -32.95, -60.64), _c("Cali", "CO", 3.45, -76.53),
             _c("Valparaíso", "CL", -33.05, -71.62), _c("Puebla", "MX", 19.04, -98.21)),
            _ASIA_SOURCING,
        ),
    )
}  # fmt: skip

SIZES: dict[str, dict[str, int]] = {
    "small": {"sites": 1, "customers": 4, "suppliers": 2, "products": 3},
    "medium": {"sites": 2, "customers": 8, "suppliers": 3, "products": 5},
    "large": {"sites": 3, "customers": 14, "suppliers": 5, "products": 8},
}

DISRUPTION_PROFILES: dict[str, dict[str, float]] = {
    "none": {"per_year": 0, "severity_min": 0, "severity_max": 0},
    "low": {"per_year": 2, "severity_min": 0.3, "severity_max": 0.6},
    "medium": {"per_year": 5, "severity_min": 0.4, "severity_max": 0.8},
    "high": {"per_year": 10, "severity_min": 0.6, "severity_max": 1.0},
}
