import os, uuid, math, re, requests
from datetime import datetime, date, timedelta
from typing import Optional, List
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, String, Integer, Float, Date, DateTime, ForeignKey, func, or_
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from pydantic import BaseModel, EmailStr
from dateutil.relativedelta import relativedelta
from passlib.context import CryptContext
from jose import jwt, JWTError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from ai_service import parse_invoice_file, parse_medicine_photo

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./medstockpk.db")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@medstockpk.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123")
UPLOAD_DIR = "uploads"
DOWNLOAD_DIR = "downloads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

SUBSCRIPTION_PLANS = {
    "Trial": {"price": 0, "days": 14, "currency": "PKR"},
    "Basic": {"price": 2500, "days": 30, "currency": "PKR"},
    "Pro": {"price": 5000, "days": 30, "currency": "PKR"},
    "Enterprise": {"price": 10000, "days": 30, "currency": "PKR"}
}

class Pharmacy(Base):
    __tablename__ = "pharmacies"
    id = Column(String, primary_key=True)
    name = Column(String)
    phone = Column(String)
    country = Column(String, default="Pakistan")
    currency = Column(String, default="PKR")
    subscription_plan = Column(String, default="Trial")
    subscription_status = Column(String, default="trial")
    subscription_expiry = Column(Date, default=lambda: date.today() + timedelta(days=14))
    created_at = Column(DateTime, default=datetime.utcnow)
    users = relationship("User", back_populates="pharmacy")
    medicines = relationship("Medicine", back_populates="pharmacy")
    stock_batches = relationship("StockBatch", back_populates="pharmacy")

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    email = Column(String, unique=True)
    hashed_password = Column(String)
    role = Column(String, default="pharmacy")
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"), nullable=True)
    pharmacy = relationship("Pharmacy", back_populates="users")

class Medicine(Base):
    __tablename__ = "medicines"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    name = Column(String)
    generic_name = Column(String, nullable=True)
    category = Column(String, nullable=True)
    barcode = Column(String, nullable=True)
    min_stock = Column(Integer, default=10)
    created_at = Column(DateTime, default=datetime.utcnow)
    pharmacy = relationship("Pharmacy", back_populates="medicines")
    stock_batches = relationship("StockBatch", back_populates="medicine")

class StockBatch(Base):
    __tablename__ = "stock_batches"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    medicine_id = Column(String, ForeignKey("medicines.id"))
    batch_number = Column(String)
    expiry_date = Column(Date)
    quantity = Column(Integer, default=0)
    purchase_price = Column(Float, default=0)
    sale_price = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    pharmacy = relationship("Pharmacy", back_populates="stock_batches")
    medicine = relationship("Medicine", back_populates="stock_batches")

class Sale(Base):
    __tablename__ = "sales"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    medicine_id = Column(String, ForeignKey("medicines.id"))
    quantity = Column(Integer, default=0)
    sale_price = Column(Float, default=0)
    sale_date = Column(DateTime, default=datetime.utcnow)

class PosSale(Base):
    __tablename__ = "pos_sales"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    total_amount = Column(Float, default=0)
    payment_method = Column(String, default="Cash")
    customer_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PosSaleItem(Base):
    __tablename__ = "pos_sale_items"
    id = Column(String, primary_key=True)
    sale_id = Column(String, ForeignKey("pos_sales.id"))
    medicine_id = Column(String, ForeignKey("medicines.id"))
    quantity = Column(Integer, default=0)
    unit_price = Column(Float, default=0)
    line_total = Column(Float, default=0)

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    status = Column(String, default="draft")
    note = Column(String, nullable=True)
    total_estimated_amount = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id = Column(String, primary_key=True)
    purchase_order_id = Column(String, ForeignKey("purchase_orders.id"))
    medicine_id = Column(String, nullable=True)
    medicine_name = Column(String)
    quantity = Column(Integer, default=0)
    estimated_cost = Column(Float, default=0)
    line_total = Column(Float, default=0)

class SubscriptionRequest(Base):
    __tablename__ = "subscription_requests"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    plan = Column(String)
    amount = Column(Float, default=0)
    payment_method = Column(String)
    transaction_reference = Column(String, nullable=True)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

class AlertLog(Base):
    __tablename__ = "alert_logs"
    id = Column(String, primary_key=True)
    pharmacy_id = Column(String, ForeignKey("pharmacies.id"))
    alert_type = Column(String)
    alert_date = Column(Date)
    channel = Column(String, default="log")
    status = Column(String, default="logged")
    message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Schemas
