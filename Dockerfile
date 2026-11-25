# Stub — CDN runtime image SoT is qtclient/portal apps/cdn/.
# bundle.yml checks that tree out and builds ghcr.io/idleot/cdn:runtime-* from it.
FROM alpine:3.20
RUN echo "CDN runtime moved to qtclient/portal apps/cdn — do not build this Dockerfile" >&2 && exit 1
