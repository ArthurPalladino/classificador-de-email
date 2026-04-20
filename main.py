import datetime
import email
import imaplib
import logging
import os
from email.header import decode_header
from typing import Iterable

import google.generativeai as genai
from dotenv import load_dotenv

VALID_LABELS = (
    "CONTAS",
    "VAGAS DE EMPREGO",
    "SOCIAL",
    "ANUNCIO",
    "OUTROS",
)

CLASSIFICATION_PROMPT = """
Você é um classificador estrito de e-mails.
Classifique o e-mail em exatamente UMA das seguintes categorias:
- CONTAS
- VAGAS DE EMPREGO
- SOCIAL
- ANUNCIO
- OUTROS

Responda somente com o nome EXATO da categoria, sem explicações.

FROM: {sender}
SUBJECT: {subject}
""".strip()


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""

    decoded_parts: list[str] = []
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            charset = encoding or "utf-8"
            decoded_parts.append(part.decode(charset, errors="replace"))
        else:
            decoded_parts.append(part)

    return "".join(decoded_parts).strip()


def extract_headers(raw_data: Iterable[tuple]) -> tuple[str, str]:
    for response_part in raw_data:
        if isinstance(response_part, tuple):
            message = email.message_from_bytes(response_part[1])
            sender = decode_mime_header(message.get("From", ""))
            subject = decode_mime_header(message.get("Subject", ""))
            return sender, subject
    return "", ""


def normalize_label(result: str) -> str:
    clean = " ".join((result or "").replace("\n", " ").strip().upper().split())
    if clean in VALID_LABELS:
        return clean
    return "OUTROS"


def classify_email(model: genai.GenerativeModel, sender: str, subject: str) -> str:
    response = model.generate_content(
        CLASSIFICATION_PROMPT.format(sender=sender, subject=subject)
    )

    result_text = getattr(response, "text", "") or ""
    if not result_text and getattr(response, "candidates", None):
        parts = response.candidates[0].content.parts
        result_text = "".join(
            getattr(part, "text", "") for part in parts if hasattr(part, "text")
        )

    return normalize_label(result_text)


def ensure_mailbox(connection: imaplib.IMAP4_SSL, mailbox: str) -> None:
    status, _ = connection.create(f'"{mailbox}"')
    if status == "OK":
        logging.info("Label criada: %s", mailbox)


def move_email_to_mailbox(
    connection: imaplib.IMAP4_SSL, message_id: bytes, mailbox: str, dry_run: bool
) -> None:
    if dry_run:
        logging.info("DRY_RUN=true: email %s seria movido para %s", message_id.decode(), mailbox)
        return

    status, _ = connection.copy(message_id, f'"{mailbox}"')
    if status != "OK":
        logging.warning("Falha ao copiar email %s para %s", message_id.decode(), mailbox)
        return

    connection.store(message_id, "+FLAGS", "\\Deleted")
    logging.info("Email %s movido para %s", message_id.decode(), mailbox)


def fetch_today_email_ids(connection: imaplib.IMAP4_SSL) -> list[bytes]:
    status, _ = connection.select("INBOX")
    if status != "OK":
        raise RuntimeError("Nao foi possivel selecionar INBOX")

    date_str = datetime.datetime.now().strftime("%d-%b-%Y")
    status, data = connection.search(None, f'(SINCE "{date_str}")')
    if status != "OK":
        raise RuntimeError("Falha ao buscar emails de hoje")

    if not data or not data[0]:
        return []

    return data[0].split()


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variavel de ambiente obrigatoria nao definida: {name}")
    return value


def run() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    email_account = get_required_env("EMAIL_ACCOUNT")
    email_password = get_required_env("EMAIL_APP_PASSWORD")
    gemini_api_key = get_required_env("GEMINI_API_KEY")

    imap_host = os.getenv("IMAP_HOST", "imap.gmail.com")
    imap_port = int(os.getenv("IMAP_PORT", "993"))
    gemini_model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    dry_run = os.getenv("DRY_RUN", "false").strip().lower() == "true"

    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel(gemini_model_name)

    connection = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        connection.login(email_account, email_password)
        logging.info("Conexao IMAP realizada com sucesso")

        message_ids = fetch_today_email_ids(connection)
        logging.info("Emails encontrados hoje: %s", len(message_ids))

        for label in VALID_LABELS:
            ensure_mailbox(connection, label)

        for message_id in message_ids:
            status, data = connection.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if status != "OK":
                logging.warning("Falha ao ler cabecalho do email %s", message_id.decode())
                continue

            sender, subject = extract_headers(data)
            label = classify_email(model, sender=sender, subject=subject)

            logging.info(
                "Email %s classificado como %s | from=%s | subject=%s",
                message_id.decode(),
                label,
                sender,
                subject,
            )

            move_email_to_mailbox(connection, message_id, label, dry_run=dry_run)

        if not dry_run:
            connection.expunge()
    finally:
        connection.logout()
        logging.info("Conexao IMAP encerrada")


if __name__ == "__main__":
    run()
