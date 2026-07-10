"""
Parse captured Hera profile data and create optimization results CSV.
"""

import argparse
import json
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime

# Default Configuration
DEFAULT_HERA_DATA_DIR = Path("hera_scraped_data")
DEFAULT_OUTPUT_CSV = "cost_tests/scenarios/scenario_8_custom/optimization_results_hera.csv"
ELEC_PRICE = 100.0
GAS_PRICE = 30.0


def load_profile_data(json_file: Path) -> dict:
    """Load and parse profile data from JSON file."""
    print(f"\n[INFO] Loading: {json_file.name}")
    
    with json_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    
    profiles = {
        'loading_bay_1': [],
        'loading_bay_2': [],
        'loading_bay_3': [],
        'loading_bay_4': [],
        'p_gas_total': []
    }
    
    # Check if this is the raw captured data (with wrapper)
    if "profileGraphs" in data:
        graph_data = data
    else:
        # It might be an "inspection" file that already has the data extracted
        graph_data = data
   
    # Look for profile graphs
    if "profileGraphs" not in graph_data:
        return profiles
    
    for graph in graph_data["profileGraphs"]:
        title = graph.get("title", "")
        print(f"  Graph: {title}")
        
        # Check if this is the gas graph (identify by title)
        is_gas_graph = "gas" in title.lower() and "inject" in title.lower()
        
        for line in graph.get("profileGraphLines", []):
            name = line.get("name", "")
            if not isinstance(name, str):
                name = str(name) if name is not None else ""
            points = line.get("points", {})
            
            print(f"    Line: {name if name else '(unnamed)'} ({len(points)} points)")
            
            #Extract values in chronological order
            sorted_timestamps = sorted(points.keys())
            values = [points[ts] for ts in sorted_timestamps]
            
            # Map to our profile keys
            if is_gas_graph:
                # This is the gas injection graph - use its data as p_gas_total
                profiles['p_gas_total'] = values
                print(f"      -> Mapped to p_gas_total")
            else:
                # Loading bays - map by name
                name_lower = name.lower()
                if "loading bay 1" in name_lower or "bay 1" in name_lower:
                    profiles['loading_bay_1'] = values
                elif "loading bay 2" in name_lower or "bay 2" in name_lower:
                    profiles['loading_bay_2'] = values
                elif "loading bay 3" in name_lower or "bay 3" in name_lower:
                    profiles['loading_bay_3'] = values
                elif "loading bay 4" in name_lower or "bay 4" in name_lower:
                    profiles['loading_bay_4'] = values
    
    return profiles


def interpolate_15min_to_1min(data_15min):
    """Convert 15-minute data to 1-minute data by repeating each value 15 times."""
    data_1min = []
    for value in data_15min:
        data_1min.extend([float(value)] * 15)
    return data_1min


def create_optimization_results(profiles_15min: dict) -> pd.DataFrame:
    """Create optimization results DataFrame from profile data."""
    print("\nCreating optimization results...")
    
    # Verify we have all the data
    for key, values in profiles_15min.items():
        print(f"  {key}: {len(values)} values")
    
    # Convert to 1-minute intervals
    print("\n  Converting 15-min to 1-min intervals...")
    bay_1_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_1'])
    bay_2_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_2'])
    bay_3_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_3'])
    bay_4_1min = interpolate_15min_to_1min(profiles_15min['loading_bay_4'])
    p_gas_total_1min = interpolate_15min_to_1min(profiles_15min['p_gas_total'])
    
    print(f"  Total minutes: {len(bay_1_1min)}")
    
    # Calculate derived values
    print("  Calculating derived values...")
    
    # P_truck_L1 = Loading Bay 2 + Loading Bay 4
    p_truck_l1 = [b2 + b4 for b2, b4 in zip(bay_2_1min, bay_4_1min)]
    
    # P_truck_L2 = Loading Bay 1 + Loading Bay 3
    p_truck_l2 = [b1 + b3 for b1, b3 in zip(bay_1_1min, bay_3_1min)]
    
    # P_Truck_Total
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
            # Negative or zero: conditional bidding  
            p_gas_conditional.append(abs(pg))
            p_gas_msol.append(0.0)
    
    # P_Physical = P_truck_Total + P_gas_Global
    p_physical = [pt + pg for pt, pg in zip(p_truck_total, p_gas_total_1min)]
    
    # P_Total_Used_Cap
    p_total_used_cap = [pt + max(0, pg) for pt, pg in zip(p_truck_total, p_gas_total_1min)]
    
    # Regime
    regime = []
    for pg_cond, pg_msol in zip(p_gas_conditional, p_gas_msol):
        if pg_msol > 0:
            regime.append('MSOL_Coverage')
        elif pg_cond > 0:
            regime.append('Conditional_Bidding')
        else:
            regime.append('Physical')
    
    num_minutes = len(p_truck_l1)
    
    # Create DataFrame
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
    """Main execution."""
    parser = argparse.ArgumentParser(description="Parse scraped Hera profile data.")
    parser.add_argument("--input-dir", type=str, default=str(DEFAULT_HERA_DATA_DIR), help="Directory containing JSON files")
    parser.add_argument("--output-csv", type=str, default=DEFAULT_OUTPUT_CSV, help="Path for output CSV")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input_dir)
    output_csv = Path(args.output_csv)
    
    print(f"\n{'='*70}")
    print(f"  HERA Profile Data Parser")
    print(f"{'='*70}\n")
    print(f"  Input Directory: {input_dir}")
    print(f"  Output CSV: {output_csv}")
    
    if not input_dir.exists():
        print(f"[ERROR] Input directory not found: {input_dir}")
        return

    # Find the most recent captured data file
    # Look for "inspection_" files as they have cleaner data
    inspection_files = sorted(input_dir.glob("inspection_*POST_GetProfilesScreenAsync.json"))
    
    if not inspection_files:
        print("[ERROR] No inspection files found!")
        print(f"   Looking for files in: {input_dir.absolute()}")
        return
    
    # Use the most recent file
    latest_file = inspection_files[-1]
    print(f"Using: {latest_file.name}")
    
    # Load profile data
    profiles = load_profile_data(latest_file)
    
    # Check if we got all profiles
    missing = [key for key, values in profiles.items() if not values]
    if missing:
        print(f"\n[WARNING] Missing profiles: {missing}")
        print("\nTrying to find the data in all captured files...")
        
        # Try all files
        all_files = list(input_dir.glob("*.json"))
        for file in all_files:
            if file.name.startswith("inspection_"):
                temp_profiles = load_profile_data(file)
                # Merge any found data
                for key in missing:
                    if temp_profiles[key]:
                        profiles[key] = temp_profiles[key]
                        print(f"  [FOUND] Found {key} in {file.name}")
        
        # Check again
        still_missing = [key for key, values in profiles.items() if not values]
        if still_missing:
            print(f"\n[ERROR] Still missing: {still_missing}")
            print("\nCannot proceed without all profiles.")
            return
    
    print("\n[SUCCESS] All profiles loaded successfully!")
    
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
    print(f"\nData Summary:")
    print(f"  Total minutes: {len(df)}")
    print(f"  Total days: {len(df) / 1440:.2f}")
    print(f"  Total hours: {len(df) / 60:.1f}")
    
    print(f"\nFirst 10 rows:")
    for col in ['P_truck_L1', 'P_truck_L2', 'P_gas_Global', 'P_Physical']:
        print(f"  {col:20s}: {df[col].min():8.2f} to {df[col].max():8.2f}")
    
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
