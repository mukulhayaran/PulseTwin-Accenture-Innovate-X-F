# PulseTwin build pipeline.
# OMP_NUM_THREADS=1 everywhere: OpenMP reduction order is not deterministic, and without
# it the "reproduces byte-for-byte" claim is false (ADR-016).
export OMP_NUM_THREADS = 1

PY = python

.PHONY: generate train build-artifact build-knowledge-index test dev-backend dev-frontend clean \
        migrate load-data up up-mqtt down test-all drift-check

generate:
	$(PY) -m offline.simulator.generate --config configs/plant_a.yaml
	$(PY) -m offline.simulator.generate --config configs/plant_b.yaml

train:
	$(PY) -m offline.ml.train --config configs/plant_a.yaml
	$(PY) -m offline.ml.train --config configs/plant_b.yaml

build-artifact:
	rm -f artifacts/pulsetwin.db
	$(PY) -m offline.build_artifact --config configs/plant_a.yaml
	$(PY) -m offline.build_artifact --config configs/plant_b.yaml

# ADR-046: the engineering-knowledge TF-IDF index (search_engineering_knowledge). Not
# plant-scoped, so it is built once, not per plant.
build-knowledge-index:
	$(PY) -m offline.build_knowledge_index

test:
	$(PY) -m pytest tests/ -q

dev-backend:
	$(PY) -m uvicorn serve.main:app --reload --port 7860

dev-frontend:
	cd frontend && npm run dev

clean:
	rm -rf data/ artifacts/

# ---- the stack (docs/PRODUCTION_ARCHITECTURE.md). Postgres only: ADR-052 retired the
# SQLite demo runtime; SQLite remains the build output and the test backend.

migrate:
	$(PY) -m migrations.migrate

load-data:
	$(PY) -m offline.load_to_postgres

up:
	docker compose -f docker-compose.prod.yml up --build

up-mqtt:
	docker compose -f docker-compose.prod.yml --profile mqtt-demo up --build

down:
	docker compose -f docker-compose.prod.yml down -v

test-all:
	$(PY) -m pytest tests/ -v --tb=short

drift-check:
	$(PY) -m mlops.drift_detector --config configs/plant_a.yaml
