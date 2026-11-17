"""Phased release acceptance with locked rules and a bounded miss budget.

Statistical contract
--------------------
* A component *survives* when its observed mileage reaches the qualified
  mileage, *fails* when it failed before reaching it, and is *pending* while it
  is still in service below the qualified mileage.
* A phase is judged only once its planned headcount has been collected and every
  unit is determined.  The test is the exact upper binomial tail: with ``n``
  determined units, survival ``p0`` and allowance ``α``, the phase passes when
  the survival count reaches the frozen acceptance number
  ``k = min{k : P(Bin(n, p0) >= k) <= α}``.  Looking at the curve before the
  headcount is collected therefore cannot produce an early pass.
* Allowances are frozen at opening and their sum may not exceed the upper
  limit, so the chance of ever passing a batch that only meets ``p0`` is bounded
  by that total.  Only a passed case may be approved, and only an approved case
  may be distributed.
"""
import math
import time
from fastapi import HTTPException
from scipy.stats import binom
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.constants.acceptance import MAX_TOTAL_ALPHA, MAX_KM, MAX_PHASES
from src.models import AcceptanceCase, AcceptancePhase, AcceptanceSample, AcceptanceReport, Dataset
from src.services.audit import record
from src.utils.serialization import checksum

SURVIVED = 'survived'
FAILED = 'failed'
PENDING = 'pending'


def acceptance_number(size, survival, alpha):
    """Smallest survival count that reaches significance at the allowance."""
    for count in range(1, size + 1):
        if float(binom.sf(count - 1, size, survival)) <= alpha + 1e-12:
            return count
    return None


def tail_risk(size, survival, count):
    """Probability of seeing at least ``count`` survivors under p0."""
    return float(binom.sf(count - 1, size, survival)) if count >= 1 else 1.0


def outcome_for(km, failed, qualified_km):
    if km >= qualified_km:
        return SURVIVED
    if int(failed) == 1:
        return FAILED
    return PENDING


def open_case(db, data, user):
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '验收案名称去除空格后至少需要两个字符')
    if db.scalar(select(AcceptanceCase).where(AcceptanceCase.name == name)):
        raise HTTPException(409, '该验收案名称已存在，请修改名称')
    model = str(data.model).strip()
    if not model:
        raise HTTPException(422, '请选择待放行的部件型号')
    qualified = float(data.qualified_km)
    if not math.isfinite(qualified) or not 1 <= qualified <= MAX_KM:
        raise HTTPException(422, '合格里程须为1至1000万公里的有限数值')
    survival = float(data.min_survival)
    if not math.isfinite(survival) or not 0.5 <= survival <= 0.999:
        raise HTTPException(422, '最低存活率须介于0.5和0.999')
    if not 1 <= len(data.phases) <= MAX_PHASES:
        raise HTTPException(422, f'验收期数须为1至{MAX_PHASES}期')
    if data.dataset_id is not None and not db.get(Dataset, data.dataset_id):
        raise HTTPException(404, '关联的数据批次不存在')
    plans, total = [], 0.0
    for number, plan in enumerate(data.phases, 1):
        size = int(plan.size)
        alpha = round(float(plan.alpha), 6)
        if size < 1:
            raise HTTPException(422, f'第 {number} 期人数至少为1件')
        if not math.isfinite(alpha) or alpha <= 0 or alpha > MAX_TOTAL_ALPHA:
            raise HTTPException(422, f'第 {number} 期误判额度须大于0且不超过{MAX_TOTAL_ALPHA}')
        count = acceptance_number(size, survival, alpha)
        if count is None:
            raise HTTPException(422, f'第 {number} 期：{size} 件在最低存活率 {survival:g} 与误判额度 {alpha:g} 下无法给出判定数，请增加人数或放宽额度')
        total += alpha
        plans.append((number, size, alpha, count))
    if total > MAX_TOTAL_ALPHA + 1e-9:
        raise HTTPException(422, f'逐期误判额度合计 {total:.4f} 超过总额度上限 {MAX_TOTAL_ALPHA:g}')
    now = time.time()
    case = AcceptanceCase(
        name=name, model=model, dataset_id=data.dataset_id,
        qualified_km=qualified, min_survival=survival, total_alpha=round(total, 6),
        status='open', result='', current_phase=1, approved=False, close_reason='',
        created_by=user.id, created_at=now,
    )
    db.add(case)
    db.flush()
    for number, size, alpha, count in plans:
        db.add(AcceptancePhase(
            case_id=case.id, number=number, size=size, alpha=alpha, acceptance_number=count,
            status='collecting' if number == 1 else 'pending', allowance_used=0.0,
        ))
    record(db, user.email, 'acceptance.open', {
        'id': case.id, 'model': case.model, 'phases': len(plans), 'total_alpha': case.total_alpha})
    db.flush()
    return case


