import json
import os
import re
import urllib.parse
import base64
import hmac
import hashlib
import time
from datetime import datetime
import boto3
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Initialize AWS clients
ses_client = boto3.client('ses')

# Global cache for Slack secrets
slack_secrets_cache = {}

def get_slack_secrets():
    """Retrieves Slack Bot Token and Signing Secret from AWS Secrets Manager."""
    global slack_secrets_cache
    if slack_secrets_cache:
        return slack_secrets_cache
        
    # For testing / local development fallback
    signing_secret_env = os.environ.get('SLACK_SIGNING_SECRET')
    bot_token_env = os.environ.get('SLACK_BOT_TOKEN')
    if signing_secret_env is not None or bot_token_env is not None:
        return {
            'slack_signing_secret': signing_secret_env or '',
            'slack_bot_token': bot_token_env or ''
        }
        
    secret_name = os.environ.get('SLACK_CREDS_SECRET_NAME', 'pragma-tickets-cloud-token-slack')
    secrets_client = boto3.client('secretsmanager')
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        if 'SecretString' in response:
            slack_secrets_cache = json.loads(response['SecretString'])
        else:
            slack_secrets_cache = json.loads(base64.b64decode(response['SecretBinary']).decode('utf-8'))
        return slack_secrets_cache
    except Exception as e:
        print(f"Error fetching Slack secrets from Secrets Manager: {e}")
        return {}

