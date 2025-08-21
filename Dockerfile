# -------- base image --------
FROM python:3.11-slim

# -------- env --------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

# -------- workdir --------
WORKDIR /app

# -------- install deps --------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -------- copy app code --------
COPY app/ /app/app

# (optional) create data dir inside container;
# RUN mkdir -p $DATA_DIR

# -------- network & command --------
EXPOSE 8080
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8080"]
