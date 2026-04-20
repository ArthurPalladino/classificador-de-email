import datetime
import email
import json
import imaplib
import logging
import os
import unicodedata
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

LABEL_ALIASES = {
    "VAGAS": "VAGAS DE EMPREGO",
    "VAGAS EMPREGO": "VAGAS DE EMPREGO",
    "EMPREGO": "VAGAS DE EMPREGO",
    "ANUNCIOS": "ANUNCIO",
}

CATEGORY_RULES = {
    "CONTAS": {
        "sender": (
            "boleto",
            "fatura",
            "nota fiscal",
            "nfe",
            "nf-e",
            "cobranca",
            "pagamento",
            "vencimento",
            "invoice",
            "bill",
            "contas",
            "mei",
        ),
        "subject": (
            "boleto",
            "fatura",
            "nota fiscal",
            "nfe",
            "nf-e",
            "cobranca",
            "pagamento",
            "vencimento",
            "invoice",
            "bill",
            "mensalidade",
            "arrecad",
        ),
    },
    "VAGAS DE EMPREGO": {
        "sender": (
            "linkedin",
            "gupy",
            "vagas",
            "emprego",
            "job",
            "career",
            "bne",
            "recruit",
            "rh",
        ),
        "subject": (
            "vaga",
            "vagas",
            "emprego",
            "oportunidade",
            "candidatura",
            "candidato",
            "entrevista",
            "processo seletivo",
            "estagio",
            "estagiario",
            "trainee",
            "hiring",
            "contratando",
        ),
    },
    "SOCIAL": {
        "sender": (
            "linkedin",
            "facebook",
            "facebookmail",
            "instagram",
            "whatsapp",
            "tiktok",
            "x.com",
            "twitter",
            "threads",
            "social",
        ),
        "subject": (
            "mensagem",
            "notificacao",
            "notification",
            "alerta",
            "amigo",
            "perfil",
            "codigo",
            "login",
            "dispositivo",
            "entrou",
            "enviou uma mensagem",
        ),
    },
    "ANUNCIO": {
        "sender": (
            "newsletter",
            "marketing",
            "promoc",
            "oferta",
            "promo",
            "campanha",
            "emkt",
            "mailing",
        ),
        "subject": (
            "newsletter",
            "promoc",
            "oferta",
            "desconto",
            "cupom",
            "marketing",
            "campanha",
            "novidade",
            "lançamento",
            "update",
            "news",
            "black friday",
        ),
    },
}

CLASSIFICATION_PROMPT = """
Você é um classificador estrito de e-mails.
Classifique cada e-mail em exatamente UMA das seguintes categorias:
- CONTAS
- VAGAS DE EMPREGO
- SOCIAL
- ANUNCIO
- OUTROS

Responda somente com JSON válido no formato:
[{{"id": "1", "label": "CONTAS"}}]

Não inclua texto adicional.

E-mails:
{emails}

Use o campo id exatamente como fornecido em cada item.

""".strip()


def resolve_gemini_model_name(preferred_model: str) -> str:
    preferred_model = preferred_model.strip()
    preferred_full_name = (
        preferred_model
        if preferred_model.startswith("models/")
        else f"models/{preferred_model}"
    )

    try:
        available_models = list(genai.list_models())
    except Exception as exc:
        logging.warning(
            "Nao foi possivel listar modelos Gemini (%s). Usando configuracao: %s",
            exc,
            preferred_model,
        )
        return preferred_model

    compatible_models: list[str] = []
    for model_info in available_models:
        methods = set(getattr(model_info, "supported_generation_methods", []) or [])
        model_name = getattr(model_info, "name", "")
        if "generateContent" in methods and model_name:
            compatible_models.append(model_name)

    if not compatible_models:
        logging.warning(
            "Nenhum modelo com generateContent encontrado. Usando configuracao: %s",
            preferred_model,
        )
        return preferred_model

    if preferred_full_name in compatible_models:
        return preferred_full_name

    # Prioriza modelos Flash para manter custo e latencia baixos.
    priority_terms = ("gemini-2.0-flash", "gemini-1.5-flash", "flash")
    for term in priority_terms:
        for model_name in compatible_models:
            if term in model_name:
                logging.warning(
                    "Modelo configurado %s indisponivel. Usando modelo compativel: %s",
                    preferred_model,
                    model_name,
                )
                return model_name

    fallback_model = compatible_models[0]
    logging.warning(
        "Modelo configurado %s indisponivel. Usando primeiro compativel: %s",
        preferred_model,
        fallback_model,
    )
    return fallback_model


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


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(character for character in normalized if not unicodedata.combining(character))
    return " ".join(stripped.upper().split())


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
    clean = LABEL_ALIASES.get(clean, clean)
    if clean in VALID_LABELS:
        return clean
    return "OUTROS"


