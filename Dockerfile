FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY whitelist.py .
COPY users.db .

CMD ["python", "bot.py"]
