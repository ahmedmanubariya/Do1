from __future__ import annotations
import os
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine, get_db
from .document_sync import sync_approved_documents
from .models import AuditEvent, ControlledDocument, DocumentVersion, QualityRecord, Role, TrainingAcknowledgement, TrainingAssignment, User
from .security import create_token, current_user, hash_password, require_roles, verify_password

app = FastAPI(title="Eaststone eQMS API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("EQMS_FRONTEND_ORIGIN", "http://localhost:5173")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def audit(db: Session, actor: User | None, action: str, entity_type: str, entity_id=None, details=None):
    db.add(AuditEvent(actor_user_id=actor.id if actor else None, action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, details=details))


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if not db.query(User).count():
            admin = User(username=os.getenv("EQMS_BOOTSTRAP_ADMIN", "admin"), full_name="System Administrator", email=os.getenv("EQMS_BOOTSTRAP_EMAIL", "admin@eaststone.local"), password_hash=hash_password(os.getenv("EQMS_BOOTSTRAP_PASSWORD", "ChangeMeNow!2026")), role=Role.ADMIN.value, active=True)
            db.add(admin); db.commit()
    finally:
        db.close()


class UserOut(BaseModel):
    id: int; username: str; full_name: str; email: str; role: str; department: str | None = None; job_role: str | None = None; active: bool
    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str; full_name: str; email: EmailStr; password: str; role: str = "user"; department: str | None = None; job_role: str | None = None


class RecordCreate(BaseModel):
    title: str; description: str; severity: str | None = None; department: str | None = None; due_date: date | None = None


class RecordUpdate(BaseModel):
    title: str | None = None; description: str | None = None; severity: str | None = None; department: str | None = None; due_date: date | None = None; status: str | None = None; root_cause: str | None = None; investigation: str | None = None; actions: str | None = None; effectiveness_check: str | None = None; owner_user_id: int | None = None


class AssignmentCreate(BaseModel):
    user_id: int; document_id: int; due_date: date | None = None


class Acknowledge(BaseModel):
    signed_name: str; password: str


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "eaststone-eqms"}


