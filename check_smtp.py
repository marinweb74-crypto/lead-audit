import smtplib, ssl, json, os
cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(cfg_path, "r") as f:
    smtp = json.load(f)["smtp"]
try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp["server"], smtp["port"], context=ctx, timeout=10) as s:
        s.login(smtp["email"], smtp["password"])
        print("SMTP OK - login successful")
except Exception as e:
    print(f"SMTP ERROR: {e}")
