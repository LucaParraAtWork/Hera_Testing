"""
Scrape Hera Profiles data using Playwright and convert to optimization results format.
Based on the existing Hera test automation code.
"""

import argparse
import json
import re
import time
import sys
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import pandas as pd
import numpy as np

try:
    from parse_hera_profiles import load_profile_data
except ImportError:
    # Try relative import if running as module or adjust path
    import sys
    sys.path.append(str(Path(__file__).parent))
    from parse_hera_profiles import load_profile_data

# Default Configuration
DEFAULT_TARGET_WEEK = 13
DEFAULT_YEAR = 2026
ELEC_PRICE = 100.0
GAS_PRICE = 30.0
HERA_PROFILES_URL = "https://herawebdev.azurewebsites.net/schedules/profiles"

# Configure via args later
start_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
DEFAULT_OUTPUT_DIR = Path(f"hera_scraped_data")
DEFAULT_OUTPUT_CSV = f"optimization_results_hera.csv"


class ProfileDataCapturer:
    """Captures and stores profile data from API responses."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.captured_data = []
        
    def _on_response(self, response):
        """Intercept API responses and save JSON data."""
        try:
            # Only capture JSON responses from API calls
            ct = response.headers.get("content-type", "").lower()
            if "application/json" in ct and response.request.resource_type in ["fetch", "xhr"]:
                url = response.url
                
                # Filter for profile-related endpoints
                # Look for endpoints that might contain profile data
                if any(keyword in url.lower() for keyword in ["profile", "schedule", "data", "graph", "chart"]):
                    try:
                        body = response.json()
                        
                        # Save the response
                        timestamp = datetime.now().strftime("%H%M%S")
                        url_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', url.split('/')[-1])[:40]
                        filename = f"{timestamp}_{response.request.method}_{url_slug}.json"
                        
                        data_wrapper = {
                            "url": url,
                            "method": response.request.method,
                            "status": response.status,
                            "data": body,
                            "timestamp": datetime.now().isoformat()
                        }
                        
                        filepath = self.output_dir / filename
                        with filepath.open("w", encoding="utf-8") as f:
                            json.dump(data_wrapper, f, indent=2)
                        
                        self.captured_data.append(data_wrapper)
                        print(f"  [INFO] Captured: {filename}")
                    except Exception as e:
                        print(f"  [ERROR] JSON parse error: {e}")
        except Exception as e:
            pass  # Silently ignore errors to not break the flow
    
    def start_capturing(self, page):
        """Start intercepting responses."""
        page.on("response", self._on_response)
    
    def stop_capturing(self, page):
        """Stop intercepting responses."""
        page.remove_listener("response", self._on_response)


def goto_week(page, target_week: int, max_clicks: int = 60):
    """Navigate to a specific week."""
    print(f"  [INFO] Navigating to Week {target_week}...")
    
    # Wait for date picker to be visible
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except:
        pass
    
    time.sleep(0.5)
    
    tries = 0
    while tries < max_clicks:
        # Get current week
        try:
            txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000)
            m = re.search(r"Week\s+(\d{1,2})", txt, re.I)
            current_week = int(m.group(1)) if m else None
        except:
            current_week = None
        
        if current_week == target_week:
            print(f"  [SUCCESS] Arrived at Week {target_week}")
            time.sleep(3)  # Increased from 1s to ensure initial loading
            
            # CRITICAL: Reload the page once we are on the correct week 
            # to ensure graphs and API calls are fresh for this week.
            print("  [INFO] Reloading page to ensure fresh data...")
            page.reload(wait_until="domcontentloaded", timeout=120000)
            time.sleep(5) 
            return True
        
        if current_week is None:
            print(f"  [WARNING] Cannot read current week, trying to navigate...")
        
        # Determine navigation direction
        if current_week:
            diff = target_week - current_week
            
            # Handle year wrap-around (heuristic)
            if diff > 26:
                go_next = False  # Wrap backward
            elif diff < -26:
                go_next = True  # Wrap forward
            else:
                go_next = (diff > 0)
        else:
            # If we can't read current week, just try going forward
            go_next = True
        
        # Click navigation button
        try:
            if go_next:
                page.locator("div.date-picker-next > button").first.click()
                print(f"  -> Next (try {tries + 1})")
            else:
                page.locator("div.date-picker-previous > button").first.click()
                print(f"  <- Prev (try {tries + 1})")
            
            time.sleep(0.3)
        except Exception as e:
            print(f"  [WARNING] Navigation click failed: {e}")
            break
        
        tries += 1
    
    print(f"  [ERROR] Failed to reach Week {target_week} after {tries} attempts")
    return False


def extract_profile_data_from_json(data_files: list[Path], output_dir: Path) -> dict:
    """
    Extract profile data from captured JSON files using shared parsing logic.
    """
    print("\nAnalyzing captured data files...")
    
    profiles = {
        'loading_bay_1': [],
        'loading_bay_2': [],
        'loading_bay_3': [],
        'loading_bay_4': [],
        'p_gas_total': []
    }
    
    # Analyze each captured file IN REVERSE (latest first) to prioritize fresh data
    for filepath in sorted(data_files, reverse=True):
        try:
            print(f"  [INFO] Checking: {filepath.name}")
            
            with filepath.open("r", encoding="utf-8") as f:
                data = json.load(f)
                
            # If data is wrapped in { "data": ... }, unwrap it for inspection
            if "data" in data and isinstance(data["data"], dict) and "profileGraphs" in data["data"]:
                content = data["data"]
            elif "profileGraphs" in data:
                content = data
            elif "data" in data:
                 content = data["data"] 
            else:
                 content = data

            # Save inspection file 
            inspection_file = output_dir / f"inspection_{filepath.stem}.json"
            with inspection_file.open("w", encoding="utf-8") as inf:
                 json.dump(content, inf, indent=2)

            # TRY TO VERIFY DATE of this file before merging
            # We want to ensure at least one point matches the target week's expected dates.
            # For Week 14 2026, start date is 2026-03-30.
            
            # Since we don't pass the target week easily here, we just look for ANY valid data.
            # But we'll try to be selective: only merge if we don't have this key yet.
            
            temp_profiles = load_profile_data(inspection_file)
            
            # Merge found profiles if they contain data
            for key, values in temp_profiles.items():
                if values and not profiles[key]:
                    profiles[key] = values
                    print(f"    [FOUND] Found {key} in {filepath.name}")

            # If we've found EVERYTHING, we can stop early (since we're going reverse)
            if all(profiles.values()):
                print("  [INFO] Successfully found all profiles in recent files.")
                break

        except Exception as e:
            print(f"  [ERROR] Error processing {filepath.name}: {e}")
    
    return profiles


def interpolate_15min_to_1min(data_15min):
    """Convert 15-minute data to 1-minute data by repeating each value 15 times."""
    data_1min = []
    for value in data_15min:
        data_1min.extend([float(value)] * 15)
    return data_1min


def create_optimization_results(profiles_15min: dict) -> pd.DataFrame:
    """Create optimization results CSV from profile data."""
    print("\nCreating optimization results...")
    
    # Convert to 1-minute intervals
    # Ensure all profiles have the same length (minimum common length)
    min_len = min(len(profiles_15min[k]) for k in profiles_15min)
    max_len = max(len(profiles_15min[k]) for k in profiles_15min)
    
    # Target for a full week (10080 min) is 672 points of 15-min
    TARGET_POINTS = 672
    
    if min_len < TARGET_POINTS and max_len >= TARGET_POINTS:
        print(f"  [INFO] Some profiles have {max_len} points, but some have {min_len}. Padding shorter profiles.")
        for k in profiles_15min:
            if len(profiles_15min[k]) < TARGET_POINTS and len(profiles_15min[k]) >= 670:
                # Pad with last value to reach target
                last_val = profiles_15min[k][-1] if profiles_15min[k] else 0.0
                while len(profiles_15min[k]) < TARGET_POINTS:
                    profiles_15min[k].append(last_val)
        min_len = TARGET_POINTS
    elif min_len == 671:
         # Special case: all have 671, pad all to 672
         print(f"  [INFO] All profiles have 671 points. Padding all to {TARGET_POINTS} for a full week.")
         for k in profiles_15min:
             profiles_15min[k].append(profiles_15min[k][-1])
         min_len = TARGET_POINTS

    if any(len(profiles_15min[k]) != min_len for k in profiles_15min):
        print(f"  [WARNING] Mismatch in profile lengths. Truncating all to {min_len} points.")
        for k in profiles_15min:
            profiles_15min[k] = profiles_15min[k][:min_len]

    bay_1_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_1'])
    bay_2_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_2'])
    bay_3_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_3'])
    bay_4_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_4'])
    p_gas_total_1min = interpolate_15min_to_1min(profiles_15min['p_gas_total'])
    
    # Calculate derived values
    p_truck_l1 = [b2 + b4 for b2, b4 in zip(bay_2_1min, bay_4_1min)]
    p_truck_l2 = [b1 + b3 for b1, b3 in zip(bay_1_1min, bay_3_1min)]
    p_truck_total = [l1 + l2 for l1, l2 in zip(p_truck_l1, p_truck_l2)]
    
    # Gas allocation
    p_gas_conditional = []
    p_gas_msol = []
    
    for pg in p_gas_total_1min:
        if pg > 0:
            # Positive: MSOL coverage
            p_gas_conditional.append(0.0)
            p_gas_msol.append(pg)
        else:
            # Negative or zero
            p_gas_conditional.append(0.0)
            p_gas_msol.append(0.0)
    
    # P_Physical
    p_physical = [pt + pg for pt, pg in zip(p_truck_total, p_gas_total_1min)]
    
    # P_Total_Used_Cap
    p_total_used_cap = [pt + max(0, pg) for pt, pg in zip(p_truck_total, p_gas_total_1min)]
    
    # Regime
    regime = []
    for pg_cond, pg_msol in zip(p_gas_conditional, p_gas_msol):
        if pg_msol > 0:
            regime.append('MSOL_Coverage')
        else:
            regime.append('Conditional_Bidding')
    
    num_minutes = len(p_truck_l1)
    
    df = pd.DataFrame({
        'Time': list(range(num_minutes)),
        'Price_Elec': [ELEC_PRICE] * num_minutes,
        'Price_Gas': [GAS_PRICE] * num_minutes,
        'P_gas_Global': p_gas_total_1min,
        'P_truck_L1': p_truck_l1,
        'P_truck_L2': p_truck_l2,
        'P_Truck_Total': p_truck_total,
        'P_Physical': p_physical,
        'P_aFRR_Reserved': [0.0] * num_minutes,
        'P_Total_Used_Cap': p_total_used_cap,
        'P_gas_Conditional_Bidding': p_gas_conditional,
        'P_gas_MSOL_Coverage': p_gas_msol,
        'Regime': regime
    })
    
    return df


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Scrape Hera energy profiles.")
    parser.add_argument("--week", type=int, default=DEFAULT_TARGET_WEEK, help="Target week number")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR, help="Target year")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Directory to save scraped JSONs")
    parser.add_argument("--output-csv", type=str, default=DEFAULT_OUTPUT_CSV, help="Path for output CSV")
    
    args = parser.parse_args()
    
    target_week = args.week
    year = args.year
    output_dir = Path(args.output_dir)
    output_csv = Path(args.output_csv)
    
    print(f"\n{'='*70}")
    print(f"  HERA Profile Data Scraper - Week {target_week} of {year}")
    print(f"{'='*70}\n")
    print(f"  Output Directory: {output_dir}")
    print(f"  Output CSV: {output_csv}")
    
    capturer = ProfileDataCapturer(output_dir)
    
    with sync_playwright() as p:
        # Launch browser
        print("Launching browser...")
        browser = p.chromium.launch(headless=False, slow_mo=500)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        
        try:
            # Start capturing
            capturer.start_capturing(page)
            
            # Navigate to profiles page
            print(f"Navigating to {HERA_PROFILES_URL}")
            page.goto(HERA_PROFILES_URL, wait_until="domcontentloaded", timeout=120000)
            print("Waiting 2 minutes for initial page load...")
            time.sleep(120)
            
            # Wait for graphs to load
            print("Waiting for graphs to load...")
            try:
                page.wait_for_selector("app-line-graph", timeout=120_000)
                print("  - Graphs loaded")
            except PlaywrightTimeout:
                print("  - Timeout waiting for graphs")
            
            # Navigate to target week
            if goto_week(page, target_week):
                # Wait for data to load after navigation
                print("Waiting 2 minutes for profile data to load before scraping...")
                time.sleep(120)  # Wait 2 minutes as requested
                
                # Try to trigger more data loads by interacting with the page
                try:
                    # Scroll slowly to trigger lazy loading of graphs
                    print("Scrolling to trigger lazy loading...")
                    for i in range(5):
                        page.evaluate(f"window.scrollTo(0, {i * 200})")
                        time.sleep(1)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(2)
                except:
                    pass
                
                # Take a screenshot
                screenshot_path = output_dir / f"week_{target_week}_profiles.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                print(f"  - Screenshot saved: {screenshot_path}")
            
            # Wait a bit more to ensure all async requests complete
            print("Waiting for all network requests to complete...")
            time.sleep(5)  # Increased from 3s
            
            # Stop capturing
            capturer.stop_capturing(page)
            
        finally:
            browser.close()
    
    # Analyze captured data
    print(f"\n{'='*70}")
    print(f"- Captured {len(capturer.captured_data)} API responses")
    print(f"{'='*70}")
    
    # List all captured files
    data_files = sorted(output_dir.glob("*.json"))
    # Filter for files from this run based on timestamp or just process all in the folder?
    # Better to look for files created recently or just process all logical "inspection" targets.
    # For now, we process all non-inspection files to create inspection files.
    
    inspection_files = [f for f in data_files if not f.name.startswith("inspection_")]
    
    print(f"\nCaptured data files:")
    for f in inspection_files:
        print(f"  - {f.name}")
    
    if not inspection_files:
        print("No new data files captured.")
        # Try to find existing inspection files
        inspection_files = sorted(output_dir.glob("inspection_*.json"))
        if not inspection_files:
            sys.exit(1)

    # Extract profile data (this saves inspection files)
    # Note: We need to pass the class instance or update function signatures if we want to be clean,
    # but global OUTPUT_DIR usage in extract_profile_data_from_json needs fixing.
    # Let's fix extract_profile_data_from_json to use local var.
    profiles = extract_profile_data_from_json(inspection_files, output_dir)
    
    # Check if we got data
    if not any(profiles.values()):
        print(f"\n{'='*70}")
        print("NO PROFILE DATA EXTRACTED")
        sys.exit(1)
    
    # Create optimization results
    df = create_optimization_results(profiles)
    
    # Save to CSV
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    
    print(f"\n{'='*70}")
    print("SUCCESS")
    print(f"{'='*70}")
    print(f"Output saved to: {output_path.absolute()}")
    print(f"\nData summary:")
    print(f"  Total minutes: {len(df)}")
    print(f"  Total days: {len(df) / 1440:.2f}")
    
if __name__ == "__main__":
    main()
