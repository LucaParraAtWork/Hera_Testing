"""
Visualize Hera profile data to verify the scraping was successful.
Creates graphs comparing the original data structure with the processed results.
"""

import argparse
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
from datetime import datetime, timedelta

# Default Configuration
DEFAULT_CSV_FILE = "../output/optimization_results_hera.csv"
DEFAULT_OUTPUT_DIR = Path("../output/visualizations")


def load_data(csv_path: str) -> pd.DataFrame:
    """Load the optimization results CSV."""
    df = pd.read_csv(csv_path)
    
    # Create datetime index (assuming data starts at a specific time)
    # Week 13 of 2026 starts around March 23, 2026
    start_time = datetime(2026, 3, 22, 23, 0)  # Based on the JSON timestamps
    df['DateTime'] = [start_time + timedelta(minutes=i) for i in range(len(df))]
    
    return df


def plot_loading_bays(df: pd.DataFrame, output_dir: Path):
    """Plot individual loading bay profiles."""
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle('Loading Bay Profiles - Week 13, 2026', fontsize=16, fontweight='bold')
    
    # Note: We don't have individual bay data in the CSV, but we can show the combined lanes
    # Bay 1 and Bay 3 contribute to P_truck_L2
    # Bay 2 and Bay 4 contribute to P_truck_L1
    
    # For visualization, we'll show the truck lanes instead
    ax1, ax2, ax3, ax4 = axes.flatten()
    
    # P_truck_L1 (Bay 2 + Bay 4)
    ax1.plot(df['DateTime'], df['P_truck_L1'], color='#0096FF', linewidth=1.5, label='P_truck_L1 (Bay 2+4)')
    ax1.set_title('Truck Loading Lane 1 (Bay 2 + Bay 4)', fontweight='bold')
    ax1.set_ylabel('Power (MW)')
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    
    # P_truck_L2 (Bay 1 + Bay 3)
    ax2.plot(df['DateTime'], df['P_truck_L2'], color='#50C878', linewidth=1.5, label='P_truck_L2 (Bay 1+3)')
    ax2.set_title('Truck Loading Lane 2 (Bay 1 + Bay 3)', fontweight='bold')
    ax2.set_ylabel('Power (MW)')
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    
    # Total Truck  
    ax3.plot(df['DateTime'], df['P_Truck_Total'], color='#FF6B35', linewidth=1.5, label='Total Truck')
    ax3.set_title('Total Truck Loading', fontweight='bold')
    ax3.set_ylabel('Power (MW)')
    ax3.grid(True, alpha=0.3)
    ax3.legend()
    
    # Gas Profile
    ax4.plot(df['DateTime'], df['P_gas_Global'], color='#0320fc', linewidth=1.5, label='Gas Injection')
    ax4.set_title('Gas Injection Profile', fontweight='bold')
    ax4.set_ylabel('Power (MW)')
    ax4.grid(True, alpha=0.3)
    ax4.legend()
    
    # Format x-axis for all subplots
    for ax in axes.flatten():
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    output_path = output_dir / "loading_bays_profiles.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()


def plot_combined_overview(df: pd.DataFrame, output_dir: Path):
    """Plot combined overview of all profiles."""
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(16, 12))
    fig.suptitle('Hera Energy Profiles Overview - Week 13, 2026', fontsize=16, fontweight='bold')
    
    # Truck loading lanes
    ax1.plot(df['DateTime'], df['P_truck_L1'], color='#0096FF', linewidth=1.5, label='Lane 1 (Bay 2+4)', alpha=0.8)
    ax1.plot(df['DateTime'], df['P_truck_L2'], color='#50C878', linewidth=1.5, label='Lane 2 (Bay 1+3)', alpha=0.8)
    ax1.plot(df['DateTime'], df['P_Truck_Total'], color='#FF6B35', linewidth=2, label='Total Truck', alpha=0.9)
    ax1.set_title('Truck Loading Profiles', fontweight='bold')
    ax1.set_ylabel('Power (MW)')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)
    
    # Gas profile
    ax2.plot(df['DateTime'], df['P_gas_Global'], color='#0320fc', linewidth=2, label='Gas Injection')
    ax2.fill_between(df['DateTime'], 0, df['P_gas_Global'], color='#0320fc', alpha=0.3)
    ax2.set_title('Gas Injection Profile', fontweight='bold')
    ax2.set_ylabel('Power (MW)')
    ax2.legend(loc='upper right')
    ax2.grid(True, alpha=0.3)
    
    # Total physical power
    ax3.plot(df['DateTime'], df['P_Physical'], color='#8B0000', linewidth=2, label='Total Physical Power', alpha=0.9)
    ax3.fill_between(df['DateTime'], 0, df['P_Physical'], color='#8B0000', alpha=0.2)
    ax3.set_title('Total Physical Power (Truck + Gas)', fontweight='bold')
    ax3.set_ylabel('Power (MW)')
    ax3.set_xlabel('Date & Time')
    ax3.legend(loc='upper right')
    ax3.grid(True, alpha=0.3)
    
    # Format x-axis
    for ax in [ax1, ax2, ax3]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    output_path = output_dir / "combined_overview.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()