class PharmacyRegister(BaseModel):
    name: str
    phone: str
    email: EmailStr
    password: str

class MedicineCreate(BaseModel):
    name: str
    generic_name: Optional[str] = None
    category: Optional[str] = None
    barcode: Optional[str] = None
    min_stock: int = 10

class StockCreate(BaseModel):
    medicine_id: str
    batch_number: str
    expiry_date: date
    quantity: int
    purchase_price: float = 0
    sale_price: float = 0

class InvoiceItem(BaseModel):
    name: str
    generic_name: Optional[str] = None
    category: Optional[str] = None
    barcode: Optional[str] = None
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    quantity: int
    purchase_price: float = 0
    sale_price: float = 0

class InvoiceApprove(BaseModel):
    items: List[InvoiceItem]

class SaleCreate(BaseModel):
    medicine_id: str
    quantity: int
    sale_price: float = 0

class PosSaleItemRequest(BaseModel):
    medicine_id: str
    quantity: int
    unit_price: float = 0

class PosSaleRequest(BaseModel):
    items: List[PosSaleItemRequest]
    payment_method: str = "Cash"
    customer_name: Optional[str] = None

class PurchaseOrderGenerateRequest(BaseModel):
    include_urgencies: List[str] = ["High", "Medium"]
    min_order_quantity: int = 1

class PurchaseOrderSelectionItem(BaseModel):
    medicine_id: str
    quantity: int

class PurchaseOrderSelectionRequest(BaseModel):
    items: List[PurchaseOrderSelectionItem]
    note: Optional[str] = "Generated from AI predictive order"

class SubscriptionRequestCreate(BaseModel):
    plan: str
    payment_method: str
    transaction_reference: Optional[str] = None

# Helpers
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password): return pwd_context.hash(password)
def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)

def create_access_token(data):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id: raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=401, detail="User not found")
    return user

def require_active_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin": return user
    if not user.pharmacy_id: raise HTTPException(status_code=403, detail="Pharmacy not found")
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == user.pharmacy_id).first()
    if not pharmacy: raise HTTPException(status_code=403, detail="Pharmacy not found")
    if pharmacy.subscription_expiry < date.today():
        pharmacy.subscription_status = "expired"
        db.commit()
    if pharmacy.subscription_status not in ["active", "trial"]:
        raise HTTPException(status_code=403, detail="Subscription expired. Please renew.")
    return user

def require_premium_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin": return user
    if not user.pharmacy_id: raise HTTPException(status_code=403, detail="Pharmacy not found")
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == user.pharmacy_id).first()
    if not pharmacy: raise HTTPException(status_code=403, detail="Pharmacy not found")
    if pharmacy.subscription_expiry < date.today():
        pharmacy.subscription_status = "expired"
        db.commit()
    if pharmacy.subscription_status != "active":
        raise HTTPException(status_code=403, detail="Active subscription required.")
    if pharmacy.subscription_plan not in ["Pro", "Enterprise"]:
        raise HTTPException(status_code=403, detail="AI features require Pro or Enterprise plan.")
    return user

async def _save_upload_file(file: UploadFile):
    ext = os.path.splitext(file.filename or ".jpg")[1].lower()
    filename = f"{uuid.uuid4()}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    content = await file.read()
    with open(path, "wb") as f: f.write(content)
    return path

def _latest_purchase_price(db, pharmacy_id, medicine_id):
    batch = db.query(StockBatch).filter(
        StockBatch.pharmacy_id == pharmacy_id, StockBatch.medicine_id == medicine_id
    ).order_by(StockBatch.created_at.desc()).first()
    return float(batch.purchase_price) if batch and batch.purchase_price else 0.0

def _reduce_stock(db, pharmacy_id, medicine_id, quantity):
    batches = db.query(StockBatch).filter(
        StockBatch.pharmacy_id == pharmacy_id, StockBatch.medicine_id == medicine_id, StockBatch.quantity > 0
    ).order_by(StockBatch.expiry_date.asc()).all()
    remaining = quantity
    auto_price = 0.0
    for b in batches:
        if remaining <= 0: break
        take = min(b.quantity, remaining)
        b.quantity -= take
        remaining -= take
        if auto_price == 0 and b.sale_price: auto_price = float(b.sale_price)
    if remaining > 0: raise HTTPException(status_code=400, detail="Not enough stock")
    return auto_price

