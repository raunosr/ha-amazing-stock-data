"""Shared identifiers for the optional history integration."""

DOMAIN = "amazing_stock_data"
PERIODS = {
    "day": ("today", None),
    "week": ("one_week", None),
    "month": ("one_month", None),
    "year": ("one_year", "day"),
    "five_years": ("five_years", "day"),
    "ten_years": ("ten_years", "week"),
    "max": ("infinity", "week"),
}
