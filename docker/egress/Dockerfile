# Egress proxy: the only way out of the ship-sandbox network. Sandbox containers (engineer, reviewer, test runs)
# sit on an internal network with no route to the host or the internet, and reach the hosts in `allowlist` through
# this proxy. Anything else, the host's Postgres and console included, is refused.
FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf allowlist /etc/tinyproxy/
USER nobody
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
