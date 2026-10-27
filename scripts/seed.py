"""Recreate the database with demo data from data/profiles/*.json.

- One professional per anonymized profile (email nome.sobrenome@nexora.demo, password demo123)
- Two managers: gestor1@nexora.demo / gestor2@nexora.demo (password demo123)
- Dates are shifted: the source resumes stop around 2016-2017, so ~70% of profiles are
  moved to look recently updated and ~30% to look 2-4 years stale (fictitious data,
  this only makes the freshness/gamification demo realistic)
- Fictitious evidence PDFs with a mix of approved / pending / missing, dated at the profile's
  last update; some education without a file is waiting for a manager to validate it
- Team (practice) derived from the job title
- Example bids from data/bids/*.json
- Scripted demo employee funcionario@nexora.demo (password demo123): proving the missing PMP
  and MBA puts them in the recommended team of the example bid

Usage: python scripts/seed.py [--profiles data/profiles] [--bids data/bids]
WARNING: deletes every row in the configured database.
"""
import argparse
import json
import random
import re
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402

PASSWORD = "demo123"
FRESH_SHARE = 0.7
# Validity in years for certifications that must be renewed
# First match wins; everyone else goes to business analysis
TEAM_KEYWORDS = [
    (main.Team.PROJECT_MANAGEMENT, ("gerente", "scrum", "programa")),
    (main.Team.ENGINEERING, ("desenvolvedor", "engenheiro", "developer", "qa")),
]
CERT_VALIDITY = {"pmp": 3, "pmi-acp": 3, "capm": 5, "csm": 2, "cspo": 2, "safe": 1, "aws": 3, "azure": 1, "cbap": 3}


