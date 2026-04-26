#!/bin/bash
# ARC dependency installer — Ubuntu 24.04+
set -e

echo "===================================="
echo " ARC Dependency Installer"
echo "===================================="
echo

echo "[1/3] Installing system packages (requires sudo)..."
sudo apt install -y \
    libguestfs-tools libguestfs-dev python3-guestfs \
    libhivex-bin python3-hivex \
    ntfs-3g fuse3 guestmount \
    virtinst qemu-system-x86 libvirt-daemon-system \
    sleuthkit

echo
echo "[2/3] Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo
echo "[3/3] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "===================================="
echo " Installation Complete!"
echo "===================================="
echo
echo "Next steps:"
echo "  1. source .venv/bin/activate"
echo "  2. Export GEMINI_API_KEY=<key>  (optional — only needed for --ai-generate)"
echo "  3. Dry-run test: python main.py --preset developer --dry-run -v"
echo "  4. Full run:     python main.py --vhdx ~/vms/run.vhdx --preset developer --random-seed 4242"
echo
echo "See GETTING_STARTED.md for the full workflow."
