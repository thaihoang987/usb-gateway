FROM alpine:3.22 AS mbusd-build

ARG MBUSD_REF=master
RUN apk add --no-cache cmake gcc git linux-headers make musl-dev && \
    git clone --depth 1 --branch "$MBUSD_REF" https://github.com/3cky/mbusd.git /src/mbusd && \
    cmake -S /src/mbusd -B /src/mbusd/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    cmake --build /src/mbusd/build --parallel && \
    cmake --install /src/mbusd/build

FROM alpine:3.22

LABEL org.opencontainers.image.title="USB Gateway" \
      org.opencontainers.image.description="Multi-port Modbus TCP/RTU and raw TCP/UART gateway for Unraid" \
      org.opencontainers.image.authors="thaihoang987" \
      org.opencontainers.image.source="https://github.com/thaihoang987/usb-gateway" \
      org.opencontainers.image.documentation="https://github.com/thaihoang987/usb-gateway#readme"

RUN apk add --no-cache python3 py3-pip && \
    mkdir -p /app /config

COPY --from=mbusd-build /usr/local/bin/mbusd /usr/local/bin/mbusd
COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY gateway_manager /app/gateway_manager
COPY static /app/static

ENV CONFIG_PATH=/config/config.json \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8098 \
    PYTHONUNBUFFERED=1

WORKDIR /app
EXPOSE 8098
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O - http://127.0.0.1:8098/api/health >/dev/null || exit 1

CMD ["python3", "-m", "gateway_manager.server"]