def verify_slack_signature(headers, body, signing_secret):
    """Verifies that the request actually came from Slack."""
    if not signing_secret:
        print("Warning: SLACK_SIGNING_SECRET is not configured. Skipping signature verification.")
        return True
        
    slack_signature = headers.get('x-slack-signature') or headers.get('X-Slack-Signature')
    slack_timestamp = headers.get('x-slack-request-timestamp') or headers.get('X-Slack-Request-Timestamp')
    
    if not slack_signature or not slack_timestamp:
        print("Verification failed: Missing Slack signature or timestamp headers.")
        return False
        
    # Check for replay attacks (timestamp within 5 minutes)
    if abs(time.time() - int(slack_timestamp)) > 60 * 5:
        print("Verification failed: Request timestamp is older than 5 minutes.")
        return False
        
    sig_basestring = f"v0:{slack_timestamp}:{body}"
    my_signature = 'v0=' + hmac.new(
        signing_secret.encode('utf-8'),
        sig_basestring.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(my_signature, slack_signature)

def clean_slack_markup(val):
    """Cleans Slack formatting tags like <mailto:email|email>, <@U123|name>, <url|label>."""
    if not val:
        return ""
    # Clean mailto links: <mailto:email@domain.com|email@domain.com> -> email@domain.com
    val = re.sub(r'<mailto:([^>|]+)(?:\|[^>]+)?>', r'\1', val)
    # Clean Slack user mentions: <@U12345|username> -> username, or <@U12345> -> U12345
    val = re.sub(r'<@[A-Z0-9]+\|([^>]+)>', r'\1', val)
    val = re.sub(r'<@([A-Z0-9]+)>', r'\1', val)
    # Clean Slack channel mentions: <#C123|name> -> name, or <#C123> -> C123
    val = re.sub(r'<#[A-Z0-9]+\|([^>]+)>', r'\1', val)
    val = re.sub(r'<#([A-Z0-9]+)>', r'\1', val)
    # Clean generic URLs: <http://url|label> -> http://url
    val = re.sub(r'<(https?://[^>|]+)(?:\|[^>]+)?>', r'\1', val)
    # Strip brackets
    val = val.replace('<', '').replace('>', '')
    return val.strip()

def parse_message_fields(message_blocks):
    """Parses Slack message blocks to extract ticket details using regex and keywords."""
    fields = {
        'titulo': 'N/A',
        'proyecto': 'N/A',
        'ambiente': 'N/A',
        'cuenta_aws': 'N/A',
        'solicitante': 'N/A',
        'lider_aprobador': 'N/A',
        'link_iac': 'N/A',
        'descripcion': 'N/A',
        'correo_solicitante': 'N/A',
        'correo_lider': 'N/A'
    }
    
    # Concatenate all text from all blocks to search through
    full_text = ""
    for block in message_blocks:
        if block.get('type') == 'section' and 'text' in block:
            full_text += block['text'].get('text', '') + "\n"
        elif block.get('type') == 'context':
            for element in block.get('elements', []):
                if element.get('type') == 'mrkdwn':
                    full_text += element.get('text', '') + "\n"
    
    # Strip markdown stars for easier regex parsing
    clean_text = full_text.replace('*', '')
    print(f"Parsed Message Cleaned Text:\n{clean_text}")
    
    # Regex search patterns (case insensitive)
    patterns = {
        'titulo': [
            r'Título\s*:\s*([^\n]+)', r'Título\s*\n\s*([^\n]+)',
            r'Titulo\s*:\s*([^\n]+)', r'Titulo\s*\n\s*([^\n]+)',
            r'Subject\s*:\s*([^\n]+)', r'Subject\s*\n\s*([^\n]+)',
            r'Title\s*:\s*([^\n]+)', r'Title\s*\n\s*([^\n]+)'
        ],
        'proyecto': [
            r'Proyecto\s*:\s*([^\n]+)', r'Proyecto\s*\n\s*([^\n]+)',
            r'Project\s*:\s*([^\n]+)', r'Project\s*\n\s*([^\n]+)'
        ],
        'ambiente': [
            r'Ambiente\s*:\s*([^\n]+)', r'Ambiente\s*\n\s*([^\n]+)',
            r'Environment\s*:\s*([^\n]+)', r'Environment\s*\n\s*([^\n]+)'
        ],
        'cuenta_aws': [
            r'Cuenta AWS/Proyecto Azure\s*:\s*([^\n]+)', r'Cuenta AWS/Proyecto Azure\s*\n\s*([^\n]+)',
            r'Cuenta AWS\s*:\s*([^\n]+)', r'Cuenta AWS\s*\n\s*([^\n]+)',
            r'Proyecto Azure\s*:\s*([^\n]+)', r'Proyecto Azure\s*\n\s*([^\n]+)'
        ],
        'solicitante': [
            r'Solicitante\s*:\s*([^\n]+)', r'Solicitante\s*\n\s*([^\n]+)',
            r'Requester\s*:\s*([^\n]+)', r'Requester\s*\n\s*([^\n]+)',
            r'Creador\s*:\s*([^\n]+)', r'Creador\s*\n\s*([^\n]+)'
        ],
        'lider_aprobador': [
            r'Líder Aprobador\s*:\s*([^\n]+)', r'Líder Aprobador\s*\n\s*([^\n]+)',
            r'Lider Aprobador\s*:\s*([^\n]+)', r'Lider Aprobador\s*\n\s*([^\n]+)',
            r'Aprobador\s*:\s*([^\n]+)', r'Aprobador\s*\n\s*([^\n]+)',
            r'Approver\s*:\s*([^\n]+)', r'Approver\s*\n\s*([^\n]+)'
        ],
        'link_iac': [
            r'Link de IaC/PR\s*:\s*([^\n]+)', r'Link de IaC/PR\s*\n\s*([^\n]+)',
            r'Link IaC/PR\s*:\s*([^\n]+)', r'Link IaC/PR\s*\n\s*([^\n]+)',
            r'Link\s*:\s*([^\n]+)', r'Link\s*\n\s*([^\n]+)',
            r'PR\s*:\s*([^\n]+)', r'PR\s*\n\s*([^\n]+)'
        ],
        'descripcion': [
            r'Descripción del requerimiento\s*:\s*([^\n]+)', r'Descripción del requerimiento\s*\n\s*([^\n]+)',
            r'Descripción\s*:\s*([^\n]+)', r'Descripción\s*\n\s*([^\n]+)',
            r'Descripcion\s*:\s*([^\n]+)', r'Descripcion\s*\n\s*([^\n]+)',
            r'Description\s*:\s*([^\n]+)', r'Description\s*\n\s*([^\n]+)'
        ],
        'correo_solicitante': [
            r'Correo del Solicitante\s*:\s*([^\n]+)', r'Correo del Solicitante\s*\n\s*([^\n]+)',
            r'Correo Solicitante\s*:\s*([^\n]+)', r'Correo Solicitante\s*\n\s*([^\n]+)',
            r'Email Solicitante\s*:\s*([^\n]+)', r'Email Solicitante\s*\n\s*([^\n]+)',
            r'Correo Requester\s*:\s*([^\n]+)', r'Correo Requester\s*\n\s*([^\n]+)'
        ],
        'correo_lider': [
            r'Correo del Líder\s*:\s*([^\n]+)', r'Correo del Líder\s*\n\s*([^\n]+)',
            r'Correo del Lider\s*:\s*([^\n]+)', r'Correo del Lider\s*\n\s*([^\n]+)',
            r'Correo Líder\s*:\s*([^\n]+)', r'Correo Líder\s*\n\s*([^\n]+)',
            r'Correo Lider\s*:\s*([^\n]+)', r'Correo Lider\s*\n\s*([^\n]+)',
            r'Email Líder\s*:\s*([^\n]+)', r'Email Líder\s*\n\s*([^\n]+)',
            r'Email Lider\s*:\s*([^\n]+)', r'Email Lider\s*\n\s*([^\n]+)'
        ]
    }
    
    for key, regexes in patterns.items():
        for regex in regexes:
            match = re.search(regex, clean_text, re.IGNORECASE)
            if match:
                val = match.group(1).strip()
                fields[key] = clean_slack_markup(val)
                break
                
    return fields

def get_google_credentials(secret_name):
    """Retrieves Google API credentials from AWS Secrets Manager."""
    secrets_client = boto3.client('secretsmanager')
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        if 'SecretString' in response:
            return json.loads(response['SecretString'])
        else:
            return json.loads(base64.b64decode(response['SecretBinary']).decode('utf-8'))
    except Exception as e:
        print(f"Error fetching Google API credentials from Secrets Manager: {e}")
        raise e

def append_to_google_sheet(creds_dict, spreadsheet_id, sheet_range, row_data):
    """Appends a new row to the specified Google Sheet."""
    try:
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        service = build('sheets', 'v4', credentials=creds)
        sheet = service.spreadsheets()
        
        body = {
            'values': [row_data]
        }
        
        result = sheet.values().append(
            spreadsheetId=spreadsheet_id,
            range=sheet_range,
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        
        print(f"Google Sheet update response: {result}")
        return result
    except Exception as e:
        print(f"Error appending to Google Sheet: {e}")
        raise e

def send_manageengine_email(sender, recipient, fields):
    """Sends a formatted email to ManageEngine via AWS SES."""
    # Clean fields using clean_slack_markup
    proyecto = clean_slack_markup(fields.get('proyecto', 'N/A'))
    ambiente = clean_slack_markup(fields.get('ambiente', 'N/A'))
    titulo = clean_slack_markup(fields.get('titulo', 'N/A'))
    cuenta_aws = clean_slack_markup(fields.get('cuenta_aws', 'N/A'))
    descripcion = clean_slack_markup(fields.get('descripcion', 'N/A'))
    link_iac = clean_slack_markup(fields.get('link_iac', 'N/A'))
    solicitante = clean_slack_markup(fields.get('solicitante', 'N/A'))
    lider_aprobador = clean_slack_markup(fields.get('lider_aprobador', 'N/A'))
    aprobado_por = clean_slack_markup(fields.get('aprobado_por', 'N/A'))
    fecha = clean_slack_markup(fields.get('fecha', 'N/A'))

    subject = f"{titulo} : {proyecto} - {ambiente}"

    def clean_email(email_str):
        if not email_str or email_str.strip().upper() == 'N/A':
            return None
        match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', email_str)
        if match:
            return match.group(0)
        return None

    # Parse leader email
    lider_email_raw = fields.get('correo_lider') or fields.get('lider_aprobador') or ""
    lider_email_clean = clean_email(clean_slack_markup(lider_email_raw)) or ""
    lider_username = lider_email_clean.split('@')[0] if '@' in lider_email_clean else lider_email_clean
    if not lider_username:
        lider_username = lider_aprobador

    # Parse requester (practitioner) email
    solicitante_email_raw = fields.get('correo_solicitante') or fields.get('solicitante') or ""
    solicitante_email_clean = clean_email(clean_slack_markup(solicitante_email_raw)) or ""
    solicitante_username = solicitante_email_clean.split('@')[0] if '@' in solicitante_email_clean else solicitante_email_clean
    if not solicitante_username:
        solicitante_username = solicitante

    # Generate friendly display name for leader (ASCII normalized to prevent SES encoding errors)
    lider_name = lider_username.replace('.', ' ').title()
    import unicodedata
    def strip_accents(text):
        return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    
    lider_name_ascii = strip_accents(lider_name)
    actual_sender = lider_email_clean if (lider_email_clean and lider_email_clean.endswith('@pragma.com.co')) else sender
    ses_source = f"{lider_name_ascii} <{actual_sender}>" if lider_name_ascii else actual_sender

    # Text fallback body
    body_text = f"""@@OPERATION=Create Request@@
@@REQUESTER={solicitante_username}@@
@@SUBJECT={subject}@@
@@Título={titulo}@@
@@Proyecto={proyecto}@@
@@Ambiente={ambiente}@@
@@Cuenta AWS/Proyecto Azure={cuenta_aws}@@
@@Link IaC/PR={link_iac}@@
@@Líder Aprobador={lider_email_clean}@@
@@DESCRIPTION=Se ha aprobado el requerimiento de ticket.
Detalles:
- Título: {titulo}
- Proyecto: {proyecto}
- Ambiente: {ambiente}
- Cuenta AWS/Proyecto Azure: {cuenta_aws}
- Solicitante: {solicitante}
- Link de IaC/PR: {link_iac}
- Descripción: {descripcion}
- Líder Aprobador: {lider_aprobador}
- Aprobado Por: {aprobado_por}
- Fecha de Aprobación: {fecha}
@@
"""

    # HTML premium body
    html_body = f"""@@OPERATION=Create Request@@
@@REQUESTER={solicitante_username}@@
@@SUBJECT={subject}@@
@@Título={titulo}@@
@@Proyecto={proyecto}@@
@@Ambiente={ambiente}@@
@@Cuenta AWS/Proyecto Azure={cuenta_aws}@@
@@Link IaC/PR={link_iac}@@
@@Líder Aprobador={lider_email_clean}@@

----------------------------------------------------------------------

<h2>🚀 Solicitud de Infraestructura Aprobada</h2>
<p>Se ha generado un nuevo ticket automatizado desde Slack tras recibir la aprobación del líder de proyecto.</p>

<table style="width:100%; border-collapse: collapse; font-family: Arial, sans-serif;">
  <tr style="background-color: #f2f2f2;">
    <th style="padding: 10px; border: 1px solid #ddd; text-align: left; width: 30%;">Campo</th>
    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Detalle</th>
  </tr>
  <tr>
    <td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">Proyecto</td>
    <td style="padding: 10px; border: 1px solid #ddd; color: #000000; font-weight: bold;">{proyecto}</td>
  </tr>
  <tr>
    <td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">Ambiente</td>
    <td style="padding: 10px; border: 1px solid #ddd;"><span style="background-color: #ffe082; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{ambiente}</span></td>
  </tr>
  <tr>
    <td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">Cuenta AWS/Proyecto Azure</td>
    <td style="padding: 10px; border: 1px solid #ddd;">{cuenta_aws}</td>
  </tr>
  <tr>
    <td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">Título del Requerimiento</td>
    <td style="padding: 10px; border: 1px solid #ddd;">{titulo}</td>
  </tr>
  <tr>
    <td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">Descripción Técnico/Funcional</td>
    <td style="padding: 10px; border: 1px solid #ddd; font-style: italic;">"{descripcion}"</td>
  </tr>
</table>

<br>

<h3 style="color: #43a047;">🔒 Trazabilidad y Gobernanza</h3>
<ul style="font-family: Arial, sans-serif; line-height: 1.6;">
  <li><strong>Solicitante original:</strong> {solicitante}</li>
  <li><strong>Líder Aprobador asignado:</strong> {lider_aprobador}</li>
  <li><strong>Aprobado físicamente por:</strong> {aprobado_por}</li>
  <li><strong>Fecha/Hora de la firma:</strong> {fecha}</li>
</ul>

<hr style="border: 0; border-top: 1px solid #eee;">
<p style="font-size: 11px; color: #999;">Este es un correo automático generado por el Slack Workflow de CloudOps. Por favor editar el solicitante del ticket, por el lider aprobador.</p>
"""

    # Parse destination and CC addresses
    destination = {'ToAddresses': [recipient]}
    cc_addresses = []

    email_solicitante = clean_email(clean_slack_markup(fields.get('correo_solicitante', '')))
    
    if email_solicitante:
        cc_addresses.append(email_solicitante)
    if lider_email_clean:
        cc_addresses.append(lider_email_clean)
        
    if cc_addresses:
        cc_addresses = list(set(cc_addresses))
        destination['CcAddresses'] = cc_addresses
        print(f"Adding CC addresses: {cc_addresses}")

    reply_to = [lider_email_clean] if lider_email_clean else [sender]

    try:
        response = ses_client.send_email(
            Source=ses_source,
            Destination=destination,
            ReplyToAddresses=reply_to,
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {
                    'Text': {'Data': body_text, 'Charset': 'UTF-8'},
                    'Html': {'Data': html_body, 'Charset': 'UTF-8'}
                }
            }
        )
        print(f"SES email sent successfully, MessageId: {response['MessageId']}")
        return response
    except Exception as e:
        print(f"Error sending SES email: {e}")
        raise e

def update_slack_message(response_url, original_message, text_summary):
    """Updates the Slack message to replace the buttons with a text confirmation."""
    blocks = original_message.get("blocks", [])
    new_blocks = []
    
    for block in blocks:
        # Exclude or replace the action buttons
        if block.get("type") == "actions":
            new_blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": text_summary
                    }
                ]
            })
        else:
            new_blocks.append(block)
            
    response_body = {
        "replace_original": "true",
        "blocks": new_blocks
    }
    
    headers = {'Content-Type': 'application/json'}
    try:
        res = requests.post(response_url, json=response_body, headers=headers)
        print(f"Slack update response code: {res.status_code}")
        return res.status_code
    except Exception as e:
        print(f"Error updating Slack message: {e}")
        return None

