"""Business-rule checks for the phased release-acceptance ledger.

Run from ``backend/`` with an isolated SQLite database::

    python3 -m pytest -q test-artifacts/gb-1017/tests

The conftest pins an isolated SQLite database, so no service is touched.
"""
import os
import sys

import pytest
from fastapi import HTTPException
from scipy.stats import binom
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'backend'))

from src.config.database import Base  # noqa: E402
from src.models import AcceptanceCase, AcceptancePhase, AcceptanceSample, AcceptanceReport, User  # noqa: E402
from src.schemas.acceptance import AcceptanceInput, PhasePlan, ReportInput, SampleInput  # noqa: E402
from src.services import acceptance  # noqa: E402
from src.services import reports as report_service  # noqa: E402


class PlainUser:
    def __init__(self, ident=1, email='analyst@demo.local'):
        self.id = ident
        self.email = email


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/acceptance.db')
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    session = Session()
    session.add(User(id=1, email='analyst@demo.local', name='分析员', role='analyst', password='x'))
    session.add(User(id=2, email='admin@demo.local', name='负责人', role='admin', password='x'))
    session.commit()
    yield session
    session.close()


ANALYST = PlainUser(1)
ADMIN = PlainUser(2, 'admin@demo.local')
COUNTER = {'n': 0}


def open_case(db, model='AC-8', km=30000, survival=0.85, phases=((30, 0.05), (30, 0.05)), name=None):
    COUNTER['n'] += 1
    data = AcceptanceInput(
        name=name or f'验收案-{COUNTER["n"]}',
        model=model, qualified_km=km, min_survival=survival,
        phases=[PhasePlan(size=size, alpha=alpha) for size, alpha in phases],
    )
    case = acceptance.open_case(db, data, ANALYST)
    db.commit()
    return case


def report(db, case, assets, phase=1, request_id=None):
    COUNTER['n'] += 1
    payload = ReportInput(
        request_id=request_id or f'req-{case.id}-{phase}-{COUNTER["n"]}',
        phase=phase,
        records=[SampleInput(asset=asset, km=km, failed=failed) for asset, km, failed in assets],
    )
    return acceptance.submit_report(db, case.id, payload, ANALYST)


def survivors(prefix, count, km=40000, start=0):
    return [(f'{prefix}-{index:03}', km, 0) for index in range(start, start + count)]


def failures(prefix, count, km=9000, start=1000):
    return [(f'{prefix}-{index:03}', km, 1) for index in range(start, start + count)]


def test_acceptance_number_is_smallest_significant_survival():
    assert acceptance.acceptance_number(30, 0.85, 0.05) == 29
    assert binom.sf(28, 30, 0.85) <= 0.05
    assert binom.sf(27, 30, 0.85) > 0.05
    assert acceptance.acceptance_number(10, 0.85, 0.0001) is None


def test_open_locks_rules_and_rejects_allowance_over_limit(db):
    case = open_case(db)
    phases = db.query(AcceptancePhase).filter_by(case_id=case.id).order_by(AcceptancePhase.number).all()
    assert [(p.size, p.alpha, p.acceptance_number) for p in phases] == [(30, 0.05, 29), (30, 0.05, 29)]
    assert case.total_alpha == pytest.approx(0.10)
    with pytest.raises(HTTPException) as error:
        acceptance.open_case(db, AcceptanceInput(
            name='超额额度案', model='AC-8', qualified_km=30000, min_survival=0.85,
            phases=[PhasePlan(size=30, alpha=0.1), PhasePlan(size=30, alpha=0.1), PhasePlan(size=30, alpha=0.1)],
        ), ANALYST)
    assert error.value.status_code == 422
    assert '超过总额度上限' in error.value.detail


def test_phase_waits_for_full_headcount(db):
    case = open_case(db)
    response = report(db, case, survivors('A', 28) + failures('A', 1))
    assert response['conclusion'] == 'collecting'
    assert response['phase_state']['status'] == 'collecting'
    assert '尚未收齐' in response['message']
    response = report(db, case, survivors('B', 1))
    assert response['conclusion'] == 'passed'
    phase = db.query(AcceptancePhase).filter_by(case_id=case.id, number=1).one()
    assert (phase.enrolled, phase.survived, phase.failed, phase.pending) == (30, 29, 1, 0)


def test_pending_unit_holds_verdict_until_supplemented(db):
    case = open_case(db)
    # 29 survivors, one in-service below qualified mileage: headcount is full but
    # the phase cannot be judged yet.
    assets = survivors('P', 29) + [('P-900', 10000, 0)]
    first = report(db, case, assets)
    assert first['conclusion'] == 'collecting'
    phase = db.query(AcceptancePhase).filter_by(case_id=case.id, number=1).one()
    assert (phase.enrolled, phase.survived, phase.pending) == (30, 29, 1)
    # Supplement resolves the pending unit without enrolling a new one.
    second = report(db, case, [('P-900', 31000, 0)])
    assert second['supplemented'] == 1 and second['accepted_new'] == 0
    assert second['conclusion'] == 'passed'
    assert db.query(AcceptanceSample).filter_by(case_id=case.id).count() == 30


