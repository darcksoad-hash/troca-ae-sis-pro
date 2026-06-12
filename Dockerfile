FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV APP_ENV=production
ENV RUN_STARTUP_TASKS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY outputs/troca-ae-sis-pro-app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY outputs/troca-ae-sis-pro-app/ ./

CMD ["python", "server.py"]
