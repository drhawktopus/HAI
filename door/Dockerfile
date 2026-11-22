FROM python:3.12-slim
RUN pip install --no-cache-dir fastmcp==4.1.6
RUN useradd --uid 10001 --no-create-home uhura
WORKDIR /app
COPY uhura.py /app/uhura.py
COPY transcript_store.py /app/transcript_store.py
USER uhura
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "/app/uhura.py"]
