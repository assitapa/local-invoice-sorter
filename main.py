import logging
import json
import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.discovery_cache.base import Cache
from datetime import datetime
from pdf_processor import PDFProcessor
from file_organizer import FileOrganizer

# Constants
FOLDER_ID = '1cfpB8Cgb_H1NXZ_sMaVXxJH1hYTwSJQ4'
KNOWN_FILES_PATH = 'known_files.json'
VENDORS_PATH = 'known_vendors.json'


class MemoryCache(Cache):
    _CACHE = {}

    def get(self, url):
        return MemoryCache._CACHE.get(url)

    def set(self, url, content):
        MemoryCache._CACHE[url] = content


def load_known_files():
    if os.path.exists(KNOWN_FILES_PATH):
        with open(KNOWN_FILES_PATH, 'r') as f:
            data = json.load(f)
            # Ensure correct structure
            if isinstance(data, dict) and 'files' in data:
                return data
            # Convert old format to new
            return {'files': {}}
    return {'files': {}}


def save_known_files(files):
    with open(KNOWN_FILES_PATH, 'w') as f:
        json.dump(files, f)


def initialize_drive_service():
    try:
        credentials = Credentials.from_service_account_file(
            'inlaid-citron-447001-g4-ce596023ffe8.json',
            scopes=['https://www.googleapis.com/auth/drive']
        )
        return build('drive', 'v3', credentials=credentials, cache=MemoryCache())
    except Exception as e:
        logging.error(f"Error initializing Drive service: {str(e)}")
        raise


def load_vendor_folders(drive_service):
    try:
        results = drive_service.files().list(
            q=f"mimeType='application/vnd.google-apps.folder' and '{FOLDER_ID}' in parents",
            fields="files(id, name)"
        ).execute()

        vendors = {folder['name']: folder['id']
                   for folder in results.get('files', [])}
        with open(VENDORS_PATH, 'w') as f:
            json.dump(vendors, f)
        return vendors
    except Exception as e:
        logging.error(f"Error loading vendor folders: {str(e)}")
        return {}


def create_vendor_folder(drive_service, vendor_name):
    try:
        file_metadata = {
            'name': vendor_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [FOLDER_ID]
        }
        folder = drive_service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()
        return folder.get('id')
    except Exception as e:
        logging.error(f"Error creating vendor folder: {str(e)}")
        return None


def process_new_files(drive_service, new_file_ids):
    processor = PDFProcessor(drive_service)
    organizer = FileOrganizer(drive_service)

    # Load vendor folders
    vendors = load_vendor_folders(drive_service)

    for file_id in new_file_ids:
        try:
            logging.info(f"Processing file {file_id}")

            pdf_content = processor.download_file(file_id)
            text = processor.extract_text(pdf_content)

            # Pass vendors list to GPT
            vendor, date = processor.get_vendor_from_gpt(
                list(vendors.keys()), text)

            if vendor and date:
                # Create new vendor folder if needed
                if vendor not in vendors:
                    logging.info(f"Creating new vendor folder: {vendor}")
                    folder_id = create_vendor_folder(drive_service, vendor)
                    if folder_id:
                        vendors[vendor] = folder_id
                        with open(VENDORS_PATH, 'w') as f:
                            json.dump(vendors, f)

                # Move file to vendor folder
                new_name = organizer.create_new_filename(vendor, date)
                if new_name:
                    target_folder = vendors.get(vendor)
                    if target_folder:
                        success = organizer.move_and_rename_file(
                            file_id, new_name, target_folder)
                        if success:
                            logging.info(
                                f"Successfully processed file: {new_name} to folder: {vendor}")
                        else:
                            logging.error(
                                f"Failed to rename/move file {file_id}")
                    else:
                        logging.error(f"Vendor folder not found for {vendor}")
            else:
                logging.warning(
                    f"Could not extract vendor/date from file {file_id}")

        except Exception as e:
            logging.error(f"Error processing file {file_id}: {str(e)}")


def check_new_files():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
    logging.info('Starting file check')
    
    try:
        known_files = load_known_files()
        if not isinstance(known_files, dict) or 'files' not in known_files:
            known_files = {'files': {}}
            
        known_file_ids = set(known_files['files'].keys())
        logging.info(f"Loaded {len(known_file_ids)} known files")
        
        drive_service = initialize_drive_service()
        results = drive_service.files().list(
            q=f"mimeType='application/pdf' and '{FOLDER_ID}' in parents",
            fields="files(id, name)",
            orderBy="createdTime desc"
        ).execute()
        
        current_files = {}
        files = results.get('files', [])
        
        if files:
            logging.info(f"Found {len(files)} files in Drive")
            for file in files:
                current_files[file['id']] = file['name']
                
            current_file_ids = set(current_files.keys())
            new_files = current_file_ids - known_file_ids
            removed_files = known_file_ids - current_file_ids
            
            if removed_files:
                logging.info(f"Detected {len(removed_files)} removed files")
                for file_id in removed_files:
                    logging.info(f"File removed: {known_files['files'].get(file_id, 'Unknown name')}")
            
            if new_files:
                logging.info(f"Processing {len(new_files)} new files")
                process_new_files(drive_service, list(new_files))
            else:
                logging.info("No new files found")
                
            # Update known files with current state
            known_files['files'] = current_files
            save_known_files(known_files)
            logging.info("Updated known files list")
        else:
            logging.info("No files found in Drive")
            known_files['files'] = {}
            save_known_files(known_files)
            
    except Exception as e:
        logging.error(f"Error checking files: {str(e)}")

if __name__ == "__main__":
    check_new_files()