def _normalize(records, model):
    seen, normalized = {}, []
    for index, row in enumerate(records, 1):
        asset = str(row.asset).strip()
        if not asset:
            raise HTTPException(422, f'第 {index} 条缺少资产号')
        if row.model and str(row.model).strip() != model:
            raise HTTPException(422, f'第 {index} 条的型号与验收案型号 {model} 不一致')
        try:
            km = float(row.km)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(422, f'第 {index} 条里程必须为数值') from None
        if not math.isfinite(km) or not 1 <= km <= MAX_KM:
            raise HTTPException(422, f'第 {index} 条里程须为1至1000万公里的有限数值')
        if row.failed not in (0, 1):
            raise HTTPException(422, f'第 {index} 条 failed 只能为 0 或 1')
        failed = int(row.failed)
        if asset in seen:
            if seen[asset] == (km, failed):
                continue
            raise HTTPException(422, f'资产号 {asset} 在同一次报期中出现不同观测值，拒收')
        seen[asset] = (km, failed)
        normalized.append((asset, km, failed))
    if not normalized:
        raise HTTPException(422, '报期记录去重后为空')
    return normalized


def _fingerprint(data):
    records = sorted(
        (str(row.asset).strip(), str(row.model).strip() if row.model else '', float(row.km), int(row.failed))
        for row in data.records
    )
    return checksum({'phase': data.phase, 'records': records})


def _phase_rows(db, case, phase):
    return db.scalars(select(AcceptanceSample).where(
        AcceptanceSample.case_id == case.id,
        AcceptanceSample.phase == phase.number).order_by(AcceptanceSample.id)).all()


def _recount(db, case, phase):
    rows = _phase_rows(db, case, phase)
    phase.enrolled = len(rows)
    phase.survived = sum(1 for row in rows if row.outcome == SURVIVED)
    phase.failed = sum(1 for row in rows if row.outcome == FAILED)
    phase.pending = sum(1 for row in rows if row.outcome == PENDING)


def _evaluate(db, case, phase, phases, now):
    """Judge a fully determined phase; never reopens an already closed case."""
    if case.status != 'open':
        return ''
    if phase.enrolled < phase.size or phase.pending > 0:
        return ''
    risk = tail_risk(phase.size, case.min_survival, phase.survived)
    phase.risk = risk
    phase.allowance_used = phase.alpha
    phase.evaluated_at = now
    if phase.survived >= phase.acceptance_number:
        phase.status = 'passed'
        case.status = 'closed'
        case.result = 'passed'
        case.closed_at = now
        case.close_reason = (f'第 {phase.number} 期存活 {phase.survived}/{phase.size} 达到判定数 '
                             f'{phase.acceptance_number}，尾部概率 {risk:.4f} 不超过误判额度 {phase.alpha:g}，判定达标')
        return 'passed'
    phase.status = 'failed'
    following = next((row for row in phases if row.number == phase.number + 1), None)
    if following is not None:
        following.status = 'collecting'
        case.current_phase = following.number
        return 'phase_failed'
    case.status = 'closed'
    case.result = 'failed'
    case.closed_at = now
    case.close_reason = f'至第 {phase.number} 期全部期数耗尽，各期均未达到存活标准'
    return 'failed'


def _result_message(case, conclusion, accepted, supplemented):
    if conclusion == 'passed':
        return f'本期收满并判定达标，验收案已封案：{case.close_reason}'
    if conclusion == 'failed':
        return f'本期判定未达标且期数耗尽，验收案已封案：{case.close_reason}'
    if conclusion == 'phase_failed':
        return f'本期判定未达标，转入第 {case.current_phase} 期继续验收。'
    return f'已受理 {accepted} 件新样本、{supplemented} 件补报，本期尚未收齐。'


