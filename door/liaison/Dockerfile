FROM python:3.12-slim
RUN useradd --uid 10002 --no-create-home liaison
WORKDIR /app
COPY liaison.py /app/liaison.py
USER liaison
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "/app/liaison.py"]
