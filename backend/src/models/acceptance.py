from sqlalchemy import String, Double, Integer, ForeignKey, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class AcceptanceCase(Base):
    """A phased release-acceptance case for one component model.

    The qualified mileage, minimum survival rate, phase sizes and each phase's
    misjudgment allowance are frozen when the case is opened; the allowances add
    up to no more than the configured upper limit.  A case is closed only when a
    phase meets the standard or when every phase has been exhausted.
    """
    __tablename__ = 'acceptance_cases'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    model: Mapped[str] = mapped_column(String(60))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    qualified_km: Mapped[float] = mapped_column(Double)
    min_survival: Mapped[float] = mapped_column(Double)
    total_alpha: Mapped[float] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(20), default='open')
    result: Mapped[str] = mapped_column(String(20), default='')
    current_phase: Mapped[int] = mapped_column(Integer, default=1)
    approved: Mapped[bool] = mapped_column(default=False)
    close_reason: Mapped[str] = mapped_column(String(300), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    closed_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class AcceptancePhase(Base):
    __tablename__ = 'acceptance_phases'
    __table_args__ = (UniqueConstraint('case_id', 'number', name='uq_acceptance_phase_number'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey('acceptance_cases.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    alpha: Mapped[float] = mapped_column(Double)
    acceptance_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk: Mapped[float | None] = mapped_column(Double, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    enrolled: Mapped[int] = mapped_column(Integer, default=0)
    survived: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    pending: Mapped[int] = mapped_column(Integer, default=0)
    tail_probability: Mapped[float | None] = mapped_column(Double, nullable=True)
    allowance_used: Mapped[float] = mapped_column(Double, default=0.0)
    evaluated_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class AcceptanceSample(Base):
    """One physical asset takes part in a case at most once.

    ``outcome`` is pending while an in-service unit has not reached the
    qualified mileage; such a unit is kept (never excluded) and may be reported
    again.  Once resolved, the outcome is final.
    """
    __tablename__ = 'acceptance_samples'
    __table_args__ = (
        UniqueConstraint('case_id', 'asset', name='uq_acceptance_sample_asset'),
        Index('ix_acceptance_sample_case_phase', 'case_id', 'phase'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey('acceptance_cases.id'), index=True)
    asset: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(60))
    km: Mapped[float] = mapped_column(Double)
    failed: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))
    phase: Mapped[int] = mapped_column(Integer)
    reported_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class AcceptanceReport(Base):
    """One submitted report round, replayable by ``request_id``.

    ``request_id`` is unique per case, so a retry after an interruption returns
    the original outcome instead of enrolling samples twice.  The response is
    stored verbatim to make that replay exact.
    """
    __tablename__ = 'acceptance_reports'
    __table_args__ = (UniqueConstraint('case_id', 'request_id', name='uq_acceptance_report_request'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey('acceptance_cases.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(80))
    phase: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    supplemented: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str] = mapped_column(String(150), default='')
    created_at: Mapped[float] = mapped_column(Double)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
