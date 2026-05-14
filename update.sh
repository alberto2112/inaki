#!/bin/bash

echo "Updating Inaki..."
git pull origin main

echo "Installing dependencies..."
.venv/bin/pip install -e .

echo "Restarting Inaki..."
sleep 2
sudo systemctl restart inaki

echo "Inaki updated and restarted."
echo "If you want to see the logs, run: journalctl -u inaki -f"