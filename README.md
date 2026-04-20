# Bot de Organizacao Inteligente de E-mails

Bot em Python que conecta ao Gmail via IMAP, classifica e-mails recebidos na data atual com Gemini 1.5 Flash e move cada mensagem para uma label especifica.

## Objetivo

- Buscar e-mails recebidos hoje na INBOX.
- Classificar cada e-mail em uma categoria definida.
- Mover para a label correspondente.
- Permitir execucao automatica diaria.

## Categorias Suportadas

O classificador responde estritamente com uma destas labels:

- CONTAS
- VAGAS DE EMPREGO
- SOCIAL
- ANUNCIO
- OUTROS

## Estrutura do Projeto

- .env
- .env.example
- .gitignore
- requirements.txt
- main.py
- cronjob.txt

## Requisitos

- Python 3.10+
- Conta Gmail com IMAP habilitado
- Senha de app do Google para IMAP
- Chave da API Gemini

## Configuracao

1. Criar ambiente virtual e ativar.
2. Instalar dependencias:

```bash
pip install -r requirements.txt
```

3. Criar arquivo de ambiente com base no exemplo:

```bash
cp .env.example .env
```

No Windows PowerShell, alternativa para copia:

```powershell
Copy-Item .env.example .env
```

4. Preencher o arquivo .env:

- EMAIL_ACCOUNT
- EMAIL_APP_PASSWORD
- IMAP_HOST (default: imap.gmail.com)
- IMAP_PORT (default: 993)
- GEMINI_API_KEY
- GEMINI_MODEL (default: gemini-1.5-flash)
- DRY_RUN (true ou false)

## Execucao Manual

```bash
python main.py
```

## Fluxo de Execucao

1. Conecta no IMAP com SSL.
2. Busca e-mails com data de hoje.
3. Extrai From e Subject.
4. Envia para o Gemini classificar.
5. Garante existencia das labels.
6. Faz COPY para a label e marca original com Deleted.
7. Executa expunge ao final (quando DRY_RUN=false).

## Agendamento Diario

As instrucoes estao em cronjob.txt com exemplos para:

- Cron (Linux/macOS)
- Task Scheduler no Windows (schtasks)

## Sequencia de Commits Sugerida

1. chore: initial project structure and gitignore
2. chore: setup dependencies and environment variables example
3. feat: implement IMAP connection and email fetching
4. feat: integrate Gemini API for classification
5. feat: implement relocation logic and cronjob instructions
