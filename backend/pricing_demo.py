"""Small pure functions for trying out test generation."""


def calculate_price(price, discount=0):
    """Return the price after applying a percentage discount (0-100)."""
    if price < 0:
        raise ValueError("price must be non-negative")
    if not 0 <= discount <= 100:
        raise ValueError("discount must be between 0 and 100")
    return round(price * (1 - discount / 100), 2)


def apply_tax(amount, rate):
    """Return amount plus tax, where rate is a fraction (0.075 = 7.5%)."""
    if rate < 0:
        raise ValueError("rate must be non-negative")
    return round(amount * (1 + rate), 2)