def _apply_records(db, case, phase, records, now):
    """Assign new samples to the phase and refresh pending ones in place."""
    samples = {row.asset: row for row in db.scalars(select(AcceptanceSample).where(
        AcceptanceSample.case_id == case.id))}
    new_records, supplemented, skipped = [], 0, 0
    for asset, km, failed in records:
        sample = samples.get(asset)
        if sample is None:
            new_records.append((asset, km, failed))
            continue
        if sample.outcome != PENDING:
            if sample.km == km and sample.failed == failed:
                skipped += 1
                continue
            raise HTTPException(409, f'资产号 {asset} 已参评，同号异值拒收')
        if km < sample.km:
            raise HTTPException(422, f'资产号 {asset} 的补报里程不得小于前次记录')
        if km == sample.km and failed == 0:
            skipped += 1
            continue
        sample.km = km
        sample.failed = failed
        sample.outcome = outcome_for(km, failed, case.qualified_km)
        sample.updated_at = now
        supplemented += 1
    if phase.enrolled + len(new_records) > phase.size:
        raise HTTPException(
            409, f'第 {phase.number} 期计划 {phase.size} 件，本次新增 {len(new_records)} 件将超出人数上限')
    for asset, km, failed in new_records:
        db.add(AcceptanceSample(
            case_id=case.id, asset=asset, model=case.model, km=km, failed=failed,
            outcome=outcome_for(km, failed, case.qualified_km), phase=phase.number,
            reported_at=now, updated_at=now,
        ))
    return len(new_records), supplemented, skipped


def submit_report(db, case_id, data, user, now=None):
    now = now or time.time()
    request_id = data.request_id.strip()
    fingerprint = _fingerprint(data)
    case = db.scalar(select(AcceptanceCase).where(
        AcceptanceCase.id == case_id).with_for_update())
    if not case:
        raise HTTPException(404, '验收案不存在')
    existing = db.scalar(select(AcceptanceReport).where(
        AcceptanceReport.case_id == case.id, AcceptanceReport.request_id == request_id))
    if existing:
        if existing.fingerprint != fingerprint:
            raise HTTPException(409, '该请求号已用于不同内容，拒收；请更换请求号重新提交')
        return existing.response
    if case.status != 'open':
        raise HTTPException(409, '验收案已封案，不再接受报期；封案结论不会被迟到记录改动')
    phase_number = data.phase if data.phase is not None else case.current_phase
    if phase_number != case.current_phase:
        if phase_number > case.current_phase:
            raise HTTPException(409, f'第 {phase_number} 期尚未开始：第 {case.current_phase} 期未定，后期等待')
        raise HTTPException(409, f'第 {phase_number} 期已结束，当前应报第 {case.current_phase} 期')
    phases = list(db.scalars(select(AcceptancePhase).where(
        AcceptancePhase.case_id == case.id).order_by(AcceptancePhase.number)))
    phase = next(row for row in phases if row.number == phase_number)
    if phase.status != 'collecting':
        raise HTTPException(409, '该期已判定，不能继续报期')
    records = _normalize(data.records, case.model)
    accepted, supplemented, skipped = _apply_records(db, case, phase, records, now)
    try:
        # Insert the samples and the request record as one unit.  A concurrent
        # report that already owns an asset or this request id fails here, so no
        # sample, allowance or conclusion is counted twice.
        db.flush()
        _recount(db, case, phase)
        if phase.enrolled > phase.size:
            raise HTTPException(
                409, f'并发报期使第 {phase.number} 期收样超过计划 {phase.size} 件，本次未生效，请刷新后重试')
        conclusion = _evaluate(db, case, phase, phases, now)
        response = {
            'request_id': request_id, 'case_id': case.id, 'phase_number': phase.number,
            'accepted_new': accepted, 'supplemented': supplemented, 'ignored_duplicates': skipped,
            'conclusion': conclusion or 'collecting',
            'message': _result_message(case, conclusion, accepted, supplemented),
            'phase_state': phase_dict(phase),
            'case_state': {'status': case.status, 'result': case.result,
                           'current_phase': case.current_phase, 'approved': case.approved},
        }
        db.add(AcceptanceReport(
            case_id=case.id, request_id=request_id, phase=phase.number, fingerprint=fingerprint,
            accepted=accepted, supplemented=supplemented, actor=user.email,
            created_at=now, response=response,
        ))
        record(db, user.email, 'acceptance.report', {
            'id': case.id, 'phase': phase.number, 'new': accepted,
            'supplemented': supplemented, 'conclusion': conclusion or 'collecting'})
        if conclusion in ('passed', 'failed'):
            record(db, user.email, 'acceptance.close', {
                'id': case.id, 'result': case.result, 'reason': case.close_reason})
        db.commit()
    except IntegrityError:
        # Two reports raced.  The first committed one owns the samples, the
        # allowance and the conclusion; neither is duplicated here.
        db.rollback()
        stored = db.scalar(select(AcceptanceReport).where(
            AcceptanceReport.case_id == case_id,
            AcceptanceReport.request_id == request_id))
        if stored and stored.fingerprint == fingerprint:
            return stored.response
        raise HTTPException(
            409, '并发报期冲突：有另一报期先占用了相同样本或该请求号，本次未生效，请刷新后重试') from None
    return response