@app.post("/api/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username, User.active.is_(True)).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    audit(db, user, "LOGIN", "user", user.id); db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "user": UserOut.model_validate(user)}


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(TrainingAssignment).filter(TrainingAssignment.user_id == user.id)
    assignments = q.all()
    today = date.today()
    completed = sum(a.status == "COMPLETED" for a in assignments)
    overdue = sum(a.status != "COMPLETED" and a.due_date and a.due_date < today for a in assignments)
    outstanding = max(0, len(assignments) - completed - overdue)
    percentage = round((completed / len(assignments) * 100), 1) if assignments else 100.0
    module_counts = {kind: db.query(QualityRecord).filter(QualityRecord.record_type == kind, QualityRecord.status != "CLOSED").count() for kind in ["complaint", "deviation", "capa", "oos", "risk"]}
    return {"training": {"total": len(assignments), "completed": completed, "outstanding": outstanding, "overdue": overdue, "percentage": percentage, "threshold": 80}, "qms": module_counts}


VALID_RECORD_TYPES = {"complaint": "CMP", "deviation": "DEV", "capa": "CAPA", "oos": "OOS", "risk": "RA"}


def next_reference(db: Session, kind: str) -> str:
    prefix = VALID_RECORD_TYPES[kind]
    year = datetime.utcnow().year
    count = db.query(QualityRecord).filter(QualityRecord.record_type == kind).count() + 1
    return f"{prefix}.{year}.{count:04d}"


@app.get("/api/qms/{kind}")
def list_records(kind: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if kind not in VALID_RECORD_TYPES: raise HTTPException(404)
    rows = db.query(QualityRecord).filter(QualityRecord.record_type == kind).order_by(QualityRecord.created_at.desc()).all()
    return [{"id": r.id, "reference": r.reference, "title": r.title, "status": r.status, "severity": r.severity, "department": r.department, "due_date": r.due_date, "created_at": r.created_at} for r in rows]


@app.post("/api/qms/{kind}")
def create_record(kind: str, payload: RecordCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if kind not in VALID_RECORD_TYPES: raise HTTPException(404)
    record = QualityRecord(record_type=kind, reference=next_reference(db, kind), title=payload.title, description=payload.description, severity=payload.severity, department=payload.department or user.department, due_date=payload.due_date, created_by=user.id, owner_user_id=user.id)
    db.add(record); db.flush(); audit(db, user, "CREATE_QMS_RECORD", kind, record.id, record.reference); db.commit(); db.refresh(record)
    return {"id": record.id, "reference": record.reference}


@app.get("/api/qms/{kind}/{record_id}")
def get_record(kind: str, record_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(QualityRecord, record_id)
    if not row or row.record_type != kind: raise HTTPException(404)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


@app.patch("/api/qms/{kind}/{record_id}")
def update_record(kind: str, record_id: int, payload: RecordUpdate, user: User = Depends(require_roles("manager", "qa", "admin")), db: Session = Depends(get_db)):
    row = db.get(QualityRecord, record_id)
    if not row or row.record_type != kind: raise HTTPException(404)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items(): setattr(row, key, value)
    audit(db, user, "UPDATE_QMS_RECORD", kind, row.id, str(changes)); db.commit()
    return {"ok": True}


@app.get("/api/documents")
def documents(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(ControlledDocument).filter(ControlledDocument.active.is_(True))
    if q:
        like = f"%{q}%"; query = query.filter((ControlledDocument.reference.ilike(like)) | (ControlledDocument.title.ilike(like)))
    rows = query.order_by(ControlledDocument.reference).all()
    return [{"id": d.id, "reference": d.reference, "title": d.title, "type": d.document_type, "department": d.department, "revision": d.current_revision, "last_seen_at": d.last_seen_at} for d in rows]


@app.get("/api/documents/{document_id}")
def document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(ControlledDocument, document_id)
    if not d: raise HTTPException(404)
    versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == d.id).order_by(DocumentVersion.created_at.desc()).all()
    return {"document": {"id": d.id, "reference": d.reference, "title": d.title, "revision": d.current_revision, "department": d.department}, "versions": [{"id": v.id, "revision": v.revision, "file_name": v.file_name, "status": v.status, "created_at": v.created_at} for v in versions]}


@app.get("/api/documents/{document_id}/open")
def open_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(ControlledDocument, document_id)
    if not d: raise HTTPException(404)
    v = db.query(DocumentVersion).filter_by(document_id=d.id, revision=d.current_revision).first()
    if not v or not Path(v.stored_path).exists(): raise HTTPException(404, "Controlled file is not available on this server")
    audit(db, user, "OPEN_CONTROLLED_DOCUMENT", "controlled_document", d.id, f"revision={v.revision}"); db.commit()
    return FileResponse(v.stored_path, filename=v.file_name)


@app.post("/api/documents/sync")
def sync_documents(user: User = Depends(require_roles("qa", "admin")), db: Session = Depends(get_db)):
    result = sync_approved_documents(db, user.id)
    return result


@app.get("/api/training/me")
def my_training(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(TrainingAssignment, ControlledDocument).join(ControlledDocument, ControlledDocument.id == TrainingAssignment.document_id).filter(TrainingAssignment.user_id == user.id).order_by(TrainingAssignment.assigned_at.desc()).all()
    return [{"id": a.id, "document_id": d.id, "reference": d.reference, "title": d.title, "revision": a.revision, "status": a.status, "due_date": a.due_date, "assigned_at": a.assigned_at} for a, d in rows]


@app.post("/api/training/assign")
def assign_training(payload: AssignmentCreate, user: User = Depends(require_roles("manager", "qa", "admin")), db: Session = Depends(get_db)):
    target = db.get(User, payload.user_id); doc = db.get(ControlledDocument, payload.document_id)
    if not target or not doc or not doc.current_revision: raise HTTPException(404)
    existing = db.query(TrainingAssignment).filter_by(user_id=target.id, document_id=doc.id, revision=doc.current_revision).first()
    if existing: return {"id": existing.id, "existing": True}
    a = TrainingAssignment(user_id=target.id, document_id=doc.id, revision=doc.current_revision, due_date=payload.due_date, status="OUTSTANDING")
    db.add(a); db.flush(); audit(db, user, "ASSIGN_TRAINING", "training_assignment", a.id, f"user={target.username}; doc={doc.reference}; rev={doc.current_revision}"); db.commit()
    return {"id": a.id, "existing": False}


@app.post("/api/training/{assignment_id}/acknowledge")
def acknowledge(assignment_id: int, payload: Acknowledge, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(TrainingAssignment, assignment_id)
    if not a or a.user_id != user.id: raise HTTPException(404)
    if a.status == "COMPLETED": return {"ok": True, "already_completed": True}
    if payload.signed_name.strip().casefold() != user.full_name.strip().casefold(): raise HTTPException(400, "Signature must match your account name")
    if not verify_password(payload.password, user.password_hash): raise HTTPException(400, "Password confirmation failed")
    ack = TrainingAcknowledgement(assignment_id=a.id, user_id=user.id, document_id=a.document_id, revision=a.revision, signed_name=payload.signed_name.strip(), statement="I confirm that I have read and understood this controlled document.", ip_address=request.client.host if request.client else None)
    db.add(ack); a.status = "COMPLETED"; audit(db, user, "READ_AND_UNDERSTOOD", "training_assignment", a.id, f"revision={a.revision}"); db.commit()
    return {"ok": True, "signed_at": ack.signed_at}


@app.get("/api/users", response_model=list[UserOut])
def users(user: User = Depends(require_roles("manager", "qa", "admin")), db: Session = Depends(get_db)):
    query = db.query(User)
    if user.role == "manager": query = query.filter(User.department == user.department)
    return query.order_by(User.full_name).all()


@app.post("/api/users", response_model=UserOut)
def create_user(payload: UserCreate, user: User = Depends(require_roles("qa", "admin")), db: Session = Depends(get_db)):
    if payload.role not in {r.value for r in Role}: raise HTTPException(400, "Invalid role")
    if db.query(User).filter((User.username == payload.username) | (User.email == payload.email)).first(): raise HTTPException(409, "Username or email already exists")
    row = User(username=payload.username, full_name=payload.full_name, email=payload.email, password_hash=hash_password(payload.password), role=payload.role, department=payload.department, job_role=payload.job_role, active=True)
    db.add(row); db.flush(); audit(db, user, "CREATE_USER", "user", row.id, f"role={row.role}"); db.commit(); db.refresh(row)
    return row


@app.patch("/api/users/{user_id}/role")
def change_role(user_id: int, role: str, user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    if role not in {r.value for r in Role}: raise HTTPException(400)
    target = db.get(User, user_id)
    if not target: raise HTTPException(404)
    old = target.role; target.role = role; audit(db, user, "CHANGE_ROLE", "user", target.id, f"{old}->{role}"); db.commit(); return {"ok": True}


@app.get("/api/audit")
def audit_log(user: User = Depends(require_roles("qa", "admin")), db: Session = Depends(get_db)):
    rows = db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(1000).all()
    return [{"id": e.id, "created_at": e.created_at, "actor_user_id": e.actor_user_id, "action": e.action, "entity_type": e.entity_type, "entity_id": e.entity_id, "details": e.details} for e in rows]
