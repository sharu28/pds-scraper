import pandas as pd
import requests
import time
import fitz  # PyMuPDF
from openai import OpenAI
import urllib3
import re
import os
import zipfile
from datetime import datetime

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
CSE_ID = os.getenv("CSE_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

def map_columns(df):
    """Ensure consistent column mapping regardless of naming variations."""
    expected_columns = ["APIR code", "Product name", "PDS date", "Web link"]
    mapped_columns = {col: expected_columns[i] for i, col in enumerate(df.columns[:4])}
    return df.rename(columns=mapped_columns)

def format_pds_date(date_str):
    """Format date into 'D Month YYYY' format."""
    try:
        date_obj = datetime.strptime(date_str, "%d %B %Y")
        return date_obj.strftime("%-d %B %Y")
    except ValueError:
        return date_str  # Return as-is if the format isn't recognized

def validate_pdf_with_ai(text, product_name, apir_code):
    """
    Validate if the first page of a PDF matches PDS requirements 
    using your AI logic, and extract the PDS date.
    """
    try:
        # 1) Simplify the system prompt for clearer AI instructions
        response = client.chat.completions.create(
            model="gpt-4o-mini-2024-07-18",
            messages=[
                {
                    "role": "system",
                    "content": f"""Analyze this text with the **primary objective of determining if it is a valid Product Disclosure Statement (PDS)** for {product_name} ({apir_code}). Identifying the document type is secondary and intended to assist with reasoning if invalid.

**Validation Criteria (Focus on PDS Validity):**
1. **Product Name Match:** Must match {product_name} exactly.
2. **APIR Code Match:** Must match {apir_code} if present.
3. **Multiple Product Names:**  If multiple product names exist, but {product_name} is present, it is still valid.
4. **Recency Check:**
   - PDS date after Jan 2023: Score `100`
   - PDS date before Jan 2023: Deduct `25`
5. **Document Type Identification (Secondary):** Analyze title and context to determine type (e.g., TMD, Supplementary Prospectus). Return `Unknown reason` if unclear.

**Scoring Rules:**
- `100 | [blank] | PDS date: D Month YYYY` (Fully Valid)
- `<score> | <reason> | PDS date:` (Partial Validity)
- `0 | <reason>` (Invalid)

**Examples:**
- `100 | [blank] | PDS date: 10 April 2023`
- `75 | Old date (-25) | PDS date: 15 March 2022`
- `0 | Supplementary Prospectus – Not PDS`
- `0 | Unknown reason`

**Important:** Primary goal: Validate PDS status. Provide reasons clearly if score <100, and return `Unknown reason` if document type is unclear.
"""
                },
                {"role": "user", "content": text[:15000]}  # truncate for safety
            ]
        )

        content = response.choices[0].message.content.strip()

        # 2) Keep the original 3-part regex but also add a fallback 2-part for 'score | reason'
        # Attempt to parse the custom response format with 3 parts
        match_3 = re.match(r"(\d+)\s*\|\s*([^|]+)\s*\|\s*PDS date:\s*(\d{1,2} [A-Za-z]+ \d{4})", content)
        if match_3:
            score = int(match_3.group(1))
            reason = match_3.group(2).strip()
            raw_date = match_3.group(3).strip()
            return score, reason, format_pds_date(raw_date)

        # Check for the 100-score pattern with no reason
        match_100 = re.match(r"100\s*\|\s*PDS date:\s*(\d{1,2} [A-Za-z]+ \d{4})", content)
        if match_100:
            return 100, "", format_pds_date(match_100.group(1))

        # Fallback: 2-part pattern (score | reason) if there's no PDS date included
        match_2 = re.match(r"(\d+)\s*\|\s*(.*)", content)
        if match_2:
            score = int(match_2.group(1))
            reason = match_2.group(2).strip()
            return score, reason, ""

        # If none of the above regexes matched, fallback with a short reason
        return 0, "Couldn’t parse AI response", ""

    except Exception as e:
        return 0, f"Error: {e}", ""

def extract_pdf_text_first_page(url):
    """Extract text from the first page of a PDF (download from URL)."""
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf", "Referer": url}
    try:
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        if response.status_code == 200 and 'application/pdf' in response.headers.get('Content-Type', ''):
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                if len(doc) > 0:
                    return doc[0].get_text()
    except Exception as e:
        print(f"Error extracting PDF: {e}")
    return ""

def search_google_for_pds(product_name, apir_code):
    """Search Google for the top PDS result by given product name + APIR code."""
    query = (
        f'"{product_name}" "{apir_code}" "Product Disclosure Statement" filetype:pdf'
        if apir_code else
        f'"{product_name}" "Product Disclosure Statement" filetype:pdf'
    )
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': GOOGLE_API_KEY, 'cx': CSE_ID, 'q': query, 'fileType': 'pdf', 'num': 1}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        results = response.json()
        if 'items' in results and results['items']:
            return results['items'][0]['link']
    except Exception as e:
        print(f"Google search error: {e}")
    return ""

def download_pdf_file(url, product_name, download_folder):
    """
    Download a PDF from the URL, rename it to match the product name,
    and store it in download_folder.
    """
    safe_product_name = re.sub(r'[\\/*?:"<>|]', "", product_name)
    file_path = os.path.join(download_folder, f"{safe_product_name}.pdf")
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf", "Referer": url}
    try:
        response = requests.get(url, headers=headers, timeout=15, verify=False, stream=True)
        response.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return file_path
    except Exception as e:
        print(f"Error downloading PDF for '{product_name}': {e}")
        return None

def process_row(row, download_folder):
    """
    Process one row of data:
      - search Google
      - extract PDF text
      - run AI validation
      - if valid, download the PDF
    """
    product_name = row['Product name'].strip()
    apir_code = str(row['APIR code']).strip() if pd.notna(row['APIR code']) else None
    pdf_url = search_google_for_pds(product_name, apir_code)
    if not pdf_url:
        return "Not found", 0, "No PDF", ""

    text = extract_pdf_text_first_page(pdf_url)
    if not text:
        return "Not found", 0, "No text extracted", ""

    score, reason, pds_date = validate_pdf_with_ai(text, product_name, apir_code)
    if score == 100 and pdf_url != "Not found":
        pdf_file_path = download_pdf_file(pdf_url, product_name, download_folder)
        # pdf_file_path is appended in main loop
    return pdf_url, score, reason, pds_date


def run_my_script(input_excel_path):
    """
    Main entry point:
      - Read user-uploaded Excel from `input_excel_path`
      - Process each row
      - Generate an output Excel
      - Possibly zip any downloaded PDFs
      - Return final output paths (Excel + ZIP if created)
    """
    # Generate unique timestamp-based output folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_folder = os.path.join(os.getcwd(), f"Valid_PDS_PDFs_{timestamp}")
    zip_output_path = os.path.join(os.getcwd(), f"Valid_PDS_PDFs_{timestamp}.zip")
    os.makedirs(download_folder, exist_ok=True)

    # Read input Excel
    data = pd.read_excel(input_excel_path)
    data = map_columns(data)

    # Prepare output columns
    data['Web link'] = ""
    data['Validity Score'] = 0
    data['Validation Reason'] = ""
    data['PDS date'] = ""

    downloaded_files = []

    # Process each row
    for index, row in data.iterrows():
        # Basic safety check
        if pd.isna(row['Product name']):
            continue

        pdf_url, score, reason, pds_date = process_row(row, download_folder)
        data.at[index, 'Web link'] = pdf_url
        data.at[index, 'Validity Score'] = score
        data.at[index, 'Validation Reason'] = reason
        data.at[index, 'PDS date'] = pds_date

        # If valid, we assume it was downloaded
        if score == 100 and pdf_url != "Not found":
            safe_product_name = re.sub(r'[\\/*?:"<>|]', "", row['Product name'])
            pdf_file_path = os.path.join(download_folder, f"{safe_product_name}.pdf")
            if os.path.exists(pdf_file_path):
                downloaded_files.append(pdf_file_path)
        time.sleep(0.5)

    # Save the completed Excel file (in the current working directory, with timestamp)
    output_excel_path = os.path.join(os.getcwd(), f"Processed_{timestamp}.xlsx")
    data.to_excel(output_excel_path, index=False)
    print(f"Processing complete! Excel file saved at: {output_excel_path}")

    # Zip the downloaded PDFs if any valid ones were downloaded
    if downloaded_files:
        with zipfile.ZipFile(zip_output_path, 'w') as zipf:
            for file in downloaded_files:
                zipf.write(file, arcname=os.path.basename(file))
        print(f"All valid PDFs have been zipped into: {zip_output_path}")
        return output_excel_path, zip_output_path

    # If no PDFs, return just the Excel
    return output_excel_path, None