def approve(db, case_id, user):
    case = db.scalar(select(AcceptanceCase).where(
        AcceptanceCase.id == case_id).with_for_update())
    if not case:
        raise HTTPException(404, '验收案不存在')
    if case.status != 'closed' or case.result != 'passed':
        raise HTTPException(409, '仅达标并已封案的验收案可批准分发')
    if not case.approved:
        case.approved = True
        record(db, user.email, 'acceptance.approve', {'id': case.id})
        db.commit()
    return case


def phase_dict(phase):
    return {
        'number': phase.number, 'size': phase.size, 'alpha': phase.alpha,
        'acceptance_number': phase.acceptance_number, 'status': phase.status,
        'enrolled': phase.enrolled, 'survived': phase.survived, 'failed': phase.failed,
        'pending': phase.pending, 'risk': phase.risk, 'allowance_used': phase.allowance_used,
        'evaluated_at': phase.evaluated_at,
    }


def sample_dict(sample):
    return {
        'asset': sample.asset, 'model': sample.model, 'km': sample.km,
        'failed': sample.failed, 'outcome': sample.outcome, 'phase': sample.phase,
        'reported_at': sample.reported_at, 'updated_at': sample.updated_at,
    }


def report_log_dict(report):
    response = report.response or {}
    return {
        'id': report.id, 'request_id': report.request_id, 'phase': report.phase,
        'accepted': report.accepted, 'supplemented': report.supplemented,
        'conclusion': response.get('conclusion', ''),
        'message': response.get('message', ''), 'actor': report.actor, 'at': report.created_at,
    }


def case_dict(case, phases, pending=None, reports=None, samples=None):
    ordered = sorted(phases, key=lambda row: row.number)
    current = next((row for row in ordered if row.number == case.current_phase), None)
    totals = {
        'enrolled': sum(row.enrolled for row in ordered),
        'survived': sum(row.survived for row in ordered),
        'failed': sum(row.failed for row in ordered),
        'pending': sum(row.pending for row in ordered),
        'allowance_used': round(sum(row.allowance_used for row in ordered), 6),
    }
    data = {
        'id': case.id, 'name': case.name, 'model': case.model, 'dataset_id': case.dataset_id,
        'qualified_km': case.qualified_km, 'min_survival': case.min_survival,
        'total_alpha': case.total_alpha, 'status': case.status, 'result': case.result,
        'current_phase': case.current_phase, 'approved': case.approved,
        'close_reason': case.close_reason, 'created_at': case.created_at, 'closed_at': case.closed_at,
        'created_by': case.created_by, 'phases': [phase_dict(row) for row in ordered],
        'totals': totals,
        'current_phase_state': phase_dict(current) if current else None,
        'waiting_new': case.status == 'open' and current is not None and current.enrolled < current.size,
        'waiting_supplement': case.status == 'open' and current is not None and current.enrolled >= current.size and current.pending > 0,
        'approvable': case.status == 'closed' and case.result == 'passed' and not case.approved,
        'distributable': case.status == 'closed' and case.result == 'passed' and case.approved,
    }
    if pending is not None:
        data['pending_samples'] = [sample_dict(row) for row in pending]
    if reports is not None:
        data['reports'] = [report_log_dict(row) for row in reports]
    if samples is not None:
        data['samples'] = [sample_dict(row) for row in samples]
    return data


