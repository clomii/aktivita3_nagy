.PHONY: data eval quick test demo web-demo docker-build docker-eval docker-quick docker-test docker-demo docker-web-demo

data:
	python scripts/materialize_corpus.py

eval:
	python run_eval.py --config configs/level2.yaml --llm-judge

quick:
	python run_eval.py --config configs/level2.yaml --quick

test:
	python -m unittest discover tests

demo:
	python -m rag_agent.demo

web-demo:
	python -m rag_agent.web_demo --host 127.0.0.1 --port 8080

docker-build:
	docker build -t eu-ai-gdpr-agent:latest .

docker-eval:
	docker compose run --rm app

docker-quick:
	docker compose --profile tools run --rm quick

docker-test:
	docker compose --profile tools run --rm test

docker-demo:
	docker compose --profile tools run --rm demo

docker-web-demo:
	docker compose --profile tools up web-demo
