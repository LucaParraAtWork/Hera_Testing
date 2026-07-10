"""
Batch runner for Hera Profile Analysis across multiple weeks.
Iterates through specified weeks and runs the full analysis pipeline for each.
"""

import argparse
import subprocess
import sys
import time
import os
from pathlib import Path
from datetime import datetime

# Configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR_SCRIPT = PROJECT_ROOT / "hera_scraping" / "scripts" / "run_hera_analysis.py"
DEFAULT_WEEKS = [13, 14, 15, 16]
YEAR = 2026

def run_week_analysis(week, year, skip_scrape):
    """Run the analysis for a single week and return the directory created."""
    print(f"\n{'#'*80}")
    print(f"### STARTING ANALYSIS FOR WEEK {week}, {year}")
    print(f"{'#'*80}\n")
    
    cmd = [
        sys.executable, str(ORCHESTRATOR_SCRIPT),
        "--week", str(week),
        "--year", str(year)
    ]
    
    if skip_scrape:
        cmd.append("--skip-scrape")
    
    start_time = time.time()
    try:
        # Run and capture output to extract the output directory
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        print(result.stdout)
        
        # Extract output directory from stdout
        output_dir = None
        for line in result.stdout.splitlines():
            if "Output Directory:" in line:
                output_dir = line.split("Output Directory:")[1].strip()
                break
                
        duration = time.time() - start_time
        print(f"\n[SUCCESS] Week {week} completed in {duration/60:.2f} minutes.")
        return output_dir
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Week {week} failed with exit code {e.returncode}.")
        print("STDERR:", e.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Batch runner for Hera Profile Analysis.")
    parser.add_argument("--weeks", type=int, nargs="+", default=DEFAULT_WEEKS, 
                        help="Week numbers to analyze (e.g. --weeks 13 14)")
    parser.add_argument("--year", type=int, default=YEAR, help=f"Year (default: {YEAR})")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping and reuse latest data for each week")
    
    args = parser.parse_args()
    weeks = args.weeks
    year = args.year
    skip_scrape = args.skip_scrape

    print(f"  Target Weeks: {weeks}")
    print(f"  Year: {year}")
    print(f"  Skip Scraping: {skip_scrape}")
    print(f"{'='*80}\n")
    
    results = []
    
    total_start = time.time()
    
    for week in weeks:
        output_path = run_week_analysis(week, year, skip_scrape)
        results.append({
            "week": week,
            "status": "SUCCESS" if output_path else "FAILED",
            "path": output_path
        })
        
        # Small delay between runs to avoid potential overlapping issues
        if week != weeks[-1]:
            print("\nWaiting 5 seconds before next run...")
            time.sleep(5)
            
    total_duration = time.time() - total_start
    
    print(f"\n{'='*80}")
    print("BATCH RUN SUMMARY")
    print(f"{'='*80}")
    print(f"Total time: {total_duration/60:.2f} minutes\n")
    
    for res in results:
        status_str = f"[{res['status']}]"
        print(f"  Week {res['week']:2d}: {status_str}")
    
    # Generate Master Hub
    successful_results = [r for r in results if r["status"] == "SUCCESS"]
    if successful_results:
        generate_master_hub(successful_results)
    
    print(f"{'='*80}\n")

def generate_master_hub(results):
    """Generate a master portal linking to all analyzed weeks"""
    hub_path = PROJECT_ROOT / "cost_tests" / "outputs" / "hera_analysis_portal.html"
    
    cards_html = []
    for res in results:
        week = res["week"]
        # Use relative path for portability
        rel_path = os.path.relpath(Path(res["path"]) / "dashboard.html", hub_path.parent).replace('\\', '/')
        
        cards_html.append(f"""
            <div class="week-card" onclick="window.location.href='{rel_path}'">
                <div class="week-num">Week {week}</div>
                <div class="week-status">Analysis Complete</div>
                <div class="week-view">View Dashboard &rarr;</div>
            </div>
        """)

    style = """
        body { font-family: 'Inter', 'Segoe UI', sans-serif; background: #0f172a; color: white; margin: 0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .container { max-width: 900px; padding: 40px; text-align: center; }
        h1 { font-size: 3rem; background: linear-gradient(to right, #38bdf8, #2563eb); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 40px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 24px; margin-top: 40px; }
        .week-card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 32px; cursor: pointer; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
        .week-card:hover { transform: translateY(-8px); border-color: #38bdf8; box-shadow: 0 12px 24px rgba(0,0,0,0.4), 0 0 20px rgba(56, 189, 248, 0.2); background: #1e3a8a; }
        .week-num { font-size: 1.5rem; font-weight: 800; margin-bottom: 8px; color: #38bdf8; }
        .week-status { font-size: 0.9rem; color: #94a3b8; margin-bottom: 24px; }
        .week-view { color: #f8fafc; font-weight: 600; font-size: 0.95rem; }
        .header-sub { color: #94a3b8; font-size: 1.2rem; }
    """

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Hera Analysis Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>{style}</style>
</head>
<body>
    <div class="container">
        <h1>Hera Analysis Portal</h1>
        <p class="header-sub">Operational and economic performance across optimization cycles</p>
        <div class="grid">
            {''.join(cards_html)}
        </div>
    </div>
</body>
</html>
"""
    with open(hub_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n[SUCCESS] Master Hub generated: {hub_path}")

if __name__ == "__main__":
    main()
