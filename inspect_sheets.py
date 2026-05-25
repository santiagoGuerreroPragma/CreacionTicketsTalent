import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

def main():
    creds_path = "terraform/google-credentials.json"
    with open(creds_path, "r") as f:
        creds_dict = json.load(f)
        
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
    )
    service = build('sheets', 'v4', credentials=creds)
    spreadsheet_id = "15HUY9QEPodGPSvi7BmT792NG5gV6RccuqYSKXiSPaLQ"
    
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = metadata.get('sheets', [])
    print("Sheets in spreadsheet:")
    for s in sheets:
        properties = s.get('properties', {})
        print(f"- Title: {properties.get('title')}, Sheet ID: {properties.get('sheetId')}")

if __name__ == "__main__":
    main()
