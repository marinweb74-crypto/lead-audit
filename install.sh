#!/bin/bash
cd /root/lead-audit
python3 -m venv venv
./venv/bin/pip install aiogram telethon reportlab requests transliterate
echo "OK installed"
