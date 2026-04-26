# Dockerfile for ARC - Artifact Reality Composer
# Ubuntu 24.04 base with libguestfs, hivex, and Python dependencies

FROM ubuntu:24.04

LABEL maintainer="ARC Development Team"
LABEL description="Artifact Reality Composer - Windows 11 VM Personalization"
LABEL version="0.9.0-alpha"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Install system dependencies
RUN apt-get update && apt-get install -y \
    # Core dependencies
    libguestfs-tools \
    python3-guestfs \
    libhivex-bin \
    python3-hivex \
    ntfs-3g \
    fuse3 \
    guestmount \
    # Python
    python3 \
    python3-pip \
    python3-venv \
    # Utilities
    git \
    curl \
    wget \
    vim \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt /app/

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY core/ /app/core/
COPY services/ /app/services/
COPY data/ /app/data/
COPY scripts/ /app/scripts/
COPY examples/ /app/examples/
COPY main.py /app/
COPY config.yaml /app/

# Copy tests (optional, for development)
COPY tests/ /app/tests/
COPY pytest.ini /app/

# Make scripts executable
RUN chmod +x /app/scripts/*.sh

# Create directories for VHDX files and output
RUN mkdir -p /vhdx /output /tmp/arc

# Set up libguestfs appliance
RUN update-guestfs-appliance || true

# Create non-root user for running ARC
RUN useradd -m -s /bin/bash arcuser && \
    chown -R arcuser:arcuser /app /vhdx /output /tmp/arc

# Switch to non-root user
USER arcuser

# Set working directory
WORKDIR /app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import sys; sys.path.insert(0, '/app'); from core import linux_mount; print('OK')" || exit 1

# Default command
ENTRYPOINT ["python3", "main.py"]
CMD ["--help"]

# Usage examples:
# Build: docker build -t arc:latest .
# Run: docker run --rm --privileged -v /path/to/vhdx:/vhdx -v /path/to/output:/output arc:latest --vhdx /vhdx/baseline.vhdx --profile office_user
# Interactive: docker run --rm -it --privileged -v /path/to/vhdx:/vhdx arc:latest /bin/bash
