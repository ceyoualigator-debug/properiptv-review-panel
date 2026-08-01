FROM python:3.12-slim
WORKDIR /app
COPY server.py .
ENV PORT=8100
EXPOSE 8100
CMD ["python", "server.py"]
