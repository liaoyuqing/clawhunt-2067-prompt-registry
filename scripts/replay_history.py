
"""Clean-clone demonstration of AC3: history returns the MOST RECENT pass rate per
version, not the average and not the first run.

Run:  python scripts/replay_history.py <path-to-sqlite-db>   (any cwd works)
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.eval_binding import CaseOutcome, record_eval_run
from app.models import AiPromptVersion

ORG = UUID("00000000-0000-0000-0000-000000000000")
SITE = UUID("00000000-0000-0000-0000-000000000000")


def main(path: str) -> int:
    engine = create_engine("sqlite:///" + path, future=True)
    with Session(engine) as session:
        rows = session.execute(
            select(AiPromptVersion).where(AiPromptVersion.version == "1.1.0")
        ).scalars().all()
        if not rows:
            print("prompt version 1.1.0 not registered; run prompt-register first")
            return 2
        pv = rows[0]
        base = datetime(2026, 11, 22, 9, 0, 0)
        # three successive runs: 3/5 = 0.6, 9/10 = 0.9, 4/5 = 0.8
        for i, (passed, total) in enumerate([(3, 5), (9, 10), (4, 5)]):
            outcomes = [
                CaseOutcome(case_id="c%02d" % (j + 1), prompt_version_id=pv.id, passed=j < passed)
                for j in range(total)
            ]
            run = record_eval_run(
                session,
                org_id=ORG,
                site_id=SITE,
                created_by="platform-ops@example.invalid",
                tool=pv.tool,
                status="finished",
                started_at=base + timedelta(minutes=10 * i),
                finished_at=base + timedelta(minutes=10 * i + 3),
                outcomes=outcomes,
                model_profile_name="model.main",
            )
            print("seeded run %s passed=%d total=%d rate=%.4f" % (run.id, passed, total, passed / total))
        session.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
