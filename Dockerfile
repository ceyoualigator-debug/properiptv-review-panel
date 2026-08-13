FROM python:3.12-slim
WORKDIR /app
# The snapshot must be in the image. Without it the container rebuilds the
# catalogue on every cold start and the first sign-in blocks for a minute --
# which is what App Review saw and rejected.
COPY server.py catalogue.json ./
ENV PORT=8100
EXPOSE 8100
CMD ["python", "server.py"]
