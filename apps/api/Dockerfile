FROM python:3.12-slim
WORKDIR /workspace
COPY apps/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY apps/api /workspace/apps/api
COPY database /workspace/database
WORKDIR /workspace/apps/api
CMD ["sh", "-c", "alembic -c /workspace/database/alembic.ini upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

