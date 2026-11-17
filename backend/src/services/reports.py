import time, os, csv, io, hashlib, json
from fastapi import HTTPException
from sqlalchemy import select
from src.models import Job, Report, Delivery, AcceptanceCase, AcceptancePhase
from src.config.settings import ARTIFACT_DIR
from src.services import acceptance as acceptance_service


def create(db, data):
    job = None
    case = None
    if data.job_id is not None and data.acceptance_case_id is not None:
        raise HTTPException(422, '分发来源只能是分析结果或达标验收案之一')
    if data.acceptance_case_id is not None:
        case = db.get(AcceptanceCase, data.acceptance_case_id)
        if not case:
            raise HTTPException(404, '验收案不存在')
        if case.status != 'closed' or case.result != 'passed':
            raise HTTPException(409, '仅判定达标并已封案的验收案可进入分发')
        if not case.approved:
            raise HTTPException(409, '达标验收案需负责人批准后才能分发')
    elif data.job_id is not None:
        job = db.get(Job, data.job_id)
        if not job or job.status != 'completed' or not job.approved:
            raise HTTPException(409, '请先选择已完成且获批准的分析结果')
    else:
        raise HTTPException(422, '请选择已完成且获批准的分析结果，或一个达标验收案')
    report = Report(
        name=data.name, job_id=job.id if job else None,
        acceptance_case_id=case.id if case else None, destination=data.destination,
        interval_seconds=data.interval_seconds, next_run=time.time() + data.delay_seconds, enabled=True)
    db.add(report)
    db.flush()
    return report


def _source(db, report):
    """Return (name, params, result) for a report's immutable source snapshot."""
    if report.acceptance_case_id is not None:
        case = db.get(AcceptanceCase, report.acceptance_case_id)
        if not case or case.status != 'closed' or case.result != 'passed' or not case.approved:
            return None
        phases = list(db.scalars(select(AcceptancePhase).where(
            AcceptancePhase.case_id == case.id).order_by(AcceptancePhase.number)))
        return case.name, {
            'model': case.model, 'qualified_km': case.qualified_km,
            'min_survival': case.min_survival, 'total_alpha': case.total_alpha,
            'case_id': case.id, 'result': case.result, 'close_reason': case.close_reason,
        }, acceptance_service.case_result(case, phases)
    job = db.get(Job, report.job_id) if report.job_id is not None else None
    if not job or not job.approved:
        return None
    return job.name, job.params, job.result


def deliver(db, report):
    source = _source(db, report)
    if source is None:
        return None
    name, params, result = source
    out = io.StringIO()
    out.write('﻿')
    table = result.get('table', {})
    cols = table.get('columns', [])
    writer = csv.writer(out)

    def safe(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        text = str(value)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text

    writer.writerow([safe(c['label']) for c in cols])
    for row in table.get('rows', []):
        writer.writerow([safe(row.get(c['key'], '')) for c in cols])
    data = out.getvalue().encode('utf-8')
    delivery = Delivery(
        report_id=report.id, created_at=time.time(),
        checksum=hashlib.sha256(data).hexdigest(), filename='', destination=report.destination)
    db.add(delivery)
    db.flush()
    delivery.filename = f'report-{report.id}-delivery-{delivery.id}.csv'
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(os.path.join(ARTIFACT_DIR, delivery.filename), 'wb') as f:
        f.write(data)
    with open(os.path.join(ARTIFACT_DIR, delivery.filename + '.json'), 'w') as f:
        json.dump({'report': name, 'params': params, 'result': result,
                   'checksum': delivery.checksum}, f, ensure_ascii=False, allow_nan=False)
    report.next_run = time.time() + report.interval_seconds
    return delivery
