"""HTTP-level checks: roles, replay, closing and distribution gating.

Run from ``backend/``::

    python3 -m pytest -q test-artifacts/gb-1017/tests
"""
import os
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix='acceptance-api-')
os.environ['DATABASE_URL'] = f'sqlite:///{ROOT}/api.db'
os.environ['ARTIFACT_DIR'] = f'{ROOT}/artifacts'
os.environ['ARTIFACT_DIR'] = f'{ROOT}/artifacts'
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'test-signing-key'
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'backend')))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from migrations.initial import migrate  # noqa: E402
from src.config.database import Session  # noqa: E402
from src.models import User, AcceptanceCase, AcceptancePhase, AcceptanceSample, AcceptanceReport  # noqa: E402
from src.utils.security import hash_password  # noqa: E402


@pytest.fixture(scope='module')
def client():
    migrate()
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=f'{role}@demo.local', name=role, role=role,
                        password=hash_password('Demo123!')))
        db.commit()
    with TestClient(app) as test_client:
        yield test_client


def token(client, role):
    response = client.post('/auth/login', json={'email': f'{role}@demo.local', 'password': 'Demo123!'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


def make_case(client, headers, name='HTTP 验收案', phases=None):
    response = client.post('/acceptance/cases', headers=headers, json={
        'name': name, 'model': 'AC-8', 'qualified_km': 30000, 'min_survival': 0.85,
        'phases': phases or [{'size': 30, 'alpha': 0.05}, {'size': 30, 'alpha': 0.05}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def surviving(n, prefix='S', km=40000, start=0):
    return [{'asset': f'{prefix}-{index:03}', 'km': km, 'failed': 0} for index in range(start, start + n)]


def failing(n, prefix='S', km=9000, start=900):
    return [{'asset': f'{prefix}-{index:03}', 'km': km, 'failed': 1} for index in range(start, start + n)]


def test_viewer_can_read_but_not_open_or_report(client):
    viewer = token(client, 'viewer')
    assert client.get('/acceptance/cases', headers=viewer).status_code == 200
    assert client.post('/acceptance/cases', headers=viewer, json={
        'name': '越权案', 'model': 'AC-8', 'qualified_km': 30000, 'min_survival': 0.85,
        'phases': [{'size': 30, 'alpha': 0.05}],
    }).status_code == 403


def test_full_http_flow_replay_and_distribution_gate(client):
    admin, analyst = token(client, 'admin'), token(client, 'analyst')
    case = make_case(client, analyst, name='HTTP 全流程案')
    assert case['status'] == 'open' and case['phases'][0]['acceptance_number'] == 29
    assert case['waiting_new'] is True

    first = client.post(f"/acceptance/cases/{case['id']}/reports", headers=analyst, json={
        'request_id': 'http-req-0001', 'phase': 1,
        'records': surviving(28) + failing(2),
    })
    assert first.status_code == 201, first.text
    assert first.json()['conclusion'] == 'phase_failed'
    assert first.json()['case_state']['current_phase'] == 2
    assert first.json()['phase_state']['status'] == 'failed'

    second = client.post(f"/acceptance/cases/{case['id']}/reports", headers=analyst, json={
        'request_id': 'http-req-0002', 'phase': 2,
        'records': surviving(29, prefix='T') + failing(1, prefix='T'),
    })
    assert second.status_code == 201, second.text
    assert second.json()['conclusion'] == 'passed'
    detail = client.get(f"/acceptance/cases/{case['id']}", headers=admin).json()
    assert detail['status'] == 'closed' and detail['result'] == 'passed'
    assert detail['distributable'] is False and detail['approvable'] is True

    # Replaying the same request returns the same ledger and does not add samples.
    replay = client.post(f"/acceptance/cases/{case['id']}/reports", headers=analyst, json={
        'request_id': 'http-req-0002', 'phase': 2,
        'records': surviving(29, prefix='T') + failing(1, prefix='T'),
    })
    assert replay.status_code == 201 and replay.json() == second.json()
    with Session() as db:
        assert db.query(AcceptanceSample).filter_by(case_id=case['id']).count() == 60
        assert db.query(AcceptanceReport).filter_by(case_id=case['id']).count() == 2

    # Late reports cannot change a closed case.
    late = client.post(f"/acceptance/cases/{case['id']}/reports", headers=analyst, json={
        'request_id': 'http-req-late', 'phase': 2, 'records': surviving(1, prefix='Z'),
    })
    assert late.status_code == 409 and '封案' in late.json()['detail']

    # Analysts cannot approve; the unapproved passed case cannot be distributed.
    assert client.post(f"/acceptance/cases/{case['id']}/approve", headers=analyst).status_code == 403
    blocked = client.post('/reports', headers=admin, json={
        'name': '未批准分发', 'acceptance_case_id': case['id'], 'destination': '复核组',
        'interval_seconds': 60, 'delay_seconds': 0,
    })
    assert blocked.status_code == 409

    approved = client.post(f"/acceptance/cases/{case['id']}/approve", headers=admin)
    assert approved.status_code == 200 and approved.json()['approved'] is True

    created = client.post('/reports', headers=admin, json={
        'name': '达标放行简报', 'acceptance_case_id': case['id'], 'destination': '复核组',
        'interval_seconds': 60, 'delay_seconds': 0,
    })
    assert created.status_code == 201, created.text
    assert created.json()['acceptance_case_id'] == case['id']


def test_filtered_views(client):
    admin = token(client, 'admin')
    case = make_case(client, admin, name='HTTP 待补报案')
    response = client.post(f"/acceptance/cases/{case['id']}/reports", headers=admin, json={
        'request_id': 'http-req-supp-1', 'phase': 1,
        'records': surviving(29) + [{'asset': 'WAIT-001', 'km': 10000, 'failed': 0}],
    })
    assert response.status_code == 201, response.text
    assert response.json()['conclusion'] == 'collecting'
    assert response.json()['phase_state']['pending'] == 1 and response.json()['phase_state']['enrolled'] == 30
    supplement = client.get('/acceptance/cases?view=supplement', headers=admin).json()
    assert any(row['id'] == case['id'] for row in supplement)
    assert not any(row['id'] == case['id'] for row in client.get('/acceptance/cases?view=collect', headers=admin).json())
    pending = client.get(f"/acceptance/cases/{case['id']}", headers=admin).json()['pending_samples']
    assert [row['asset'] for row in pending] == ['WAIT-001']


def test_failed_case_is_closed_and_never_distributable(client):
    admin = token(client, 'admin')
    case = make_case(client, admin, name='HTTP 未达标案', phases=[{'size': 30, 'alpha': 0.05}])
    response = client.post(f"/acceptance/cases/{case['id']}/reports", headers=admin, json={
        'request_id': 'http-req-fail-1', 'phase': 1,
        'records': surviving(10) + failing(20),
    })
    assert response.status_code == 201, response.text
    assert response.json()['conclusion'] == 'failed'
    body = client.get(f"/acceptance/cases/{case['id']}", headers=admin).json()
    assert body['status'] == 'closed' and body['result'] == 'failed'
    assert client.post(f"/acceptance/cases/{case['id']}/approve", headers=admin).status_code == 409
    assert client.post('/reports', headers=admin, json={
        'name': '未达标分发', 'acceptance_case_id': case['id'], 'destination': '复核组',
        'interval_seconds': 60, 'delay_seconds': 0,
    }).status_code == 409
