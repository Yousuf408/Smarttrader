"""
Angel One Margin and Leverage Calculator
"""
def calculate_angel_margin(price, qty, leverage=5):
    required = (price * qty) / leverage
    return round(required, 2)