def detail(db, case_id):
    case = db.get(AcceptanceCase, case_id)
    if not case:
        raise HTTPException(404, '验收案不存在')
    phases = db.scalars(select(AcceptancePhase).where(
        AcceptancePhase.case_id == case.id).order_by(AcceptancePhase.number)).all()
    pending = db.scalars(select(AcceptanceSample).where(
        AcceptanceSample.case_id == case.id,
        AcceptanceSample.outcome == PENDING).order_by(AcceptanceSample.phase, AcceptanceSample.id)).all()
    samples = db.scalars(select(AcceptanceSample).where(
        AcceptanceSample.case_id == case.id).order_by(AcceptanceSample.phase, AcceptanceSample.id).limit(300)).all()
    reports = db.scalars(select(AcceptanceReport).where(
        AcceptanceReport.case_id == case.id).order_by(AcceptanceReport.id)).all()
    return case_dict(case, phases, pending=pending, reports=reports, samples=samples)


def listing(db, view='all'):
    cases = db.scalars(select(AcceptanceCase).order_by(AcceptanceCase.id.desc())).all()
    phases = db.scalars(select(AcceptancePhase).order_by(
        AcceptancePhase.case_id, AcceptancePhase.number)).all()
    grouped = {}
    for phase in phases:
        grouped.setdefault(phase.case_id, []).append(phase)
    rows = [case_dict(case, grouped.get(case.id, [])) for case in cases]
    if view == 'collect':
        rows = [row for row in rows if row['waiting_new']]
    elif view == 'supplement':
        rows = [row for row in rows if row['waiting_supplement']]
    elif view == 'open':
        rows = [row for row in rows if row['status'] == 'open']
    elif view == 'passed':
        rows = [row for row in rows if row['result'] == 'passed']
    elif view == 'failed':
        rows = [row for row in rows if row['result'] == 'failed']
    elif view != 'all':
        raise HTTPException(422, '未知的验收案筛选条件')
    return rows


def case_result(case, phases):
    """Snapshot used by distribution: per-phase evidence only, no live query."""
    ordered = sorted(phases, key=lambda row: row.number)
    return {
        'summary': (f'{case.model} 的放行验收案「{case.name}」于合格里程 {case.qualified_km:,.0f} 公里、'
                    f'最低存活率 {case.min_survival:.0%} 下判定达标并封案：{case.close_reason}'),
        'metrics': [
            {'label': '合格里程', 'value': round(case.qualified_km, 1), 'unit': '公里'},
            {'label': '最低存活率', 'value': round(case.min_survival, 4)},
            {'label': '逐期误判额度合计', 'value': round(case.total_alpha, 4)},
            {'label': '已判定期数', 'value': sum(1 for row in ordered if row.status in ('passed', 'failed'))},
            {'label': '累计存活/失败/待补', 'value': f"{sum(row.survived for row in ordered)}/{sum(row.failed for row in ordered)}/{sum(row.pending for row in ordered)}"},
        ],
        'table': {
            'columns': [
                {'key': 'number', 'label': '期号'}, {'key': 'size', 'label': '计划人数'},
                {'key': 'alpha', 'label': '误判额度'}, {'key': 'acceptance_number', 'label': '判定数'},
                {'key': 'survived', 'label': '存活'}, {'key': 'failed', 'label': '提前失效'},
                {'key': 'pending', 'label': '待补报'}, {'key': 'risk', 'label': '尾部概率'},
                {'key': 'status', 'label': '判定'},
            ],
            'rows': [
                {'number': row.number, 'size': row.size, 'alpha': row.alpha,
                 'acceptance_number': row.acceptance_number, 'survived': row.survived,
                 'failed': row.failed, 'pending': row.pending,
                 'risk': '' if row.risk is None else round(row.risk, 6),
                 'status': row.status}
                for row in ordered
            ],
        },
    }
