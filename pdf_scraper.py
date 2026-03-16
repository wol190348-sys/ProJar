import requests
from bs4 import BeautifulSoup
import time
import os
import boto3
import sys
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse


class AljaridaPDFScraper:
    def __init__(self, aws_access_key=None, aws_secret_key=None, bucket_name=None):
        self.base_url = "https://www.aljarida.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # Cache for month pages to avoid refetching
        self.month_cache = {}
        
        # AWS S3 configuration
        self.bucket_name = bucket_name
        if aws_access_key and aws_secret_key:
            print(f"\nInitializing S3 client...")
            print(f"Access Key: {aws_access_key[:8]}...{aws_access_key[-4:]}")
            print(f"Bucket: {bucket_name}")
            
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
            
            # Test the connection
            try:
                print("\nTesting S3 connection...")
                self.s3_client.head_bucket(Bucket=bucket_name)
                print(f"✓ Successfully connected to S3 bucket: {bucket_name}")
            except Exception as e:
                print(f"✗ Failed to connect to S3: {e}")
                print(f"\nPlease check:")
                print(f"1. The AWS Access Key exists in IAM (not deleted/deactivated)")
                print(f"2. The Secret Key matches the Access Key")
                print(f"3. The IAM user has s3:PutObject permission for bucket '{bucket_name}'")
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
    
    def upload_pdf_to_s3(self, pdf_url, year, month, day):
        """Download PDF and upload to S3"""
        if self.s3_client is None or self.bucket_name is None:
            print("S3 client not configured, skipping upload")
            return False
        
        # Extract filename from URL
        filename = os.path.basename(urlparse(pdf_url).path)
        if not filename or not filename.endswith('.pdf'):
            filename = f"aljarida-{year}{month:02d}{day:02d}-1.pdf"
        
        # Remove query string from filename
        filename = filename.split('?')[0]
        
        s3_key = f"aljarida/year={year}/month={month:02d}/day={day:02d}/magazinepdf/{filename}"
        
        try:
            # Check if PDF already exists in S3
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
                print(f"✓ PDF already exists: s3://{self.bucket_name}/{s3_key}")
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
            
            # Upload to S3
            print(f"Uploading to s3://{self.bucket_name}/{s3_key}...")
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=pdf_content,
                ContentType='application/pdf'
            )
            
            print(f"✓ Uploaded PDF: s3://{self.bucket_name}/{s3_key}")
            return True
            
        except Exception as e:
            print(f"✗ Error uploading PDF: {e}")
            return False
    
    def get_checkpoint_key(self):
        """Get S3 key for checkpoint file"""
        return "aljarida/_state/pdf_last_success_date.txt"
    
    def get_last_checkpoint_date(self):
        """Get last successfully processed date from S3"""
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
        """Save last successfully processed date to S3"""
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
        """Scrape PDFs and upload to S3 with runtime limits - Goes BACKWARDS from recent to old"""
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
                    success = self.upload_pdf_to_s3(
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
    # Get AWS credentials from environment variables
    AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
    BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
    
    # Validate credentials are set
    if not AWS_ACCESS_KEY:
        print("ERROR: AWS_ACCESS_KEY_ID environment variable is not set!")
        sys.exit(1)
    
    if not AWS_SECRET_KEY:
        print("ERROR: AWS_SECRET_ACCESS_KEY environment variable is not set!")
        sys.exit(1)
    
    if not BUCKET_NAME:
        print("ERROR: S3_BUCKET_NAME environment variable is not set!")
        sys.exit(1)
    
    # Strip whitespace
    AWS_ACCESS_KEY = AWS_ACCESS_KEY.strip()
    AWS_SECRET_KEY = AWS_SECRET_KEY.strip()
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
    
    print(f"Initializing PDF scraper...")
    print(f"S3 Bucket: {BUCKET_NAME}")
    print(f"AWS Access Key: {AWS_ACCESS_KEY[:4]}...{AWS_ACCESS_KEY[-4:]}")
    print(f"Scrape Mode: {SCRAPE_MODE}")
    
    # Initialize scraper
    scraper = AljaridaPDFScraper(
        aws_access_key=AWS_ACCESS_KEY,
        aws_secret_key=AWS_SECRET_KEY,
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
