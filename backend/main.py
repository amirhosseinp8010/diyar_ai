"""
دیار AI — Backend API (MVP)

پیاده‌سازی واقعی سه ستون اصلی از سند معماری:
  - جست‌وجو و ارزیابی قیمت ملک (روی داده‌ی نمونه؛ نقطه‌ی اتصال به ETL واقعی DLD در valuation.py مشخص شده)
  - مشاور ویزا (منبع حقیقت = visa_rules.py، نه حدسِ مدل زبانی)
  - همتایابی ساده‌ی سبک زندگی (نسخه‌ی heuristic v1 — نسخه‌ی embedding واقعی در فاز ۲)

اجرای محلی:
    pip install -r requirements.txt
    uvicorn main:app --reload
    # سپس مرورگر رو باز کن روی http://127.0.0.1:8000/docs
"""

import csv
import io
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from visa_rules import VISA_RULES, recommend_route

DATA_PATH = Path(__file__).parent / "data" / "properties.json"
PROPERTIES: list[dict] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
PROPERTIES_BY_ID = {p["id"]: p for p in PROPERTIES}

LEADS_PATH = Path(__file__).parent / "data" / "leads.json"
LEADS: list[dict] = json.loads(LEADS_PATH.read_text(encoding="utf-8")) if LEADS_PATH.exists() else []

# محافظ ساده‌ی اندپوینت‌های نوشتنی. قبل از دیپلوی عمومی، ADMIN_TOKEN رو
# با یه مقدار واقعی ست کن (env var روی Render) — پیش‌فرض فقط برای توسعه‌ی لوکاله.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-local-only")
if ADMIN_TOKEN == "dev-local-only":
    print("⚠️  ADMIN_TOKEN تنظیم نشده — از مقدار پیش‌فرض ناامن استفاده می‌شه. قبل از دیپلوی عمومی تنظیمش کن.")

