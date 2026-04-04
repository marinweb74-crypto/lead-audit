import smtplib, ssl
try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.mail.ru", 465, context=ctx, timeout=10) as s:
        s.login("stunagncy@mail.ru", "qznXcf4919C9Y2Cj4nm1")
        print("SMTP OK - login successful")
except Exception as e:
    print(f"SMTP ERROR: {e}")
