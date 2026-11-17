"""Pin the isolated database/artifacts before any application module is imported."""
import os
import tempfile

ROOT = tempfile.mkdtemp(prefix='acceptance-suite-')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{ROOT}/suite.db')
os.environ.setdefault('ARTIFACT_DIR', f'{ROOT}/artifacts')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.setdefault('JWT_SECRET', 'test-signing-key')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