def purchase_order_dict(po, db):
    items = db.query(PurchaseOrderItem).filter(PurchaseOrderItem.purchase_order_id == po.id).all()
    return {"id": po.id, "pharmacy_id": po.pharmacy_id, "status": po.status, "note": po.note,
            "total_estimated_amount": po.total_estimated_amount,
            "created_at": po.created_at.isoformat() if po.created_at else None,
            "items": [{"id": i.id, "medicine_id": i.medicine_id, "medicine_name": i.medicine_name,
                       "quantity": i.quantity, "estimated_cost": i.estimated_cost, "line_total": i.line_total} for i in items]}

def calculate_predictive_orders(db, pharmacy_id):
    medicines = db.query(Medicine).filter(Medicine.pharmacy_id == pharmacy_id).all()
    now = datetime.utcnow()
    results = []
    for m in medicines:
        current = int(db.query(func.sum(StockBatch.quantity)).filter(
            StockBatch.pharmacy_id == pharmacy_id, StockBatch.medicine_id == m.id).scalar() or 0)
        s7 = int(db.query(func.sum(Sale.quantity)).filter(
            Sale.pharmacy_id == pharmacy_id, Sale.medicine_id == m.id, Sale.sale_date >= now - timedelta(days=7)).scalar() or 0)
        s30 = int(db.query(func.sum(Sale.quantity)).filter(
            Sale.pharmacy_id == pharmacy_id, Sale.medicine_id == m.id, Sale.sale_date >= now - timedelta(days=30)).scalar() or 0)
        s90 = int(db.query(func.sum(Sale.quantity)).filter(
            Sale.pharmacy_id == pharmacy_id, Sale.medicine_id == m.id, Sale.sale_date >= now - timedelta(days=90)).scalar() or 0)
        a7, a30, a90 = s7/7.0, s30/30.0, s90/90.0
        wavg = (a7*0.5 + a30*0.3 + a90*0.2) if (s7+s30+s90) > 0 else 0.0
        ltd = wavg * 7
        ss = ltd * 0.3
        rp = ltd + ss
        exp30 = wavg * 30
        ro = max(0, math.ceil(exp30 + ss - current))
        ms = m.min_stock or 0
        if ro <= 0 and ms and current <= ms: ro = max(0, math.ceil(ms*2 - current))
        proj = math.floor(current - exp30)
        urg = "High" if (ro > 0 and (current <= rp or (ms and current <= ms))) else ("Medium" if ro > 0 else "Normal")
        results.append({"medicine_id": m.id, "medicine_name": m.name, "generic_name": m.generic_name,
                        "barcode": m.barcode, "current_stock": current, "min_stock": ms,
                        "sales_7_days": s7, "sales_30_days": s30, "sales_90_days": s90,
                        "weighted_avg_daily_sales": round(wavg, 2), "lead_time_days": 7,
                        "lead_time_demand": math.ceil(ltd), "safety_stock": math.ceil(ss),
                        "reorder_point": math.ceil(rp), "expected_30_day_demand": math.ceil(exp30),
                        "recommended_order": int(ro), "projected_stock_after_30_days": int(proj), "urgency": urg})
    uo = {"High": 0, "Medium": 1, "Normal": 2}
    results.sort(key=lambda x: (uo.get(x["urgency"], 3), -x["recommended_order"]))
    return results

