import sys
import os
import json
import urllib.parse
import unittest
from unittest.mock import MagicMock, patch

import sys
from unittest.mock import MagicMock

# Mock out external libraries in sys.modules before importing lambda_function
sys.modules['boto3'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['google.oauth2'] = MagicMock()
sys.modules['google.oauth2.service_account'] = MagicMock()
sys.modules['googleapiclient'] = MagicMock()
sys.modules['googleapiclient.discovery'] = MagicMock()

# Ensure the lambda directory is in the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'lambda'))

import lambda_function

class TestSlackTicketsLambda(unittest.TestCase):

    def setUp(self):
        # Configure test environment variables
        os.environ['SLACK_SIGNING_SECRET'] = '' # Disable signature check for testing
        os.environ['AUTHORIZED_LEADERS'] = 'U999999,admin.user'
        os.environ['GOOGLE_SHEETS_ID'] = 'test-sheets-id-123'
        os.environ['GOOGLE_SHEETS_RANGE'] = 'Sheet1!A:E'
        os.environ['GOOGLE_CREDS_SECRET_NAME'] = 'test-creds-secret'
        os.environ['SES_SENDER_EMAIL'] = 'sender@pragma.com.co'
        os.environ['CLOUDOPS_EMAIL'] = 'cloudops@pragma.com.co'

        # Sample message blocks matching our expected Slack layout
        self.sample_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Título:* Requerimiento de prueba\n*Proyecto:* Talent-Portal-App\n*Ambiente:* Dev-Stage\n*Solicitante:* <@U111111|juan.perez>\n*Líder Aprobador:* Santiago Guerrero\n*Link de IaC/PR:* <https://github.com/pragma/iac-repo/pull/12|Ver PR>\n*Descripción:* Creación de cola SQS y bucket S3 para optimización de colas\n*Correo Solicitante:* juan.perez@pragma.com.co\n*Correo Líder:* santiago.guerrero@pragma.com.co"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve_button",
                        "text": {"type": "plain_text", "text": "Aprobar"},
                        "value": "approved"
                    },
                    {
                        "type": "button",
                        "action_id": "reject_button",
                        "text": {"type": "plain_text", "text": "Rechazar"},
                        "value": "rejected"
                    }
                ]
            }
        ]

    def create_slack_event(self, clicker_id, clicker_username, clicker_name, action_value="approved", action_id="approve_button"):
        payload = {
            "type": "block_actions",
            "user": {
                "id": clicker_id,
                "username": clicker_username,
                "name": clicker_name
            },
            "response_url": "https://hooks.slack.com/actions/T123/mock-response-url",
            "actions": [
                {
                    "action_id": action_id,
                    "value": action_value,
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Aprobar" if action_value == "approved" else "Rechazar"}
                }
            ],
            "message": {
                "type": "message",
                "blocks": self.sample_blocks
            }
        }
        # Slack payload is sent as x-www-form-urlencoded
        body = urllib.parse.urlencode({"payload": json.dumps(payload)})
        return {
            "body": body,
            "isBase64Encoded": False,
            "headers": {
                "content-type": "application/x-www-form-urlencoded"
            }
        }

    def test_parse_message_fields(self):
        """Test parsing helper correctly extracts variables from Slack message format."""
        fields = lambda_function.parse_message_fields(self.sample_blocks)
        self.assertEqual(fields['titulo'], 'Requerimiento de prueba')
        self.assertEqual(fields['proyecto'], 'Talent-Portal-App')
        self.assertEqual(fields['ambiente'], 'Dev-Stage')
        self.assertEqual(fields['solicitante'], 'juan.perez') # Extracted from '<@U111111|juan.perez>'
        self.assertEqual(fields['lider_aprobador'], 'Santiago Guerrero')
        self.assertEqual(fields['link_iac'], 'https://github.com/pragma/iac-repo/pull/12')
        self.assertEqual(fields['descripcion'], 'Creación de cola SQS y bucket S3 para optimización de colas')
        self.assertEqual(fields['correo_solicitante'], 'juan.perez@pragma.com.co')
        self.assertEqual(fields['correo_lider'], 'santiago.guerrero@pragma.com.co')

    @patch('lambda_function.update_slack_message')
    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    @patch('lambda_function.get_google_credentials')
    def test_approve_by_assigned_leader(self, mock_get_creds, mock_append, mock_email, mock_update):
        """Test that the assigned leader can successfully approve."""
        # Clicker matches "Santiago Guerrero" (fuzzy match logic)
        event = self.create_slack_event(
            clicker_id="U123456", 
            clicker_username="santiago.guerrero", 
            clicker_name="Santiago Guerrero"
        )
        
        mock_get_creds.return_value = {"client_email": "mock-email"}
        mock_update.return_value = 200

        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        
        # Verify sheets logic called
        mock_get_creds.assert_called_once_with('test-creds-secret')
        mock_append.assert_called_once()
        
        # Verify email sent
        mock_email.assert_called_once()
        
        # Verify Slack UI updated
        mock_update.assert_called_once()
        update_text = mock_update.call_args[0][2]
        self.assertIn("Aprobado", update_text)
        self.assertIn("U123456", update_text)

    @patch('lambda_function.update_slack_message')
    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    @patch('lambda_function.get_google_credentials')
    def test_approve_by_assigned_leader_email(self, mock_get_creds, mock_append, mock_email, mock_update):
        """Test that the leader matches even if they are selected as an email address (e.g. jmesa@pragma.com.co)."""
        blocks_with_email = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Proyecto:* creci\n*Ambiente:* Producción\n*Solicitante:* Juan Perez\n*Líder Aprobador:* <mailto:jmesa@pragma.com.co|jmesa@pragma.com.co>\n*Descripción:* Se cayó creci\n*Correo Solicitante:* juan.perez@pragma.com.co\n*Correo Líder:* jmesa@pragma.com.co"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve_button",
                        "text": {"type": "plain_text", "text": "Aprobar"},
                        "value": "approved"
                    }
                ]
            }
        ]
        payload = {
            "type": "block_actions",
            "user": {
                "id": "U_JMESA",
                "username": "jmesa",
                "name": "Jorge Mesa"
            },
            "response_url": "https://hooks.slack.com/actions/T123/mock-response-url",
            "actions": [
                {
                    "action_id": "approve_button",
                    "value": "approved",
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Aprobar"}
                }
            ],
            "message": {
                "type": "message",
                "blocks": blocks_with_email
            }
        }
        body = urllib.parse.urlencode({"payload": json.dumps(payload)})
        event = {
            "body": body,
            "isBase64Encoded": False,
            "headers": {"content-type": "application/x-www-form-urlencoded"}
        }
        
        mock_get_creds.return_value = {"client_email": "mock-email"}
        mock_update.return_value = 200

        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        mock_append.assert_called_once()
        mock_email.assert_called_once()
        mock_update.assert_called_once()

    @patch('lambda_function.update_slack_message')
    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    @patch('lambda_function.get_google_credentials')
    def test_approve_by_other_authorized_leader(self, mock_get_creds, mock_append, mock_email, mock_update):
        """Test that an authorized leader not listed as assigned leader can approve."""
        # Clicker is Pedro López (who is in allowed leaders list)
        event = self.create_slack_event(
            clicker_id="U777777", 
            clicker_username="pedro.lopez", 
            clicker_name="Pedro Lopez"
        )
        
        mock_get_creds.return_value = {"client_email": "mock-email"}
        mock_update.return_value = 200

        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        
        # Verify sheets & email triggered
        mock_append.assert_called_once()
        mock_email.assert_called_once()

    @patch('lambda_function.update_slack_message')
    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    def test_unauthorized_user(self, mock_append, mock_email, mock_update):
        """Test that an unauthorized user receives an ephemeral error message and doesn't trigger integrations."""
        event = self.create_slack_event(
            clicker_id="U888888", 
            clicker_username="random.developer", 
            clicker_name="Random Dev"
        )

        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200) # Slack expects 200 for ephemeral warnings
        
        body = json.loads(response['body'])
        self.assertEqual(body['response_type'], 'ephemeral')
        self.assertIn("no estás autorizado", body['text'])
        
        # Verify integrations are NOT called
        mock_append.assert_not_called()
        mock_email.assert_not_called()
        mock_update.assert_not_called()

    @patch('lambda_function.update_slack_message')
    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    def test_reject_by_leader(self, mock_append, mock_email, mock_update):
        """Test that a leader can reject and it stops further flows while updating Slack UI."""
        event = self.create_slack_event(
            clicker_id="U123456", 
            clicker_username="santiago.guerrero", 
            clicker_name="Santiago Guerrero",
            action_value="rejected",
            action_id="reject_button"
        )
        
        mock_update.return_value = 200

        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        
        # Verify integrations are NOT called for rejection
        mock_append.assert_not_called()
        mock_email.assert_not_called()
        
        # Verify Slack UI updated with Rejection
        mock_update.assert_called_once()
        update_text = mock_update.call_args[0][2]
        self.assertIn("Rechazado", update_text)
        self.assertIn("U123456", update_text)

    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    @patch('lambda_function.get_google_credentials')
    def test_direct_json_approval(self, mock_get_creds, mock_append, mock_email):
        """Test that direct JSON from Slack Workflow Builder executes successfully for assigned leader."""
        payload = {
            "titulo": "Mock Titulo",
            "proyecto": "Talent-App",
            "ambiente": "Dev",
            "solicitante": "Juan Perez",
            "lider_aprobador": "Santiago Guerrero",
            "link_iac": "https://github.com/...",
            "descripcion": "Requerimiento de prueba",
            "correo_solicitante": "juan.perez@pragma.com.co",
            "correo_lider": "santiago.guerrero@pragma.com.co",
            "aprobador_real": "Santiago Guerrero",
            "accion": "aprobado"
        }
        event = {
            "body": json.dumps(payload),
            "isBase64Encoded": False,
            "headers": {"content-type": "application/json"}
        }
        
        mock_get_creds.return_value = {"client_email": "mock-email"}
        
        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        mock_append.assert_called_once()
        mock_email.assert_called_once()
        
        # Verify email function was called with correct cc email parameters
        fields_passed = mock_email.call_args[0][2]
        self.assertEqual(fields_passed['correo_solicitante'], 'juan.perez@pragma.com.co')
        self.assertEqual(fields_passed['correo_lider'], 'santiago.guerrero@pragma.com.co')

    @patch('lambda_function.send_manageengine_email')
    @patch('lambda_function.append_to_google_sheet')
    def test_direct_json_unauthorized(self, mock_append, mock_email):
        """Test that direct JSON from unauthorized clicker gets blocked."""
        payload = {
            "proyecto": "Talent-App",
            "ambiente": "Dev",
            "solicitante": "Juan Perez",
            "lider_aprobador": "Santiago Guerrero",
            "link_iac": "https://github.com/...",
            "descripcion": "Requerimiento de prueba",
            "aprobador_real": "Random Person",
            "accion": "aprobado"
        }
        event = {
            "body": json.dumps(payload),
            "isBase64Encoded": False,
            "headers": {"content-type": "application/json"}
        }
        
        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        body = json.loads(response['body'])
        self.assertIn("no estás autorizado", body['text'])
        mock_append.assert_not_called()
        mock_email.assert_not_called()

    def test_clean_slack_markup(self):
        """Test clean_slack_markup properly cleans various Slack markups."""
        self.assertEqual(lambda_function.clean_slack_markup("<mailto:santiago.guerrero@pragma.com.co|santiago.guerrero@pragma.com.co>"), "santiago.guerrero@pragma.com.co")
        self.assertEqual(lambda_function.clean_slack_markup("<@U093K3SJGTW|santiago.guerrero>"), "santiago.guerrero")
        self.assertEqual(lambda_function.clean_slack_markup("<@U093K3SJGTW>"), "U093K3SJGTW")
        self.assertEqual(lambda_function.clean_slack_markup("<https://github.com/PR|Ver PR>"), "https://github.com/PR")
        self.assertEqual(lambda_function.clean_slack_markup("santiago.guerrero"), "santiago.guerrero")
        self.assertEqual(lambda_function.clean_slack_markup("N/A"), "N/A")

    def test_send_manageengine_email_cc(self):
        """Test that send_manageengine_email correctly extracts and appends CC addresses, structures HTML/Text body, and overrides requester."""
        mock_ses = MagicMock()
        lambda_function.ses_client = mock_ses
        
        fields = {
            'titulo': 'Test title',
            'cuenta_aws': '123456789012',
            'proyecto': 'Test-Project',
            'ambiente': 'Dev',
            'solicitante': 'Juan Perez',
            'lider_aprobador': 'Santiago Guerrero',
            'link_iac': 'https://github.com/PR',
            'descripcion': 'Test description',
            'aprobado_por': 'Santiago Guerrero',
            'fecha': '2026-05-21 12:00:00',
            'correo_solicitante': 'juan.perez@pragma.com.co',
            'correo_lider': 'santiago.guerrero@pragma.com.co'
        }
        
        lambda_function.send_manageengine_email('sender@pragma.com.co', 'recipient@pragma.com.co', fields)
        
        mock_ses.send_email.assert_called_once()
        call_kwargs = mock_ses.send_email.call_args[1]
        destination = call_kwargs['Destination']
        self.assertIn('CcAddresses', destination)
        self.assertIn('juan.perez@pragma.com.co', destination['CcAddresses'])
        self.assertIn('santiago.guerrero@pragma.com.co', destination['CcAddresses'])
        
        # Verify subject matches "{titulo} : {proyecto} - {ambiente}"
        self.assertEqual(call_kwargs['Message']['Subject']['Data'], 'Test title : Test-Project - Dev')
        
        # Verify ReplyToAddresses and Source headers
        self.assertEqual(call_kwargs['ReplyToAddresses'], ['santiago.guerrero@pragma.com.co'])
        self.assertEqual(call_kwargs['Source'], 'Santiago Guerrero <santiago.guerrero@pragma.com.co>')
        
        # Verify Body contains both Text and Html formats
        body = call_kwargs['Message']['Body']
        self.assertIn('Text', body)
        self.assertIn('Html', body)
        
        # Verify @@REQUESTER=@@ is overridden to requester (practitioner) username
        self.assertIn("@@REQUESTER=juan.perez@@", body['Text']['Data'])
        self.assertIn("@@REQUESTER=juan.perez@@", body['Html']['Data'])
        
        # Test cleaning logic for mailto links
        fields['correo_solicitante'] = '<mailto:juan.perez@pragma.com.co|juan.perez@pragma.com.co>'
        mock_ses.reset_mock()
        lambda_function.send_manageengine_email('sender@pragma.com.co', 'recipient@pragma.com.co', fields)
        call_kwargs = mock_ses.send_email.call_args[1]
        destination = call_kwargs['Destination']
        self.assertIn('juan.perez@pragma.com.co', destination['CcAddresses'])

    @patch('lambda_function.requests.post')
    def test_slash_command_open_modal(self, mock_post):
        """Test that receiving a slash command calls views.open with the modal view."""
        os.environ['SLACK_BOT_TOKEN'] = 'xoxb-test-bot-token'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = 'ok'
        mock_post.return_value = mock_response
        
        event = {
            "body": "command=%2Fsolicitar-despliegue&trigger_id=12345.67890.abc&channel_id=C123&user_id=U111&user_name=juan.perez",
            "isBase64Encoded": False,
            "headers": {"content-type": "application/x-www-form-urlencoded"}
        }
        
        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        call_kwargs = mock_post.call_args[1]
        
        self.assertEqual(call_url, "https://slack.com/api/views.open")
        self.assertIn("Authorization", call_kwargs["headers"])
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer xoxb-test-bot-token")
        self.assertEqual(call_kwargs["json"]["trigger_id"], "12345.67890.abc")
        self.assertEqual(call_kwargs["json"]["view"]["private_metadata"], "C123")

    @patch('lambda_function.requests.post')
    def test_modal_view_submission(self, mock_post):
        """Test that submitting the modal posts approval message blocks to the channel."""
        os.environ['SLACK_BOT_TOKEN'] = 'xoxb-test-bot-token'
        os.environ['SLACK_CHANNEL_ID'] = 'C999'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = 'ok'
        mock_post.return_value = mock_response
        
        payload = {
            "type": "view_submission",
            "view": {
                "callback_id": "deploy_request_modal",
                "private_metadata": "C123",
                "state": {
                    "values": {
                        "titulo_block": {
                            "titulo_input": {"value": "Mi titulo de ticket"}
                        },
                        "proyecto_block": {
                            "proyecto_input": {
                                "selected_option": {"value": "creci"}
                            }
                        },
                        "ambiente_block": {
                            "ambiente_input": {
                                "selected_option": {"value": "Producción"}
                            }
                        },
                        "solicitante_block": {
                            "solicitante_input": {"value": "Juan Perez"}
                        },
                        "correo_solicitante_block": {
                            "correo_solicitante_input": {"value": "juan@pragma.com.co"}
                        },
                        "cuenta_aws_block": {
                            "cuenta_aws_input": {"value": "123456789012"}
                        },
                        "lider_aprobador_block": {
                            "lider_aprobador_input": {
                                "selected_option": {"value": "jmesa@pragma.com.co"}
                            }
                        },
                        "descripcion_block": {
                            "descripcion_input": {"value": "Test description"}
                        }
                    }
                }
            }
        }
        
        body = urllib.parse.urlencode({"payload": json.dumps(payload)})
        event = {
            "body": body,
            "isBase64Encoded": False,
            "headers": {"content-type": "application/x-www-form-urlencoded"}
        }
        
        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        call_kwargs = mock_post.call_args[1]
        
        self.assertEqual(call_url, "https://slack.com/api/chat.postMessage")
        self.assertEqual(call_kwargs["json"]["channel"], "C999")
        
        blocks = call_kwargs["json"]["blocks"]
        text_content = blocks[0]["text"]["text"]
        self.assertIn("Mi titulo de ticket", text_content)
        self.assertIn("creci", text_content)
        self.assertIn("Producción", text_content)
        self.assertIn("123456789012", text_content)
        self.assertIn("Juan Perez", text_content)
        self.assertIn("jmesa@pragma.com.co", text_content)

    def test_calculate_default_aws_account(self):
        """Test the mapping from project + environment to AWS account."""
        self.assertEqual(lambda_function.calculate_default_aws_account("PL", "Desarrollo"), "Pragma Reserva")
        self.assertEqual(lambda_function.calculate_default_aws_account("ET", "Desarrollo"), "Pragma Reserva")
        self.assertEqual(lambda_function.calculate_default_aws_account("ET", "Producción"), "Pragma Reserva")
        self.assertEqual(lambda_function.calculate_default_aws_account("PL", "Producción"), "Plataforma de ingreso prod")
        self.assertEqual(lambda_function.calculate_default_aws_account("Creci", "Desarrollo"), "Mapa de crecimiento dev (nueva org)")
        self.assertEqual(lambda_function.calculate_default_aws_account("Creci", "QA"), "Mapa de crecimiento dev (antigua org)")
        self.assertEqual(lambda_function.calculate_default_aws_account("Creci", "Producción"), "Mapa de crecimiento dev (antigua org)")
        self.assertEqual(lambda_function.calculate_default_aws_account("Swat", "Desarrollo"), "Operations dev")
        self.assertEqual(lambda_function.calculate_default_aws_account("Swat", "Producción"), "Operations pdn")
        self.assertIsNone(lambda_function.calculate_default_aws_account("Datos", "Desarrollo"))

    @patch('lambda_function.requests.post')
    def test_modal_view_submission_auto_populates_account(self, mock_post):
        """Test that submitting the modal with empty cuenta_aws auto-populates it."""
        os.environ['SLACK_BOT_TOKEN'] = 'xoxb-test-bot-token'
        os.environ['SLACK_CHANNEL_ID'] = 'C999'
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = 'ok'
        mock_post.return_value = mock_response
        
        payload = {
            "type": "view_submission",
            "view": {
                "callback_id": "deploy_request_modal",
                "private_metadata": "C123",
                "state": {
                    "values": {
                        "titulo_block": {
                            "titulo_input": {"value": "Mi titulo de ticket"}
                        },
                        "proyecto_block": {
                            "proyecto_input": {
                                "selected_option": {"value": "Creci"}
                            }
                        },
                        "ambiente_block": {
                            "ambiente_input": {
                                "selected_option": {"value": "Desarrollo"}
                            }
                        },
                        "solicitante_block": {
                            "solicitante_input": {"value": "Juan Perez"}
                        },
                        "correo_solicitante_block": {
                            "correo_solicitante_input": {"value": "juan@pragma.com.co"}
                        },
                        "cuenta_aws_block": {
                            "cuenta_aws_input": {"value": ""} # Empty!
                        },
                        "lider_aprobador_block": {
                            "lider_aprobador_input": {
                                "selected_option": {"value": "jmesa@pragma.com.co"}
                            }
                        },
                        "descripcion_block": {
                            "descripcion_input": {"value": "Test description"}
                        }
                    }
                }
            }
        }
        
        body = urllib.parse.urlencode({"payload": json.dumps(payload)})
        event = {
            "body": body,
            "isBase64Encoded": False,
            "headers": {"content-type": "application/x-www-form-urlencoded"}
        }
        
        response = lambda_function.lambda_handler(event, None)
        self.assertEqual(response['statusCode'], 200)
        
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        blocks = call_kwargs["json"]["blocks"]
        text_content = blocks[0]["text"]["text"]
        
        # Verify it auto-populated to "Mapa de crecimiento dev (nueva org)"
        self.assertIn("Mapa de crecimiento dev (nueva org)", text_content)

if __name__ == '__main__':
    unittest.main()

