from pydantic import BaseModel, Field
from src.constants.acceptance import MAX_TOTAL_ALPHA, MAX_PHASES, MAX_PHASE_SIZE, MAX_KM, MAX_SAMPLES_PER_CASE


class PhasePlan(BaseModel):
    size: int = Field(ge=1, le=MAX_PHASE_SIZE)
    alpha: float = Field(gt=0, le=MAX_TOTAL_ALPHA)


class AcceptanceInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    model: str = Field(min_length=1, max_length=60)
    qualified_km: float = Field(gt=0, le=MAX_KM)
    min_survival: float = Field(ge=0.5, le=0.999)
    phases: list[PhasePlan] = Field(min_length=1, max_length=MAX_PHASES)
    dataset_id: int | None = None


class SampleInput(BaseModel):
    asset: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=60)
    km: float
    failed: int = Field(ge=0, le=1)


class ReportInput(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    phase: int | None = Field(default=None, ge=1, le=MAX_PHASES)
    records: list[SampleInput] = Field(min_length=1, max_length=MAX_SAMPLES_PER_CASE)