# App
app = FastAPI(title="MedStock PK API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

scheduler = AsyncIOScheduler()

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if not db.query(User).filter(User.email == ADMIN_EMAIL).first():
        db.add(User(id=str(uuid.uuid4()), email=ADMIN_EMAIL, hashed_password=hash_password(ADMIN_PASSWORD), role="admin"))
        db.commit()
    db.close()
    scheduler.start()

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()

@app.post("/register")
def register(p: PharmacyRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == p.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")
    pid = str(uuid.uuid4())
    db.add(Pharmacy(id=pid, name=p.name, phone=p.phone))
    db.add(User(id=str(uuid.uuid4()), email=p.email, hashed_password=hash_password(p.password), role="pharmacy", pharmacy_id=pid))
    db.commit()
    return {"message": "Registered successfully"}

@app.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect credentials")
    return {"access_token": create_access_token({"sub": user.id, "role": user.role}),
            "token_type": "bearer", "role": user.role, "pharmacy_id": user.pharmacy_id}

@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role, "pharmacy_id": user.pharmacy_id}

@app.post("/medicines")
def create_medicine(p: MedicineCreate, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    m = Medicine(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, name=p.name,
                 generic_name=p.generic_name, category=p.category, barcode=p.barcode, min_stock=p.min_stock)
    db.add(m); db.commit(); db.refresh(m)
    return {"id": m.id, "name": m.name}

@app.get("/medicines")
def get_medicines(user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return []
    meds = db.query(Medicine).filter(Medicine.pharmacy_id == user.pharmacy_id).all()
    result = []
    for m in meds:
        qty = int(db.query(func.sum(StockBatch.quantity)).filter(
            StockBatch.medicine_id == m.id, StockBatch.pharmacy_id == user.pharmacy_id).scalar() or 0)
        result.append({"id": m.id, "name": m.name, "generic_name": m.generic_name, "category": m.category,
                       "barcode": m.barcode, "min_stock": m.min_stock, "total_quantity": qty})
    return result

@app.get("/medicines/search")
def search_medicines(query: str = "", user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return []
    q = db.query(Medicine).filter(Medicine.pharmacy_id == user.pharmacy_id)
    if query: q = q.filter(or_(Medicine.name.ilike(f"%{query}%"), Medicine.generic_name.ilike(f"%{query}%"), Medicine.barcode.ilike(f"%{query}%")))
    return [{"id": m.id, "name": m.name, "generic_name": m.generic_name, "barcode": m.barcode,
             "total_quantity": int(db.query(func.sum(StockBatch.quantity)).filter(
                 StockBatch.medicine_id == m.id, StockBatch.pharmacy_id == user.pharmacy_id).scalar() or 0)} for m in q.all()]

@app.get("/medicines/barcode/{barcode}")
def get_by_barcode(barcode: str, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    m = db.query(Medicine).filter(Medicine.pharmacy_id == user.pharmacy_id, Medicine.barcode == barcode).first()
    if not m: raise HTTPException(status_code=404, detail="Not found")
    qty = int(db.query(func.sum(StockBatch.quantity)).filter(
        StockBatch.medicine_id == m.id, StockBatch.pharmacy_id == user.pharmacy_id).scalar() or 0)
    return {"id": m.id, "name": m.name, "generic_name": m.generic_name, "barcode": m.barcode, "total_quantity": qty}

@app.post("/stock")
def add_stock(p: StockCreate, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    m = db.query(Medicine).filter(Medicine.id == p.medicine_id, Medicine.pharmacy_id == user.pharmacy_id).first()
    if not m: raise HTTPException(status_code=404, detail="Medicine not found")
    s = StockBatch(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, medicine_id=p.medicine_id,
                   batch_number=p.batch_number, expiry_date=p.expiry_date, quantity=p.quantity,
                   purchase_price=p.purchase_price, sale_price=p.sale_price)
    db.add(s); db.commit()
    return {"message": "Stock added", "medicine": m.name, "batch": p.batch_number}

@app.get("/stock")
def get_stock(user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return []
    batches = db.query(StockBatch).filter(StockBatch.pharmacy_id == user.pharmacy_id).order_by(StockBatch.expiry_date).all()
    result = []
    for b in batches:
        m = db.query(Medicine).filter(Medicine.id == b.medicine_id).first()
        dte = (b.expiry_date - date.today()).days if b.expiry_date else None
        result.append({"id": b.id, "medicine_id": b.medicine_id, "medicine_name": m.name if m else "Unknown",
                       "batch_number": b.batch_number, "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                       "quantity": b.quantity, "purchase_price": b.purchase_price, "sale_price": b.sale_price, "days_to_expiry": dte})
    return result

@app.get("/dashboard")
def dashboard(user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return {"message": "Admin"}
    meds = db.query(Medicine).filter(Medicine.pharmacy_id == user.pharmacy_id).all()
    total_qty = int(db.query(func.sum(StockBatch.quantity)).filter(StockBatch.pharmacy_id == user.pharmacy_id).scalar() or 0)
    warn = date.today() + relativedelta(months=7)
    exp7 = db.query(StockBatch).filter(StockBatch.pharmacy_id == user.pharmacy_id, StockBatch.expiry_date <= warn, StockBatch.quantity > 0).count()
    low = sum(1 for m in meds if (int(db.query(func.sum(StockBatch.quantity)).filter(
        StockBatch.medicine_id == m.id, StockBatch.pharmacy_id == user.pharmacy_id).scalar() or 0) <= (m.min_stock or 0)))
    return {"total_medicines": len(meds), "total_stock_quantity": total_qty, "expiring_within_7_months": exp7, "low_stock_items": low}

@app.get("/expiry-warning")
def expiry_warning(user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return []
    warn = date.today() + relativedelta(months=7)
    batches = db.query(StockBatch).filter(StockBatch.pharmacy_id == user.pharmacy_id, StockBatch.expiry_date <= warn, StockBatch.quantity > 0).order_by(StockBatch.expiry_date).all()
    result = []
    for b in batches:
        m = db.query(Medicine).filter(Medicine.id == b.medicine_id).first()
        dte = (b.expiry_date - date.today()).days if b.expiry_date else None
        result.append({"medicine_name": m.name if m else "Unknown", "batch_number": b.batch_number,
                       "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None, "quantity": b.quantity,
                       "days_to_expiry": dte, "warning": "Expired" if dte and dte < 0 else "Expires within 7 months"})
    return result

@app.post("/sales")
def record_sale(p: SaleCreate, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    if p.quantity <= 0: raise HTTPException(status_code=400, detail="Quantity must be > 0")
    m = db.query(Medicine).filter(Medicine.id == p.medicine_id, Medicine.pharmacy_id == user.pharmacy_id).first()
    if not m: raise HTTPException(status_code=404, detail="Medicine not found")
    _reduce_stock(db, user.pharmacy_id, p.medicine_id, p.quantity)
    db.add(Sale(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, medicine_id=p.medicine_id, quantity=p.quantity, sale_price=p.sale_price))
    db.commit()
    return {"message": "Sale recorded", "medicine": m.name, "quantity": p.quantity}

@app.post("/sales/pos")
def pos_sale(p: PosSaleRequest, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    if not p.items: raise HTTPException(status_code=400, detail="Cart empty")
    sale = PosSale(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, total_amount=0, payment_method=p.payment_method, customer_name=p.customer_name)
    db.add(sale); db.flush()
    total = 0.0
    for idx, item in enumerate(p.items, 1):
        m = db.query(Medicine).filter(Medicine.id == item.medicine_id, Medicine.pharmacy_id == user.pharmacy_id).first()
        if not m: raise HTTPException(status_code=404, detail=f"Medicine not found line {idx}")
        if item.quantity <= 0: raise HTTPException(status_code=400, detail=f"Invalid quantity line {idx}")
        auto_p = _reduce_stock(db, user.pharmacy_id, item.medicine_id, item.quantity)
        up = item.unit_price if item.unit_price > 0 else auto_p
        lt = up * item.quantity
        db.add(PosSaleItem(id=str(uuid.uuid4()), sale_id=sale.id, medicine_id=item.medicine_id, quantity=item.quantity, unit_price=up, line_total=lt))
        total += lt
    sale.total_amount = round(total, 2)
    db.commit()
    return {"message": "POS sale completed", "sale_id": sale.id, "total_amount": sale.total_amount}

@app.get("/predictive-orders")
def predictive_orders(user: User = Depends(require_premium_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return []
    return calculate_predictive_orders(db, user.pharmacy_id)

@app.post("/purchase-orders/generate-from-predictive")
def gen_po_predictive(p: PurchaseOrderGenerateRequest, user: User = Depends(require_premium_subscription), db: Session = Depends(get_db)):
    suggestions = calculate_predictive_orders(db, user.pharmacy_id)
    selected = [s for s in suggestions if s["urgency"] in p.include_urgencies and s["recommended_order"] >= p.min_order_quantity]
    if not selected: raise HTTPException(status_code=400, detail="No items match criteria")
    po = PurchaseOrder(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, status="draft", note="AI generated", total_estimated_amount=0)
    db.add(po); db.flush()
    total = 0.0
    for s in selected:
        ec = _latest_purchase_price(db, user.pharmacy_id, s["medicine_id"])
        lt = ec * s["recommended_order"]
        db.add(PurchaseOrderItem(id=str(uuid.uuid4()), purchase_order_id=po.id, medicine_id=s["medicine_id"],
                                 medicine_name=s["medicine_name"], quantity=s["recommended_order"], estimated_cost=ec, line_total=lt))
        total += lt
    po.total_estimated_amount = round(total, 2)
    db.commit()
    return purchase_order_dict(po, db)

@app.post("/purchase-orders/generate-from-selection")
def gen_po_selection(p: PurchaseOrderSelectionRequest, user: User = Depends(require_premium_subscription), db: Session = Depends(get_db)):
    if not p.items: raise HTTPException(status_code=400, detail="No items selected")
    po = PurchaseOrder(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, status="draft", note=p.note, total_estimated_amount=0)
    db.add(po); db.flush()
    total = 0.0
    count = 0
    for idx, item in enumerate(p.items, 1):
        if item.quantity <= 0: continue
        m = db.query(Medicine).filter(Medicine.id == item.medicine_id, Medicine.pharmacy_id == user.pharmacy_id).first()
        if not m: raise HTTPException(status_code=404, detail=f"Medicine not found line {idx}")
        ec = _latest_purchase_price(db, user.pharmacy_id, m.id)
        lt = ec * item.quantity
        db.add(PurchaseOrderItem(id=str(uuid.uuid4()), purchase_order_id=po.id, medicine_id=m.id,
                                 medicine_name=m.name, quantity=item.quantity, estimated_cost=ec, line_total=lt))
        total += lt; count += 1
    if count == 0: raise HTTPException(status_code=400, detail="No valid items")
    po.total_estimated_amount = round(total, 2)
    db.commit()
    return purchase_order_dict(po, db)

@app.get("/purchase-orders")
def get_pos(user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": return []
    orders = db.query(PurchaseOrder).filter(PurchaseOrder.pharmacy_id == user.pharmacy_id).order_by(PurchaseOrder.created_at.desc()).all()
    return [purchase_order_dict(o, db) for o in orders]

@app.get("/purchase-orders/{order_id}/pdf")
def po_pdf(order_id: str, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    order = db.query(PurchaseOrder).filter(PurchaseOrder.id == order_id, PurchaseOrder.pharmacy_id == user.pharmacy_id).first()
    if not order: raise HTTPException(status_code=404, detail="Not found")
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == user.pharmacy_id).first()
    items = db.query(PurchaseOrderItem).filter(PurchaseOrderItem.purchase_order_id == order.id).all()
    fp = os.path.join(DOWNLOAD_DIR, f"po_{order.id}.pdf")
    doc = SimpleDocTemplate(fp, pagesize=A4)
    els = [Paragraph(pharmacy.name if pharmacy else "MedStock PK", getSampleStyleSheet()["Title"]), Spacer(1, 10),
           Paragraph(f"PO: {order.id}", getSampleStyleSheet()["Heading2"]),
           Paragraph(f"Date: {order.created_at.strftime('%d-%m-%Y') if order.created_at else '-'}", getSampleStyleSheet()["Normal"]), Spacer(1, 16)]
    td = [["Medicine", "Qty", "Est. Cost", "Total"]]
    for i in items: td.append([i.medicine_name, str(i.quantity), f"PKR {i.estimated_cost:.2f}", f"PKR {i.line_total:.2f}"])
    td.append(["", "", "Total", f"PKR {order.total_estimated_amount:.2f}"])
    t = Table(td, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.teal), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                           ("GRID", (0,0), (-1,-1), 0.5, colors.grey), ("ALIGN", (1,0), (-1,-1), "RIGHT")]))
    els.append(t)
    doc.build(els)
    return FileResponse(fp, media_type="application/pdf", filename=f"po_{order.id}.pdf")

@app.post("/ai/invoice-multiple")
async def ai_invoice_multi(files: List[UploadFile] = File(...), user: User = Depends(require_active_subscription)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    all_items, all_warnings, all_errors, ai_modes = [], [], [], []
    for f in files:
        fp = await _save_upload_file(f)
        r = parse_invoice_file(fp)
        ai_modes.append({"file": f.filename, "ai_mode": r.get("ai_mode")})
        if r.get("error"): all_errors.append({"file": f.filename, "error": r["error"]})
        all_warnings.extend([{"file": f.filename, "warning": w} for w in r.get("warnings", [])])
        for item in r.get("items", []): item["source_file"] = f.filename; all_items.append(item)
    return {"status": "completed", "ai_modes": ai_modes, "items": all_items, "warnings": all_warnings, "errors": all_errors}

@app.post("/ai/invoice/approve")
def approve_invoice(p: InvoiceApprove, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    added, errors = [], []
    for ln, item in enumerate(p.items, 1):
        name = (item.name or "").strip()
        bn = (item.batch_number or "").strip()
        if not name: errors.append({"line": ln, "error": "Name required"}); continue
        if not bn: errors.append({"line": ln, "medicine": name, "error": "Batch required"}); continue
        if not item.expiry_date: errors.append({"line": ln, "medicine": name, "error": "Expiry required"}); continue
        if item.quantity <= 0: errors.append({"line": ln, "medicine": name, "error": "Quantity > 0"}); continue
        m = None
        if item.barcode: m = db.query(Medicine).filter(Medicine.pharmacy_id == user.pharmacy_id, Medicine.barcode == item.barcode).first()
        if not m: m = db.query(Medicine).filter(Medicine.pharmacy_id == user.pharmacy_id, Medicine.name == name).first()
        if not m:
            m = Medicine(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, name=name, generic_name=item.generic_name,
                         category=item.category, barcode=item.barcode, min_stock=10)
            db.add(m)
        db.add(StockBatch(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, medicine_id=m.id, batch_number=bn,
                          expiry_date=item.expiry_date, quantity=item.quantity, purchase_price=item.purchase_price, sale_price=item.sale_price))
        added.append({"medicine": m.name, "batch": bn, "qty": item.quantity})
    db.commit()
    return {"message": f"{len(added)} items added", "added_count": len(added), "errors": errors, "items": added}

@app.post("/ai/medicine-photo-v2")
async def ai_med_photo(file: UploadFile = File(...), user: User = Depends(require_active_subscription)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    fp = await _save_upload_file(file)
    r = parse_medicine_photo(fp)
    r["file_path"] = fp
    return r

@app.get("/reports/sales")
def sales_report(start_date: Optional[date] = None, end_date: Optional[date] = None, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    today = date.today()
    sd = start_date or (today - timedelta(days=30))
    ed = end_date or today
    sdt = datetime.combine(sd, datetime.min.time())
    edt = datetime.combine(ed, datetime.max.time())
    total_amt, total_units, tx_count = 0.0, 0, 0
    daily, med_rpt = {}, {}
    for sale in db.query(PosSale).filter(PosSale.pharmacy_id == user.pharmacy_id, PosSale.created_at >= sdt, PosSale.created_at <= edt).all():
        dk = sale.created_at.date().isoformat()
        if dk not in daily: daily[dk] = {"date": dk, "amount": 0.0, "units": 0, "transactions": 0}
        su = 0
        for si in db.query(PosSaleItem).filter(PosSaleItem.sale_id == sale.id).all():
            su += si.quantity
            mn = db.query(Medicine).filter(Medicine.id == si.medicine_id).first()
            mn_name = mn.name if mn else "Unknown"
            if mn_name not in med_rpt: med_rpt[mn_name] = {"medicine_name": mn_name, "quantity": 0, "revenue": 0.0}
            med_rpt[mn_name]["quantity"] += si.quantity; med_rpt[mn_name]["revenue"] += si.line_total
        total_amt += sale.total_amount; total_units += su; tx_count += 1
        daily[dk]["amount"] += sale.total_amount; daily[dk]["units"] += su; daily[dk]["transactions"] += 1
    return {"start_date": sd.isoformat(), "end_date": ed.isoformat(), "total_amount": round(total_amt, 2),
            "total_units": total_units, "transaction_count": tx_count,
            "by_date": sorted(daily.values(), key=lambda x: x["date"]),
            "by_medicine": sorted(med_rpt.values(), key=lambda x: x["revenue"], reverse=True)}

@app.get("/reports/expiry")
def expiry_report(months: int = 7, user: User = Depends(require_active_subscription), db: Session = Depends(get_db)):
    if user.role != "pharmacy": raise HTTPException(status_code=403, detail="Pharmacy only")
    threshold = date.today() + relativedelta(months=months)
    batches = db.query(StockBatch).filter(StockBatch.pharmacy_id == user.pharmacy_id, StockBatch.expiry_date <= threshold, StockBatch.quantity > 0).order_by(StockBatch.expiry_date).all()
    items, tq, tpv, tsv = [], 0, 0.0, 0.0
    for b in batches:
        m = db.query(Medicine).filter(Medicine.id == b.medicine_id).first()
        dte = (b.expiry_date - date.today()).days
        items.append({"medicine_name": m.name if m else "Unknown", "batch_number": b.batch_number,
                      "expiry_date": b.expiry_date.isoformat(), "quantity": b.quantity, "days_to_expiry": dte,
                      "purchase_price": b.purchase_price, "sale_price": b.sale_price, "status": "Expired" if dte < 0 else "Expiring"})
        tq += b.quantity; tpv += b.quantity * b.purchase_price; tsv += b.quantity * b.sale_price
    return {"months": months, "threshold_date": threshold.isoformat(), "total_batches": len(items),
            "total_quantity": tq, "total_purchase_value": round(tpv, 2), "total_sale_value": round(tsv, 2), "items": items}

@app.get("/subscriptions/plans")
def get_plans(): return SUBSCRIPTION_PLANS

@app.post("/subscriptions/request")
def sub_request(p: SubscriptionRequestCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "pharmacy" or not user.pharmacy_id: raise HTTPException(status_code=403, detail="Pharmacy only")
    plan = SUBSCRIPTION_PLANS.get(p.plan)
    if not plan: raise HTTPException(status_code=400, detail="Invalid plan")
    r = SubscriptionRequest(id=str(uuid.uuid4()), pharmacy_id=user.pharmacy_id, plan=p.plan, amount=plan["price"],
                            payment_method=p.payment_method, transaction_reference=p.transaction_reference, status="pending")
    db.add(r); db.commit()
    return {"id": r.id, "plan": r.plan, "amount": r.amount, "status": r.status}

@app.get("/admin/pharmacies")
def admin_pharmacies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(status_code=403, detail="Admin only")
    return [{"id": p.id, "name": p.name, "phone": p.phone, "subscription_plan": p.subscription_plan,
             "subscription_status": p.subscription_status, "subscription_expiry": p.subscription_expiry.isoformat() if p.subscription_expiry else None}
            for p in db.query(Pharmacy).all()]

@app.post("/admin/pharmacies/{pid}/activate-subscription")
def activate_sub(pid: str, p: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(status_code=403, detail="Admin only")
    ph = db.query(Pharmacy).filter(Pharmacy.id == pid).first()
    if not ph: raise HTTPException(status_code=404, detail="Not found")
    ph.subscription_plan = p.get("plan", "Pro")
    ph.subscription_status = "active"
    ph.subscription_expiry = date.today() + timedelta(days=int(p.get("days", 30)))
    db.commit()
    return {"message": "Subscription activated"}

@app.get("/admin/subscription-requests")
def admin_sub_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(status_code=403, detail="Admin only")
    reqs = db.query(SubscriptionRequest).order_by(SubscriptionRequest.created_at.desc()).all()
    result = []
    for r in reqs:
        ph = db.query(Pharmacy).filter(Pharmacy.id == r.pharmacy_id).first()
        result.append({"id": r.id, "pharmacy_id": r.pharmacy_id, "pharmacy_name": ph.name if ph else "Unknown",
                       "plan": r.plan, "amount": r.amount, "payment_method": r.payment_method,
                       "transaction_reference": r.transaction_reference, "status": r.status})
    return result

@app.post("/admin/subscription-requests/{rid}/approve")
def approve_req(rid: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(status_code=403, detail="Admin only")
    r = db.query(SubscriptionRequest).filter(SubscriptionRequest.id == rid).first()
    if not r: raise HTTPException(status_code=404, detail="Not found")
    if r.status != "pending": raise HTTPException(status_code=400, detail="Not pending")
    plan = SUBSCRIPTION_PLANS.get(r.plan)
    if not plan: raise HTTPException(status_code=400, detail="Invalid plan")
    ph = db.query(Pharmacy).filter(Pharmacy.id == r.pharmacy_id).first()
    if ph:
        ph.subscription_plan = r.plan; ph.subscription_status = "active"
        ph.subscription_expiry = date.today() + timedelta(days=plan["days"])
    r.status = "approved"; db.commit()
    return {"message": "Approved and activated"}

@app.post("/admin/subscription-requests/{rid}/reject")
def reject_req(rid: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(status_code=403, detail="Admin only")
    r = db.query(SubscriptionRequest).filter(SubscriptionRequest.id == rid).first()
    if not r: raise HTTPException(status_code=404, detail="Not found")
    r.status = "rejected"; db.commit()
    return {"message": "Rejected"}