def test_same_asset_different_value_is_rejected(db):
    case = open_case(db, km=50000, survival=0.5, phases=((5, 0.1),))
    report(db, case, survivors('X', 3, km=60000) + failures('X', 1) + [('X-900', 10000, 0)])
    with pytest.raises(HTTPException) as error:
        report(db, case, [('X-000', 59000, 0)])
    assert error.value.status_code == 409 and '同号异值拒收' in error.value.detail


def test_duplicate_inside_one_report_is_rejected(db):
    case = open_case(db, km=50000, survival=0.5, phases=((5, 0.2),))
    with pytest.raises(HTTPException) as error:
        report(db, case, [('D-1', 60000, 0), ('D-1', 59000, 0)])
    assert error.value.status_code == 422 and '同一次报期' in error.value.detail


def test_later_phase_waits_for_earlier_verdict(db):
    case = open_case(db)
    with pytest.raises(HTTPException) as error:
        report(db, case, survivors('C', 30), phase=2)
    assert error.value.status_code == 409 and '后期等待' in error.value.detail


def test_failed_phase_moves_on_and_exhaustion_closes_case(db):
    case = open_case(db, survival=0.5, phases=((5, 0.1), (5, 0.1)))
    response = report(db, case, failures('F', 2) + survivors('F', 3))
    assert response['conclusion'] == 'phase_failed'
    assert response['case_state']['status'] == 'open' and response['case_state']['current_phase'] == 2
    response = report(db, case, failures('G', 3) + survivors('G', 2), phase=2)
    assert response['conclusion'] == 'failed'
    assert response['case_state']['status'] == 'closed'
    assert '期数耗尽' in response['message']
    with pytest.raises(HTTPException) as error:
        report(db, case, survivors('LATE', 1), phase=2)
    assert error.value.status_code == 409 and '封案' in error.value.detail


def test_passing_closes_case_and_only_admin_approves(db):
    case = open_case(db)
    first = report(db, case, survivors('H', 28) + failures('H', 2))
    assert first['conclusion'] == 'phase_failed'
    second = report(db, case, survivors('I', 29) + failures('I', 1), phase=2)
    assert second['conclusion'] == 'passed'
    assert second['case_state']['status'] == 'closed' and second['case_state']['result'] == 'passed'
    acceptance.approve(db, case.id, ADMIN)
    assert db.get(AcceptanceCase, case.id).approved is True


def test_unpassed_case_cannot_be_approved(db):
    case = open_case(db, survival=0.5, phases=((5, 0.05),))
    report(db, case, failures('J', 3) + survivors('J', 2))
    with pytest.raises(HTTPException) as error:
        acceptance.approve(db, case.id, ADMIN)
    assert error.value.status_code == 409


def test_request_retry_does_not_double_count(db):
    case = open_case(db, phases=((30, 0.05),))
    assets = survivors('K', 29) + failures('K', 1)
    first = report(db, case, assets, request_id='fixed-request-0001')
    assert first['conclusion'] == 'passed'
    replay = report(db, case, assets, request_id='fixed-request-0001')
    assert replay == first
    assert db.query(AcceptanceSample).filter_by(case_id=case.id).count() == 30
    assert db.query(AcceptanceReport).filter_by(case_id=case.id).count() == 1
    with pytest.raises(HTTPException) as error:
        report(db, case, survivors('OTHER', 1), request_id='fixed-request-0001')
    assert error.value.status_code == 409 and '不同内容' in error.value.detail


def test_headcount_over_plan_is_refused(db):
    case = open_case(db, survival=0.5, phases=((5, 0.2),))
    with pytest.raises(HTTPException) as error:
        report(db, case, survivors('M', 6))
    assert error.value.status_code == 409 and '超出人数上限' in error.value.detail


def test_resolved_sample_cannot_take_part_in_a_later_phase(db):
    case = open_case(db, survival=0.5, phases=((5, 0.1), (5, 0.1)))
    report(db, case, failures('N', 2) + survivors('N', 3))
    with pytest.raises(HTTPException) as error:
        report(db, case, [('N-000', 45000, 0)], phase=2)
    assert error.value.status_code == 409 and '已参评' in error.value.detail
    assert db.query(AcceptanceSample).filter_by(case_id=case.id).count() == 5


def test_distribution_requires_approved_pass(db, monkeypatch, tmp_path):
    monkeypatch.setattr(report_service, 'ARTIFACT_DIR', str(tmp_path))
    case = open_case(db, phases=((30, 0.05),))
    report(db, case, survivors('O', 29) + failures('O', 1))
    from src.schemas.report import ReportInput as DistributionInput
    with pytest.raises(HTTPException):
        report_service.create(db, DistributionInput(name='未批准验收简报', acceptance_case_id=case.id))
    acceptance.approve(db, case.id, ADMIN)
    plan = report_service.create(db, DistributionInput(name='达标放行简报', acceptance_case_id=case.id))
    delivery = report_service.deliver(db, plan)
    assert delivery is not None
    with open(os.path.join(str(tmp_path), delivery.filename), encoding='utf-8-sig') as handle:
        text = handle.read()
    assert '存活' in text and '期号' in text
