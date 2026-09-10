FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN pip install --upgrade pip && \
    pip install -r requirements.lock

COPY stock.py ./
COPY web.py ./
COPY stocklib ./stocklib
COPY static ./static
COPY cache ./cache

EXPOSE 8888

CMD ["python", "-X", "utf8", "stock.py", "--web", "--port", "8888"]