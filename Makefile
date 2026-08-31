# PulseTwin build pipeline.
# OMP_NUM_THREADS=1 everywhere: OpenMP reduction order is not deterministic, and without
# it the "reproduces byte-for-byte" claim is false (ADR-016).
export OMP_NUM_THREADS = 1

PY = python

.PHONY: generate train build-artifact test demo dev-backend dev-frontend clean

generate:
	$(PY) -m offline.simulator.generate --config configs/plant_a.yaml
	$(PY) -m offline.simulator.generate --config configs/plant_b.yaml

test:
	$(PY) -m pytest tests/ -q

dev-backend:
	$(PY) -m uvicorn serve.main:app --reload --port 7860

dev-frontend:
	cd frontend && npm run dev

clean:
	rm -rf data/ artifacts/
