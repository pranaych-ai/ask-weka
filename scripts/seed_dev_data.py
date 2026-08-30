"""Seed SYNTHETIC development data for Ask WEKA.

Dev must never run on a copy of production data. This script populates the
development database with clearly-fake sample data so the app is usable in
dev without touching production records.

Safety:
  - Refuses to run in production (REPLIT_DEPLOYMENT set).
  - Refuses to run against a database stamped 'production'.
  - Idempotent: skips anything already seeded (matched by the SYNTH marker).

Run from the repo root:  python -m scripts.seed_dev_data
"""

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from backend.env import PRODUCTION, is_deployment, read_marker  # noqa: E402

SYNTH = "[SYNTHETIC]"

SAMPLE_CONVERSATIONS = [
    ("dev-user-1@example.test", "How do I request a new laptop?",
     f"{SYNTH} You can request hardware through the IT Service Portal."),
    ("dev-user-2@example.test", "What is the PTO policy?",
     f"{SYNTH} See the HR handbook section on paid time off."),
]

SAMPLE_GOLDEN = [
    ("How do I reset my Okta password?", "Mentions the Okta self-service reset flow."),
    ("Who do I contact about payroll?", "Points to the HR/payroll contact."),
]


def main() -> int:
    if is_deployment():
        print("REFUSED: seed_dev_data must never run in production.")
        return 1

    from backend.db import Base, SessionLocal, engine
    from backend.models import Conversation, GoldenQuestion, Message

    Base.metadata.create_all(bind=engine)

    marker = read_marker(engine)
    if marker == PRODUCTION:
        print("REFUSED: this database is stamped 'production'. "
              "Point DATABASE_URL at the development database.")
        return 1

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        created = 0
        for username, question, answer in SAMPLE_CONVERSATIONS:
            exists = (
                db.query(Conversation)
                .filter(Conversation.username == username)
                .first()
            )
            if exists:
                continue
            conv = Conversation(username=username, title=question[:80])
            db.add(conv)
            db.flush()
            db.add(Message(conversation_id=conv.id, role="user", content=question))
            db.add(Message(conversation_id=conv.id, role="assistant", content=answer))
            created += 1

        for question, criteria in SAMPLE_GOLDEN:
            exists = (
                db.query(GoldenQuestion)
                .filter(GoldenQuestion.question == question)
                .first()
            )
            if exists:
                continue
            db.add(GoldenQuestion(question=question, expected_topic=criteria,
                                  created_by="seed_dev_data"))
            created += 1

        db.commit()
        print(f"Seeded {created} synthetic record group(s) at {now.isoformat()} "
              f"(existing synthetic data left untouched).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
