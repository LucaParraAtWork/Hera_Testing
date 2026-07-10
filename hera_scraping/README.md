# Hera Profile Data Scraping Project

This folder contains all scripts, data, and outputs related to scraping energy profile data from the Hera web application.

## 📁 Project Structure

```
hera_scraping/
├── scripts/               # Python scripts for scraping and processing
│   ├── scrape_hera_profiles_playwright.py  # Main scraper using Playwright
│   ├── parse_hera_profiles.py              # Data parser and transformer
│   └── visualize_profiles.py               # Visualization generator
├── data/                  # Raw scraped data (JSON files)
│   ├── *.json            # Captured API responses
│   └── week_13_profiles.png  # Screenshot of source page
├── output/                # Processed outputs
│   ├── optimization_results_hera.csv  # Final CSV output
│   └── visualizations/    # Generated graphs
└── README.md             # This file
```

## 🚀 Quick Start

### 1. Scrape Data from Hera

```bash
cd scripts
python scrape_hera_profiles_playwright.py
```

This will:
- Launch a browser window
- Navigate to Week 13 of 2026
- Capture API responses containing profile data
- Save JSON files to `../data/`
- Take a screenshot

### 2. Process the Data

```bash
python parse_hera_profiles.py
```

This will:
- Read captured JSON files from `../data/`
- Extract loading bay and gas profiles
- Convert 15-minute intervals to 1-minute intervals
- Calculate derived fields (truck lanes, gas allocation, etc.)
- Generate `../output/optimization_results_hera.csv`

### 3. Visualize the Results

```bash
python visualize_profiles.py
```

This will:
- Read the generated CSV file
- Create comprehensive visualizations
- Save graphs to `../output/visualizations/`

## 📊 Output Data

### CSV File: `optimization_results_hera.csv`

Contains **10,006 minutes** (~7 days) of energy profile data with the following columns:

| Column | Description | Unit |
|--------|-------------|------|
| Time | Minute index (0-10005) | - |
| Price_Elec | Electricity price | €/MWh |
| Price_Gas | Gas price | €/MWh |
| P_gas_Global | Total gas power | MW |
| P_truck_L1 | Truck lane 1 (Bay 2+4) | MW |
| P_truck_L2 | Truck lane 2 (Bay 1+3) | MW |
| P_Truck_Total | Total truck loading | MW |
| P_Physical | Total physical power | MW |
| P_aFRR_Reserved | Reserved aFRR capacity | MW |
| P_Total_Used_Cap | Total used capacity | MW |
| P_gas_Conditional_Bidding | Gas conditional bidding | MW |
| P_gas_MSOL_Coverage | Gas MSOL coverage | MW |
| Regime | Operating regime | - |

### Visualizations

Generated graphs include:
1. **loading_bays_profiles.png** - Individual loading bay/lane profiles
2. **combined_overview.png** - Combined view of all profiles
3. **regime_analysis.png** - Operating regime distribution
4. **data_statistics.png** - Statistical histograms

## 🔧 Technical Details

### Data Source
- **URL**: https://herawebdev.azurewebsites.net/schedules/profiles
- **Week**: Week 13, 2026 (March 22-29, 2026)
- **Original Granularity**: 15-minute intervals (667 data points)
- **Processed Granularity**: 1-minute intervals (10,005 data points)

### Scraping Method
- **Technology**: Playwright (Chromium browser automation)
- **Approach**: Intercept API responses (`/api/schedules/profiles`)
- **Data Format**: JSON with timestamped power values

### Data Transformations

1. **Interpolation**: 15-min → 1-min by repeating each value 15 times
2. **Lane Calculations**:
   - P_truck_L1 = Bay 2 + Bay 4
   - P_truck_L2 = Bay 1 + Bay 3
3. **Gas Allocation**:
   - Positive gas values → MSOL_Coverage
   - Zero gas → Physical regime
4. **Total Power**: P_Physical = P_Truck_Total + P_gas_Global

## 📦 Dependencies

```bash
pip install playwright pandas matplotlib
playwright install chromium
```

## 🔄 Re-running for Different Weeks

To scrape a different week, edit `scrape_hera_profiles_playwright.py`:

```python
TARGET_WEEK = 15  # Change to desired week number
YEAR = 2026
```

Then run the scraper again.

## 📈 Data Validation

The visualization script generates graphs to visually verify:
- ✓ Loading patterns match expected truck filling schedules
- ✓ Gas injection profile is consistent
- ✓ No data gaps or anomalies
- ✓ Value ranges are realistic

## 🔗 Integration

To use this data for cost analysis:

```bash
# From the project root
python cost_determination.py cost_tests/scenarios/scenario_8_custom
```

Note: You may need to update the path in `cost_determination.py` to point to:
`hera_scraping/output/optimization_results_hera.csv`

## 📝 Notes

- Data starts at **2026-03-22 23:00 UTC**
- Gas injection is constant at **5 MW**
- Maximum truck loading: **~25-26 MW per lane**
- Total physical power ranges from **5-30 MW**

## 🐛 Troubleshooting

**Scraper fails to start browser:**
```bash
playwright install chromium
```

**No data captured:**
- Check if Hera website is accessible
- Verify the week exists in the system
- Check browser console for errors

**Visualization fails:**
```bash
pip install --upgrade matplotlib pandas
```

## 📄 License

Internal project - Viryah Energy Optimization
