import smtplib, ssl, json
from email.mime.text import MIMEText

with open("config.json") as f:
    cfg = json.load(f)

smtp = cfg["smtp"]
msg = MIMEText("Test from VPS", "plain", "utf-8")
msg["From"] = f'{smtp["from_name"]} <{smtp["email"]}>'
msg["To"] = smtp["email"]
msg["Subject"] = "Test SMTP from VPS"

ctx = ssl.create_default_context()
s = smtplib.SMTP_SSL(smtp["server"], smtp["port"], context=ctx)
s.login(smtp["email"], smtp["password"])
s.sendmail(smtp["email"], smtp["email"], msg.as_string())
s.quit()
print("OK! Email sent.")
