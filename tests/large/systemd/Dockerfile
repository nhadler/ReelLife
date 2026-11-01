# A Debian host with systemd as PID 1, to test deploy/systemd for real.
FROM debian:trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus ca-certificates curl python3-pip \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
 && pip install --break-system-packages --no-cache-dir uv==0.12.17 \
 && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
