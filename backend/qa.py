"""QA dashboard & golden-question APIs (admin-only, centrally audited)."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .admin import admin_audited
from .ai_safety import SAFE_REFUSAL, screen_answer, screen_question
from .analysis import classify_domain, extract_sources
from .audit import log_event
from .db import SessionLocal, get_db
from .judge import judge_answer
from .models import Feedback, GoldenQuestion, GoldenResult, GoldenRun
from .prompts import build_system_prompt
from .providers import get_provider

router = APIRouter(prefix="/api/admin/qa", dependencies=[Depends(admin_audited)])

REVIEW_STATUSES = ("open", "reviewed", "resolved")


# ---------- Feedback stats & review ----------


@router.get("/stats")
def qa_stats(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(Feedback)
        .filter(Feedback.logged_time >= since)
        .order_by(Feedback.logged_time.asc())
        .all()
    )
    by_day: dict[str, dict] = {}
    by_domain: dict[str, dict] = {}
    for f in rows:
        day = f.logged_time.strftime("%Y-%m-%d")
        d = by_day.setdefault(day, {"date": day, "up": 0, "down": 0})
        dom = by_domain.setdefault(f.domain or "?", {"domain": f.domain or "?", "up": 0, "down": 0, "total": 0})
        dom["total"] += 1
        if f.thumbs in ("up", "down"):
            d[f.thumbs] += 1
            dom[f.thumbs] += 1
    recent_negative = (
        db.query(Feedback)
        .filter((Feedback.thumbs == "down") | (Feedback.feedback_text != ""))
        .order_by(Feedback.logged_time.desc())
        .limit(10)
        .all()
    )
    open_count = db.query(func.count(Feedback.id)).filter(Feedback.review_status == "open").scalar() or 0
    return {
        "days": days,
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
        "by_domain": sorted(by_domain.values(), key=lambda x: x["domain"]),
        "open_reviews": open_count,
        "recent_negative": [_fb_out(f) for f in recent_negative],
    }


def _fb_out(f: Feedback) -> dict:
    return {
        "id": f.id,
        "question": f.question,
        "answer_summary": f.answer_summary,
        "feedback_text": f.feedback_text,
        "thumbs": f.thumbs,
        # No username here: feedback is anonymous (spec) — only a hash is
        # stored and even that is never sent to the admin UI.
        "domain": f.domain,
        "sources": [s for s in (f.cited_sources or "").split("\n") if s],
        "review_status": f.review_status,
        "logged_time": f.logged_time.isoformat(),
    }


@router.get("/feedback")
def qa_feedback(
    thumbs: Optional[str] = None,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Feedback)
    if thumbs in ("up", "down", "none"):
        q = q.filter(Feedback.thumbs == ("" if thumbs == "none" else thumbs))
    if domain:
        q = q.filter(Feedback.domain == domain)
    if status:
        if status not in REVIEW_STATUSES:
            raise HTTPException(400, "Invalid status")
        q = q.filter(Feedback.review_status == status)
    for name, val, op in (("from", from_, "ge"), ("to", to, "lt")):
        if val:
            try:
                dt = datetime.fromisoformat(val)
            except ValueError:
                raise HTTPException(400, f"Invalid '{name}' date")
            if op == "ge":
                q = q.filter(Feedback.logged_time >= dt)
            else:
                # Date-only upper bounds are inclusive of the whole selected day.
                if len(val) == 10:
                    dt = dt + timedelta(days=1)
                q = q.filter(Feedback.logged_time < dt)
    total = q.count()
    rows = (
        q.order_by(Feedback.logged_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size, "items": [_fb_out(f) for f in rows]}


class ReviewUpdate(BaseModel):
    review_status: str


@router.patch("/feedback/{feedback_id}")
def qa_feedback_review(
    feedback_id: str,
    req: ReviewUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    if req.review_status not in REVIEW_STATUSES:
        raise HTTPException(400, "Invalid status")
    fb = db.get(Feedback, feedback_id)
    if not fb:
        raise HTTPException(404, "Feedback not found")
    fb.review_status = req.review_status
    log_event(db, user["username"], "qa.feedback.review", f"id={fb.id} status={req.review_status}")
    db.commit()
    return {"ok": True}


# ---------- Golden questions ----------


class GoldenIn(BaseModel):
    question: str
    expected_topic: str = ""
    domain: str = ""


def _gq_out(g: GoldenQuestion) -> dict:
    return {
        "id": g.id,
        "question": g.question,
        "expected_topic": g.expected_topic,
        "domain": g.domain,
        "active": g.active,
        "created_by": g.created_by,
        "created_at": g.created_at.isoformat(),
    }


@router.get("/golden")
def golden_list(db: Session = Depends(get_db)):
    rows = db.query(GoldenQuestion).order_by(GoldenQuestion.created_at.desc()).all()
    return [_gq_out(g) for g in rows]


@router.post("/golden")
def golden_create(
    req: GoldenIn, db: Session = Depends(get_db), user: dict = Depends(admin_audited)
):
    if not req.question.strip():
        raise HTTPException(400, "Question required")
    if req.domain not in ("", "IT", "HR"):
        raise HTTPException(400, "domain must be IT, HR, or empty")
    g = GoldenQuestion(
        question=req.question.strip(),
        expected_topic=req.expected_topic.strip(),
        domain=req.domain or classify_domain(req.question, ""),
        created_by=user["username"],
    )
    db.add(g)
    db.flush()
    log_event(db, user["username"], "qa.golden.create", f"id={g.id}")
    db.commit()
    return _gq_out(g)


class GoldenPatch(BaseModel):
    question: Optional[str] = None
    expected_topic: Optional[str] = None
    domain: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/golden/{question_id}")
def golden_update(
    question_id: str,
    req: GoldenPatch,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    g = db.get(GoldenQuestion, question_id)
    if not g:
        raise HTTPException(404, "Golden question not found")
    if req.question is not None:
        if not req.question.strip():
            raise HTTPException(400, "Question required")
        g.question = req.question.strip()
    if req.expected_topic is not None:
        g.expected_topic = req.expected_topic.strip()
    if req.domain is not None:
        if req.domain not in ("", "IT", "HR"):
            raise HTTPException(400, "domain must be IT, HR, or empty")
        g.domain = req.domain
    if req.active is not None:
        g.active = req.active
    log_event(db, user["username"], "qa.golden.update", f"id={g.id} active={g.active}")
    db.commit()
    return _gq_out(g)


# ---------- Golden runs ----------


def _run_out(r: GoldenRun, include_results: bool = False) -> dict:
    out = {
        "id": r.id,
        "started_by": r.started_by,
        "started_at": r.started_at.isoformat(),
        "status": r.status,
        "total": len(r.results),
        # Effective verdict: the human verdict when set, otherwise the AI judge's.
        "passed": sum(1 for x in r.results if (x.verdict or x.ai_verdict) == "pass"),
        "failed": sum(1 for x in r.results if (x.verdict or x.ai_verdict) == "fail"),
        "flagged": sum(1 for x in r.results if x.auto_flagged),
    }
    if include_results:
        out["results"] = [
            {
                "id": x.id,
                "question": x.question,
                "expected_topic": x.expected_topic,
                "answer": x.answer,
                "sources_count": x.sources_count,
                "auto_flagged": x.auto_flagged,
                "ai_verdict": x.ai_verdict,
                "ai_reasoning": x.ai_reasoning,
                "verdict": x.verdict,
                "reviewed_by": x.reviewed_by,
                "error": x.error,
            }
            for x in r.results
        ]
    return out


@router.get("/golden/runs")
def golden_runs(db: Session = Depends(get_db)):
    rows = db.query(GoldenRun).order_by(GoldenRun.started_at.desc()).limit(20).all()
    return [_run_out(r) for r in rows]


@router.get("/golden/runs/{run_id}")
def golden_run_detail(run_id: str, db: Session = Depends(get_db)):
    r = db.get(GoldenRun, run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    return _run_out(r, include_results=True)


@router.post("/golden/run")
async def golden_run(user: dict = Depends(admin_audited)):
    """Run all active golden questions through the assistant pipeline (on demand)."""
    db = SessionLocal()
    try:
        questions = (
            db.query(GoldenQuestion)
            .filter(GoldenQuestion.active == True)  # noqa: E712
            .order_by(GoldenQuestion.created_at.asc())
            .all()
        )
        if not questions:
            raise HTTPException(400, "No active golden questions to run")
        try:
            provider = get_provider()
        except (RuntimeError, ValueError) as e:
            raise HTTPException(503, str(e))

        run = GoldenRun(started_by=user["username"])
        db.add(run)
        db.flush()
        log_event(db, user["username"], "qa.golden.run", f"run_id={run.id} questions={len(questions)}")
        db.commit()
        run_id = run.id

        system = build_system_prompt()
        any_error = False
        for gq in questions:
            # Input screen: golden questions are admin-authored but still go
            # to Gemini; injection-looking ones (often deliberate red-team
            # cases) are flagged in the audit log, never silently dropped.
            inbound = screen_question(gq.question)
            if inbound.findings:
                log_event(
                    db,
                    user["username"],
                    "chat.injection_flagged",
                    f"via=golden_run question_id={gq.id} findings={','.join(inbound.findings)}",
                )
                db.commit()
            answer_parts: list[str] = []
            error = ""
            try:
                async for chunk in provider.stream_chat(
                    system, [{"role": "user", "content": gq.question}]
                ):
                    answer_parts.append(chunk)
            except Exception as e:  # record and continue with remaining questions
                error = str(e)[:2000]
                any_error = True
            answer = "".join(answer_parts)
            # Safety gate: golden answers are persisted and shown to admins,
            # so a blocked answer is stored as the refusal, recorded as a
            # safety error, and auto-flagged — never persisted raw.
            gate = screen_answer(answer)
            if gate.blocked:
                answer = SAFE_REFUSAL
                error = f"safety_blocked: {','.join(gate.findings)}"
                any_error = True
            sources = extract_sources(answer)
            ai_verdict, ai_reasoning = "", ""
            if answer and not error:
                try:
                    graded = await judge_answer(gq.question, gq.expected_topic, answer)
                    ai_verdict = graded["verdict"]
                    ai_reasoning = graded["reasoning"]
                    # Judge reasoning is model output too — same gate applies.
                    if screen_answer(ai_reasoning).blocked:
                        ai_reasoning = "(judge reasoning withheld by safety checks)"
                except Exception as e:  # leave ungraded rather than invent a verdict
                    ai_reasoning = f"AI grading unavailable: {str(e)[:500]}"
            db.add(
                GoldenResult(
                    run_id=run_id,
                    question_id=gq.id,
                    question=gq.question,
                    expected_topic=gq.expected_topic,
                    answer=answer,
                    sources_count=len(sources),
                    auto_flagged=(not sources) or bool(error) or ai_verdict == "fail",
                    ai_verdict=ai_verdict,
                    ai_reasoning=ai_reasoning,
                    error=error,
                )
            )
            db.commit()

        run = db.get(GoldenRun, run_id)
        run.status = "error" if any_error else "done"
        db.commit()
        out = _run_out(run, include_results=True)

        # Best-effort Slack notifications — never affect the run's result.
        import asyncio as _asyncio

        from . import slack_service as _slack

        summary = (
            f"*Golden run complete* — {out['passed']} passed, {out['failed']} failed, "
            f"{out['flagged']} flagged of {out['total']} questions."
        )
        _asyncio.get_running_loop().create_task(
            _slack.notify_user("golden_notifications", user["username"], summary)
        )
        if out["failed"] > 0 or out["flagged"] > 0:
            _asyncio.get_running_loop().create_task(
                _slack.notify_channel(
                    "regression_posts",
                    f":warning: {summary} Review at {_slack.public_base_url()}/admin",
                )
            )
        return out
    finally:
        db.close()


class VerdictUpdate(BaseModel):
    verdict: str  # "pass" | "fail" | ""


@router.patch("/golden/results/{result_id}")
def golden_result_verdict(
    result_id: str,
    req: VerdictUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(admin_audited),
):
    if req.verdict not in ("pass", "fail", ""):
        raise HTTPException(400, "verdict must be 'pass', 'fail', or empty")
    res = db.get(GoldenResult, result_id)
    if not res:
        raise HTTPException(404, "Result not found")
    res.verdict = req.verdict
    res.reviewed_by = user["username"] if req.verdict else ""
    log_event(db, user["username"], "qa.golden.verdict", f"result_id={res.id} verdict={req.verdict or 'cleared'}")
    db.commit()
    return {"ok": True}
