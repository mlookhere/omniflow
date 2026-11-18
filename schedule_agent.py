"""OmniFlow background schedule agent (run on desktop login when installed)."""
import os
os.environ.setdefault("OMP_NUM_THREADS","1")
os.environ.setdefault("OPENBLAS_NUM_THREADS","1")
from omniflow.agent import main

if __name__=='__main__':
    main()
