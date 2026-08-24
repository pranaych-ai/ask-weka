"""Knowledge base & sources management APIs (admin-only, centrally audited)."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .admin import admin_audited
from .audit import log_event
from .db import get_db
from .models import KBSection, KBSectionVersion, KnowledgeSource

router = APIRouter(prefix="/api/admin/kb", dependencies=[Depends(admin_audited)])

MAX_BODY = 500_000  # per-section char cap
SOURCE_TYPES = ("upload", "notion", "gdrive", "manual")


def _section_out(s: KBSection, with_body: bool = False) -> dict:
    out = {
        "id": s.id,
        "title": s.title,
        "domain": s.domain,
        "position": s.position,
        "active": s.active,
        "version": s.version,
        "source_id": s.source_id,
        "updated_by": s.updated_by,
        "updated_at": s.updated_at.isoformat(),
        "body_chars": len(s.body or ""),
    }
    if with_body:
        out["body"] = s.body
    return out


def _snapshot(db: Session, s: KBSection, edited_by: str) -> None:
    """Store the section's current state as a version row before changing it."""
    db.add(
        KBSectionVersion(
            section_id=s.id,
            version=s.version,
            title=s.title,
            domain=s.domain,
            body=s.body,
            active=s.active,
            position=s.position,
            edited_by=edited_by,
        )
    )


def _bump_version_guarded(db: Session, s: KBSection, user: str) -> None:
    """Snapshot current state and bump version with optimistic concurrency.

    The conditional UPDATE guarantees that if two admins race on the same
    section, exactly one wins and the loser gets a 409 — no history is lost.
    The unique (section_id, version) constraint is a second line of defense.
    """
    _snapshot(db, s, user)
    old = s.version
    try:
        updated = (
            db.query(KBSection)
            .filter(KBSection.id == s.id, KBSection.version == old)
            .update({KBSection.version: old + 1}, synchronize_session=False)
        )
        if updated != 1:
            raise IntegrityError("stale version", None, Exception("conflict"))
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Section was modified by someone else — reload and retry"
        )
    s.version = old + 1


def _validate_domain(domain: str) -> None:
    if domain not in ("", "IT", "HR"):
        raise HTTPException(400, "domain must be IT, HR, or empty")


# ---------- Sections ----------


@router.get("/sections")
def sections_list(db: Session = Depends(get_db)):
    rows = (
        db.query(KBSection)
        .order_by(KBSection.position.asc(), KBSection.created_at.asc())
        .all()
    )
    return [_section_out(s) for s in rows]


@router.get("/sections/{section_id}")
def section_get(section_id: str, db: Session = Depends(get_db)):
    s = db.get(KBSection, section_id)
    if not s:
        raise HTTPException(404, "Section not found")
    return _section_out(s, with_body=True)


class SectionIn(BaseModel):
    title: str
    domain: str = ""
    body: str = ""
    active: bool = True


@router.post("/sections")
def section_create(
    req: SectionIn, db: Session = Depends(get_db), user: dict = Depends(admin_audited)
):
    if not req.title.strip():
        raise HTTPException(400, "Title required")
    _validate_domain(req.domain)
    if len(req.body) > MAX_BODY:
        raise HTTPException(400, "Body too large")
    max_pos = db.query(func.max(KBSection.position)).scalar() or 0
    s = KBSection(
        title=req.title.strip(),
        domain=req.domain,
        body=req.body,
        active=req.active,
        position=max_pos + 1,
        updated_by=user["username"],
    )
    db.add(s)
    db.flush()
    log_event(db, user["username"], "kb.section.create", f"id={s.id}")
    db.commit()
    return _section_out(s, with_body=True)


class SectionPatch(BaseModel):
    title: Optional[str] = None
    domain: Optional[str] = None
    body: Optional[str] = None
    active: Optional[bool] = None
    position: Optional[int] = None


