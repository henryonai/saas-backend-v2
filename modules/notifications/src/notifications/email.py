"""
Email sending behind an interface. The container picks SES in AWS, or a
log-only sender when no verified sender is configured.

In AWS: verify your sender domain in SES and request production access
(new SES accounts can only email verified addresses).
"""
from typing import Protocol

from aws_lambda_powertools import Logger

logger = Logger(child=True)


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SesEmailSender:
    def __init__(self, client, sender: str):
        self.client = client
        self.sender = sender

    def send(self, to: str, subject: str, body: str) -> None:
        self.client.send_email(
            Source=self.sender,
            Destination={"ToAddresses": [to]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
        )


class LogOnlyEmailSender:
    def send(self, to: str, subject: str, body: str) -> None:
        logger.info("Email (not sent, no EMAIL_SENDER)", extra={"to": to, "subject": subject})
