"""
منبع حقیقتِ آستانه‌های ویزا. عمداً کد جدا از مدل زبانی است — آستانه‌ها اینجا
تغییر می‌کنند (نسخه‌بندی‌شده)، نه چیزی که یک LLM حدس بزند.

⚠️ این ارقام نمونه و برای دموی محصول هستند؛ قبل از استفاده‌ی واقعی با GDRFA/ICP
یا مشاور مهاجرتی مجاز تطبیق داده شوند.
"""

VISA_RULES = {
    "property_2y": {
        "name_fa": "ویزای اقامتی ۲ ساله (ملکی)",
        "duration_years": 2,
        "requires_sponsor": False,
        "min_investment_aed": 750_000,
        "explanation_fa": "سرمایه‌گذاری ملکی حداقل ۷۵۰,۰۰۰ درهم — ویزای اقامتی ۲ ساله، قابل تمدید تا زمانی که مالک ملک باشی.",
    },
    "golden_property": {
        "name_fa": "ویزای طلایی ۱۰ ساله (ملکی)",
        "duration_years": 10,
        "requires_sponsor": False,
        "min_investment_aed": 2_000_000,
        "explanation_fa": "سرمایه‌گذاری ملکی حداقل ۲,۰۰۰,۰۰۰ درهم — ویزای طلایی ۱۰ ساله، بدون نیاز به کفیل.",
    },
    "golden_entrepreneur": {
        "name_fa": "ویزای طلایی کارآفرینی ۵ ساله",
        "duration_years": 5,
        "requires_sponsor": False,
        "min_investment_aed": 500_000,
        "explanation_fa": "تاییدِ پروژه توسط یک انکوباتور معتبر (مثل in5 یا DTEC) با ارزش پروژه حداقل ۵۰۰,۰۰۰ درهم — ویزای طلایی ۵ ساله.",
    },
}


def recommend_route(budget_aed: int) -> dict:
    if budget_aed >= VISA_RULES["golden_property"]["min_investment_aed"]:
        key = "golden_property"
    elif budget_aed >= VISA_RULES["property_2y"]["min_investment_aed"]:
        key = "property_2y"
    else:
        key = "golden_entrepreneur"

    rule = VISA_RULES[key]
    result = dict(rule)
    result["key"] = key
    if budget_aed < VISA_RULES["property_2y"]["min_investment_aed"]:
        result["explanation_fa"] = (
            "با این بودجه هنوز به آستانه‌ی ویزای ملکی (۷۵۰,۰۰۰ درهم) نرسیده‌ای؛ "
            "مسیر جایگزین، ویزای طلاییِ کارآفرینی از طریق یک استارتاپِ تاییدشده توسط انکوباتور است."
        )
    return result
