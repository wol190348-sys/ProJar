import requests
from bs4 import BeautifulSoup
import time
import os
import boto3
import sys
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from botocore.config import Config


class AljaridaPDFScraper:
    def __init__(self, access_key=None, secret_key=None, endpoint_url=None, bucket_name=None):
        self.base_url = "https://www.aljarida.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # Cache for month pages to avoid refetching
        self.month_cache = {}
        
        # Cloudflare R2 configuration
        self.bucket_name = bucket_name
        if access_key and secret_key and endpoint_url:
            print(f"\nInitializing Cloudflare R2 client...")
            print(f"Access Key: {access_key[:8]}...{access_key[-4:]}")
            print(f"Endpoint: {endpoint_url}")
            print(f"Bucket: {bucket_name}")
            
            self.s3_client = boto3.client(
                's3',
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name='us-east-1',  # dummy value, R2 ignores it
                config=Config(
                    signature_version='s3v4',
                    s3={'addressing_style': 'path'}
                )
            )
            
            # Test the connection
            try:
                print("\nTesting R2 connection...")
                self.s3_client.head_bucket(Bucket=bucket_name)
                print(f"✓ Successfully connected to R2 bucket: {bucket_name}")
            except Exception as e:
                print(f"✗ Failed to connect to R2: {e}")
                print(f"\nPlease check:")
                print(f"1. The R2 Access Key ID is correct")
                print(f"2. The Secret Access Key matches the Access Key")
                print(f"3. The endpoint URL is correct (format: https://<account_id>.r2.cloudflarestorage.com)")
                print(f"4. The R2 token has write permission for bucket '{bucket_name}'")
                raise
        else:
            self.s3_client = None
    
    def get_page_content(self, url, max_retries=3):
        """Fetch page content with retry logic"""
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                response.encoding = 'utf-8'
                return response.text
            except Exception as e:
                print(f"Error fetching {url} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return None
        return None
    
    def scrape_pdf_month_index(self, year, month):
        """Scrape month page and return dict of date -> pdf_url"""
        cache_key = f"{year}-{month:02d}"
        
        if cache_key in self.month_cache:
            print(f"Using cached data for {cache_key}")
            return self.month_cache[cache_key]
        
        url = f"{self.base_url}/الأعداد-السابقة?monthFilter={year}-{month:02d}"
        print(f"\nScraping PDF archive: {url}")
        
        html = self.get_page_content(url)
        if not html:
            self.month_cache[cache_key] = {}
            return {}
        
        soup = BeautifulSoup(html, "html.parser")
        pdf_widget = soup.find("div", class_="aljarida-archive-pdf")
        
        date_to_pdf = {}
        
        if not pdf_widget:
            print(f"No PDF widget found for {year}-{month:02d}")
            self.month_cache[cache_key] = {}
            return {}
        
        previews = pdf_widget.find_all("div", class_="pdf-preview")
        print(f"Found {len(previews)} PDF previews")
        
        for preview in previews:
            date_div = preview.find("div", class_="date")
            pdf_link = preview.find("a", href=True)
            
            if not date_div or not pdf_link:
                continue
            
            # Extract date from text like "النسخة الورقية<br>2026-01-29"
            date_text = date_div.get_text(" ", strip=True)
            match = re.search(r"(\d{4}-\d{2}-\d{2})", date_text)
            
            if not match:
                continue
            
            date_str = match.group(1)
            pdf_url = urljoin(self.base_url, pdf_link["href"])
            date_to_pdf[date_str] = pdf_url
            print(f"  {date_str}: {pdf_url}")
        
        self.month_cache[cache_key] = date_to_pdf
        return date_to_pdf
    
    def upload_pdf_to_r2(self, pdf_url, year, month, day):
        """Download PDF and upload to Cloudflare R2"""
        if self.s3_client is None or self.bucket_name is None:
            print("R2 client not configured, skipping upload")
            return False
        
        # Extract filename from URL
        filename = os.path.basename(urlparse(pdf_url).path)
        if not filename or not filename.endswith('.pdf'):
            filename = f"aljarida-{year}{month:02d}{day:02d}-1.pdf"
        
        # Remove query string from filename
        filename = filename.split('?')[0]
        
        # Same partition structure as S3 version
        s3_key = f"aljarida/year={year}/month={month:02d}/day={day:02d}/magazinepdf/{filename}"
        
        try:
            # Check if PDF already exists in R2
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
                print(f"✓ PDF already exists: r2://{self.bucket_name}/{s3_key}")
                return True
            except Exception:
                pass  # PDF doesn't exist, continue with upload
            
            # Download PDF
            print(f"Downloading PDF: {pdf_url}")
            response = self.session.get(pdf_url, timeout=60)
            response.raise_for_status()
            
            # Get PDF content
            pdf_content = response.content
            
            # Check if content is valid
            if len(pdf_content) == 0:
                print(f"✗ Downloaded PDF is empty (0 bytes)")
                return False
            
            # Get content size
            size_mb = len(pdf_content) / (1024 * 1024)
            print(f"PDF size: {size_mb:.2f} MB")
            
            # Verify it's actually a PDF file
            if not pdf_content.startswith(b'%PDF'):
                print(f"✗ Downloaded file is not a valid PDF (missing PDF header)")
                return False
            
            # Upload to R2
            print(f"Uploading to r2://{self.bucket_name}/{s3_key}...")
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=pdf_content,
                ContentType='application/pdf'
            )
            
            print(f"✓ Uploaded PDF: r2://{self.bucket_name}/{s3_key}")
            return True
            
        except Exception as e:
            print(f"✗ Error uploading PDF: {e}")
            return False
    
    def get_checkpoint_key(self):
        """Get R2 key for checkpoint file"""
        return "aljarida/_state/pdf_last_success_date.txt"
    
    def get_last_checkpoint_date(self):
        """Get last successfully processed date from R2"""
        if self.s3_client is None or self.bucket_name is None:
            return None
        
        try:
            obj = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=self.get_checkpoint_key()
            )
            date_str = obj['Body'].read().decode('utf-8').strip()
            return datetime.strptime(date_str, '%Y-%m-%d')
        except Exception:
            return None
    
    def set_last_checkpoint_date(self, date_value):
        """Save last successfully processed date to R2"""
        if self.s3_client is None or self.bucket_name is None:
            return
        
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=self.get_checkpoint_key(),
                Body=date_value.strftime('%Y-%m-%d').encode('utf-8')
            )
        except Exception as e:
            print(f"Warning: failed to update checkpoint: {e}")
    
    def scrape_and_upload(self, start_date, end_date=None, max_days_per_run=50, max_runtime_minutes=330):
        """Scrape PDFs and upload to R2 with runtime limits - Goes BACKWARDS from recent to old"""
        if end_date is None:
            end_date = datetime(2007, 6, 1)  # Earliest date
        
        # Check if we've already gone past the end date
        if start_date < end_date:
            print(f"\n{'='*60}")
            print(f"Already completed! Start date ({start_date.strftime('%Y-%m-%d')}) is before end date ({end_date.strftime('%Y-%m-%d')})")
            print(f"All PDFs from {end_date.strftime('%Y-%m-%d')} onwards have been processed.")
            print(f"{'='*60}\n")
            return
        
        current_date = start_date
        total_uploaded = 0
        total_skipped = 0
        total_failed = 0
        started_at = time.time()
        
        print(f"\n{'='*60}")
        print(f"PDF Scraper Started (BACKWARDS: recent → old)")
        print(f"Starting from: {start_date.strftime('%Y-%m-%d')}")
        print(f"Going back to: {end_date.strftime('%Y-%m-%d')}")
        print(f"Max days per run: {max_days_per_run}")
        print(f"Max runtime: {max_runtime_minutes} minutes")
        print(f"{'='*60}\n")
        
        while current_date >= end_date:
            try:
                # Check runtime and day limits
                elapsed_minutes = (time.time() - started_at) / 60
                total_days = total_uploaded + total_skipped + total_failed
                
                if total_days >= max_days_per_run:
                    print(f"\nReached max days per run: {max_days_per_run}")
                    break
                
                if elapsed_minutes >= max_runtime_minutes:
                    print(f"\nReached max runtime: {max_runtime_minutes} minutes")
                    break
                
                print(f"\n{'='*60}")
                print(f"Processing date: {current_date.strftime('%Y-%m-%d')}")
                print(f"{'='*60}")
                
                # Get PDF index for this month
                pdf_index = self.scrape_pdf_month_index(current_date.year, current_date.month)
                
                # Check if PDF exists for this date
                date_str = current_date.strftime('%Y-%m-%d')
                pdf_url = pdf_index.get(date_str)
                
                if pdf_url:
                    success = self.upload_pdf_to_r2(
                        pdf_url,
                        current_date.year,
                        current_date.month,
                        current_date.day
                    )
                    
                    if success:
                        total_uploaded += 1
                        # Update checkpoint after successful upload
                        self.set_last_checkpoint_date(current_date)
                    else:
                        total_failed += 1
                else:
                    print(f"No PDF found for {date_str}")
                    total_skipped += 1
                
                # Move to PREVIOUS day (going backwards)
                current_date -= timedelta(days=1)
                
                # Small delay between requests
                time.sleep(1)
                
            except Exception as e:
                print(f"Error processing {current_date}: {e}")
                total_failed += 1
                current_date -= timedelta(days=1)  # Go backwards
                continue
        
        print(f"\n{'='*60}")
        print(f"PDF Scraping Complete!")
        print(f"Total uploaded: {total_uploaded}")
        print(f"Total skipped: {total_skipped}")
        print(f"Total failed: {total_failed}")
        print(f"Runtime: {(time.time() - started_at) / 60:.2f} minutes")
        print(f"{'='*60}")


