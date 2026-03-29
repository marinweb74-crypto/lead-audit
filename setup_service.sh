#!/bin/bash
cp /root/lead-audit/leadaudit.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable leadaudit
systemctl start leadaudit
systemctl status leadaudit
