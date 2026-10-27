import enum
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
import bcrypt
from jose import jwt, JWTError
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Enum, JSON
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
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

SECTORS = {"energia", "financeiro", "seguros", "saude", "governo", "varejo", "telecom",
           "tecnologia", "industria", "logistica", "educacao", "outros"}
# Exact team search explores every combination of the top candidates, so keep teams small
MAX_TEAM_SLOTS = 8

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

class CriterionType(str, enum.Enum):
    CERTIFICATION = "certification"
    SECTOR_PROJECTS = "sector_projects"
    EDUCATION = "education"
    YEARS_EXPERIENCE = "years_experience"

class Bid(Base):
    """Tender (edital): a team of roles, each scored by its own criteria."""
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    client = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("professionals.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    roles = relationship("BidRole", cascade="all, delete-orphan", order_by="BidRole.id")

    @property
    def max_points(self) -> int:
        return sum(role.max_points * role.quantity for role in self.roles)

class BidRole(Base):
    __tablename__ = "bid_roles"
    id = Column(Integer, primary_key=True, index=True)
    bid_id = Column(Integer, ForeignKey("bids.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    criteria = relationship("BidCriterion", cascade="all, delete-orphan", order_by="BidCriterion.id")

    @property
    def max_points(self) -> int:
        return sum(criterion.max_points for criterion in self.criteria)

class BidCriterion(Base):
    """One scoring rule; which parameter columns apply depends on `type` (see CriterionIn)."""
    __tablename__ = "bid_criteria"
    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("bid_roles.id", ondelete="CASCADE"), nullable=False, index=True)
    description = Column(String(500), nullable=False)
    type = Column(Enum(CriterionType), nullable=False)
    max_points = Column(Integer, nullable=False)
    points_per_item = Column(Integer, nullable=True)
    keywords = Column(JSON, nullable=True)  # certificate names, or course names for education
    sectors = Column(JSON, nullable=True)  # empty = any sector
    min_level = Column(Enum(EducationLevel), nullable=True)
    min_years = Column(Integer, nullable=True)

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

CRITERION_REQUIRED_FIELDS = {
    CriterionType.CERTIFICATION: ("keywords",),
    CriterionType.SECTOR_PROJECTS: ("points_per_item",),
    CriterionType.EDUCATION: ("min_level",),
    CriterionType.YEARS_EXPERIENCE: ("min_years",),
}

class CriterionIn(BaseModel):
    """
    - certification: max_points if a certificate name matches any of `keywords`
    - sector_projects: points_per_item per project in `sectors` (any sector if empty), capped at max_points
    - education: max_points if a degree is at least `min_level` (and the course matches `keywords`, if given)
    - years_experience: max_points if the experience proven by approved projects reaches `min_years`
    """
    description: str
    type: CriterionType
    max_points: int = Field(gt=0)
    points_per_item: Optional[int] = Field(default=None, gt=0)
    keywords: Optional[List[str]] = None
    sectors: Optional[List[str]] = None
    min_level: Optional[EducationLevel] = None
    min_years: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check_params(self):
        missing = [name for name in CRITERION_REQUIRED_FIELDS[self.type] if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.type.value} criterion requires: {', '.join(missing)}")
        unknown = set(self.sectors or []) - SECTORS
        if unknown:
            raise ValueError(f"Unknown sectors: {', '.join(sorted(unknown))}")
        return self

class CriterionOut(CriterionIn):
    id: int
    model_config = ConfigDict(from_attributes=True)

class BidRoleIn(BaseModel):
    name: str
    quantity: int = Field(default=1, ge=1)
    criteria: List[CriterionIn] = Field(min_length=1)

class BidRoleOut(BaseModel):
    id: int
    name: str
    quantity: int
    max_points: int
    criteria: List[CriterionOut]
    model_config = ConfigDict(from_attributes=True)

class BidCreate(BaseModel):
    title: str
    client: Optional[str] = None
    description: Optional[str] = None
    roles: List[BidRoleIn] = Field(min_length=1)

    @model_validator(mode="after")
    def check_team_size(self):
        if sum(role.quantity for role in self.roles) > MAX_TEAM_SLOTS:
            raise ValueError(f"A bid can have at most {MAX_TEAM_SLOTS} team members")
        return self

class BidOut(BaseModel):
    id: int
    title: str
    client: Optional[str] = None
    description: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime
    max_points: int
    roles: List[BidRoleOut]
    model_config = ConfigDict(from_attributes=True)

class MatchedItem(BaseModel):
    type: EvidenceTarget
    id: int
    label: str
    status: str  # approved, pending, rejected, missing (no evidence) or expired
    evidence_id: Optional[int] = None

class CriterionScore(BaseModel):
    criterion_id: int
    description: str
    points: int
    potential_points: int
    max_points: int
    explanation: str
    items: List[MatchedItem]

class CandidateScore(BaseModel):
    professional_id: int
    full_name: str
    job_title: Optional[str] = None
    years_experience: Optional[int] = None
    points: int
    potential_points: int
    max_points: int
    criteria: List[CriterionScore]

class RoleRanking(BaseModel):
    role_id: int
    role_name: str
    quantity: int
    max_points: int
    candidates: List[CandidateScore]

class TeamSlot(BaseModel):
    role_id: int
    role_name: str
    member: Optional[CandidateScore] = None

class PendingGain(BaseModel):
    evidence_id: int
    professional_id: int
    full_name: str
    role_name: str
    item: str
    gain: int

class TeamOut(BaseModel):
    bid_id: int
    title: str
    points: int
    potential_points: int
    max_points: int
    slots: List[TeamSlot]
    pending_gains: List[PendingGain]  # approving these evidences raises the team score the most

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

def build_bid(data: BidCreate, created_by: Optional[int]) -> Bid:
    return Bid(
        title=data.title,
        client=data.client,
        description=data.description,
        created_by=created_by,
        roles=[
            BidRole(name=role.name, quantity=role.quantity,
                    criteria=[BidCriterion(**criterion.model_dump()) for criterion in role.criteria])
            for role in data.roles
        ],
    )

# Matching: only items with an approved evidence score; pending ones count as potential points
STATUS_RANK = {"missing": 0, "rejected": 1, "pending": 2, "approved": 3}
STATUS_LABELS = {"approved": "aprovado", "pending": "pendente", "rejected": "rejeitado",
                 "missing": "sem comprovante", "expired": "vencida"}
LEVEL_RANK = {
    EducationLevel.TECHNICAL: 0, EducationLevel.BACHELOR: 1, EducationLevel.SPECIALIZATION: 2,
    EducationLevel.MBA: 2, EducationLevel.MASTER: 3, EducationLevel.DOCTORATE: 4,
}

@dataclass
class ProfileSnapshot:
    professional: Professional
    certificates: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    education: list = field(default_factory=list)
    evidences: dict = field(default_factory=dict)  # (EvidenceTarget, item id) -> [Evidence]

def load_profiles(db: Session) -> dict[int, ProfileSnapshot]:
    profiles = {p.id: ProfileSnapshot(p) for p in db.query(Professional).all()}
    for model, attr in ((Certificate, "certificates"), (Project, "projects"), (Education, "education")):
        for item in db.query(model).all():
            getattr(profiles[item.professional_id], attr).append(item)
    for evidence in db.query(Evidence).all():
        target = next(t for t, (_, fk) in EVIDENCE_TARGETS.items() if getattr(evidence, fk))
        item_id = getattr(evidence, EVIDENCE_TARGETS[target][1])
        profiles[evidence.professional_id].evidences.setdefault((target, item_id), []).append(evidence)
    return profiles

def item_status(profile: ProfileSnapshot, target: EvidenceTarget, item_id: int,
                assume_approved: frozenset = frozenset()) -> tuple[str, Optional[int]]:
    """Best status among the item's evidences; `assume_approved` simulates approving pending ones."""
    best = ("missing", None)
    for evidence in profile.evidences.get((target, item_id), []):
        status = "approved" if evidence.id in assume_approved else evidence.status.value
        if STATUS_RANK[status] > STATUS_RANK[best[0]]:
            best = (status, evidence.id)
    return best

def matches_keywords(text: Optional[str], keywords: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(k.strip())}\b", text or "", re.IGNORECASE) for k in keywords if k.strip())

def years_covered(projects: list, now: datetime) -> float:
    """Years covered by the union of the projects' periods (overlaps count once)."""
    spans = sorted((p.start_date, min(p.end_date or now, now)) for p in projects if p.start_date)
    total, current = timedelta(), None
    for start, end in spans:
        if end <= start:
            continue
        if current and start <= current[1]:
            current = (current[0], max(current[1], end))
        else:
            if current:
                total += current[1] - current[0]
            current = (start, end)
    if current:
        total += current[1] - current[0]
    return total.days / 365.25

def score_criterion(criterion: BidCriterion, profile: ProfileSnapshot, now: datetime,
                    assume_approved: frozenset = frozenset()) -> CriterionScore:
    items = []

    def add(target, item, label, expired=False):
        status, evidence_id = item_status(profile, target, item.id, assume_approved)
        items.append(MatchedItem(type=target, id=item.id, label=label,
                                 status="expired" if expired else status, evidence_id=evidence_id))

    if criterion.type == CriterionType.CERTIFICATION:
        for cert in profile.certificates:
            if matches_keywords(cert.name, criterion.keywords):
                add(EvidenceTarget.CERTIFICATE, cert, cert.name, expired=bool(cert.expiry_date and cert.expiry_date < now))
    elif criterion.type == CriterionType.EDUCATION:
        for edu in profile.education:
            if LEVEL_RANK[edu.level] >= LEVEL_RANK[criterion.min_level] and (
                    not criterion.keywords or matches_keywords(edu.course, criterion.keywords)):
                add(EvidenceTarget.EDUCATION, edu, f"{edu.course} ({edu.level.value})")
    else:
        for project in profile.projects:
            if criterion.type == CriterionType.SECTOR_PROJECTS and criterion.sectors and project.sector not in criterion.sectors:
                continue
            if criterion.type == CriterionType.YEARS_EXPERIENCE and not project.start_date:
                continue
            add(EvidenceTarget.PROJECT, project, f"{project.name} ({project.sector or 'sem setor'})")

    counts = Counter(i.status for i in items)
    approved, pending = counts["approved"], counts["pending"]
    if criterion.type == CriterionType.SECTOR_PROJECTS:
        points = min(approved * criterion.points_per_item, criterion.max_points)
        potential = min((approved + pending) * criterion.points_per_item, criterion.max_points)
        where = f" em {', '.join(criterion.sectors)}" if criterion.sectors else ""
        explanation = f"{approved} projeto(s) comprovado(s){where} × {criterion.points_per_item} pts = {points}/{criterion.max_points}"
        if approved * criterion.points_per_item > criterion.max_points:
            explanation += " (teto)"
    elif criterion.type == CriterionType.YEARS_EXPERIENCE:
        by_id = {p.id: p for p in profile.projects}
        proven = years_covered([by_id[i.id] for i in items if i.status == "approved"], now)
        likely = max(years_covered([by_id[i.id] for i in items if i.status in ("approved", "pending")], now),
                     profile.professional.years_experience or 0)
        points = criterion.max_points if proven >= criterion.min_years else 0
        potential = criterion.max_points if likely >= criterion.min_years else 0
        explanation = (f"{proven:.1f} anos comprovados por projetos aprovados (mínimo {criterion.min_years}); "
                       f"{likely:.1f} contando pendentes e anos declarados")
    else:
        points = criterion.max_points if approved else 0
        potential = criterion.max_points if approved or pending else 0
        if approved:
            explanation = "Comprovado: " + next(i.label for i in items if i.status == "approved")
        elif items:
            explanation = "Sem comprovação aprovada: " + "; ".join(f"{i.label} ({STATUS_LABELS[i.status]})" for i in items)
        else:
            explanation = "Nada compatível no perfil"
    if potential > points:
        explanation += f". +{potential - points} pts potenciais, falta aprovar comprovação"
    if counts["missing"]:
        explanation += f". {counts['missing']} item(ns) sem comprovante"
    return CriterionScore(criterion_id=criterion.id, description=criterion.description, points=points,
                          potential_points=potential, max_points=criterion.max_points,
                          explanation=explanation, items=items)

def score_candidate(role: BidRole, profile: ProfileSnapshot, now: datetime,
                    assume_approved: frozenset = frozenset()) -> CandidateScore:
    criteria = [score_criterion(c, profile, now, assume_approved) for c in role.criteria]
    professional = profile.professional
    return CandidateScore(
        professional_id=professional.id, full_name=professional.full_name, job_title=professional.job_title,
        years_experience=professional.years_experience, points=sum(c.points for c in criteria),
        potential_points=sum(c.potential_points for c in criteria), max_points=role.max_points, criteria=criteria,
    )

def rank_role(role: BidRole, profiles: dict[int, ProfileSnapshot], now: datetime) -> list[CandidateScore]:
    scores = [score_candidate(role, profile, now) for profile in profiles.values()]
    scores.sort(key=lambda c: (c.points, c.potential_points, c.years_experience or 0), reverse=True)
    return scores

def best_team(slots: list[list[CandidateScore]]) -> list[Optional[CandidateScore]]:
    """Exact search for the highest-scoring team where nobody fills two slots.

    Each slot's options must be its top-N candidates, N = number of slots: whoever an optimal
    team uses outside that list can be swapped for an unused top-N candidate scoring at least as much.
    """
    best = {"key": (-1, -1), "team": [None] * len(slots)}
    bounds = [max((c.points for c in options), default=0) for options in slots]

    def search(i, used, team, points, potential):
        if points + sum(bounds[i:]) < best["key"][0]:
            return
        if i == len(slots):
            if (points, potential) > best["key"]:
                best.update(key=(points, potential), team=list(team))
            return
        free = [c for c in slots[i] if c.professional_id not in used]
        # Leaving a slot empty only helps when later slots may need every free candidate
        options = free + [None] if len(free) < len(slots) - i else free
        for candidate in options:
            if candidate:
                used.add(candidate.professional_id)
            team.append(candidate)
            search(i + 1, used, team, points + (candidate.points if candidate else 0),
                   potential + (candidate.potential_points if candidate else 0))
            team.pop()
            if candidate:
                used.discard(candidate.professional_id)

    search(0, set(), [], 0, 0)
    return best["team"]

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

# Routes: bids and matching (managers; responses expose other professionals' profiles)
def get_bid_or_404(db: Session, bid_id: int) -> Bid:
    bid = db.get(Bid, bid_id)
    if not bid:
        raise HTTPException(404, "Bid not found")
    return bid

@app.post("/bids", response_model=BidOut, status_code=201)
def create_bid(data: BidCreate, manager: Professional = Depends(require_manager), db: Session = Depends(get_db)):
    return create_item(db, build_bid(data, manager.id))

@app.get("/bids", response_model=List[BidOut])
def list_bids(_: Professional = Depends(require_manager), db: Session = Depends(get_db)):
    return db.query(Bid).order_by(Bid.created_at.desc()).all()

@app.get("/bids/{bid_id}", response_model=BidOut)
def get_bid(bid_id: int, _: Professional = Depends(require_manager), db: Session = Depends(get_db)):
    return get_bid_or_404(db, bid_id)

@app.delete("/bids/{bid_id}", status_code=204)
def delete_bid(bid_id: int, _: Professional = Depends(require_manager), db: Session = Depends(get_db)):
    db.delete(get_bid_or_404(db, bid_id))
    db.commit()

@app.get("/bids/{bid_id}/ranking", response_model=List[RoleRanking])
def bid_ranking(
    bid_id: int,
    role_id: Optional[int] = None,
    limit: int = Query(default=20, ge=1, le=200),
    _: Professional = Depends(require_manager),
    db: Session = Depends(get_db)
):
    bid = get_bid_or_404(db, bid_id)
    roles = [role for role in bid.roles if role_id is None or role.id == role_id]
    if not roles:
        raise HTTPException(404, "Role not found")
    profiles, now = load_profiles(db), utcnow()
    return [
        RoleRanking(role_id=role.id, role_name=role.name, quantity=role.quantity, max_points=role.max_points,
                    candidates=rank_role(role, profiles, now)[:limit])
        for role in roles
    ]

@app.get("/bids/{bid_id}/team", response_model=TeamOut)
def bid_team(bid_id: int, _: Professional = Depends(require_manager), db: Session = Depends(get_db)):
    bid = get_bid_or_404(db, bid_id)
    profiles, now = load_profiles(db), utcnow()
    slot_roles = [role for role in bid.roles for _ in range(role.quantity)]
    rankings = {role.id: rank_role(role, profiles, now)[:len(slot_roles)] for role in bid.roles}
    team = best_team([rankings[role.id] for role in slot_roles])

    gains = []
    for role, member in zip(slot_roles, team):
        if not member:
            continue
        pending = {(i.evidence_id, i.label) for c in member.criteria for i in c.items if i.status == "pending"}
        for evidence_id, label in pending:
            simulated = score_candidate(role, profiles[member.professional_id], now, frozenset({evidence_id}))
            if simulated.points > member.points:
                gains.append(PendingGain(evidence_id=evidence_id, professional_id=member.professional_id,
                                         full_name=member.full_name, role_name=role.name, item=label,
                                         gain=simulated.points - member.points))
    gains.sort(key=lambda g: g.gain, reverse=True)

    return TeamOut(
        bid_id=bid.id, title=bid.title, max_points=bid.max_points,
        points=sum(m.points for m in team if m), potential_points=sum(m.potential_points for m in team if m),
        slots=[TeamSlot(role_id=role.id, role_name=role.name, member=member) for role, member in zip(slot_roles, team)],
        pending_gains=gains,
    )

@app.get("/health")
def health():
    return {"status": "ok", "service": "null-limite"}
