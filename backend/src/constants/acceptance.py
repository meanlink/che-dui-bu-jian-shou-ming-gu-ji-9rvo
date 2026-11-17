"""Locked rules for phased release acceptance.

The misjudgment allowance is the probability of declaring a batch qualified
when its true survival rate is only at (or below) the minimum.  Each phase gets
a share of the total allowance; the shares are committed when the case is
opened and may never add up to more than the upper limit.
"""
import os

MAX_TOTAL_ALPHA = float(os.getenv('ACCEPTANCE_MAX_TOTAL_ALPHA', '0.2'))
MAX_PHASES = int(os.getenv('ACCEPTANCE_MAX_PHASES', '12'))
MAX_PHASE_SIZE = int(os.getenv('ACCEPTANCE_MAX_PHASE_SIZE', '1000'))
MAX_SAMPLES_PER_CASE = int(os.getenv('ACCEPTANCE_MAX_SAMPLES', '5000'))
MAX_KM = 10_000_000.0
