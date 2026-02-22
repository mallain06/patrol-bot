FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

VOLUME /app/data
ENV DATABASE_PATH=/app/data/patrol_stats.db

CMD ["python", "bot.py"]
