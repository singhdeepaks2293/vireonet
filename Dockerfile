FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY policies/ ./policies/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "vireonet.main:app", "--host", "0.0.0.0", "--port", "8000"]