if __name__ == "__main__":
    # Get Cloudflare R2 credentials from environment variables
    CF_ACCESS_KEY = os.getenv('CF_R2_ACCESS_KEY_ID')
    CF_SECRET_KEY = os.getenv('CF_R2_SECRET_ACCESS_KEY')
    CF_ENDPOINT_URL = os.getenv('CF_R2_ENDPOINT_URL')
    BUCKET_NAME = os.getenv('CF_R2_BUCKET_NAME')
    
    # Validate credentials are set
    if not CF_ACCESS_KEY:
        print("ERROR: CF_R2_ACCESS_KEY_ID environment variable is not set!")
        sys.exit(1)
    
    if not CF_SECRET_KEY:
        print("ERROR: CF_R2_SECRET_ACCESS_KEY environment variable is not set!")
        sys.exit(1)
    
    if not CF_ENDPOINT_URL:
        print("ERROR: CF_R2_ENDPOINT_URL environment variable is not set!")
        print("Expected format: https://<account_id>.r2.cloudflarestorage.com")
        sys.exit(1)
    
    if not BUCKET_NAME:
        print("ERROR: CF_R2_BUCKET_NAME environment variable is not set!")
        sys.exit(1)
    
    # Strip whitespace
    CF_ACCESS_KEY = CF_ACCESS_KEY.strip()
    CF_SECRET_KEY = CF_SECRET_KEY.strip()
    CF_ENDPOINT_URL = CF_ENDPOINT_URL.strip()
    BUCKET_NAME = BUCKET_NAME.strip()
    
    # Runtime limits for GitHub Actions (6 hour limit, use 5.5 hours to be safe)
    MAX_DAYS_PER_RUN = int(os.getenv("MAX_DAYS_PER_RUN", "5000"))
    MAX_RUNTIME_MINUTES = int(os.getenv("MAX_RUNTIME_MINUTES", "330"))
    USE_CHECKPOINT = os.getenv("USE_CHECKPOINT", "1") == "1"
    SCRAPE_MODE = os.getenv("SCRAPE_MODE", "checkpoint")  # 'monthly' or 'checkpoint'
    
    # Date range configuration - START from TODAY, go BACK to 2007
    START_DATE = datetime.now()  # Start from today
    END_DATE = datetime(2007, 6, 1)  # Go back to earliest date (June 1, 2007)
    
    # Allow command line arguments for date range
    if len(sys.argv) >= 2:
        try:
            START_DATE = datetime.strptime(sys.argv[1], '%Y-%m-%d')
        except:
            pass
    
    if len(sys.argv) >= 3:
        try:
            END_DATE = datetime.strptime(sys.argv[2], '%Y-%m-%d')
        except:
            pass
    
    print(f"Initializing PDF scraper (Cloudflare R2)...")
    print(f"R2 Bucket: {BUCKET_NAME}")
    print(f"R2 Endpoint: {CF_ENDPOINT_URL}")
    print(f"R2 Access Key: {CF_ACCESS_KEY[:4]}...{CF_ACCESS_KEY[-4:]}")
    print(f"Scrape Mode: {SCRAPE_MODE}")
    
    # Initialize scraper
    scraper = AljaridaPDFScraper(
        access_key=CF_ACCESS_KEY,
        secret_key=CF_SECRET_KEY,
        endpoint_url=CF_ENDPOINT_URL,
        bucket_name=BUCKET_NAME
    )
    
    # Handle different scrape modes if no explicit dates provided
    if len(sys.argv) < 2:
        if SCRAPE_MODE == "monthly":
            # Calculate previous month range
            today = datetime.now()
            # Get first day of current month, then subtract one day to get last day of previous month
            first_day_current_month = today.replace(day=1)
            last_day_previous_month = first_day_current_month - timedelta(days=1)
            first_day_previous_month = last_day_previous_month.replace(day=1)
            
            START_DATE = last_day_previous_month
            END_DATE = first_day_previous_month
            
            print(f"\n{'='*60}")
            print(f"MONTHLY MODE: Scraping previous month")
            print(f"Previous month: {last_day_previous_month.strftime('%B %Y')}")
            print(f"Date range: {first_day_previous_month.strftime('%Y-%m-%d')} to {last_day_previous_month.strftime('%Y-%m-%d')}")
            print(f"{'='*60}\n")
        elif USE_CHECKPOINT:
            # Resume from checkpoint if enabled
            checkpoint_date = scraper.get_last_checkpoint_date()
            if checkpoint_date:
                START_DATE = checkpoint_date - timedelta(days=1)  # Go one day earlier
                print(f"Resuming from checkpoint (going backwards): {START_DATE.strftime('%Y-%m-%d')}")
    
    # Run scraper
    scraper.scrape_and_upload(
        START_DATE,
        END_DATE,
        max_days_per_run=MAX_DAYS_PER_RUN,
        max_runtime_minutes=MAX_RUNTIME_MINUTES
    )
