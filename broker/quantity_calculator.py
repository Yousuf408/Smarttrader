"""
Intraday Margin and Quantity Allocation Calculator
"""
import math

def calculate_quantity(budget, parts, price, leverage=5):
    """
    Calculates number of shares to buy based on total budget, parts divisor, and price with intraday leverage.
    """
    if not budget or not parts or not price or price <= 0:
        return 0
    allocated_budget = budget / parts
    total_purchasing_power = allocated_budget * leverage
    return math.floor(total_purchasing_power / price)