def classify_email_locally(sender: str, subject: str) -> str:
    sender_text = normalize_text(sender)
    subject_text = normalize_text(subject)
    combined_text = f"{sender_text} {subject_text}"

    best_label = "OUTROS"
    best_score = 0

    for label, rules in CATEGORY_RULES.items():
        score = 0
        for term in rules["sender"]:
            if term.upper() in sender_text:
                score += 2
        for term in rules["subject"]:
            if term.upper() in subject_text or term.upper() in combined_text:
                score += 1

        if score > best_score:
            best_label = label
            best_score = score

    return best_label if best_score > 0 else "OUTROS"


def build_batch_prompt(emails: list[dict[str, str]]) -> str:
    lines = []
    for item in emails:
        lines.append(
            f'- id: {item["id"]} | from: {item["sender"]} | subject: {item["subject"]}'
        )
    return CLASSIFICATION_PROMPT.format(emails="\n".join(lines))


def classify_emails_batch(
    model: genai.GenerativeModel, emails: list[dict[str, str]]
) -> dict[str, str]:
    if not emails:
        return {}

    try:
        response = model.generate_content(
            build_batch_prompt(emails),
            generation_config={"temperature": 0},
        )
    except Exception as exc:
        logging.error("Falha na classificacao Gemini em lote (%s). Aplicando OUTROS.", exc)
        return {item["id"]: "OUTROS" for item in emails}

    result_text = getattr(response, "text", "") or ""
    if not result_text and getattr(response, "candidates", None):
        parts = response.candidates[0].content.parts
        result_text = "".join(
            getattr(part, "text", "") for part in parts if hasattr(part, "text")
        )

    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        logging.warning("Resposta Gemini invalida. Aplicando OUTROS para todos os emails.")
        return {item["id"]: "OUTROS" for item in emails}

    classifications: dict[str, str] = {item["id"]: "OUTROS" for item in emails}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            email_id = str(item.get("id", "")).strip()
            label = normalize_label(str(item.get("label", "")))
            if email_id in classifications:
                classifications[email_id] = label

    return classifications


def classify_email_with_fallback(
    model: genai.GenerativeModel, sender: str, subject: str
) -> str:
    local_label = classify_email_locally(sender, subject)
    if local_label != "OUTROS":
        return local_label

    return "OUTROS"


def ensure_mailbox(connection: imaplib.IMAP4_SSL, mailbox: str) -> None:
    status, _ = connection.create(f'"{mailbox}"')
    if status == "OK":
        logging.info("Label criada: %s", mailbox)


def safe_decode_message_id(message_id: bytes) -> str:
    return message_id.decode(errors="ignore")


def move_email_to_mailbox(
    connection: imaplib.IMAP4_SSL, message_id: bytes, mailbox: str, dry_run: bool
) -> None:
    if dry_run:
        logging.info(
            "DRY_RUN=true: email %s seria movido para %s",
            safe_decode_message_id(message_id),
            mailbox,
        )
        return

    status, _ = connection.copy(message_id, f'"{mailbox}"')
    if status != "OK":
        logging.warning(
            "Falha ao copiar email %s para %s",
            safe_decode_message_id(message_id),
            mailbox,
        )
        return

    status, _ = connection.store(message_id, "+FLAGS", "\\Deleted")
    if status != "OK":
        logging.warning(
            "Falha ao marcar email %s como Deleted",
            safe_decode_message_id(message_id),
        )
        return

    logging.info("Email %s movido para %s", safe_decode_message_id(message_id), mailbox)


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
    resolved_model_name = resolve_gemini_model_name(gemini_model_name)
    logging.info("Modelo Gemini em uso: %s", resolved_model_name)
    model = genai.GenerativeModel(resolved_model_name)

    connection = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        connection.login(email_account, email_password)
        logging.info("Conexao IMAP realizada com sucesso")

        message_ids = fetch_today_email_ids(connection)
        logging.info("Emails encontrados hoje: %s", len(message_ids))

        for label in VALID_LABELS:
            ensure_mailbox(connection, label)

        emails_to_classify: list[dict[str, str]] = []
        for message_id in message_ids:
            status, data = connection.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if status != "OK":
                logging.warning(
                    "Falha ao ler cabecalho do email %s",
                    safe_decode_message_id(message_id),
                )
                continue

            sender, subject = extract_headers(data)

            emails_to_classify.append(
                {
                    "id": safe_decode_message_id(message_id),
                    "sender": sender,
                    "subject": subject,
                }
            )

        classifications: dict[str, str] = {}
        ambiguous_emails: list[dict[str, str]] = []

        for email_info in emails_to_classify:
            local_label = classify_email_locally(email_info["sender"], email_info["subject"])
            if local_label != "OUTROS":
                classifications[email_info["id"]] = local_label
            else:
                ambiguous_emails.append(email_info)

        classifications.update(classify_emails_batch(model, ambiguous_emails))

        for email_info in emails_to_classify:
            label = classifications.get(email_info["id"], "OUTROS")
            print(
                f"De: {email_info['sender']} | Assunto: {email_info['subject']} | Classificacao: {label}"
            )
            move_email_to_mailbox(
                connection,
                email_info["id"].encode(),
                label,
                dry_run=dry_run,
            )

        if not dry_run:
            connection.expunge()
    finally:
        connection.logout()
        logging.info("Conexao IMAP encerrada")


if __name__ == "__main__":
    run()
