"""
Master orchestrator script for Hera Profile Analysis.
Runs the full pipeline: Scraping -> Parsing -> Cost Analysis -> Visualization -> Dashboard.

Usage:
    python hera_scraping/scripts/run_hera_analysis.py --week 13 --year 2026
"""

import argparse
import subprocess
import sys
import shutil
import os
from pathlib import Path
from datetime import datetime

# Configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIO_8_DIR = PROJECT_ROOT / "cost_tests" / "scenarios" / "scenario_8_custom"
OUTPUT_BASE_DIR = PROJECT_ROOT / "cost_tests" / "outputs"

# Scripts
SCRAPE_SCRIPT = PROJECT_ROOT / "hera_scraping" / "scripts" / "scrape_hera_profiles_playwright.py"
PARSE_SCRIPT = PROJECT_ROOT / "hera_scraping" / "scripts" / "parse_hera_profiles.py"
COST_SCRIPT = PROJECT_ROOT / "cost_determination.py"
DASHBOARD_SCRIPT = PROJECT_ROOT / "cost_tests" / "generate_dashboard.py"
VISUALIZE_SCRIPT = PROJECT_ROOT / "hera_scraping" / "scripts" / "visualize_profiles.py"

def run_step(command, description):
    """Run a subprocess command and check for errors."""
    print(f"\n{'='*60}")
    print(f"STEP: {description}")
    print(f"{'='*60}")
    print(f"Executing: {' '.join(command)}")
    
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(result.stdout)
        print(f"[SUCCESS] {description} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error during {description}: {e}")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return False

def main():
    parser = argparse.ArgumentParser(description="Run full Hera analysis pipeline.")
    parser.add_argument("--week", type=int, default=13, help="Week number")
    parser.add_argument("--year", type=int, default=2026, help="Year")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping if data already exists")
    parser.add_argument("--input-csv", type=str, help="Explicit path to optimization_results.csv to reuse")
    
    args = parser.parse_args()
    
    # Setup paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir_name = f"hera_week_{args.week}_{timestamp}"
    output_dir = OUTPUT_BASE_DIR / output_dir_name
    
    # Data storage for scraping (persistent across runs if needed, or per run?)
    # User wanted "everything related to this scrapping in a single folder".
    # But usually intermediate raw data might be kept separate?
    # Let's put raw data in a 'raw_data' subfolder of the output to be self-contained as requested.
    raw_data_dir = output_dir / "raw_data"
    
    print(f"Starting Hera Analysis for Week {args.week}, {args.year}")
    print(f"Output Directory: {output_dir}")
    
    # 1. Create Directories
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_data_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. Copy Parameters (Requirement for cost_determination)
    print("\n[INFO] Copying parameters.csv...")
    params_src = SCENARIO_8_DIR / "parameters.csv"
    if not params_src.exists():
        print(f"[ERROR] Error: parameters.csv not found at {params_src}")
        sys.exit(1)
        
    shutil.copy(params_src, output_dir / "parameters.csv")
    print("   - Copied parameters.csv")
    
    # 3. Scrape / Parse
    optimization_results_csv = output_dir / "optimization_results.csv"
    
    if args.skip_scrape:
        print("\n[INFO] Skipping scraping as requested.")
        
        source_csv = None
        if args.input_csv:
            source_csv = Path(args.input_csv)
            if not source_csv.exists():
                print(f"[ERROR] Specified input CSV not found: {source_csv}")
                sys.exit(1)
        else:
            # Auto-find latest run for this week
            print(f"[INFO] Searching for latest existing data for Week {args.week}...")
            possible_dirs = list(OUTPUT_BASE_DIR.glob(f"hera_week_{args.week}_*"))
            # Sort by name (timestamp format ensures chronological order)
            possible_dirs.sort(key=lambda x: x.name, reverse=True)
            
            for d in possible_dirs:
                candidate = d / "optimization_results.csv"
                if candidate.exists() and d != output_dir:
                    source_csv = candidate
                    print(f"[SUCCESS] Found existing data at: {source_csv}")
                    break
        
        if source_csv:
            print(f"[INFO] Copying existing data from {source_csv}...")
            shutil.copy(source_csv, optimization_results_csv)
        else:
            print(f"[ERROR] No existing data found for Week {args.week}. Cannot skip scraping.")
            sys.exit(1)
    else:
        scrape_cmd = [
            "python", str(SCRAPE_SCRIPT),
            "--week", str(args.week),
            "--year", str(args.year),
            "--output-dir", str(raw_data_dir),
            "--output-csv", str(optimization_results_csv)
        ]
        
        if not run_step(scrape_cmd, "Scraping & Parsing Data"):
            sys.exit(1)
            
    if not optimization_results_csv.exists():
        print("[ERROR] Error: optimization_results.csv was not generated.")
        sys.exit(1)
        
    # 4. Cost Analysis
    # Must run inside the output dir because cost_determination.py looks for files in CWD
    print(f"\n{'='*60}")
    print(f"STEP: Cost Determination")
    print(f"{'='*60}")
    
    # Calculate relative path to script
    # We are in output_dir. script is at ../../../cost_determination.py
    # Actually, we can just use the absolute path to the script.
    
    try:
        subprocess.run(
            ["python", str(COST_SCRIPT)], 
            cwd=str(output_dir),
            check=True
        )
        print("[SUCCESS] Cost Determination completed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error during Cost Determination: {e}")
        sys.exit(1)
        
    # Check for cost_results.csv
    cost_results_csv = output_dir / "cost_results.csv"
    if not cost_results_csv.exists():
        print("[ERROR] Error: cost_results.csv was not generated.")
        sys.exit(1)
        
    # 5. Dashboard Generation
    dashboard_html = output_dir / "dashboard.html"
    dashboard_cmd = [
        "python", str(DASHBOARD_SCRIPT),
        str(cost_results_csv),
        str(dashboard_html)
    ]
    
    if not run_step(dashboard_cmd, "Dashboard Generation"):
        print("[WARNING] Dashboard generation failed, continuing...")
    
    # 6. Graphs Visualization
    viz_dir = output_dir / "visualizations"
    viz_cmd = [
        "python", str(VISUALIZE_SCRIPT),
        "--input-csv", str(optimization_results_csv),
        "--output-dir", str(viz_dir)
    ]
    
    run_step(viz_cmd, "Graph Visualization")
    
    print(f"\n{'='*70}")
    print("HERA ANALYSIS COMPLETE")
    print(f"{'='*70}")
    print(f"Results are in: {output_dir}")
    print(f"   - Dashboard: {dashboard_html.name}")
    print(f"   - Graphs: visualizations/")
    print(f"   - Cost Results: cost_results.csv")
    print(f"   - Raw Data: raw_data/")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
