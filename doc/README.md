# Aljarida PDF Archive Scraper

Automated scraper for downloading PDF archives from [Aljarida](https://www.aljarida.com) newspaper and uploading them to AWS S3 storage.

## Features

- 📄 **PDF Archive Scraping**: Downloads newspaper PDFs from Aljarida's archive
- ☁️ **S3 Integration**: Automatically uploads PDFs to AWS S3 with organized folder structure
- 📅 **Monthly Automation**: Runs on day 3 of each month to scrape the previous month
- 🔄 **Checkpoint System**: Resumes from last successful date to avoid duplicate work
- ⚡ **Smart Caching**: Caches month pages to reduce API calls
- ✅ **PDF Validation**: Verifies downloaded files are valid PDFs before uploading
- 🛡️ **Error Handling**: Retry logic and robust error handling

## How It Works

The scraper operates in two modes:

### 1. Monthly Mode (Scheduled)
- Runs automatically on the **3rd day of every month** at 3 AM UTC
- Scrapes all PDFs from the **previous month**
- Example: On March 3rd → scrapes all February PDFs (Feb 1 - Feb 28/29)

### 2. Checkpoint Mode (Manual/Continuous)
- Resumes from the last successfully processed date
- Goes backwards in time from recent to oldest dates
- Useful for initial backfill or catching up

## Setup

### Prerequisites

- Python 3.11+
- AWS Account with S3 access
- GitHub repository (for automated runs)

### Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd Project4-AN-a1Ja
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure AWS credentials**

   Create an IAM user with the following permissions:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "s3:PutObject",
           "s3:GetObject",
           "s3:HeadBucket",
           "s3:HeadObject"
         ],
         "Resource": [
           "arn:aws:s3:::YOUR-BUCKET-NAME/*",
           "arn:aws:s3:::YOUR-BUCKET-NAME"
         ]
       }
     ]
   }
   ```

4. **Set up GitHub Secrets** (for automated runs)

   Go to your repository → Settings → Secrets and variables → Actions, and add:
   - `AWS_ACCESS_KEY_ID`: Your AWS access key
   - `AWS_SECRET_ACCESS_KEY`: Your AWS secret key
   - `S3_BUCKET_NAME`: Your S3 bucket name

## Usage

### Running Locally

**Monthly Mode** (scrape previous month):
```bash
# Windows PowerShell
$env:AWS_ACCESS_KEY_ID="your-key"
$env:AWS_SECRET_ACCESS_KEY="your-secret"
$env:S3_BUCKET_NAME="your-bucket"
$env:SCRAPE_MODE="monthly"
python pdf_scraper.py
```

**Checkpoint Mode** (continue from last checkpoint):
```bash
$env:SCRAPE_MODE="checkpoint"
python pdf_scraper.py
```

**Custom Date Range**:
```bash
# Scrape from Jan 31 to Jan 1, 2026
python pdf_scraper.py 2026-01-31 2026-01-01
```

### Running on GitHub Actions

**Automatic (Monthly)**:
- Runs automatically on the 3rd of each month at 3 AM UTC
- No action needed!

**Manual Run**:
1. Go to Actions tab in your repository
2. Select "Scrape Aljarida PDF Archive"
3. Click "Run workflow"
4. Optionally specify custom dates

## S3 Folder Structure

PDFs are organized in S3 using the following structure:

```
s3://your-bucket/
└── aljarida/
    ├── year=2026/
    │   ├── month=01/
    │   │   ├── day=01/
    │   │   │   └── magazinepdf/
    │   │   │       └── file.pdf
    │   │   ├── day=02/
    │   │   │   └── magazinepdf/
    │   │   │       └── file.pdf
    │   │   └── ...
    │   └── month=02/
    │       └── ...
    └── _state/
        └── pdf_last_success_date.txt  # Checkpoint file
```

This partitioned structure is optimized for:
- Easy querying with AWS Athena
- Efficient batch processing
- Clear organization by date

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | AWS access key | Required |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | Required |
| `S3_BUCKET_NAME` | S3 bucket name | Required |
| `SCRAPE_MODE` | `monthly` or `checkpoint` | `checkpoint` |
| `USE_CHECKPOINT` | Resume from checkpoint | `1` (enabled) |
| `MAX_DAYS_PER_RUN` | Max days to process per run | `5000` |
| `MAX_RUNTIME_MINUTES` | Max runtime in minutes | `330` (5.5 hours) |

## Workflow Schedule

The GitHub Actions workflow is scheduled using cron syntax:

```yaml
schedule:
  - cron: '0 3 3 * *'  # At 03:00 on day 3 of every month
```

This means:
- **Minute**: 0 (at the start of the hour)
- **Hour**: 3 (3 AM UTC)
- **Day**: 3 (3rd day of the month)
- **Month**: * (every month)
- **Day of week**: * (any day)

## Error Handling

The scraper includes several safety features:

- ✅ **PDF Validation**: Checks if downloaded file is a valid PDF (starts with `%PDF`)
- ✅ **Size Validation**: Rejects empty (0 byte) files
- ✅ **Retry Logic**: Retries failed downloads up to 3 times
- ✅ **Duplicate Prevention**: Checks if PDF already exists in S3 before uploading
- ✅ **Checkpoint System**: Saves progress to avoid re-processing completed dates
- ✅ **Timeout Protection**: Stops before GitHub Actions 6-hour limit

## Monitoring

Check the GitHub Actions logs to monitor:
- Number of PDFs uploaded
- Number of PDFs skipped (already exist)
- Number of failures
- Total runtime
- Current checkpoint date

Example output:
```
============================================================
PDF Scraping Complete!
Total uploaded: 28
Total skipped: 3
Total failed: 0
Runtime: 2.45 minutes
============================================================
```

## Troubleshooting

**PDFs showing 0.00 MB:**
- Fixed! The scraper now properly downloads content before uploading

**"Already completed" message:**
- You've scraped all historical data
- Set `USE_CHECKPOINT=0` to re-scrape, or use custom dates

**S3 permission errors:**
- Verify IAM user has `s3:PutObject` permission
- Check bucket name is correct
- Ensure AWS credentials are valid

**No PDFs found:**
- Date might not have a published PDF
- Check the Aljarida website archive for that date

## Project Structure

```
Project4-AN-a1Ja/
├── pdf_scraper.py                          # Main PDF scraper script
├── requirements.txt                        # Python dependencies
├── .github/
│   └── workflows/
│       └── scrape-pdf.yml                  # GitHub Actions automation
└── doc/
    └── README.md                           # This documentation
```

## License

This project is for educational and archival purposes.

## Contributing

Feel free to open issues or submit pull requests for improvements!
