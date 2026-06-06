"""
mailer.py -- SMTP email sender for BizWhiz
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(smtp_host, smtp_port, smtp_user, smtp_password,
               from_addr, to_addr, subject, body):
    msg = MIMEMultipart()
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    port = int(smtp_port)
    if port == 465:
        with smtplib.SMTP_SSL(smtp_host, port) as s:
            s.login(smtp_user, smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, port) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_user, smtp_password)
            s.send_message(msg)
