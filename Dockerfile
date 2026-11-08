# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.12-slim

LABEL org.opencontainers.image.title="dgeo" \
      org.opencontainers.image.description="Acoustic geolocation from aircraft Doppler curves (proof of concept)" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/Guigouu/dgeo"

# ffmpeg: converts phone recordings (m4a, aac, opus…) to WAV (GPL/LGPL, see THIRD_PARTY.md)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dgeo ./dgeo
COPY app ./app
COPY tests ./tests
COPY LICENSE THIRD_PARTY.md ./

RUN useradd -m -u 1000 dgeo && mkdir -p /data && chown dgeo:dgeo /data
USER dgeo

ENV DGEO_DATA=/data \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/mpl \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

VOLUME /data
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app/app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.maxUploadSize=500", "--server.headless=true"]
