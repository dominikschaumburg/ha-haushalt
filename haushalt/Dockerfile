ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 tzdata

COPY app /app

CMD ["/command/with-contenv", "python3", "-u", "/app/server.py"]
