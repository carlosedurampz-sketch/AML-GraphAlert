.PHONY: setup run test clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run:
	.venv/bin/python -m src.run_pipeline

test:
	.venv/bin/python -m pytest tests/ -v

clean:
	rm -rf outputs/* data/synthetic/*.csv __pycache__ src/__pycache__ tests/__pycache__ .pytest_cache
