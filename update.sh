#!/bin/bash

echo "Updating Iñaki..."
git pull origin main

echo "Installing dependencies..."
.venv/bin/pip install -e .

echo "Restarting Iñaki..."
sleep 2
sudo systemctl restart inaki

echo "Iñaki updated and restarted."
echo "If you want to see the logs, run: journalctl -u inaki -f"