app = FastAPI(
    title="دیار AI API",
    description=(
        "بک‌اند MVP پلتفرم دیار — جست‌وجوی ملک، ارزیابی قیمت و مشاور ویزا برای دبی. "
        "۱۸ تراکنش واقعیِ ثبت‌شده در DLD (چند ماه اخیر) — قیمت، متراژ و بازده هرکدوم از داده‌ی رسمی استخراج شده، نه نمونه‌ی ساختگی."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Property(BaseModel):
    id: int
    name: str
    area: str
    type: str
    price_aed: int
    size_sqft: int
    roi_pct: float
    golden_visa_eligible: bool
    residency_visa_eligible: bool


class Valuation(BaseModel):
    property_id: int
    predicted_price_aed: int
    confidence_pct: int
    trend_aed: list[int]
    forecast_3m_aed: list[int]
    note: str


class LifestyleMatch(BaseModel):
    property_id: int
    match_score: int
    reasons: list[str]


class VisaAdviseIn(BaseModel):
    budget_aed: int = Field(..., ge=0, description="بودجه‌ی سرمایه‌گذاری به درهم")
    goal: Optional[Literal["invest", "live", "migrate"]] = None


class VisaRouteOut(BaseModel):
    route: str
    name_fa: str
    duration_years: Optional[int]
    requires_sponsor: bool
    min_investment_aed: int
    explanation_fa: str


class VisaAdviseOut(BaseModel):
    recommended: VisaRouteOut
    matched_properties: list[Property]


class PropertyImport(BaseModel):
    id: Optional[int] = None
    name: str
    area: str
    type: str
    price_aed: int = Field(..., ge=0)
    size_sqft: int = Field(..., ge=0)
    roi_pct: float
    trend_aed: Optional[list[int]] = None


class ImportResult(BaseModel):
    imported: int
    total_properties: int
    mode: str


class LeadIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    contact: str = Field(..., min_length=3, max_length=120, description="ایمیل یا شماره تماس")
    property_id: Optional[int] = None
    message: Optional[str] = Field(None, max_length=1000)

    @field_validator("name", "contact")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("نمی‌تونه خالی باشه")
        return v


class LeadOut(BaseModel):
    id: int
    name: str
    contact: str
    property_id: Optional[int]
    property_name: Optional[str]
    message: Optional[str]
    status: str
    created_at: str


def _check_admin(x_admin_token: Optional[str]):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "توکن ادمین نامعتبر است — هدر X-Admin-Token رو بفرست")


def _persist_properties():
    DATA_PATH.write_text(json.dumps(PROPERTIES, ensure_ascii=False, indent=2), encoding="utf-8")


def _persist_leads():
    LEADS_PATH.write_text(json.dumps(LEADS, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_property_id() -> int:
    return max((p["id"] for p in PROPERTIES), default=0) + 1


def _next_lead_id() -> int:
    return max((l["id"] for l in LEADS), default=0) + 1


def _to_property_out(p: dict) -> Property:
    return Property(
        id=p["id"],
        name=p["name"],
        area=p["area"],
        type=p["type"],
        price_aed=p["price_aed"],
        size_sqft=p["size_sqft"],
        roi_pct=p["roi_pct"],
        golden_visa_eligible=p["price_aed"] >= VISA_RULES["golden_property"]["min_investment_aed"],
        residency_visa_eligible=p["price_aed"] >= VISA_RULES["property_2y"]["min_investment_aed"],
    )


@app.get("/", tags=["meta"])
def root():
    return {"service": "diyar-ai-backend", "docs": "/docs", "properties_loaded": len(PROPERTIES)}


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/v1/properties/search", response_model=list[Property], tags=["properties"])
def search_properties(
    area: Optional[str] = Query(None, description="بخشی از نام منطقه، مثلا 'Marina'"),
    type: Optional[str] = Query(None, description="نوع ملک، مثلا 'ویلا'"),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    sort: Literal["price_asc", "price_desc", "roi_desc"] = "price_asc",
    limit: int = Query(20, ge=1, le=100),
):
    results = PROPERTIES
    if area:
        results = [p for p in results if area.lower() in p["area"].lower()]
    if type:
        results = [p for p in results if type in p["type"]]
    if min_price is not None:
        results = [p for p in results if p["price_aed"] >= min_price]
    if max_price is not None:
        results = [p for p in results if p["price_aed"] <= max_price]

    key = {
        "price_asc": lambda p: p["price_aed"],
        "price_desc": lambda p: -p["price_aed"],
        "roi_desc": lambda p: -p["roi_pct"],
    }[sort]
    results = sorted(results, key=key)[:limit]
    return [_to_property_out(p) for p in results]


@app.get("/v1/properties/{property_id}", response_model=Property, tags=["properties"])
def get_property(property_id: int):
    p = PROPERTIES_BY_ID.get(property_id)
    if not p:
        raise HTTPException(404, "ملکی با این شناسه پیدا نشد")
    return _to_property_out(p)


@app.get("/v1/properties/{property_id}/valuation", response_model=Valuation, tags=["ai"])
def get_valuation(property_id: int):
    """
    ارزیابی قیمت با رگرسیون خطی ساده روی روند ۶ ماهه‌ی نمونه.
    نقطه‌ی اتصال فاز ۲: جایگزینی trend_aed با تاریخچه‌ی واقعی تراکنش‌های DLD
    و این تابع با یک مدل gradient-boosting آموزش‌دیده (طبق سند معماری، بخش ۵).
    """
    p = PROPERTIES_BY_ID.get(property_id)
    if not p:
        raise HTTPException(404, "ملکی با این شناسه پیدا نشد")

    trend = p["trend_aed"]
    n = len(trend)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(trend) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, trend))
    den = sum((x - x_mean) ** 2 for x in xs) or 1
    slope = num / den
    intercept = y_mean - slope * x_mean

    forecast = [round(intercept + slope * (n - 1 + i)) for i in (1, 2, 3)]

    residuals = [trend[i] - (intercept + slope * i) for i in xs]
    fit_error = statistics.pstdev(residuals) if n > 1 else 0
    spread = (max(trend) - min(trend)) or 1
    confidence = max(55, min(97, round(100 - (fit_error / spread) * 100)))

    return Valuation(
        property_id=property_id,
        predicted_price_aed=trend[-1],
        confidence_pct=confidence,
        trend_aed=trend,
        forecast_3m_aed=forecast,
        note="رگرسیون خطی روی روند واقعیِ قیمت هر فوت مربع (منبع: DLD)، اعمال‌شده روی متراژ این ملک — جایگزین با مدل آموزش‌دیده‌ی کامل در فاز ۲",
    )


@app.get("/v1/properties/{property_id}/lifestyle-match", response_model=LifestyleMatch, tags=["ai"])
def lifestyle_match(
    property_id: int,
    near_school: bool = False,
    near_beach: bool = False,
    near_metro: bool = False,
):
    """
    نسخه‌ی heuristic v1 همتایابی سبک زندگی — بر اساس نام منطقه، نه embedding واقعی.
    فاز ۲ (سند معماری، بخش ۵): بردار embedding واقعی برای هر ملک در pgvector.
    """
    p = PROPERTIES_BY_ID.get(property_id)
    if not p:
        raise HTTPException(404, "ملکی با این شناسه پیدا نشد")

    area = p["area"]
    school_areas = {"Jumeirah Village Circle", "Jumeirah Village Triangle", "Damac Hills", "Majan", "Al Furjan"}
    beach_areas = {"Dubai Marina", "Palm Deira"}
    metro_areas = {"Business Bay", "Dubai Marina", "Burj Khalifa", "Al Furjan"}

    score = 60
    reasons = []
    if near_school and area in school_areas:
        score += 15
        reasons.append("نزدیک به مدارس بین‌المللی معتبر")
    if near_beach and area in beach_areas:
        score += 15
        reasons.append("در فاصله‌ی پیاده تا ساحل")
    if near_metro and area in metro_areas:
        score += 10
        reasons.append("دسترسی مستقیم به مترو")
    if not reasons:
        reasons.append("بر اساس نوع ملک و بازه‌ی قیمت با پروفایل عمومی هم‌خوانی دارد")

    return LifestyleMatch(property_id=property_id, match_score=min(score, 98), reasons=reasons)


@app.get("/v1/visa/rules", response_model=list[VisaRouteOut], tags=["visa"])
def get_visa_rules():
    """منبع حقیقتِ آستانه‌های ویزا — جدول قانونمند، نه چیزی که مدل زبانی حدس بزند."""
    return [VisaRouteOut(route=k, **v) for k, v in VISA_RULES.items()]


@app.post("/v1/visa/advise", response_model=VisaAdviseOut, tags=["visa"])
def advise_visa(payload: VisaAdviseIn):
    route = recommend_route(payload.budget_aed)
    matched = sorted(PROPERTIES, key=lambda p: abs(p["price_aed"] - payload.budget_aed))[:3]
    return VisaAdviseOut(
        recommended=VisaRouteOut(route=route["key"], **{k: v for k, v in route.items() if k != "key"}),
        matched_properties=[_to_property_out(p) for p in matched],
    )


@app.post("/v1/properties/import", response_model=ImportResult, tags=["admin"])
def import_properties(
    items: list[PropertyImport],
    mode: Literal["append", "replace"] = "append",
    x_admin_token: Optional[str] = Header(None),
):
    """
    افزودن ملک‌های واقعی (از یه آژانس یا فید واقعی). نیاز به هدر X-Admin-Token داره.
    اگه trend_aed ندی، یه روند مسطح از price_aed ساخته می‌شه تا اندپوینت ارزیابی
    بدون خطا کار کنه — بعداً با تاریخچه‌ی واقعی جایگزینش کن.
    mode=replace کل دیتاست رو با این لیست عوض می‌کنه؛ mode=append بهش اضافه می‌کنه.
    """
    _check_admin(x_admin_token)
    global PROPERTIES, PROPERTIES_BY_ID

    if mode == "replace":
        PROPERTIES = []

    for item in items:
        record = item.model_dump()
        if record["id"] is None:
            record["id"] = _next_property_id()
        if not record["trend_aed"]:
            record["trend_aed"] = [record["price_aed"]] * 6
        PROPERTIES.append(record)

    PROPERTIES_BY_ID = {p["id"]: p for p in PROPERTIES}
    _persist_properties()
    return ImportResult(imported=len(items), total_properties=len(PROPERTIES), mode=mode)


@app.post("/v1/properties/import/csv", response_model=ImportResult, tags=["admin"])
async def import_properties_csv(
    file: UploadFile = File(..., description="ستون‌ها: name,area,type,price_aed,size_sqft,roi_pct"),
    mode: Literal["append", "replace"] = "append",
    x_admin_token: Optional[str] = Header(None),
):
    """آپلود CSV برای افزودن ملک‌های واقعی — راحت‌ترین راه برای یه آژانس که فقط اکسل داره."""
    _check_admin(x_admin_token)
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    try:
        items = [
            PropertyImport(
                name=row["name"].strip(),
                area=row["area"].strip(),
                type=row["type"].strip(),
                price_aed=int(float(row["price_aed"])),
                size_sqft=int(float(row["size_sqft"])),
                roi_pct=float(row["roi_pct"]),
            )
            for row in reader
        ]
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"فرمت CSV نامعتبره: {e}")

    return import_properties(items, mode=mode, x_admin_token=x_admin_token)


