import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
import aiofiles

# Config
DATABASE_URL = "sqlite:///./null_limite.db"
SECRET_KEY = "dev-secret-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
UPLOAD_DIR = "./uploads"
MAX_FILE_SIZE = 10 * 1024 * 1024

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

# Database
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Security
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Models
class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class ResumeStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class Resume(Base):
    __tablename__ = "resumes"
    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    status = Column(Enum(ResumeStatus), default=ResumeStatus.PENDING, nullable=False)
    parsed_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Certificate(Base):
    __tablename__ = "certificates"
    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    issuer = Column(String(255), nullable=True)
    issue_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    credential_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# Schemas
class CandidateCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str

class CandidateLogin(BaseModel):
    email: EmailStr
    password: str

class CandidateOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    created_at: datetime
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class ResumeOut(BaseModel):
    id: int
    candidate_id: int
    filename: str
    file_size: int
    status: ResumeStatus
    parsed_data: Optional[str] = None
    created_at: datetime
    class Config:
        from_attributes = True

class CertificateCreate(BaseModel):
    name: str
    issuer: Optional[str] = None
    issue_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    credential_url: Optional[str] = None

class CertificateOut(CertificateCreate):
    id: int
    candidate_id: int
    created_at: datetime
    class Config:
        from_attributes = True

# Security functions
def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

# Dependencies
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Candidate:
    email = decode_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = db.query(Candidate).filter(Candidate.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# App
app = FastAPI(title="Null Limite - S1 Fundação")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Routes
@app.post("/auth/register", response_model=CandidateOut, status_code=201)
def register(data: CandidateCreate, db: Session = Depends(get_db)):
    if db.query(Candidate).filter(Candidate.email == data.email).first():
        raise HTTPException(400, "Email already registered")
    user = Candidate(
        email=data.email,
        full_name=data.full_name,
        hashed_password=get_password_hash(data.password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@app.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(Candidate).filter(Candidate.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/auth/me", response_model=CandidateOut)
def me(user: Candidate = Depends(get_current_user)):
    return user

@app.post("/candidates/me/resumes", response_model=ResumeOut, status_code=202)
async def upload_resume(
    file: UploadFile = File(...),
    user: Candidate = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "Only PDF files allowed")
    
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large (max 10MB)")
    
    ext = Path(file.filename).suffix
    unique_name = f"{uuid.uuid4()}{ext}"
    file_path = Path(UPLOAD_DIR) / unique_name
    
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    resume = Resume(
        candidate_id=user.id,
        filename=file.filename,
        file_path=str(file_path),
        file_size=len(content),
        status=ResumeStatus.PENDING
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume

@app.get("/candidates/me/resumes", response_model=List[ResumeOut])
def list_resumes(user: Candidate = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Resume).filter(Resume.candidate_id == user.id).all()

@app.post("/candidates/me/certificates", response_model=CertificateOut, status_code=201)
def add_certificate(
    cert: CertificateCreate,
    user: Candidate = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_cert = Certificate(candidate_id=user.id, **cert.model_dump())
    db.add(db_cert)
    db.commit()
    db.refresh(db_cert)
    return db_cert

@app.get("/candidates/me/certificates", response_model=List[CertificateOut])
def list_certificates(user: Candidate = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Certificate).filter(Certificate.candidate_id == user.id).all()

@app.get("/health")
def health():
    return {"status": "ok", "service": "null-limite-s1"}