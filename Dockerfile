FROM hub.hamdocker.ir/python:3.9-slim

WORKDIR /app

COPY . /app/

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1


CMD ["sh", "-c", "kopf run --standalone /app/mysql_operator.py"]