def calculate_default_aws_account(proyecto, ambiente):
    """Calculates default AWS Account/Azure Project based on Project + Environment."""
    proj = str(proyecto).strip().upper()
    env = str(ambiente).strip().upper()
    
    # Map environment names
    is_dev = env in ["DESARROLLO", "DEV", "DEVELOPMENT"]
    is_qa = env in ["QA", "PRUEBAS", "TEST"]
    is_prod = env in ["PRODUCCIÓN", "PRODUCCION", "PROD", "PDN"]
    
    if proj in ["PL", "ET"] and is_dev:
        return "Pragma Reserva"
    elif proj == "ET" and is_prod:
        return "Pragma Reserva"
    elif proj == "PL" and is_prod:
        return "Plataforma de ingreso prod"
    elif proj == "CRECI" and is_dev:
        return "Mapa de crecimiento dev (nueva org)"
    elif proj == "CRECI" and is_qa:
        return "Mapa de crecimiento dev (antigua org)"
    elif proj == "CRECI" and is_prod:
        return "Mapa de crecimiento dev (antigua org)"
    elif proj == "SWAT" and is_dev:
        return "Operations dev"
    elif proj == "SWAT" and is_prod:
        return "Operations pdn"
    elif proj == "IA-EVA" and is_dev:
        return "Pragma Intelligence Dev"
    elif proj == "IA-EVA" and is_prod:
        return "Pragma intelligence Prod"
        
    return None

