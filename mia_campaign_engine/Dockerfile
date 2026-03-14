# ─────────────────────────────────────────────────────────────────────────────
# Mia Campaign Engine — Multi-purpose Docker Image
# Runs as: API server OR Celery worker (image/video)
# Base: Python 3.12 slim + FFmpeg for video generation
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# ─── System deps (FFmpeg, fonts, build tools) ─────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libfontconfig1 \
    libfreetype6 \
    libjpeg-dev \
    libpng-dev \
    libwebp-dev \
    fontconfig \
    curl \
    git \
    # For PostgreSQL driver
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ─── EB Garamond font (open source) ──────────────────────────────────────────
RUN mkdir -p /usr/share/fonts/mia && \
    curl -sL "https://github.com/octaviopardo/EBGaramond12/raw/master/fonts/EBGaramond12-Regular.ttf" \
         -o /usr/share/fonts/mia/EBGaramond-Regular.ttf && \
    curl -sL "https://github.com/octaviopardo/EBGaramond12/raw/master/fonts/EBGaramond12-Bold.ttf" \
         -o /usr/share/fonts/mia/EBGaramond-Bold.ttf 2>/dev/null || true && \
    fc-cache -f -v

# ─── App directory ────────────────────────────────────────────────────────────
WORKDIR /app

# ─── Python dependencies ──────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Application code ─────────────────────────────────────────────────────────
COPY . .

# Create required directories
RUN mkdir -p /app/uploads /app/data \
             /app/assets/fonts/garamond \
             /app/assets/fonts/gotham \
             /app/assets/fonts/fallback \
             /app/assets/image_templates \
             /app/assets/video_templates

# Remove any macOS-corrupted Gotham fonts that slipped through COPY (belt-and-suspenders)
# .dockerignore already excludes assets/fonts/gotham/ — this handles any edge case
RUN rm -rf /app/assets/fonts/gotham && mkdir -p /app/assets/fonts/gotham

# Copy EB Garamond from system into app fonts dir (clean — downloaded in Docker, not from macOS)
RUN cp /usr/share/fonts/mia/EBGaramond-Regular.ttf /app/assets/fonts/garamond/ && \
    cp /usr/share/fonts/mia/EBGaramond-Bold.ttf    /app/assets/fonts/garamond/ 2>/dev/null || true

# ─── Download Playfair Display + Lato from Google Fonts (open-source) ─────────
# These are used as brand-appropriate replacements for the commercial Gotham fonts.
# Downloaded fresh in Docker — zero risk of macOS extended-attribute corruption.
RUN mkdir -p /app/assets/fonts/playfair /app/assets/fonts/lato && \
    curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf" \
         -o /app/assets/fonts/playfair/PlayfairDisplay-Bold.ttf && \
    curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/static/PlayfairDisplay-Regular.ttf" \
         -o /app/assets/fonts/playfair/PlayfairDisplay-Regular.ttf && \
    curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/static/PlayfairDisplay-BoldItalic.ttf" \
         -o /app/assets/fonts/playfair/PlayfairDisplay-BoldItalic.ttf 2>/dev/null || true && \
    curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/lato/Lato-Regular.ttf" \
         -o /app/assets/fonts/lato/Lato-Regular.ttf && \
    curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/lato/Lato-Bold.ttf" \
         -o /app/assets/fonts/lato/Lato-Bold.ttf && \
    curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/lato/Lato-Italic.ttf" \
         -o /app/assets/fonts/lato/Lato-Italic.ttf 2>/dev/null || true && \
    echo "Playfair Display + Lato fonts downloaded OK"

# Generate default template xlsx programmatically (avoids binary file corruption during build)
RUN python3 /app/data/create_default_template.py

# Generate default JPEG image templates programmatically (avoids macOS binary corruption)
RUN python3 /app/data/create_default_images.py

# ─── Init scripts ─────────────────────────────────────────────────────────────
COPY infra/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ─── Environment ──────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FONT_GARAMOND_REGULAR=/app/assets/fonts/garamond/EBGaramond-Regular.ttf \
    FONT_GARAMOND_BOLD=/app/assets/fonts/garamond/EBGaramond-Bold.ttf \
    FONT_PLAYFAIR_BOLD=/app/assets/fonts/playfair/PlayfairDisplay-Bold.ttf \
    FONT_PLAYFAIR_REGULAR=/app/assets/fonts/playfair/PlayfairDisplay-Regular.ttf \
    FONT_LATO_REGULAR=/app/assets/fonts/lato/Lato-Regular.ttf \
    FONT_LATO_BOLD=/app/assets/fonts/lato/Lato-Bold.ttf \
    FFMPEG_PATH=ffmpeg

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