def plot_regime_distribution(df: pd.DataFrame, output_dir: Path):
    """Plot regime distribution over time."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10))
    fig.suptitle('Operating Regime Analysis - Week 13, 2026', fontsize=16, fontweight='bold')
    
    # Regime over time (color-coded)
    regimes = df['Regime'].unique()
    colors = {'MSOL_Coverage': '#0320fc', 'Physical': '#FF6B35', 'Conditional_Bidding': '#FFA500'}
    
    for regime in regimes:
        mask = df['Regime'] == regime
        regime_data = df[mask].copy()
        ax1.scatter(regime_data['DateTime'], regime_data['P_Physical'], 
                   c=colors.get(regime, '#888888'), label=regime, alpha=0.6, s=10)
    
    ax1.set_title('Operating Regime Over Time', fontweight='bold')
    ax1.set_ylabel('Physical Power (MW)')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)
    
    # Regime distribution (pie chart on the right side of second subplot)
    regime_counts = df['Regime'].value_counts()
    
    # Create a table showing regime statistics
    ax2.axis('off')
    table_data = []
    table_data.append(['Regime', 'Minutes', 'Percentage', 'Avg Power (MW)'])
    
    for regime in regime_counts.index:
        count = regime_counts[regime]
        percentage = (count / len(df)) * 100
        avg_power = df[df['Regime'] == regime]['P_Physical'].mean()
        table_data.append([regime, f"{count:,}", f"{percentage:.1f}%", f"{avg_power:.2f}"])
    
    table = ax2.table(cellText=table_data, cellLoc='left', loc='center',
                     colWidths=[0.3, 0.2, 0.2, 0.2])
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 2)
    
    # Style header row
    for i in range(4):
        table[(0, i)].set_facecolor('#4472C4')
        table[(0, i)].set_text_props(weight='bold', color='white')
    
    # Format x-axis
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    ax1.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    output_path = output_dir / "regime_analysis.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()


def plot_data_statistics(df: pd.DataFrame, output_dir: Path):
    """Generate statistics summary plot."""
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle('Data Statistics - Week 13, 2026', fontsize=16, fontweight='bold')
    
    columns = ['P_truck_L1', 'P_truck_L2', 'P_Truck_Total', 'P_gas_Global', 'P_Physical', 'P_Total_Used_Cap']
    colors = ['#0096FF', '#50C878', '#FF6B35', '#0320fc', '#8B0000', '#FFD700']
    
    for idx, (col, color) in enumerate(zip(columns, colors)):
        ax = axes.flatten()[idx]
        
        # Histogram
        ax.hist(df[col], bins=50, color=color, alpha=0.7, edgecolor='black')
        ax.set_title(col.replace('_', ' '), fontweight='bold')
        ax.set_xlabel('Power (MW)')
        ax.set_ylabel('Frequency (minutes)')
        ax.grid(True, alpha=0.3, axis='y')
        
        # Add statistics text
        mean_val = df[col].mean()
        max_val = df[col].max()
        min_val = df[col].min()
        
        stats_text = f'Min: {min_val:.2f}\nMean: {mean_val:.2f}\nMax: {max_val:.2f}'
        ax.text(0.98, 0.97, stats_text, transform=ax.transAxes,
               verticalalignment='top', horizontalalignment='right',
               bbox=dict(boxstyle='round', facecolor='white', alpha=0.8),
               fontsize=9, family='monospace')
    
    plt.tight_layout()
    output_path = output_dir / "data_statistics.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Visualize Hera profile data.")
    parser.add_argument("--input-csv", type=str, default=DEFAULT_CSV_FILE, help="Path to optimization results CSV")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Directory to save visualizations")
    
    args = parser.parse_args()
    
    csv_file = args.input_csv
    output_dir = Path(args.output_dir)
    
    print(f"\n{'='*70}")
    print(f"  Hera Profile Data Visualization")
    print(f"{'='*70}\n")
    print(f"  Input CSV: {csv_file}")
    print(f"  Output Directory: {output_dir}")
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load data
    print("Loading data...")
    try:
        df = load_data(csv_file)
        print(f"  Loaded {len(df):,} data points ({len(df)/60:.1f} hours)")
    except Exception as e:
        print(f"  [ERROR] Error loading data: {e}")
        return
    
    # Generate plots
    print("\nGenerating visualizations...")
    
    print("\n  1. Loading bay profiles...")
    plot_loading_bays(df, output_dir)
    
    print("\n  2. Combined overview...")
    plot_combined_overview(df, output_dir)
    
    print("\n  3. Regime analysis...")
    plot_regime_distribution(df, output_dir)
    
    print("\n  4. Data statistics...")
    plot_data_statistics(df, output_dir)
    
    print(f"\n{'='*70}")
    print(f"Visualization Complete!")
    print(f"{'='*70}")
    print(f"\nOutput directory: {output_dir.absolute()}")
    print(f"\nGenerated {len(list(output_dir.glob('*.png')))} visualization files:")
    for img in sorted(output_dir.glob('*.png')):
        print(f"  - {img.name}")
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
