FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
COPY static/ ./static/
EXPOSE 8099
CMD ["python", "-u", "server.py"]