@router.patch("/sections/{section_id}")
def section_update(
    section_id: str,
    req: SectionPatch,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    s = db.get(KBSection, section_id)
    if not s:
        raise HTTPException(404, "Section not found")
    # EVERY persisted mutation is versioned so it can be reverted.
    any_change = any(
        v is not None for v in (req.title, req.domain, req.body, req.active, req.position)
    )
    if req.title is not None and not req.title.strip():
        raise HTTPException(400, "Title required")
    if req.domain is not None:
        _validate_domain(req.domain)
    if req.body is not None and len(req.body) > MAX_BODY:
        raise HTTPException(400, "Body too large")

    if any_change:
        _bump_version_guarded(db, s, user["username"])
    if req.title is not None:
        s.title = req.title.strip()
    if req.domain is not None:
        s.domain = req.domain
    if req.body is not None:
        s.body = req.body
    if req.active is not None:
        s.active = req.active
    if req.position is not None:
        s.position = req.position
    s.updated_by = user["username"]
    s.updated_at = datetime.now(timezone.utc)
    log_event(
        db, user["username"], "kb.section.update",
        f"id={s.id} version={s.version} active={s.active}",
    )
    db.commit()
    return _section_out(s, with_body=True)


@router.get("/sections/{section_id}/versions")
def section_versions(section_id: str, db: Session = Depends(get_db)):
    if not db.get(KBSection, section_id):
        raise HTTPException(404, "Section not found")
    rows = (
        db.query(KBSectionVersion)
        .filter(KBSectionVersion.section_id == section_id)
        .order_by(KBSectionVersion.version.desc())
        .all()
    )
    return [
        {
            "id": v.id,
            "version": v.version,
            "title": v.title,
            "domain": v.domain,
            "active": v.active,
            "position": v.position,
            "edited_by": v.edited_by,
            "created_at": v.created_at.isoformat(),
            "body_chars": len(v.body or ""),
        }
        for v in rows
    ]


class RevertIn(BaseModel):
    version: int


@router.post("/sections/{section_id}/revert")
def section_revert(
    section_id: str,
    req: RevertIn,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    s = db.get(KBSection, section_id)
    if not s:
        raise HTTPException(404, "Section not found")
    v = (
        db.query(KBSectionVersion)
        .filter(
            KBSectionVersion.section_id == section_id,
            KBSectionVersion.version == req.version,
        )
        .first()
    )
    if not v:
        raise HTTPException(404, "Version not found")
    _bump_version_guarded(db, s, user["username"])
    s.title, s.domain, s.body, s.active, s.position = (
        v.title, v.domain, v.body, v.active, v.position,
    )
    s.updated_by = user["username"]
    s.updated_at = datetime.now(timezone.utc)
    log_event(
        db, user["username"], "kb.section.revert",
        f"id={s.id} to_version={req.version} new_version={s.version}",
    )
    db.commit()
    return _section_out(s, with_body=True)


# ---------- Sources ----------


def _source_out(src: KnowledgeSource) -> dict:
    return {
        "id": src.id,
        "name": src.name,
        "type": src.type,
        "url": src.url,
        "notes": src.notes,
        "domain": src.domain,
        "last_sync_at": src.last_sync_at.isoformat() if src.last_sync_at else None,
        "last_sync_status": src.last_sync_status,
        "last_sync_detail": src.last_sync_detail,
        "created_by": src.created_by,
    }


class SourceIn(BaseModel):
    name: str
    type: str = "upload"
    url: str = ""
    notes: str = ""
    domain: str = ""


@router.get("/sources")
def sources_list(db: Session = Depends(get_db)):
    rows = db.query(KnowledgeSource).order_by(KnowledgeSource.created_at.asc()).all()
    return [_source_out(s) for s in rows]


@router.post("/sources")
def source_create(
    req: SourceIn, db: Session = Depends(get_db), user: dict = Depends(admin_audited)
):
    if not req.name.strip():
        raise HTTPException(400, "Name required")
    if req.type not in SOURCE_TYPES:
        raise HTTPException(400, f"type must be one of {SOURCE_TYPES}")
    _validate_domain(req.domain)
    src = KnowledgeSource(
        name=req.name.strip(),
        type=req.type,
        url=req.url.strip(),
        notes=req.notes.strip(),
        domain=req.domain,
        created_by=user["username"],
    )
    db.add(src)
    db.flush()
    log_event(db, user["username"], "kb.source.create", f"id={src.id} type={src.type}")
    db.commit()
    return _source_out(src)


class SourcePatch(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None
    domain: Optional[str] = None


@router.patch("/sources/{source_id}")
def source_update(
    source_id: str,
    req: SourcePatch,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    src = db.get(KnowledgeSource, source_id)
    if not src:
        raise HTTPException(404, "Source not found")
    if req.name is not None:
        if not req.name.strip():
            raise HTTPException(400, "Name required")
        src.name = req.name.strip()
    if req.url is not None:
        src.url = req.url.strip()
    if req.notes is not None:
        src.notes = req.notes.strip()
    if req.domain is not None:
        _validate_domain(req.domain)
        src.domain = req.domain
    log_event(db, user["username"], "kb.source.update", f"id={src.id}")
    db.commit()
    return _source_out(src)


@router.delete("/sources/{source_id}")
def source_delete(
    source_id: str, db: Session = Depends(get_db), user: dict = Depends(admin_audited)
):
    src = db.get(KnowledgeSource, source_id)
    if not src:
        raise HTTPException(404, "Source not found")
    db.delete(src)
    log_event(db, user["username"], "kb.source.delete", f"id={source_id}")
    db.commit()
    return {"ok": True}


@router.post("/sources/{source_id}/sync")
async def source_sync(
    source_id: str,
    file: Optional[UploadFile] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    """Sync a source into the KB. Upload sources take a markdown/text file that
    becomes (or updates) a KB section tied to this source. Connector types are
    manual placeholders until IT-approved service accounts exist."""
    src = db.get(KnowledgeSource, source_id)
    if not src:
        raise HTTPException(404, "Source not found")
    if src.type != "upload":
        src.last_sync_status = "error"
        src.last_sync_detail = (
            "Automatic sync is not available for this source type yet — "
            "connector-based sync requires an IT-approved service account. "
            "Paste content into a KB section manually for now."
        )
        db.commit()
        raise HTTPException(400, src.last_sync_detail)
    if file is None:
        raise HTTPException(400, "Attach a markdown or text file to sync")
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, "File too large (max 2 MB)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 text/markdown")
    if not text.strip():
        raise HTTPException(400, "File is empty")

    # One section per source: update it (with a version snapshot) if it exists.
    s = db.query(KBSection).filter(KBSection.source_id == src.id).first()
    if s:
        _bump_version_guarded(db, s, user["username"])
        s.body = text
        s.updated_by = user["username"]
        s.updated_at = datetime.now(timezone.utc)
    else:
        max_pos = db.query(func.max(KBSection.position)).scalar() or 0
        s = KBSection(
            title=src.name,
            domain=src.domain,
            body=text,
            position=max_pos + 1,
            source_id=src.id,
            updated_by=user["username"],
        )
        db.add(s)
        db.flush()
    src.last_sync_at = datetime.now(timezone.utc)
    src.last_sync_status = "ok"
    src.last_sync_detail = f"Synced {len(text)} chars into section"
    log_event(
        db, user["username"], "kb.source.sync",
        f"source_id={src.id} section_id={s.id} chars={len(text)}",
    )
    db.commit()
    return {"ok": True, "section": _section_out(s)}
