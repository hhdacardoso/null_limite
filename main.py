import enum
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, Field
import bcrypt
from jose import jwt, JWTError
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import sessionmaker, declarative_base, Session
import aiofiles
from dotenv import load_dotenv

load_dotenv()

# Config
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./null_limite.db")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 7))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 10 * 1024 * 1024))
# Emails registered with these addresses get the manager role (can review evidences)
MANAGER_EMAILS = {e.strip().lower() for e in os.getenv("MANAGER_EMAILS", "").split(",") if e.strip()}

RESUME_EXTENSIONS = {".pdf"}
EVIDENCE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

def utcnow() -> datetime:
    # naive UTC, matches what SQLite stores
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Database
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Security
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Models
class UserRole(str, enum.Enum):
    PROFESSIONAL = "professional"
    MANAGER = "manager"

class Professional(Base):
    __tablename__ = "professionals"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    job_title = Column(String(255), nullable=True)
    years_experience = Column(Integer, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.PROFESSIONAL, nullable=False)
    created_at = Column(DateTime, default=utcnow)

class ResumeStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class Resume(Base):
    __tablename__ = "resumes"
    id = Column(Integer, primary_key=True, index=True)
    professional_id = Column(Integer, ForeignKey("professionals.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    status = Column(Enum(ResumeStatus), default=ResumeStatus.PENDING, nullable=False)
    parsed_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

class Certificate(Base):
    __tablename__ = "certificates"
    id = Column(Integer, primary_key=True, index=True)
    professional_id = Column(Integer, ForeignKey("professionals.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    issuer = Column(String(255), nullable=True)
    issue_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    credential_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow)

class Project(Base):
    """Professional experience on a project; counts toward bid criteria like sector experience."""
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    professional_id = Column(Integer, ForeignKey("professionals.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    client = Column(String(255), nullable=True)
    sector = Column(String(100), nullable=True, index=True)
    role = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)  # null = ongoing
    created_at = Column(DateTime, default=utcnow)

class EducationLevel(str, enum.Enum):
    TECHNICAL = "technical"
    BACHELOR = "bachelor"
    SPECIALIZATION = "specialization"
    MBA = "mba"
    MASTER = "master"
    DOCTORATE = "doctorate"

class Education(Base):
    __tablename__ = "education"
    id = Column(Integer, primary_key=True, index=True)
    professional_id = Column(Integer, ForeignKey("professionals.id", ondelete="CASCADE"), nullable=False, index=True)
    level = Column(Enum(EducationLevel), nullable=False)
    course = Column(String(255), nullable=False)
    institution = Column(String(255), nullable=True)
    completion_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

class EvidenceTarget(str, enum.Enum):
    CERTIFICATE = "certificate"
    PROJECT = "project"
    EDUCATION = "education"

class EvidenceStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class Evidence(Base):
    """Supporting document (certificate, diploma, atestado) for exactly one profile item."""
    __tablename__ = "evidences"
    id = Column(Integer, primary_key=True, index=True)
    professional_id = Column(Integer, ForeignKey("professionals.id", ondelete="CASCADE"), nullable=False, index=True)
    certificate_id = Column(Integer, ForeignKey("certificates.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    education_id = Column(Integer, ForeignKey("education.id", ondelete="CASCADE"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    status = Column(Enum(EvidenceStatus), default=EvidenceStatus.PENDING, nullable=False)
    review_note = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("professionals.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

EVIDENCE_TARGETS = {
    EvidenceTarget.CERTIFICATE: (Certificate, "certificate_id"),
    EvidenceTarget.PROJECT: (Project, "project_id"),
    EvidenceTarget.EDUCATION: (Education, "education_id"),
}

Base.metadata.create_all(bind=engine)

# Schemas
class ProfessionalCreate(BaseModel):
    email: EmailStr
    full_name: str
    job_title: Optional[str] = None
    years_experience: Optional[int] = Field(default=None, ge=0)
    password: str = Field(min_length=6, max_length=72)  # bcrypt limit

class ProfessionalUpdate(BaseModel):
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    years_experience: Optional[int] = Field(default=None, ge=0)

class ProfessionalOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    job_title: Optional[str] = None
    years_experience: Optional[int] = None
    role: UserRole
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class ResumeOut(BaseModel):
    id: int
    professional_id: int
    filename: str
    file_size: int
    status: ResumeStatus
    parsed_data: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class CertificateCreate(BaseModel):
    name: str
    issuer: Optional[str] = None
    issue_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    credential_url: Optional[str] = None

class CertificateOut(CertificateCreate):
    id: int
    professional_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ProjectCreate(BaseModel):
    name: str
    client: Optional[str] = None
    sector: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

class ProjectOut(ProjectCreate):
    id: int
    professional_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class EducationCreate(BaseModel):
    level: EducationLevel
    course: str
    institution: Optional[str] = None
    completion_date: Optional[datetime] = None

class EducationOut(EducationCreate):
    id: int
    professional_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class EvidenceOut(BaseModel):
    id: int
    professional_id: int
    certificate_id: Optional[int] = None
    project_id: Optional[int] = None
    education_id: Optional[int] = None
    filename: str
    file_size: int
    status: EvidenceStatus
    review_note: Optional[str] = None
    reviewed_by: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class EvidenceReview(BaseModel):
    status: EvidenceStatus
    note: Optional[str] = None

# Security functions
def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
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

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Professional:
    email = decode_token(token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = db.query(Professional).filter(Professional.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def require_manager(user: Professional = Depends(get_current_user)) -> Professional:
    if user.role != UserRole.MANAGER:
        raise HTTPException(403, "Manager role required")
    return user

# Helpers
async def save_upload(file: UploadFile, allowed_extensions: set) -> tuple[str, int]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(400, f"Allowed file types: {', '.join(sorted(allowed_extensions))}")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large (max 10MB)")

    file_path = Path(UPLOAD_DIR) / f"{uuid.uuid4()}{ext}"
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    return str(file_path), len(content)

def create_item(db: Session, item):
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

# App
app = FastAPI(title="Null Limite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes: auth
@app.post("/auth/register", response_model=ProfessionalOut, status_code=201)
def register(data: ProfessionalCreate, db: Session = Depends(get_db)):
    if db.query(Professional).filter(Professional.email == data.email).first():
        raise HTTPException(400, "Email already registered")
    user = Professional(
        email=data.email,
        full_name=data.full_name,
        job_title=data.job_title,
        years_experience=data.years_experience,
        hashed_password=get_password_hash(data.password),
        role=UserRole.MANAGER if data.email.lower() in MANAGER_EMAILS else UserRole.PROFESSIONAL,
    )
    return create_item(db, user)

@app.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(Professional).filter(Professional.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/auth/me", response_model=ProfessionalOut)
def me(user: Professional = Depends(get_current_user)):
    return user

# Routes: own profile
@app.patch("/professionals/me", response_model=ProfessionalOut)
def update_me(data: ProfessionalUpdate, user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user

@app.post("/professionals/me/resumes", response_model=ResumeOut, status_code=202)
async def upload_resume(
    file: UploadFile = File(...),
    user: Professional = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    file_path, file_size = await save_upload(file, RESUME_EXTENSIONS)
    resume = Resume(
        professional_id=user.id,
        filename=file.filename,
        file_path=file_path,
        file_size=file_size,
        status=ResumeStatus.PENDING
    )
    return create_item(db, resume)

@app.get("/professionals/me/resumes", response_model=List[ResumeOut])
def list_resumes(user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Resume).filter(Resume.professional_id == user.id).all()

@app.post("/professionals/me/certificates", response_model=CertificateOut, status_code=201)
def add_certificate(cert: CertificateCreate, user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_item(db, Certificate(professional_id=user.id, **cert.model_dump()))

@app.get("/professionals/me/certificates", response_model=List[CertificateOut])
def list_certificates(user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Certificate).filter(Certificate.professional_id == user.id).all()

@app.post("/professionals/me/projects", response_model=ProjectOut, status_code=201)
def add_project(project: ProjectCreate, user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_item(db, Project(professional_id=user.id, **project.model_dump()))

@app.get("/professionals/me/projects", response_model=List[ProjectOut])
def list_projects(user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.professional_id == user.id).all()

@app.post("/professionals/me/education", response_model=EducationOut, status_code=201)
def add_education(education: EducationCreate, user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_item(db, Education(professional_id=user.id, **education.model_dump()))

@app.get("/professionals/me/education", response_model=List[EducationOut])
def list_education(user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Education).filter(Education.professional_id == user.id).all()

@app.post("/professionals/me/evidences", response_model=EvidenceOut, status_code=201)
async def upload_evidence(
    target_type: EvidenceTarget = Form(...),
    target_id: int = Form(...),
    file: UploadFile = File(...),
    user: Professional = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    model, fk_column = EVIDENCE_TARGETS[target_type]
    target = db.query(model).filter(model.id == target_id, model.professional_id == user.id).first()
    if not target:
        raise HTTPException(404, f"{target_type.value} not found")
    file_path, file_size = await save_upload(file, EVIDENCE_EXTENSIONS)
    evidence = Evidence(
        professional_id=user.id,
        filename=file.filename,
        file_path=file_path,
        file_size=file_size,
        **{fk_column: target.id},
    )
    return create_item(db, evidence)

@app.get("/professionals/me/evidences", response_model=List[EvidenceOut])
def list_my_evidences(user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Evidence).filter(Evidence.professional_id == user.id).all()

# Routes: evidence review (managers)
@app.get("/evidences", response_model=List[EvidenceOut])
def list_evidences(
    status: Optional[EvidenceStatus] = None,
    _: Professional = Depends(require_manager),
    db: Session = Depends(get_db)
):
    query = db.query(Evidence)
    if status:
        query = query.filter(Evidence.status == status)
    return query.order_by(Evidence.created_at).all()

@app.post("/evidences/{evidence_id}/review", response_model=EvidenceOut)
def review_evidence(
    evidence_id: int,
    review: EvidenceReview,
    manager: Professional = Depends(require_manager),
    db: Session = Depends(get_db)
):
    if review.status == EvidenceStatus.PENDING:
        raise HTTPException(400, "Review must approve or reject")
    evidence = db.get(Evidence, evidence_id)
    if not evidence:
        raise HTTPException(404, "Evidence not found")
    if evidence.professional_id == manager.id:
        raise HTTPException(403, "Cannot review your own evidence")
    evidence.status = review.status
    evidence.review_note = review.note
    evidence.reviewed_by = manager.id
    evidence.reviewed_at = utcnow()
    db.commit()
    db.refresh(evidence)
    return evidence

@app.get("/evidences/{evidence_id}/file")
def download_evidence(evidence_id: int, user: Professional = Depends(get_current_user), db: Session = Depends(get_db)):
    # Files hold personal data (LGPD): only the owner and managers can read them
    evidence = db.get(Evidence, evidence_id)
    if not evidence or (evidence.professional_id != user.id and user.role != UserRole.MANAGER):
        raise HTTPException(404, "Evidence not found")
    return FileResponse(evidence.file_path, filename=evidence.filename)

@app.get("/health")
def health():
    return {"status": "ok", "service": "null-limite"}