def months_between(earlier: datetime, later: datetime) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def add_months(date: datetime, months: int) -> datetime:
    index = date.year * 12 + date.month - 1 + months
    return date.replace(year=index // 12, month=index % 12 + 1, day=1)


def parse_month(value) -> datetime | None:
    if not value:
        return None
    try:
        year, month = (str(value).split("-") + ["1"])[:2]
        return datetime(int(year), max(1, min(12, int(month))), 1)
    except ValueError:
        return None


def from_year(year) -> datetime | None:
    return datetime(int(year), 7, 1) if year and 1960 < int(year) < 2100 else None


def latest_date(profile) -> datetime | None:
    dates = [parse_month(p.get("start")) for p in profile["projects"]]
    dates += [parse_month(p.get("end")) for p in profile["projects"]]
    dates += [from_year(c.get("issue_year")) for c in profile["certifications"]]
    dates += [from_year(e.get("completion_year")) for e in profile["education"]]
    return max((d for d in dates if d), default=None)


def team_for(job_title: str | None) -> main.Team:
    title = (job_title or "").lower()
    return next((team for team, words in TEAM_KEYWORDS if any(w in title for w in words)), main.Team.BUSINESS_ANALYSIS)


def email_for(full_name: str, taken: set) -> str:
    ascii_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode().lower()
    base = ".".join(ascii_name.split())
    email, n = f"{base}@nexora.demo", 2
    while email in taken:
        email, n = f"{base}{n}@nexora.demo", n + 1
    taken.add(email)
    return email


def fake_pdf(text: str) -> bytes:
    """Single-page PDF with one line of text, enough for a demo evidence file."""
    safe = text.encode("latin-1", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    stream = b"BT /F1 14 Tf 50 750 Td (" + safe + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def add_evidence(db, rng, upload_dir: Path, owner, reviewer, label: str, approve_share: float, pending_share: float,
                 created_at: datetime, **target):
    roll = rng.random()
    if roll >= approve_share + pending_share:
        return False  # no evidence uploaded
    approved = roll < approve_share
    path = upload_dir / f"{uuid.uuid4()}.pdf"
    content = fake_pdf(f"Comprovante ficticio - {label} - {owner.full_name}")
    path.write_bytes(content)
    db.add(main.Evidence(
        professional_id=owner.id,
        filename=f"comprovante-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')[:40]}.pdf",
        file_path=str(path),
        file_size=len(content),
        status=main.EvidenceStatus.APPROVED if approved else main.EvidenceStatus.PENDING,
        reviewed_by=reviewer.id if approved else None,
        reviewed_at=main.utcnow() if approved else None,
        created_at=created_at,
        **target,
    ))
    return True


def add_demo_employee(db, upload_dir: Path, now: datetime, password_hash: str, reviewer):
    """Fictitious employee for the live demo: strong in energy, but PMP and MBA have no evidence yet."""
    last_update = add_months(now, -20)
    employee = main.Professional(
        email="funcionario@nexora.demo", full_name="Lucas Martins", job_title="Gerente de Projetos",
        years_experience=12, team=main.Team.PROJECT_MANAGEMENT, hashed_password=password_hash, created_at=last_update,
    )
    db.add(employee)
    db.flush()
    items = [
        (main.Certificate(name="PMP", issuer="PMI", issue_date=add_months(now, -12), expiry_date=add_months(now, 24)), False),
        (main.Education(level=main.EducationLevel.MBA, course="MBA em Gestão de Projetos"), False),
        (main.Project(name="Medição inteligente", client="Distribuidora (fictícia)", sector="energia", role="Gerente de Projetos",
                      start_date=add_months(now, -150), end_date=add_months(now, -80)), True),
        (main.Project(name="Novo sistema de faturamento", client="Distribuidora (fictícia)", sector="energia",
                      role="Gerente de Projetos", start_date=add_months(now, -79), end_date=add_months(now, -24)), True),
        (main.Project(name="Portal do cliente", client="Distribuidora (fictícia)", sector="energia", role="Gerente de Projetos",
                      start_date=add_months(now, -23), end_date=add_months(now, -20)), False),
    ]
    for item, approved in items:
        item.professional_id, item.created_at = employee.id, last_update
        db.add(item)
        db.flush()
        if approved:
            path = upload_dir / f"{uuid.uuid4()}.pdf"
            content = fake_pdf(f"Comprovante ficticio - {item.name} - {employee.full_name}")
            path.write_bytes(content)
            db.add(main.Evidence(professional_id=employee.id, project_id=item.id, filename="atestado.pdf", file_path=str(path),
                                 file_size=len(content), status=main.EvidenceStatus.APPROVED, reviewed_by=reviewer.id,
                                 reviewed_at=main.utcnow(), created_at=last_update))


def main_seed():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=Path("data/profiles"))
    parser.add_argument("--bids", type=Path, default=Path("data/bids"))
    args = parser.parse_args()

    profiles = [json.loads(p.read_text(encoding="utf8")) for p in sorted(args.profiles.glob("p*.json"))]
    if not profiles:
        sys.exit(f"No profiles in {args.profiles}/ (run scripts/ingest/structure.py first)")

    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    upload_dir = Path(main.UPLOAD_DIR) / "seed"
    upload_dir.mkdir(parents=True, exist_ok=True)
    for old in upload_dir.glob("*.pdf"):
        old.unlink()

    rng = random.Random(7)
    # Separate generator so the no-file requests don't change the rest of the demo data
    no_file_rng = random.Random(11)
    now = main.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    password_hash = main.get_password_hash(PASSWORD)
    db = main.SessionLocal()
    taken = set()

    managers = []
    for n in (1, 2):
        manager = main.Professional(email=f"gestor{n}@nexora.demo", full_name=f"Gestor {n}", job_title="Gerente de Propostas",
                                    hashed_password=password_hash, role=main.UserRole.MANAGER)
        taken.add(manager.email)
        db.add(manager)
        managers.append(manager)
    db.commit()

    fresh = 0
    for profile in profiles:
        latest = latest_date(profile)
        if rng.random() < FRESH_SHARE:
            target = add_months(now, -rng.randint(0, 15))
            fresh += 1
        else:
            target = add_months(now, -rng.randint(24, 48))
        shift = months_between(latest, target) if latest else 0

        def shifted(date):
            return add_months(date, shift) if date else None

        last_update = target if latest else now
        professional = main.Professional(
            email=email_for(profile["full_name"], taken),
            full_name=profile["full_name"],
            job_title=profile.get("job_title"),
            years_experience=profile.get("years_experience"),
            team=team_for(profile.get("job_title")),
            hashed_password=password_hash,
            created_at=last_update,
        )
        db.add(professional)
        db.flush()
        reviewer = rng.choice(managers)

        for cert in profile["certifications"]:
            issue = shifted(from_year(cert.get("issue_year")))
            validity = next((years for key, years in CERT_VALIDITY.items() if key in cert["name"].lower()), None)
            item = main.Certificate(
                professional_id=professional.id, name=cert["name"], issuer=cert.get("issuer"), issue_date=issue,
                expiry_date=add_months(issue, 12 * validity) if issue and validity else None, created_at=last_update,
            )
            db.add(item)
            db.flush()
            add_evidence(db, rng, upload_dir, professional, reviewer, cert["name"], 0.5, 0.2, last_update, certificate_id=item.id)

        for edu in profile["education"]:
            item = main.Education(
                professional_id=professional.id, level=main.EducationLevel(edu["level"]), course=edu["course"],
                institution=edu.get("institution"), completion_date=shifted(from_year(edu.get("completion_year"))),
                created_at=last_update,
            )
            db.add(item)
            db.flush()
            uploaded = add_evidence(db, rng, upload_dir, professional, reviewer, edu["course"], 0.5, 0.2, last_update,
                                    education_id=item.id)
            if not uploaded and no_file_rng.random() < 0.4:
                db.add(main.Evidence(professional_id=professional.id, education_id=item.id, created_at=last_update))

        for proj in profile["projects"]:
            item = main.Project(
                professional_id=professional.id, name=proj["name"], client=proj.get("client"), sector=proj.get("sector"),
                role=proj.get("role"), description=proj.get("description"),
                start_date=shifted(parse_month(proj.get("start"))), end_date=shifted(parse_month(proj.get("end"))),
                created_at=last_update,
            )
            db.add(item)
            db.flush()
            add_evidence(db, rng, upload_dir, professional, reviewer, proj["name"], 0.3, 0.15, last_update, project_id=item.id)

    add_demo_employee(db, upload_dir, now, password_hash, managers[0])
    for path in sorted(args.bids.glob("*.json")):
        db.add(main.build_bid(main.BidCreate.model_validate_json(path.read_text(encoding="utf8")), managers[0].id))
    db.commit()

    counts = {model.__tablename__: db.query(model).count()
              for model in (main.Professional, main.Certificate, main.Education, main.Project, main.Evidence, main.Bid)}
    print(f"Seeded {len(profiles)} profiles ({fresh} recently updated, {len(profiles) - fresh} stale): {counts}")
    print(f"Logins: funcionario@nexora.demo, gestor1@nexora.demo, gestor2@nexora.demo, <nome.sobrenome>@nexora.demo / password {PASSWORD}")
    db.close()


if __name__ == "__main__":
    main_seed()
