FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY configs ./configs
COPY rag_agent ./rag_agent
COPY report ./report
COPY scripts ./scripts
COPY tests ./tests
COPY Makefile README.md run_eval.py ./

RUN mkdir -p data outputs

CMD ["python", "run_eval.py", "--config", "configs/level2.yaml", "--llm-judge"]
