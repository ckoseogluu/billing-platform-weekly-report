# BillingPlatform — Weekly Demand Gen Report

Automated weekly report that pulls HubSpot CRM, Email, and Ads data, builds a formatted Excel file, and emails it every Monday at 8:00 AM UTC.

---

## What it does

Each run covers the **full prior calendar month** (e.g. a run on July 7 → report for June 1–30). The output is a five-sheet `.xlsx` file:

| Sheet | Data Source |
|---|---|
| Lead & MQL Metrics | HubSpot Contacts, Deals, Companies APIs |
| Email Metrics | HubSpot Email Analytics API |
| LinkedIn Metrics | HubSpot Ads API (LinkedIn campaigns) |
| Google Metrics | HubSpot Ads API (Google campaigns) |
| 6Sense Metrics | Manual — 6Sense has no HubSpot API integration |

**Color coding:**
- White — pulled automatically from HubSpot CRM API
- Amber `#FFC000` — manual entry required (value left blank)
- Light red `#FFCCCC` — HubSpot Ads API data
- Green `#E2EFDA` — HubSpot Dashboard metric (manual)

If any API call fails, the affected cell shows `API ERROR` in red and the script continues building the rest of the report — it never crashes the entire run.

---

## File structure

```
billingplatform-weekly-report/
├── .github/
│   └── workflows/
│       └── weekly_report.yml   # GitHub Actions cron + manual trigger
├── src/
│   ├── main.py                 # Orchestrator
│   ├── hubspot_client.py       # All HubSpot API calls
│   ├── report_builder.py       # openpyxl Excel builder
│   ├── email_sender.py         # Gmail SMTP sender
│   └── utils.py                # Date helpers, config loader
├── config.yaml                 # Goals, report title, email template
├── requirements.txt
├── .env.example                # Template for local dev
└── README.md
```

---

## Setup

### 1. Create a HubSpot Private App

1. In HubSpot, go to **Settings → Integrations → Private Apps**
2. Click **Create a private app**
3. Name it something like `Periti Digital — Weekly Report`
4. Under **Scopes**, enable:
   - `crm.objects.contacts.read`
   - `crm.objects.deals.read`
   - `crm.objects.companies.read`
   - `crm.objects.marketing_events.read`
   - `marketing-emails` (read)
   - `ads` (read)
5. Click **Create app** → copy the **Access token** (starts with `pat-na1-…`)

> Hub ID for this client: `20300238`

### 2. Set up Gmail App Password

Gmail requires an App Password (not your account password) when 2-Step Verification is on:

1. Go to your Google Account → **Security** → **2-Step Verification** → scroll to **App Passwords**
2. Select app: **Mail**, device: **Other** → name it `BillingPlatform Report`
3. Copy the 16-character password shown (format: `xxxx xxxx xxxx xxxx`)

### 3. Create a GitHub repository

```bash
git init
git remote add origin https://github.com/YOUR_ORG/billingplatform-weekly-report.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

### 4. Add GitHub Secrets

In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `HUBSPOT_API_KEY` | HubSpot Private App token (`pat-na1-…`) |
| `GMAIL_SENDER` | Gmail address used to send (e.g. `reports@peritidigital.com`) |
| `GMAIL_APP_PASSWORD` | 16-character App Password (no spaces) |
| `REPORT_RECIPIENTS` | Comma-separated recipient emails (can be empty for now) |

### 5. Add recipient emails

When the client confirms, add them to the `REPORT_RECIPIENTS` secret:

```
client@billingplatform.com,teammate@peritidigital.com
```

No code changes needed — the secret is read at runtime.

---

## Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy the env template and fill in real values
cp .env.example .env
# Edit .env with your actual tokens

# Run the report (covers the prior calendar month)
python src/main.py
```

The `.env` file is loaded automatically via `python-dotenv`. Never commit `.env` to git.

---

## Adjusting goals

Open `config.yaml` and update the `goals` section:

```yaml
goals:
  leads_goal: 80
  mqls_goal: 40
  sals_goal: 55
  meetings_goal: 13
```

---

## Manual trigger

In GitHub → **Actions** → **BillingPlatform Weekly Demand Gen Report** → **Run workflow**.
The report is always uploaded as a workflow artifact (retained 90 days) even if email delivery fails.

---

## Design assumptions

- **6Sense:** The 6Sense sheet is a static template with all rows pre-labeled. Values are left blank with an amber fill and a note to pull from the 6Sense platform. If 6Sense exposes a HubSpot integration or API in the future, `hubspot_client.py` is the right place to add it.
- **LinkedIn / Google campaign classification:** Campaigns are distinguished by the string `"brand"` or `"abm"` appearing in the campaign name. If BillingPlatform's naming convention differs, update the `campaign_type_hint` argument in the `get_linkedin_metrics` / `get_google_metrics` calls in `main.py`.
- **MQL date fields:** The script queries `lead_mql_date`, `mql_date_stamp`, `lead_ft_mql_date`, `ft_mql_date_stamp`, and `date_mql_fast_track__c`. If the actual HubSpot property names in this portal differ, update the field names in `hubspot_client.py`.
- **Rate limiting:** A 0.2 s delay is inserted between every API call. HubSpot's standard rate limit is 100 requests/10 s; this keeps the script well within bounds.
- **Report period:** Computed dynamically from `date.today()` at runtime — never hardcoded.