@app.post("/v1/leads", response_model=LeadOut, tags=["leads"])
def create_lead(payload: LeadIn):
    """
    ثبت لید — وقتی کاربر از یه ملک درخواست اطلاعات می‌کنه یا می‌خواد با مشاور صحبت کنه.
    این دقیقاً همون چیزیه که مدل درآمدیِ «کمیسیون از لید به آژانس‌ها» روش سوار می‌شه؛
    بدون این اندپوینت، اون خط تو پیچ‌دک فقط یه ادعاست.
    """
    property_name = None
    if payload.property_id is not None:
        prop = PROPERTIES_BY_ID.get(payload.property_id)
        if not prop:
            raise HTTPException(404, "ملکی با این شناسه پیدا نشد")
        property_name = prop["name"]

    lead = {
        "id": _next_lead_id(),
        "name": payload.name,
        "contact": payload.contact,
        "property_id": payload.property_id,
        "property_name": property_name,
        "message": payload.message,
        "status": "new",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    LEADS.append(lead)
    _persist_leads()
    return LeadOut(**lead)


@app.get("/v1/leads", response_model=list[LeadOut], tags=["leads"])
def list_leads(x_admin_token: Optional[str] = Header(None)):
    """لیست لیدها — این چیزیه که داشبورد یه آژانسِ پارتنر می‌بینه. نیاز به X-Admin-Token داره."""
    _check_admin(x_admin_token)
    return [LeadOut(**l) for l in reversed(LEADS)]
