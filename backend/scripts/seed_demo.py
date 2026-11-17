import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, AcceptanceCase, AcceptancePhase
from src.schemas.acceptance import AcceptanceInput, ReportInput, PhasePlan, SampleInput
from src.services import acceptance
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum


def _case(db, name):
    return db.scalar(select(AcceptanceCase).where(AcceptanceCase.name == name))


def _submit(db, user, case, rows, phase, suffix):
    records = [
        SampleInput(asset=row['asset'], model=row['model'], km=row['km'], failed=row['failed'])
        for row in rows
    ]
    payload = ReportInput(request_id=f'seed-{case.id}-p{phase}-{suffix}', phase=phase, records=records)
    return acceptance.submit_report(db, case.id, payload, user, now=time.time())


def _seed_cases(db, rows, admin, dataset):
    """The demo story: one passed case, one awaiting supplement, one exhausted."""
    ac8 = [row for row in rows if row['model'] == 'AC-8']
    ac12 = [row for row in rows if row['model'] == 'AC-12']

    if not _case(db, 'AC-8 放行验收 · 30,000 公里'):
        case = acceptance.open_case(db, AcceptanceInput(
            name='AC-8 放行验收 · 30,000 公里', model='AC-8', dataset_id=dataset.id,
            qualified_km=30000, min_survival=0.85,
            phases=[PhasePlan(size=30, alpha=0.05), PhasePlan(size=30, alpha=0.05)],
        ), admin)
        db.commit()
        _submit(db, admin, case, ac8[0:30], 1, 'a')
        _submit(db, admin, case, ac8[30:60], 2, 'b')
        acceptance.approve(db, case.id, admin)

    if not _case(db, 'AC-12 放行验收 · 40,000 公里'):
        case = acceptance.open_case(db, AcceptanceInput(
            name='AC-12 放行验收 · 40,000 公里', model='AC-12', dataset_id=dataset.id,
            qualified_km=40000, min_survival=0.8,
            phases=[PhasePlan(size=30, alpha=0.06), PhasePlan(size=30, alpha=0.06)],
        ), admin)
        db.commit()
        _submit(db, admin, case, ac12[0:30], 1, 'a')

    if not _case(db, 'AC-12 放行验收 · 20,000 公里'):
        case = acceptance.open_case(db, AcceptanceInput(
            name='AC-12 放行验收 · 20,000 公里', model='AC-12', dataset_id=dataset.id,
            qualified_km=20000, min_survival=0.85,
            phases=[PhasePlan(size=30, alpha=0.05), PhasePlan(size=30, alpha=0.05)],
        ), admin)
        db.commit()
        _submit(db, admin, case, ac12[30:50], 1, 'a')

    if not _case(db, 'AC-8 放行验收 · 80,000 公里'):
        resolved = [row for row in ac8 if row['km'] >= 80000 or row['failed'] == 1]
        case = acceptance.open_case(db, AcceptanceInput(
            name='AC-8 放行验收 · 80,000 公里', model='AC-8', dataset_id=dataset.id,
            qualified_km=80000, min_survival=0.85,
            phases=[PhasePlan(size=30, alpha=0.05), PhasePlan(size=30, alpha=0.05)],
        ), admin)
        db.commit()
        _submit(db, admin, case, resolved[0:30], 1, 'a')
        _submit(db, admin, case, resolved[30:60], 2, 'b')


def seed():
    migrate()
    with Session() as db:
        for role, name in [('admin', '林知 · 业务负责人'), ('analyst', '陈舟 · 分析员'), ('viewer', '周禾 · 观察员')]:
            email = role + '@demo.local'
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, name=name, role=role, password=hash_password('Demo123!')))
        db.flush()
        m = metadata()
        d = db.scalar(select(Dataset).where(Dataset.name == m['dataset_name']))
        if not d:
            rows = seed_records()
            d = Dataset(name=m['dataset_name'], source=m['short'] + '-202611.csv', records=rows,
                        checksum=checksum(rows), created_at=time.time())
            db.add(d)
            db.flush()
        if not db.scalar(select(Job).where(Job.name == '基线复核 · ' + m['name'])):
            params = {p['key']: p['default'] for p in m.get('parameters', [])}
            result = analyze(d.records, params)
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            admin = db.scalar(select(User).where(User.role == 'admin'))
            j = Job(name='基线复核 · ' + m['name'], dataset_id=d.id, params=params, status='completed',
                    result=result, created_by=admin.id, created_at=time.time() - 3600,
                    finished_at=time.time() - 3595, approved=True)
            db.add(j)
            db.flush()
            r = Report(name='每日业务复核简报', job_id=j.id, destination='业务复核组 · 本地收件箱',
                       interval_seconds=86400, next_run=time.time() + 86400, enabled=True)
            db.add(r)
            db.flush()
            deliver(db, r)
            db.add(Audit(actor='seed', action='demo.baseline', detail={'dataset': d.id}, at=time.time()))
            db.commit()
        admin = db.scalar(select(User).where(User.role == 'admin'))
        _seed_cases(db, d.records, admin, d)
        db.commit()
        counts = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                  for model in [User, Dataset, Job, Report, Delivery, AcceptanceCase, AcceptancePhase, Audit]}
        print(json.dumps(counts, ensure_ascii=False))
        return counts


if __name__ == '__main__':
    seed()
