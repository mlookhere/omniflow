"""OmniFlow GUI entry point."""
import os
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("OPENBLAS_NUM_THREADS","1")
from omniflow.ui import main

if __name__ == '__main__':
    raise SystemExit(main())
