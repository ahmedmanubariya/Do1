from __future__ import annotations
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from .models import AuditEvent, ControlledDocument, DocumentVersion, TrainingAssignment

SUPPORTED = {".pdf", ".docx", ".pptx", ".txt"}
EASTSTONE = re.compile(r"(?i)^(?P<ref>ES\.[A-Z]+\.\d{3}(?:\.[A-Z]\d{2})?)\.(?P<rev>V\d{2,3})\s*-\s*(?P<title>.+)$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_name(path: Path):
    m = EASTSTONE.match(path.stem.strip())
    if m:
        return m.group("ref").upper(), m.group("rev").upper(), m.group("title").strip()
    return path.stem, str(int(path.stat().st_mtime)), path.stem


def revision_key(value: str):
    return tuple(int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", value or ""))


def sync_approved_documents(db: Session, actor_user_id: int | None = None):
    configured = os.getenv("EQMS_APPROVED_DOCS_ROOT", "")
    if not configured:
        return {"configured": False, "message": "EQMS_APPROVED_DOCS_ROOT is not set"}
    root = Path(configured)
    if not root.exists() or not root.is_dir():
        return {"configured": False, "root": configured, "message": "Approved document folder is not reachable from this server"}

    repository = Path(os.getenv("EQMS_DOCUMENT_REPOSITORY", "./controlled_repository"))
    repository.mkdir(parents=True, exist_ok=True)

    latest = {}
    scanned = 0
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED:
            continue
        scanned += 1
        ref, rev, title = parse_name(p)
        previous = latest.get(ref)
        if not previous or (revision_key(rev), p.stat().st_mtime) > (revision_key(previous[1]), previous[0].stat().st_mtime):
            latest[ref] = (p, rev, title)

    created = updated = unchanged = 0
    for ref, (source, rev, title) in latest.items():
        digest = sha256(source)
        doc = db.query(ControlledDocument).filter(ControlledDocument.reference == ref).first()
        if doc and doc.source_hash == digest and doc.current_revision == rev:
            doc.last_seen_at = datetime.utcnow()
            unchanged += 1
            continue
        if not doc:
            doc = ControlledDocument(reference=ref, title=title, document_type="SOP", current_revision=rev, active=True)
            db.add(doc); db.flush(); created += 1
        else:
            updated += 1
            for v in doc.versions:
                if v.status == "CURRENT":
                    v.status = "SUPERSEDED"
            doc.title = title
            doc.current_revision = rev
            doc.active = True

        target_dir = repository / ref.replace("/", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        shutil.copy2(source, target)
        version = db.query(DocumentVersion).filter_by(document_id=doc.id, revision=rev).first()
        if not version:
            version = DocumentVersion(document_id=doc.id, revision=rev, file_name=source.name, stored_path=str(target), file_hash=digest, status="CURRENT")
            db.add(version)
        else:
            version.file_name = source.name; version.stored_path = str(target); version.file_hash = digest; version.status = "CURRENT"
        doc.source_path = str(source)
        doc.source_hash = digest
        doc.last_seen_at = datetime.utcnow()

        # Any existing assignee for this document must train on the current revision.
        users = {a.user_id for a in db.query(TrainingAssignment).filter(TrainingAssignment.document_id == doc.id).all()}
        for user_id in users:
            existing = db.query(TrainingAssignment).filter_by(user_id=user_id, document_id=doc.id, revision=rev).first()
            if not existing:
                db.add(TrainingAssignment(user_id=user_id, document_id=doc.id, revision=rev, status="OUTSTANDING"))

        db.add(AuditEvent(actor_user_id=actor_user_id, action="DOCUMENT_SYNC", entity_type="controlled_document", entity_id=str(doc.id), details=f"{ref} {rev} from {source}"))

    db.commit()
    return {"configured": True, "root": configured, "scanned": scanned, "current_documents": len(latest), "created": created, "updated": updated, "unchanged": unchanged}
