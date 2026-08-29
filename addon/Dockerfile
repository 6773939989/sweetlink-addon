ARG BUILD_FROM
FROM $BUILD_FROM

# This tags our image with the correct tags for HA
LABEL \
  io.hass.version="VERSION" \
  io.hass.type="addon" \
  io.hass.arch="armhf|aarch64|i386|amd64|armv7"

# Since we target the python docker images, there's no need to install python.

# Copy our PY requirements and install them.
COPY requirements.txt /

# Install build dependencies before Python packages so native wheels (e.g. aiohttp, zstandard) compile successfully.
# Note we don't install PY 3 so it doesn't exit and we don't accidentally use it instead of the base image python.
RUN apk add --no-cache zstd python3-dev gcc zlib-dev musl-dev libffi-dev g++ openssl-dev cargo make

RUN pip install --upgrade pip setuptools wheel Cython
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# Install the optional packages for zstandard compression. The extra packages are required for the zstandard python pip install to build.
# THIS VERSION STRING MUST STAY IN SYNC with Compression.ZStandardPipPackageString
RUN pip install --no-cache-dir -q "zstandard>=0.21.0,<0.23.0"

# Install curl to download cloudflared
RUN apk add --no-cache curl

# ── cloudflared: VERSIONE FISSA E IMPRONTA VERIFICATA ─────────────────────────────────
#
# COSA C'ERA PRIMA. Il binario si scaricava da .../releases/latest/... e si rendeva eseguibile
# senza guardarlo. Due cose diverse, tutte e due gravi.
#
# La prima: "latest" cambia da solo. Due immagini costruite a distanza di una settimana
# contengono binari diversi, e nessuno lo sa. Se una versione di cloudflared rompe qualcosa, non
# c'e' niente a cui tornare indietro perche' non c'e' scritto da nessuna parte cosa c'era prima.
#
# La seconda, che e' la ragione per cui la voce stava fra le due gravi del documento di sicurezza:
# quel binario gira da root dentro un container che ha la rete dell'host, il ruolo manager sul
# Supervisor e la scrittura sulla configurazione di Home Assistant. Qualunque cosa arrivasse da
# quell'indirizzo veniva eseguita su ogni hub della flotta. Non serve immaginare un attacco
# sofisticato: basta un giorno storto nella catena di distribuzione di qualcun altro.
#
# CLOUDFLARE NON PUBBLICA CHECKSUM. Verificato sul loro elenco dei rilasci: 26 file allegati alla
# versione 2026.8.2, nessuno di impronte. Quindi le impronte qui sotto le abbiamo calcolate noi
# scaricando i binari, e ricalcolate da un secondo scaricamento per accorgerci se il canale
# servisse due byte diversi. Non e' una firma dell'autore ed e' bene dirlo: e' un blocco. Dice
# "questo e' esattamente il file che abbiamo guardato", non "questo file viene da Cloudflare".
#
# PER AGGIORNARE cloudflared: si cambia la versione, si scaricano i due binari, si calcola
# sha256sum, e si scrivono qui le impronte nuove. Se si cambia solo la versione la costruzione
# fallisce, ed e' voluto: e' l'unica cosa che impedisce di tornare per distrazione a prendere per
# buono quello che arriva.
ARG BUILD_ARCH
ARG CLOUDFLARED_VERSION=2026.8.2
ARG CLOUDFLARED_SHA256_AMD64=fcfb02b575a52ca1af2e3267af4e1517bcdeb30ac48c834c69abaed3c0576ad2
ARG CLOUDFLARED_SHA256_ARM64=7747d94570fb390cf47dcb4f9555c193c6355cda9793f0d878d9049e5d6a7790
RUN set -eux && \
    if [ "${BUILD_ARCH}" = "aarch64" ]; then \
        CF_ARCH="arm64"; CF_SHA="${CLOUDFLARED_SHA256_ARM64}"; \
    elif [ "${BUILD_ARCH}" = "amd64" ]; then \
        CF_ARCH="amd64"; CF_SHA="${CLOUDFLARED_SHA256_AMD64}"; \
    else \
        echo "Architettura non prevista: ${BUILD_ARCH}. config.yaml ne dichiara due, aarch64 e amd64." >&2; \
        exit 1; \
    fi && \
    curl -fsSL --retry 3 --output /tmp/cloudflared \
        "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${CF_ARCH}" && \
    echo "${CF_SHA}  /tmp/cloudflared" | sha256sum -c - && \
    mv /tmp/cloudflared /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared && \
    /usr/local/bin/cloudflared --version

# Create a working dir for all of our files.
WORKDIR /app

# Copy all of our files in.
COPY . ./

# Helpful for debugging.
RUN ls
RUN whoami

#
# Since we are running in a docker container, there's no need for dynamic configs.
# The repo root is /app, as we define in our Dockerfile
# The storage root it the Home Assistant docker mapped dir, /data/
#   Note that all of the data is stored in this flat dir, the config, storage data, and logs.
#
# This is the json config, that's base64 encoded and sent as an argument.
# { "VersionFileDir":"/app", "AddonDataRootDir":"/data", "StorageDir":"/data", "LogsDir":"/data", "IsRunningInHaAddonEnv":true }
#
# Note if we every move past "python3", we need to update apparmor.
#CMD [ "python3", "-m", "sweetlink_linuxhost", "eyAiVmVyc2lvbkZpbGVEaXIiOiIvYXBwIiwgIkFkZG9uRGF0YVJvb3REaXIiOiIvZGF0YSIsICJTdG9yYWdlRGlyIjoiL2RhdGEiLCAiTG9nc0RpciI6Ii9kYXRhIiwgIklzUnVubmluZ0luSGFBZGRvbkVudiI6dHJ1ZSB9" ]

# Set the run script exectuable.
RUN chmod a+x /app/run.sh

# Create a command to launch the run script.
CMD [ "/app/run.sh" ]

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Sweetplace" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Sweetplace" \
    org.opencontainers.image.authors="Sweetplace" \
    org.opencontainers.image.licenses="AGPL-3.0-only" \
    org.opencontainers.image.url="https://github.com/6773939989/sweetlink-addon" \
    org.opencontainers.image.source="https://github.com/6773939989/sweetlink-addon" \
    org.opencontainers.image.documentation="https://github.com/6773939989/sweetlink-addon/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