def lambda_handler(event, context):
    print(f"Received Event: {json.dumps(event)}")
    
    # 1. Parse request body
    body = event.get('body', '')
    is_base64 = event.get('isBase64Encoded', False)
    if is_base64:
        body = base64.b64decode(body).decode('utf-8')
        
    headers = event.get('headers', {})
    
    # 2. Verify request signature (Slack security best practice)
    slack_secrets = get_slack_secrets()
    slack_secret = slack_secrets.get('slack_signing_secret', '')
    if not verify_slack_signature(headers, body, slack_secret):
        return {
            'statusCode': 401,
            'body': json.dumps({'error': 'Unauthorized: Invalid Slack signature'})
        }
        
    # Detect if body is direct JSON (e.g. Slack Workflow Builder Web Request)
    is_direct_json = False
    json_data = {}
    try:
        json_data = json.loads(body)
        if isinstance(json_data, dict) and 'payload' not in json_data:
            is_direct_json = True
    except Exception:
        pass

    if is_direct_json:
        print("Direct JSON Payload detected (Slack Workflow Builder).")
        fields = {
            'titulo': clean_slack_markup(json_data.get('titulo', 'N/A')),
            'proyecto': clean_slack_markup(json_data.get('proyecto', 'N/A')),
            'ambiente': clean_slack_markup(json_data.get('ambiente', 'N/A')),
            'solicitante': clean_slack_markup(json_data.get('solicitante', 'N/A')),
            'lider_aprobador': clean_slack_markup(json_data.get('lider_aprobador', 'N/A')),
            'link_iac': clean_slack_markup(json_data.get('link_iac', 'N/A')),
            'descripcion': clean_slack_markup(json_data.get('descripcion', 'N/A')),
            'correo_solicitante': clean_slack_markup(json_data.get('correo_solicitante', 'N/A')),
            'correo_lider': clean_slack_markup(json_data.get('correo_lider', 'N/A'))
        }
        
        clicker_id = json_data.get('aprobador_real_id', '')
        clicker_username = json_data.get('aprobador_real', '')
        clicker_name = json_data.get('aprobador_real', '')
        
        accion_str = json_data.get('accion', '').lower()
        is_approve = accion_str in ['approved', 'aprobado', 'aprobar']
        is_reject = accion_str in ['rejected', 'rechazado', 'rechazar']
        
        response_url = None
        original_message = {}
        
        print(f"Clicker: Username/Name='{clicker_username}', ID='{clicker_id}'")
        print(f"Action: Action='{accion_str}', is_approve={is_approve}, is_reject={is_reject}")
    else:
        # Slack interactive components or slash command send payload as url-encoded
        parsed_body = urllib.parse.parse_qs(body)
        
        # A. Handle Slash Command (Practitioner requests form)
        if 'command' in parsed_body:
            command = parsed_body['command'][0]
            trigger_id = parsed_body['trigger_id'][0]
            channel_id = parsed_body['channel_id'][0]
            user_id = parsed_body['user_id'][0]
            user_name = parsed_body['user_name'][0]
            
            print(f"Slash command received: {command} from user {user_id} in channel {channel_id}")
            
            bot_token = slack_secrets.get('slack_bot_token', '')
            if not bot_token:
                print("Error: slack_bot_token not found in secrets.")
                return {
                    'statusCode': 500,
                    'body': 'Error: Slack OAuth configurations are missing.'
                }
                
            modal_view = {
                "type": "modal",
                "callback_id": "deploy_request_modal",
                "private_metadata": channel_id,
                "title": {
                    "type": "plain_text",
                    "text": "Solicitud de Ticket"
                },
                "submit": {
                    "type": "plain_text",
                    "text": "Enviar"
                },
                "close": {
                    "type": "plain_text",
                    "text": "Cancelar"
                },
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "titulo_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "titulo_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Escribe el título de la solicitud"
                            }
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Título"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "proyecto_block",
                        "element": {
                            "type": "static_select",
                            "action_id": "proyecto_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Selecciona el proyecto"
                            },
                            "options": [
                                {"text": {"type": "plain_text", "text": "PL"}, "value": "PL"},
                                {"text": {"type": "plain_text", "text": "Creci"}, "value": "Creci"},
                                {"text": {"type": "plain_text", "text": "Swat"}, "value": "Swat"},
                                {"text": {"type": "plain_text", "text": "ET"}, "value": "ET"},
                                {"text": {"type": "plain_text", "text": "Datos"}, "value": "Datos"},
                                {"text": {"type": "plain_text", "text": "Mission Match"}, "value": "Mission Match"},
                                {"text": {"type": "plain_text", "text": "Ia-Eva"}, "value": "Ia-Eva"}
                            ]
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Proyecto"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "ambiente_block",
                        "element": {
                            "type": "static_select",
                            "action_id": "ambiente_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Selecciona el ambiente"
                            },
                            "options": [
                                {
                                    "text": {"type": "plain_text", "text": "Desarrollo (Dev)"},
                                    "value": "Desarrollo"
                                },
                                {
                                    "text": {"type": "plain_text", "text": "Pruebas (QA)"},
                                    "value": "QA"
                                },
                                {
                                    "text": {"type": "plain_text", "text": "Producción (Prod)"},
                                    "value": "Producción"
                                },
                                {
                                    "text": {"type": "plain_text", "text": "Azure"},
                                    "value": "Azure"
                                },
                                {
                                    "text": {"type": "plain_text", "text": "GCP"},
                                    "value": "GCP"
                                }
                            ]
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Ambiente"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "solicitante_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "solicitante_input",
                            "initial_value": user_name,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Nombre del practicante"
                            }
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Nombre del Solicitante"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "correo_solicitante_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "correo_solicitante_input",
                            "initial_value": f"{user_name}@pragma.com.co",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "practicante@pragma.com.co"
                            }
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Correo del Solicitante"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "cuenta_aws_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "cuenta_aws_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Ej. 298782619489 o Proyecto-Azure-xyz (Opcional)"
                            }
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Cuenta AWS/Proyecto Azure"
                        },
                        "optional": True
                    },
                    {
                        "type": "input",
                        "block_id": "lider_aprobador_block",
                        "element": {
                            "type": "static_select",
                            "action_id": "lider_aprobador_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Selecciona el líder aprobador"
                            },
                            "options": [
                                {
                                    "text": {"type": "plain_text", "text": "Jorge Mesa (jmesa@pragma.com.co)"},
                                    "value": "jmesa@pragma.com.co"
                                },
                                {
                                    "text": {"type": "plain_text", "text": "Pedro López (pedro.lopez@pragma.com.co)"},
                                    "value": "pedro.lopez@pragma.com.co"
                                },
                                {
                                    "text": {"type": "plain_text", "text": "Santiago Guerrero (santiago.guerrero@pragma.com.co)"},
                                    "value": "santiago.guerrero@pragma.com.co"
                                }
                            ]
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Líder Aprobador"
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "descripcion_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "descripcion_input",
                            "multiline": True,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Describe el requerimiento"
                            }
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Descripción del requerimiento"
                        }
                    }
                ]
            }
            
            headers = {
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8"
            }
            res = requests.post(
                "https://slack.com/api/views.open",
                headers=headers,
                json={"trigger_id": trigger_id, "view": modal_view}
            )
            print(f"views.open response: {res.status_code} - {res.text}")
            
            return {
                'statusCode': 200,
                'body': ''
            }

        # B. Handle Interactive Component Actions or Modal Submission
        if 'payload' not in parsed_body:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Invalid request: payload or command parameter is missing'})
            }
            
        try:
            payload = json.loads(parsed_body['payload'][0])
        except Exception as e:
            print(f"Error parsing JSON payload: {e}")
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Invalid JSON payload: {str(e)}'})
            }
            
        payload_type = payload.get('type')
        
        # B1. Handle Modal Submission (view_submission)
        if payload_type == 'view_submission':
            view = payload.get('view', {})
            values = view.get('state', {}).get('values', {})
            callback_id = view.get('callback_id')
            
            if callback_id == 'deploy_request_modal':
                titulo = values.get('titulo_block', {}).get('titulo_input', {}).get('value', 'N/A')
                
                proyecto_opt = values.get('proyecto_block', {}).get('proyecto_input', {}).get('selected_option')
                proyecto = proyecto_opt.get('value', 'N/A') if proyecto_opt else 'N/A'
                
                ambiente_opt = values.get('ambiente_block', {}).get('ambiente_input', {}).get('selected_option')
                ambiente = ambiente_opt.get('value', 'N/A') if ambiente_opt else 'N/A'
                
                solicitante = values.get('solicitante_block', {}).get('solicitante_input', {}).get('value', 'N/A')
                correo_solicitante = values.get('correo_solicitante_block', {}).get('correo_solicitante_input', {}).get('value', 'N/A')
                
                cuenta_aws_val = values.get('cuenta_aws_block', {}).get('cuenta_aws_input', {}).get('value')
                if not cuenta_aws_val or cuenta_aws_val.strip() == "" or cuenta_aws_val.strip().upper() == "N/A":
                    calculated_aws = calculate_default_aws_account(proyecto, ambiente)
                    cuenta_aws = calculated_aws if calculated_aws else (cuenta_aws_val or 'N/A')
                else:
                    cuenta_aws = cuenta_aws_val
                
                lider_opt = values.get('lider_aprobador_block', {}).get('lider_aprobador_input', {}).get('selected_option')
                lider_id = lider_opt.get('value', 'N/A') if lider_opt else 'N/A'
                correo_lider = lider_id
                
                link_iac = 'N/A'
                descripcion = values.get('descripcion_block', {}).get('descripcion_input', {}).get('value', 'N/A')
                
                target_channel = os.environ.get('SLACK_CHANNEL_ID') or view.get('private_metadata')
                bot_token = slack_secrets.get('slack_bot_token', '')
                
                if not bot_token or not target_channel:
                    print(f"Error: Missing configuration. bot_token_present={bool(bot_token)}, target_channel={target_channel}")
                    return {
                        'statusCode': 200,
                        'body': ''
                    }
                    
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Título:* {titulo}\n*Proyecto:* {proyecto}\n*Ambiente:* {ambiente}\n*Cuenta AWS/Proyecto Azure:* {cuenta_aws}\n*Solicitante:* {solicitante}\n*Líder Aprobador:* {lider_id}\n*Descripción:* {descripcion}\n*Correo Solicitante:* {correo_solicitante}\n*Correo Líder:* {correo_lider}"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "approve_button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Aprobar"
                                },
                                "style": "primary",
                                "value": "approved"
                            },
                            {
                                "type": "button",
                                "action_id": "reject_button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Rechazar"
                                },
                                "style": "danger",
                                "value": "rejected"
                            }
                        ]
                    }
                ]
                
                headers = {
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8"
                }
                payload_msg = {
                    "channel": target_channel,
                    "text": f"Nueva solicitud de ticket: {titulo}",
                    "blocks": blocks
                }
                res = requests.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=headers,
                    json=payload_msg
                )
                print(f"chat.postMessage response: {res.status_code} - {res.text}")
                
                return {
                    'statusCode': 200,
                    'body': ''
                }
                
            return {
                'statusCode': 200,
                'body': ''
            }
            
        # B2. Handle Interactive Button Clicks (block_actions)
        # Get interactive action parameters
        actions = payload.get('actions', [])
        if not actions:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'No actions found in payload'})
            }
            
        action = actions[0]
        action_value = action.get('value', '')
        action_id = action.get('action_id', '')
        
        # Check if the button pressed is Approve or Reject
        is_approve = "approve" in action_id.lower() or "approved" in action_value.lower() or "aprobar" in action.get("text", {}).get("text", "").lower()
        is_reject = "reject" in action_id.lower() or "rejected" in action_value.lower() or "rechazar" in action.get("text", {}).get("text", "").lower()
        
        clicker = payload.get('user', {})
        clicker_id = clicker.get('id', '')
        clicker_username = clicker.get('username', '')
        clicker_name = clicker.get('name', '')
        
        response_url = payload.get('response_url')
        original_message = payload.get('message', {})
        
        print(f"Clicker: ID={clicker_id}, Username={clicker_username}, Name={clicker_name}")
        print(f"Action: ID={action_id}, Value={action_value}, is_approve={is_approve}, is_reject={is_reject}")
        
        # Parse fields from message content
        fields = parse_message_fields(original_message.get('blocks', []))
    
    # 4. Security Validation (Ensure clicker is authorized)
    # 4. Security Validation (Ensure clicker is authorized)
    allowed_leaders = ['jmesa', 'pedro.lopez', 'santiago.guerrero']
    
    clicker_username_lower = clicker_username.lower()
    clicker_name_lower = clicker_name.lower()
    
    clicker_is_allowed_leader = False
    for leader in allowed_leaders:
        if clicker_username_lower == leader:
            clicker_is_allowed_leader = True
            break
        # Fuzzy matching for clicker name/username
        clean_leader = re.sub(r'[^a-z0-9]', '', leader)
        clean_clicker_username = re.sub(r'[^a-z0-9]', '', clicker_username_lower)
        clean_clicker_name = re.sub(r'[^a-z0-9]', '', clicker_name_lower)
        if clean_leader == clean_clicker_username or clean_leader == clean_clicker_name:
            clicker_is_allowed_leader = True
            break
            
    is_authorized = clicker_is_allowed_leader
    print(f"Authorization evaluation: is_authorized={is_authorized} (Clicker: '{clicker_username}', Allowed: {clicker_is_allowed_leader})")
    
    if not is_authorized:
        # Inform the clicker they are not authorized in an ephemeral response
        warning_msg = f"❌ Lo siento <@{clicker_id}>, no estás autorizado para aprobar o rechazar este requerimiento. Debe ser aprobado por uno de los Líderes autorizados (Jorge Mesa, Pedro López o Santiago Guerrero)."
        # Return ephemeral response to Slack
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'response_type': 'ephemeral',
                'replace_original': False,
                'text': warning_msg
            })
        }
        
    # Handle Rejection Flow
    if is_reject:
        feedback_text = f"❌ *Requerimiento Rechazado* por <@{clicker_id}> el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        if response_url:
            update_slack_message(response_url, original_message, feedback_text)
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Rejection registered and Slack message updated.'})
        }
        
    if not is_approve:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Unsupported action.'})
        }
        
    # Handle Approval Flow
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fields['fecha'] = now_str
    fields['aprobado_por'] = f"{clicker_username} ({clicker_id})"
    
    # A. Register in Google Sheets
    sheet_id = os.environ.get('GOOGLE_SHEETS_ID')
    sheet_range = os.environ.get('GOOGLE_SHEETS_RANGE', 'Sheet1!A:J')
    google_secret_name = os.environ.get('GOOGLE_CREDS_SECRET_NAME')
    
    if sheet_id and google_secret_name:
        try:
            creds = get_google_credentials(google_secret_name)
            
            # Clean emails from slack wrappers if present (e.g., <mailto:user@pragma.com.co|user@pragma.com.co>)
            sol_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', fields.get('correo_solicitante', ''))
            email_solicitante_clean = sol_match.group(0) if sol_match else fields.get('correo_solicitante', 'N/A')
            
            lider_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', fields.get('correo_lider', ''))
            email_lider_clean = lider_match.group(0) if lider_match else fields.get('correo_lider', 'N/A')
            
            # Data row matching columns:
            # A: ID -> ""
            # B: status -> "Aprobado"
            # C: team -> fields['proyecto']
            # D: user -> fields['correo_solicitante']
            # E: Supervisor -> fields['correo_lider']
            # F: subject -> fields['titulo']
            # G: Messsage -> fields['descripcion']
            # H: ""
            # I: ""
            # J: start_date -> fields['fecha']
            row_data = [
                "", 
                "Aprobado", 
                fields.get('proyecto', 'N/A'), 
                email_solicitante_clean, 
                email_lider_clean, 
                fields.get('titulo', 'N/A'), 
                fields.get('descripcion', 'N/A'), 
                "", 
                "", 
                fields['fecha']
            ]
            append_to_google_sheet(creds, sheet_id, sheet_range, row_data)
        except Exception as e:
            print(f"Skipping Sheet logging due to error: {e}")
    else:
        print("Warning: GOOGLE_SHEETS_ID or GOOGLE_CREDS_SECRET_NAME environment variables are missing. Skipping Sheets update.")
        
    # B. Send Ticket Creation Email to ManageEngine via SES
    ses_sender = os.environ.get('SES_SENDER_EMAIL')
    cloudops_email = os.environ.get('CLOUDOPS_EMAIL', 'cloudops@pragma.com.co')
    
    if ses_sender:
        try:
            send_manageengine_email(ses_sender, cloudops_email, fields)
        except Exception as e:
            print(f"Skipping Email notification due to error: {e}")
    else:
        print("Warning: SES_SENDER_EMAIL environment variable is missing. Skipping ManageEngine ticket creation.")
        
    # C. Update Slack Message Thread
    feedback_text = f"✅ *Requerimiento Aprobado* por <@{clicker_id}> el {now_str}"
    if response_url:
        update_slack_message(response_url, original_message, feedback_text)
        
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Approval processed successfully.'})
    }
