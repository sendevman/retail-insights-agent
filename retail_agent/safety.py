"""Data minimization is the primary PII control; redaction is defense in depth."""

import re


class SafeError(Exception):
    """Fixed, safe-to-display error. Never put provider exception text here."""


class QueryError(SafeError):
    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def redact(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL REDACTED]", text)
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[ID REDACTED]", text)
    text = re.sub(r"(?<!\w)(?:\+?\d[ ()-]*){10,16}(?!\w)", "[NUMBER REDACTED]", text)
    # Terminal control characters, ANSI escape introducers and bidi overrides.
    return "".join(
        c
        for c in text
        if c in "\n\t"
        or (
            ord(c) >= 32
            and ord(c) != 127
            and c not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        )
    )


def private_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(emails?|e-mails?|phones?|telephone|password|ssn|first_name|last_name|"
            r"ip_address|street_address|postal|zip.?code|latitude|longitude)\b|"
            r"\b(customer|user)[ '\w]{0,20}\b(names?|addresses?|identit(?:y|ies))\b",
            text,
            re.I,
        )
    )


SCHEMA_HELP = """Available data (restricted to your assigned product IDs):
- orders: order dates and status; joined at item grain, never summed at order grain.
- order_items: sale price, item status, order/product keys, item dates.
- products: product ID and category. Names/SKU/free text are intentionally excluded.
- users: state/country for aggregate comparisons; customer identity becomes an opaque key.
Metrics: completed-item revenue and units, scoped orders/customers, average completed
order value, and returned-item fraction. Group by day/month, product ID, category,
state/country, status, or pseudonymous customer key. Dates use order-item creation
time in UTC and [start, end). Up to three comparisons per turn.
Examples: monthly revenue; compare products 1 and 2; top customers by completed spend;
compare states; create a quarterly report with actions. No direct PII is available.
Churn requires an agreed inactivity definition; this is not subscription data.
"""
