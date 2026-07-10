import pandas as pd
import json
import os
import sys
import numpy as np
from pathlib import Path

# ==========================================
# 1. PARAMÈTRES & CONSTANTES DE CONVERSION
# ==========================================
def load_config(file_path="parameters.csv"):
    try:
        df = pd.read_csv(file_path)
        return dict(zip(df['Parameter'], df['Value']))
    except FileNotFoundError:
        print(f"Attention : {file_path} introuvable. Utilisation des valeurs par défaut.")
        return {}

def find_latest_profiles_json():
    print("=== RECHERCHE DU FICHIER JSON PROFILES (STEP 07) ===")
    artifacts_dir = Path("artifacts")
    
    if not artifacts_dir.exists():
        print("Erreur : Le dossier 'artifacts' n'existe pas.")
        return None

    runs = sorted([d for d in artifacts_dir.iterdir() if d.is_dir() and d.name[0].isdigit()])
    if not runs: return None
        
    latest_run = runs[-1]
    data_dir = latest_run / "step07_profiles" / "data"
    
    if not data_dir.exists(): return None
        
    json_files = list(data_dir.glob("*POST_*ProfilesScreen*.json"))
    if not json_files: return None

    return json_files[0]
def find_latest_scheduling_json():
    print("=== RECHERCHE DU FICHIER JSON SCHEDULING (STEP 06) ===")
    artifacts_dir = Path("artifacts")
    
    if not artifacts_dir.exists():
        return None

    runs = sorted([d for d in artifacts_dir.iterdir() if d.is_dir() and d.name[0].isdigit()])
    if not runs: return None
        
    latest_run = runs[-1]
    data_dir = latest_run / "step06_confirm" / "data"
    
    if not data_dir.exists(): return None
        
    # FIX: Removed the underscore before POST. 
    # Added sorting to ensure if there are multiple weeks, it grabs the most recent one.
    json_files = list(data_dir.glob("*POST_api_SchedulingScreen*.json"))
    
    if not json_files: return None

    # Sort files by name so the latest date (e.g., 2026-06-22) is at the end of the list, and pick it
    json_files.sort()
    
    return json_files[-1]

# ==========================================
# 2. FONCTION D'EFFICACITÉ DYNAMIQUE (U-Curve)
# ==========================================
def get_plant_power_mw(flow_gs):
    """
    Applique la cartographie d'efficacité dynamique U-Curve
    flow_gs -> consommation spécifique (kWh/Nm3) -> Puissance (MW)
    """
    GS_TO_NM3H = 40.0445
    
    FLOW_POINTS_GS = [18.88, 60.432, 124.86]  # MSOL, BEP, MCR
    CS_POINTS      = [5.09, 4.54, 4.66]       # Specific Consumption kWh/Nm3
    
    flow_gs = np.asarray(flow_gs)
    flow_clipped = np.clip(flow_gs, 18.88, 124.86)
    
    cs_kwh_nm3 = np.interp(flow_clipped, FLOW_POINTS_GS, CS_POINTS)
    flow_nm3h = flow_gs * GS_TO_NM3H
    
    power_mw = np.where(flow_gs > 1e-3, (flow_nm3h * cs_kwh_nm3) / 1000.0, 0.0)
    return power_mw

# ==========================================
# 3. HTML DASHBOARD GENERATOR
# ==========================================
def generate_html_dashboard(output_data, output_path):
    """
    Generates an HTML file replicating the Costs Breakdown dashboard tab.
    Reads from the structured output_data dict produced by calculate_metrics.
    """
    date_str = output_data.get("Date", "N/A")
    offtakers = output_data.get("Offtakers", {})
    offtaker_names = list(offtakers.keys())
    name_l1 = offtaker_names[0] if len(offtaker_names) > 0 else "Offtaker 1"
    name_l2 = offtaker_names[1] if len(offtaker_names) > 1 else "Offtaker 2"

    plant = output_data.get("Plant_Summary", {})
    l1    = offtakers.get(name_l1, {})
    l2    = offtakers.get(name_l2, {})

    def g(d, *keys, default=0.0):
        for k in keys:
            if not isinstance(d, dict): return default
            d = d.get(k, default)
        return d if d is not None else default

    price_elec = output_data.get("Price_Elec_Eur_per_MWh", 100.0)
    price_gas  = output_data.get("Price_Gas_Eur_per_MWh", 30.0)

    # --- Total Planning ---
    tp_grid_elec_mwh       = g(plant, "Energy_Flows_MWh", "Grid_Electricity")
    tp_truck_load_mwh      = g(plant, "Energy_Flows_MWh", "Truck_Load")
    tp_gas_vol_mwh         = 0.0  
    tp_gas_msol_mwh        = g(plant, "Energy_Flows_MWh", "Gas_Injection_Electricity")
    tp_gas_inj_mwh         = g(plant, "Energy_Flows_MWh", "Gas_Injection")

    tp_cost_truck_eur      = tp_truck_load_mwh * price_elec
    tp_cost_gas_vol_eur    = 0.0
    tp_gas_vol_rev_eur     = 0.0
    tp_msol_coverage_eur   = (tp_gas_msol_mwh * price_elec) - tp_gas_inj_mwh * price_gas
    tp_net_costs_eur       = tp_cost_truck_eur + (tp_gas_msol_mwh * price_elec) - tp_gas_inj_mwh * price_gas

    # --- L1 ---
    l1_grid_elec_mwh       = g(l1, "Energy_Flows_MWh", "Grid_Electricity")
    l1_truck_load_mwh      = g(l1, "Energy_Flows_MWh", "Truck_Load")
    l1_gas_vol_mwh         = 0.0
    l1_gas_msol_mwh        = g(l1, "Energy_Flows_MWh", "Gas_Injection_Electricity")
    l1_gas_inj_mwh         = g(l1, "Energy_Flows_MWh", "Gas_Injection")

    l1_cost_truck_eur      = l1_truck_load_mwh * price_elec
    l1_cost_gas_vol_eur    = 0.0
    l1_gas_vol_rev_eur     = 0.0
    l1_msol_coverage_eur   = l1_gas_inj_mwh * price_gas
    l1_net_costs_eur       = l1_cost_truck_eur + (l1_gas_msol_mwh * price_elec) - l1_msol_coverage_eur

    # --- L2 ---
    l2_grid_elec_mwh       = g(l2, "Energy_Flows_MWh", "Grid_Electricity")
    l2_truck_load_mwh      = g(l2, "Energy_Flows_MWh", "Truck_Load")
    l2_gas_vol_mwh         = 0.0
    l2_gas_msol_mwh        = g(l2, "Energy_Flows_MWh", "Gas_Injection_Electricity")
    l2_gas_inj_mwh         = g(l2, "Energy_Flows_MWh", "Gas_Injection")

    l2_cost_truck_eur      = l2_truck_load_mwh * price_elec
    l2_cost_gas_vol_eur    = 0.0
    l2_gas_vol_rev_eur     = 0.0
    l2_msol_coverage_eur   = l2_gas_inj_mwh * price_gas
    l2_net_costs_eur       = l2_cost_truck_eur + (l2_gas_msol_mwh * price_elec) - l2_msol_coverage_eur

    l1_total_kg  = g(l1, "Total_H2_Allocated_kg")
    l2_total_kg  = g(l2, "Total_H2_Allocated_kg")
    tp_total_kg  = g(plant, "Total_H2_Allocated_kg")

    l1_ncc = round(l1_net_costs_eur / l1_total_kg, 2) if l1_total_kg > 0 else 0.0
    l2_ncc = round(l2_net_costs_eur / l2_total_kg, 2) if l2_total_kg > 0 else 0.0
    tp_ncc = round(tp_net_costs_eur / tp_total_kg, 2) if tp_total_kg > 0 else 0.0

    def fmt_mwh(v): return f"{v:,.2f} MWh".replace(",", "\u00a0")
    def fmt_eur(v):
        s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        prefix = "- " if v < 0 else ""
        return f"€\u00a0{prefix}{s}"
    def fmt_ncc(v):
        s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} €/kg"

    rows = [
        ("Electricity Consumed",                 fmt_mwh(l1_grid_elec_mwh),    fmt_mwh(l2_grid_elec_mwh),    fmt_mwh(tp_grid_elec_mwh),    False),
        ("Truck Filling",                        fmt_mwh(l1_truck_load_mwh),   fmt_mwh(l2_truck_load_mwh),   fmt_mwh(tp_truck_load_mwh),   False),
        ("Gas (Voluntary)",                      fmt_mwh(l1_gas_vol_mwh),      fmt_mwh(l2_gas_vol_mwh),      fmt_mwh(tp_gas_vol_mwh),      False),
        ("Gas (MSOL)",                           fmt_mwh(l1_gas_msol_mwh),     fmt_mwh(l2_gas_msol_mwh),     fmt_mwh(tp_gas_msol_mwh),     False),
        ("Electricity Costs For Truck Filling",  fmt_eur(l1_cost_truck_eur),   fmt_eur(l2_cost_truck_eur),   fmt_eur(tp_cost_truck_eur),   True),
        ("Electricity Costs for Voluntary Gas",  fmt_eur(l1_cost_gas_vol_eur), fmt_eur(l2_cost_gas_vol_eur), fmt_eur(tp_cost_gas_vol_eur), True),
        ("Voluntary Gas Revenue",                fmt_eur(l1_gas_vol_rev_eur),  fmt_eur(l2_gas_vol_rev_eur),  fmt_eur(tp_gas_vol_rev_eur),  True),
        ("MSOL Coverage",                        fmt_eur(l1_msol_coverage_eur),fmt_eur(l2_msol_coverage_eur),fmt_eur(tp_msol_coverage_eur),True),
        ("Net Costs",                            fmt_eur(l1_net_costs_eur),    fmt_eur(l2_net_costs_eur),    fmt_eur(tp_net_costs_eur),    True),
    ]

    def build_row(label, v_l1, v_l2, v_tp, separator_after=False):
        sep_style = ' style="border-bottom: 2px solid #c8d8d0;"' if separator_after else ''
        return f"""
        <tr{sep_style}>
            <td class="label-cell">{label}</td>
            <td class="value-cell">{v_l1}</td>
            <td class="value-cell">{v_l2}</td>
            <td class="value-cell total-cell">{v_tp}</td>
        </tr>"""

    rows_html = ""
    for i, (label, v_l1, v_l2, v_tp, _) in enumerate(rows):
        sep = (i == 3)
        rows_html += build_row(label, v_l1, v_l2, v_tp, separator_after=sep)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Costs Breakdown — {date_str}</title>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        :root {{
            --teal-dark:   #1a5c4a;
            --teal-mid:    #1d7a5e;
            --teal-light:  #2a9d7e;
            --teal-pale:   #e8f4f0;
            --teal-border: #c8d8d0;
            --header-bg:   #2d3b35;
            --row-odd:     #f7fbf9;
            --row-even:    #ffffff;
            --text-main:   #1a2520;
            --text-muted:  #5a7060;
            --ncc-red:     #c0392b;
            --total-bg:    #f0f7f4;
            --tab-active:  #ffffff;
            --tab-inactive:#e0e8e4;
        }}
        body {{ font-family: 'IBM Plex Sans', sans-serif; background: #f2f7f5; color: var(--text-main); min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 24px 16px; }}
        .dashboard-card {{ background: #ffffff; border: 1px solid var(--teal-border); border-radius: 10px; width: 100%; max-width: 1100px; overflow: hidden; box-shadow: 0 4px 20px rgba(26,92,74,0.10); }}
        .tab-bar {{ display: flex; border-bottom: 2px solid var(--teal-border); background: var(--tab-inactive); }}
        .tab {{ padding: 12px 28px; font-size: 0.88rem; font-weight: 500; color: var(--text-muted); cursor: default; border-right: 1px solid var(--teal-border); letter-spacing: 0.01em; }}
        .tab.active {{ background: var(--tab-active); color: var(--teal-dark); font-weight: 600; border-bottom: 2px solid var(--teal-dark); margin-bottom: -2px; }}
        .toolbar {{ display: flex; align-items: center; justify-content: space-between; padding: 12px 20px; border-bottom: 1px solid var(--teal-border); background: #fafcfb; }}
        .unit-toggle {{ display: flex; border: 1.5px solid var(--teal-dark); border-radius: 6px; overflow: hidden; }}
        .unit-btn {{ padding: 6px 16px; font-size: 0.82rem; font-weight: 600; font-family: 'IBM Plex Mono', monospace; cursor: pointer; border: none; background: transparent; color: var(--teal-dark); transition: background 0.15s, color 0.15s; }}
        .unit-btn.active {{ background: var(--teal-dark); color: #fff; }}
        .date-nav {{ display: flex; align-items: center; gap: 6px; }}
        .date-nav .date-label {{ padding: 6px 18px; background: var(--teal-dark); color: #fff; border-radius: 6px; font-size: 0.85rem; font-weight: 600; font-family: 'IBM Plex Mono', monospace; letter-spacing: 0.03em; }}
        .nav-btn {{ width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: var(--teal-dark); color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 700; }}
        .table-wrapper {{ overflow-x: auto; padding: 0; }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead tr {{ background: var(--header-bg); }}
        thead th {{ padding: 13px 20px; text-align: left; font-size: 0.82rem; font-weight: 600; color: #ffffff; letter-spacing: 0.04em; text-transform: uppercase; }}
        thead th:not(:first-child) {{ text-align: right; }}
        tbody tr:nth-child(odd)  {{ background: var(--row-odd);  }}
        tbody tr:nth-child(even) {{ background: var(--row-even); }}
        tbody tr:hover {{ background: var(--teal-pale); transition: background 0.12s; }}
        td {{ padding: 11px 20px; font-size: 0.88rem; border-bottom: 1px solid #edf3f0; }}
        .label-cell {{ font-weight: 400; color: var(--text-main); white-space: nowrap; }}
        .value-cell {{ text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 0.86rem; color: var(--text-main); white-space: nowrap; }}
        .total-cell {{ color: var(--teal-dark); font-weight: 600; }}
        tr.net-costs-row td {{ font-weight: 600; border-top: 2px solid var(--teal-border); color: var(--teal-dark); }}
        .ncc-section {{ display: flex; justify-content: flex-end; gap: 16px; padding: 20px 24px 24px; flex-wrap: wrap; }}
        .ncc-card {{ border: 2px solid var(--teal-dark); border-radius: 8px; padding: 14px 24px; min-width: 180px; text-align: center; }}
        .ncc-card.total {{ border-color: #2563eb; }}
        .ncc-label {{ font-size: 0.78rem; font-weight: 600; color: var(--text-muted); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 6px; }}
        .ncc-value {{ font-family: 'IBM Plex Mono', monospace; font-size: 1.45rem; font-weight: 700; color: var(--ncc-red); line-height: 1.1; }}
        .ncc-entity {{ font-size: 0.78rem; color: var(--text-muted); margin-top: 4px; font-weight: 500; }}
        .dashboard-footer {{ margin-top: 18px; font-size: 0.74rem; color: var(--text-muted); letter-spacing: 0.03em; }}
    </style>
</head>
<body>

<div class="dashboard-card">
    <div class="tab-bar">
        <div class="tab">Maintenance</div>
        <div class="tab">Bay Occupancy</div>
        <div class="tab">Profiles</div>
        <div class="tab active">Costs Breakdown</div>
    </div>
    <div class="toolbar">
        <div class="unit-toggle">
            <button class="unit-btn active" onclick="setUnit('mwh', this)">€/MWh</button>
            <button class="unit-btn" onclick="setUnit('kg', this)">€/kg</button>
        </div>
        <div class="date-nav">
            <button class="nav-btn">&lt;</button>
            <span class="date-label">{date_str}</span>
            <button class="nav-btn">&gt;</button>
            <button class="nav-btn">&gt;&gt;</button>
        </div>
    </div>
    <div class="table-wrapper">
        <table>
            <thead>
                <tr>
                    <th>Quantities</th>
                    <th style="text-align:right">{name_l1}</th>
                    <th style="text-align:right">{name_l2}</th>
                    <th style="text-align:right">Total Planning</th>
                </tr>
            </thead>
            <tbody id="table-body">
                {rows_html}
            </tbody>
        </table>
    </div>
    <div class="ncc-section">
        <div class="ncc-card">
            <div class="ncc-label">NCC Forecast</div>
            <div class="ncc-value">{fmt_ncc(l1_ncc)}</div>
            <div class="ncc-entity">{name_l1}</div>
        </div>
        <div class="ncc-card">
            <div class="ncc-label">NCC Forecast</div>
            <div class="ncc-value">{fmt_ncc(l2_ncc)}</div>
            <div class="ncc-entity">{name_l2}</div>
        </div>
        <div class="ncc-card total">
            <div class="ncc-label">NCC Forecast</div>
            <div class="ncc-value">{fmt_ncc(tp_ncc)}</div>
            <div class="ncc-entity">Total Planning</div>
        </div>
    </div>
</div>

<div class="dashboard-footer">
    Generated by calculate_metrics.py &mdash; Date: {date_str}
    &nbsp;|&nbsp; Price Elec: {price_elec:.1f} €/MWh &nbsp;|&nbsp; Price Gas: {price_gas:.1f} €/MWh
</div>

<script>
    (function() {{
        const rows = document.querySelectorAll('#table-body tr');
        if (rows.length > 0) {{ rows[rows.length - 1].classList.add('net-costs-row'); }}
    }})();
    function setUnit(unit, btn) {{
        document.querySelectorAll('.unit-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }}
</script>

</body>
</html>"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  → HTML dashboard saved: {output_path}")


import re
def get_costs_breakdown_api_data_by_date():
    print("\n" + "="*60)
    print("🔍 CLASSIFICATION ET LECTURE DES FICHIERS API NETTOYÉS (STEP 09)")
    print("="*60)
    
    artifacts_dir = Path("artifacts")
    if not artifacts_dir.exists(): return {}

    runs = sorted([d for d in artifacts_dir.iterdir() if d.is_dir() and d.name[0].isdigit()])
    if not runs: return {}
        
    latest_run = runs[-1]
    step09_dirs = list(latest_run.glob("step09*"))
    if not step09_dirs: return {}
    
    data_dir = step09_dirs[0] / "data"
    if not data_dir.exists(): return {}
        
    # CORRECTION DU GLOB : On cherche tous les fichiers contenant CostsBreakdownScreen
    json_files = list(data_dir.glob("*CostsBreakdownScreen*.json"))
    
    # Tri par date de modification du fichier (les plus récents en dernier)
    json_files.sort(key=lambda p: p.stat().st_mtime)
    
    api_data_by_date = {}
    file_mapping_log = {} 
    date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
    
    for file_path in json_files:
        match = date_pattern.search(file_path.name)
        if match:
            date_str = match.group(1)
            try:
                with open(file_path, 'r', encoding="utf-8") as f:
                    content = json.load(f)
                
                if content.get("status") == 200 and "data" in content:
                    # Grâce à votre capture nettoyée, chaque jour s'écrase proprement.
                    if date_str in api_data_by_date:
                        file_mapping_log[date_str] = f"{file_path.name} (Mise à jour/Écrasement)"
                    else:
                        file_mapping_log[date_str] = f"{file_path.name} [Aligné]"
                        
                    api_data_by_date[date_str] = content
            except Exception as e:
                print(f"❌ Erreur de lecture {file_path.name}: {e}")
                
    print("\n📅 CORRESPONDANCE STRICTE DES JOURS RECONNUS :")
    print("-" * 60)
    for d_str in sorted(api_data_by_date.keys()):
        print(f"  • {d_str} ➡️ {file_mapping_log.get(d_str)}")
    print("-" * 60 + "\n")
    
    return api_data_by_date
    
def compare_ncc_terminal_daily(api_data, calculated_nccs, current_date_str):
    if not api_data:
        print(f"⚠️ Aucune donnée API trouvée pour le {current_date_str}. Comparaison annulée.")
        return

    try:
        api_forecasts = api_data.get("data", {}).get("netCommodityCostForecasts", [])
        api_ncc_dict = {item["name"]: item["value"] for item in api_forecasts}

        print("\n" + "="*60)
        print(f"📊 COMPARAISON NCC STRICTE POUR LE : {current_date_str}")
        print("="*60)

        for entity_name, calc_val in calculated_nccs.items():
            api_val = api_ncc_dict.get(entity_name)
            
            if api_val is not None:
                diff = calc_val - api_val
                pct_diff = (diff / api_val * 100) if api_val != 0 else 0.0
                
                print(f"[{entity_name}]")
                print(f"  Source API : {api_val:.4f} €/kg")
                print(f"  Calcul Py  : {calc_val:.4f} €/kg")
                
                diff_str = f"{diff:+.4f} €/kg ({pct_diff:+.2f}%)"
                if abs(pct_diff) < 1.0 or abs(diff) < 0.02:
                    print(f"  Résultat   : ✅ {diff_str}")
                else:
                    print(f"  Résultat   : ❌ {diff_str}")
                print("-" * 60)
            else:
                print(f"⚠️ {entity_name} non trouvé dans l'API pour ce jour.")

    except Exception as e:
        print(f"❌ Erreur lors de la comparaison pour {current_date_str}: {e}")

# ==========================================
# MAIN EXECUTION
# ==========================================
def calculate_metrics(output_dir_name="daily_metrics"):
    # Chargement des paramètres
    params = load_config()
    
    P_MAX     = float(params.get('P_MAX', 25.0))
    M_SOL     = float(params.get('M_SOL', 5.0))
    ETA       = float(params.get('ETA', 0.643))
    P_FLUXYS  = float(params.get('P_FLUXYS', 25.0))
    PI_AFRR   = float(params.get('PI_AFRR', 0.0))
    PRICE_ELEC = float(params.get('PRICE_ELEC', 100.0))
    PRICE_GAS  = float(params.get('PRICE_GAS', 30.0))

    LHV_H2 = 0.033333  # MWh/kg
    HHV_H2 = 0.03941   # MWh/kg
    MW_PER_GS = (3.6 * LHV_H2) / ETA
    GS_PER_MW = 1.0 / MW_PER_GS
    M_SOL_GS  = M_SOL * GS_PER_MW

    # --- 1. LECTURE PROFILES JSON (STEP 07) ---
    json_file = find_latest_profiles_json()
    if not json_file:
        print("Échec de la recherche du fichier JSON d'entrée.")
        sys.exit(1)
        
    try:
        with open(json_file, 'r', encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Erreur de lecture de {json_file} : {e}")
        sys.exit(1)
        
    api_data = data.get('data', {})
    
    # --- 2. LECTURE SCHEDULING JSON (STEP 06) ---
    sched_file = find_latest_scheduling_json()
    pickups = []
    company_map = {}
    
    if sched_file:
        try:
            with open(sched_file, 'r', encoding="utf-8") as f:
                sched_data = json.load(f)
                
            # Build company map (ID -> Name)
            for comp in sched_data.get('data', {}).get('availableCompanies', []):
                company_map[comp['id']] = comp['name']
                
            # In step 2 (LECTURE SCHEDULING JSON)
            for slot in sched_data.get('data', {}).get('slots', []):
                for transfer in slot.get('scheduledTransfers', []):
                    if transfer.get('type', '').lower() == 'pickup' and transfer.get('status', '').lower() == 'confirmed':
                        pickups.append({
                            'time': pd.to_datetime(transfer['earliest'], utc=True),
                            'company': company_map.get(transfer['companyId'], 'Unknown')
                        })
            
            # Sort pickups by time to ensure we grab the "first pickup after X"
            pickups = sorted(pickups, key=lambda x: x['time'])
            print(f"  → {len(pickups)} Pickups chargés depuis le Scheduling.")
            
        except Exception as e:
            print(f"Erreur lors du traitement de {sched_file} : {e}")
    else:
        print("  → Attention: Fichier Scheduling (step06) non trouvé. Allocation par défaut sera utilisée.")

    # Analyse des données Profils
    df_data = {}
    bay_names_found = []
    
    for graph in api_data.get('profileGraphs', []):
        title = graph.get('title', '')
        for line in graph.get('profileGraphLines', []):
            name   = line.get('name', '')
            points = line.get('points', {})
            
            if "truck fillings by loading bay" in title.lower():
                safe_name = f"Bay_{name.replace(' ', '_')}"
                df_data[safe_name] = points
                bay_names_found.append(safe_name)
            elif ("gas injection" in title.lower()
                  or "injection to fluxys" in title.lower()
                  or ("gas" in title.lower() and "injection" in title.lower())):
                df_data['M_gas_Global'] = points
            elif "afrr" in title.lower() or "capacity allocation" in title.lower():
                df_data['P_aFRR_Reserved'] = points

    if not df_data:
        print("Erreur : Aucune donnée.")
        sys.exit(1)

    df = pd.DataFrame(df_data)
    # Ensure UTC awareness for strict time matching
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)
    
    if 'P_aFRR_Reserved' not in df.columns: df['P_aFRR_Reserved'] = 0.0
    df.fillna(0, inplace=True)
    
    df['Price_Elec'] = PRICE_ELEC
    df['Price_Gas']  = PRICE_GAS
    
    offtaker_names = [oc.get('offtakerName') for oc in api_data.get('netCommodityCostsChart', {}).get('offtakerCosts', [])]
    if len(offtaker_names) >= 2:
        name_l1, name_l2 = offtaker_names[0], offtaker_names[1]
    elif len(offtaker_names) == 1:
        name_l1, name_l2 = offtaker_names[0], "Offtaker 2"
    else:
        name_l1, name_l2 = "Virya Energy NV", "Messer Belgium NV"

    # ==========================================
    # NEW LOGIC: SMART TRUCK FILLING ALLOCATION
    # ==========================================
    df['M_truck_Total'] = df[bay_names_found].sum(axis=1) if bay_names_found else 0.0
    df['M_truck_L1'] = 0.0
    df['M_truck_L2'] = 0.0
    
    trailer_tracking_log = [] # Keeps track for the JSON injection

    print("\n" + "="*60)
    print("🚚 DYNAMIC TRAILER ALLOCATION LOG")
    print("="*60)
    # Obtenir le nom du fichier utilisé pour les Pickups
    sched_file_name = sched_file.name if sched_file else "Aucun fichier Scheduling (Default)"
    
    # Compute a smart default: the company with the most confirmed pickups.
    # This avoids silently defaulting unmatched fillings to name_l1 when only
    # one offtaker has nominations confirmed in the scheduling JSON.
    from collections import Counter
    if pickups:
        pickup_counts = Counter(p['company'] for p in pickups)
        default_company = pickup_counts.most_common(1)[0][0]
        print(f"  Confirmed pickup distribution: {dict(pickup_counts)}")
        print(f"  Default offtaker for unmatched fillings: {default_company}\n")
    else:
        default_company = name_l1
        print(f"  No confirmed pickups found — defaulting all fillings to {name_l1}\n")

    # Work on a mutable copy so each pickup can only be claimed once.
    # This mirrors the API: one physical trailer = one pickup = one filling session.
    available_pickups = list(pickups)

    # Collect all filling windows across all bays first, then sort by start time
    # so the closest-in-time bay always wins a contested pickup.
    all_fillings = []
    for bay in bay_names_found:
        is_flowing = df[bay] > 0
        trailer_id = (is_flowing != is_flowing.shift()).cumsum()
        active_flows = df[is_flowing]
        for t_id, trailer_data in active_flows.groupby(trailer_id):
            if not trailer_data.empty:
                all_fillings.append((bay, t_id, trailer_data))

    all_fillings.sort(key=lambda x: x[2].index[0])

    for bay, t_id, trailer_data in all_fillings:
        end_time = trailer_data.index[-1]
        start_time = trailer_data.index[0]

        assigned_company = default_company
        pickup_matched = None

        if available_pickups:
            # Find the closest still-available pickup to this filling start.
            # Window is 4 hours to safely cover UTC/CEST offset (2h) plus slot variance.
            closest_pickup = min(available_pickups, key=lambda p: abs((p['time'] - start_time).total_seconds()))

            if abs((closest_pickup['time'] - start_time).total_seconds()) <= 14400:
                assigned_company = closest_pickup['company']
                pickup_matched = str(closest_pickup['time'])
                available_pickups.remove(closest_pickup)  # consume — cannot be reused

        mass_allocated_kg = round((trailer_data[bay] * 900).sum() / 1000, 2)

        print(f"[{bay.replace('Bay_', '')}] {start_time.strftime('%Y-%m-%d %H:%M')} -> {end_time.strftime('%H:%M')} "
              f"| {mass_allocated_kg:,.2f} kg | Offtaker: {assigned_company} "
              f"| (Matched Pickup: {pickup_matched})")

        trailer_tracking_log.append({
            "bay": bay.replace("Bay_", ""),
            "start_time": str(start_time),
            "end_time": str(end_time),
            "assigned_offtaker": assigned_company,
            "matched_pickup_time": pickup_matched,
            "mass_allocated_kg": mass_allocated_kg
        })

        if assigned_company == name_l2:
            df.loc[trailer_data.index, 'M_truck_L2'] += trailer_data[bay]
        else:
            df.loc[trailer_data.index, 'M_truck_L1'] += trailer_data[bay]

    if available_pickups:
        print(f"  WARN: {len(available_pickups)} pickup(s) were not matched to any bay filling:")
        for p in available_pickups:
            print(f"    - {p['company']} @ {p['time']}")

    print("="*60 + "\n")
    # Si le profil M_gas_Global n'a pas été trouvé dans le JSON, fallback MSOL
    if 'M_gas_Global' not in df.columns:
        print("Warning: 'M_gas_Global' profile not found. Falling back to MSOL formula.")
        df['M_gas_Global'] = np.maximum(0, M_SOL_GS - df['M_truck_Total'])
    
    # ==========================================
    # 4. CALCUL DES ALPHAS
    # ==========================================
    alpha_gas_l1_list,  alpha_gas_l2_list  = [], []
    alpha_afrr_l1_list, alpha_afrr_l2_list = [], []
    
    for index, row in df.iterrows():
        m_truck_l1    = row['M_truck_L1']
        m_truck_l2    = row['M_truck_L2']
        m_truck_total = row['M_truck_Total']
        m_gas         = row['M_gas_Global']
        
        # --- GAS ALPHA ---
        m_cb = 0.0
        m_cov = 0.0
        
        # Fallback to separate CB vs MSOL if only Global Gas is available
        if m_gas > 1e-6:
            if (m_truck_total + m_gas) > (M_SOL_GS + 1e-4):
                m_cb = m_gas
            else:
                m_cov = m_gas

        if m_gas <= 1e-6:
            g_alpha_l1, g_alpha_l2 = 0.0, 0.0
        elif m_cb > 0:
            # Conditional Bidding: Share based on remaining capacity
            limit_per_offtaker_gs = min(P_MAX, P_FLUXYS) * GS_PER_MW / 2.0
            f_1 = max(0, limit_per_offtaker_gs - m_truck_l1)
            f_2 = max(0, limit_per_offtaker_gs - m_truck_l2)
            total_f = f_1 + f_2
            if total_f > 0:
                g_alpha_l1, g_alpha_l2 = f_1 / total_f, f_2 / total_f
            else:
                g_alpha_l1, g_alpha_l2 = 0.5, 0.5
        elif m_cov > 0:
            # MSOL Coverage: Penalty for taking fewer trucks
            shortfall_1 = min(0, m_truck_l1 - M_SOL_GS / 2.0)
            shortfall_2 = min(0, m_truck_l2 - M_SOL_GS / 2.0)
            total_shortfall = shortfall_1 + shortfall_2
            if total_shortfall < 0:
                g_alpha_l1, g_alpha_l2 = shortfall_1 / total_shortfall, shortfall_2 / total_shortfall
            else:
                g_alpha_l1, g_alpha_l2 = 0.5, 0.5
                
        alpha_gas_l1_list.append(g_alpha_l1)
        alpha_gas_l2_list.append(g_alpha_l2)
        
        # --- aFRR ALPHA ---
        p_truck_l1_mw    = m_truck_l1    * MW_PER_GS
        p_truck_l2_mw    = m_truck_l2    * MW_PER_GS
        p_truck_total_mw = m_truck_total * MW_PER_GS

        limit_cap = min(P_MAX, P_FLUXYS)
        base_flex = (limit_cap - M_SOL) / 2.0
        
        if p_truck_total_mw <= M_SOL:
            a_alpha_l1, a_alpha_l2 = 0.5, 0.5
        else:
            l1_low = p_truck_l1_mw <= M_SOL / 2.0
            l2_low = p_truck_l2_mw <= M_SOL / 2.0
            
            if l1_low and not l2_low:
                c_2 = (p_truck_total_mw - M_SOL) * (p_truck_l2_mw / p_truck_total_mw)
                f_2 = max(0, base_flex - c_2)
                f_1 = base_flex
                tot_f = f_1 + f_2
                a_alpha_l1 = f_1 / tot_f if tot_f > 0 else 0.5
                a_alpha_l2 = 1.0 - a_alpha_l1
            elif l2_low and not l1_low:
                c_1 = (p_truck_total_mw - M_SOL) * (p_truck_l1_mw / p_truck_total_mw)
                f_1 = max(0, base_flex - c_1)
                f_2 = base_flex
                tot_f = f_1 + f_2
                a_alpha_l1 = f_1 / tot_f if tot_f > 0 else 0.5
                a_alpha_l2 = 1.0 - a_alpha_l1
            else:
                limit_half = limit_cap / 2.0
                f_1 = max(0, limit_half - p_truck_l1_mw)
                f_2 = max(0, limit_half - p_truck_l2_mw)
                tot_f = f_1 + f_2
                if tot_f > 0:
                    a_alpha_l1 = f_1 / tot_f
                    a_alpha_l2 = f_2 / tot_f
                else:
                    a_alpha_l1, a_alpha_l2 = 0.5, 0.5
                    
        alpha_afrr_l1_list.append(a_alpha_l1)
        alpha_afrr_l2_list.append(a_alpha_l2)
        
    df['Alpha_Gas_L1']  = alpha_gas_l1_list
    df['Alpha_Gas_L2']  = alpha_gas_l2_list
    df['Alpha_aFRR_L1'] = alpha_afrr_l1_list
    df['Alpha_aFRR_L2'] = alpha_afrr_l2_list

    # ==========================================
    # 5. TOP-DOWN FINANCIAL CALCULATIONS
    # ==========================================
    dt_sec = 900.0
    dt_hr  = 15.0 / 60.0

    df['Mass_Truck_L1_kg']    = (df['M_truck_L1']    + df['M_truck_L1'].shift(-1))    / 2.0 * dt_sec / 1000.0
    df['Mass_Truck_L2_kg']    = (df['M_truck_L2']    + df['M_truck_L2'].shift(-1))    / 2.0 * dt_sec / 1000.0
    df['Mass_Truck_Total_kg'] = df['Mass_Truck_L1_kg'] + df['Mass_Truck_L2_kg']
    
    df['Mass_Gas_Total_kg'] = (df['M_gas_Global'] + df['M_gas_Global'].shift(-1)) / 2.0 * dt_sec / 1000.0
    df['Mass_Gas_L1_kg']    = df['Mass_Gas_Total_kg'] * df['Alpha_Gas_L1']
    df['Mass_Gas_L2_kg']    = df['Mass_Gas_Total_kg'] * df['Alpha_Gas_L2']
    
    df['Mass_Total_kg'] = df['Mass_Truck_Total_kg'] + df['Mass_Gas_Total_kg']

    for bay_col in bay_names_found:
        df[f'Mass_{bay_col}_kg'] = (df[bay_col] + df[bay_col].shift(-1)) / 2.0 * dt_sec / 1000.0

    total_flow_gs   = df['M_truck_Total'] + df['M_gas_Global']
    power_mw        = get_plant_power_mw(total_flow_gs)
    power_mw_next   = get_plant_power_mw(total_flow_gs.shift(-1))
    
    df['Energy_Elec_Total_MWh'] = (power_mw + power_mw_next) / 2.0 * dt_hr
    cost_elec_total = df['Energy_Elec_Total_MWh'] * df['Price_Elec']

    df['Share_L1']  = df['Mass_Truck_L1_kg']    / (df['Mass_Total_kg'] + 1e-9)
    df['Share_L2']  = df['Mass_Truck_L2_kg']    / (df['Mass_Total_kg'] + 1e-9)
    df['Share_Gas'] = df['Mass_Gas_Total_kg']   / (df['Mass_Total_kg'] + 1e-9)

    df['Elec_Truck_L1_MWh']  = df['Energy_Elec_Total_MWh'] * df['Share_L1']
    df['Elec_Truck_L2_MWh']  = df['Energy_Elec_Total_MWh'] * df['Share_L2']
    df['Elec_Gas_Total_MWh'] = df['Energy_Elec_Total_MWh'] * df['Share_Gas']
    df['Elec_Gas_L1_MWh']    = df['Elec_Gas_Total_MWh'] * df['Alpha_Gas_L1']
    df['Elec_Gas_L2_MWh']    = df['Elec_Gas_Total_MWh'] * df['Alpha_Gas_L2']

    df['Cost_Truck_L1']  = cost_elec_total * df['Share_L1']
    df['Cost_Truck_L2']  = cost_elec_total * df['Share_L2']
    df['Cost_Gas_Total'] = cost_elec_total * df['Share_Gas']
    df['Cost_Gas_L1']    = df['Cost_Gas_Total'] * df['Alpha_Gas_L1']
    df['Cost_Gas_L2']    = df['Cost_Gas_Total'] * df['Alpha_Gas_L2']

    df['Rev_Gas_Total'] = df['Mass_Gas_Total_kg'] * HHV_H2 * df['Price_Gas']
    df['Rev_Gas_L1']    = df['Rev_Gas_Total'] * df['Alpha_Gas_L1']
    df['Rev_Gas_L2']    = df['Rev_Gas_Total'] * df['Alpha_Gas_L2']

    p_afrr          = df['P_aFRR_Reserved']
    energy_afrr_cap = (p_afrr + p_afrr.shift(-1)) / 2.0 * dt_hr
    
    df['Rev_aFRR_Total'] = energy_afrr_cap * PI_AFRR
    df['Rev_aFRR_L1']    = df['Rev_aFRR_Total'] * df['Alpha_aFRR_L1']
    df['Rev_aFRR_L2']    = df['Rev_aFRR_Total'] * df['Alpha_aFRR_L2']

    df = df.dropna().copy()
    # Conversion to local time zone for daily grouping
    df.index = df.index.tz_convert('Europe/Brussels')

    # ==========================================
    # 6. GÉNÉRATION DES MÉTRIQUES
    # ==========================================
    output_dir = Path(output_dir_name)
    output_dir.mkdir(exist_ok=True, parents=True)

    def format_entity(mass_truck_kg, mass_gas_kg, elec_truck_mwh, elec_gas_mwh, cost_truck, cost_gas, rev_gas, rev_afrr):
        truck_mass  = mass_truck_kg.sum()
        gas_mass    = mass_gas_kg.sum()
        total_kg    = truck_mass + gas_mass
        truck_mwh   = elec_truck_mwh.sum()
        gas_elec_mwh_total = elec_gas_mwh.sum()
        net_cost    = (cost_truck + cost_gas - rev_gas - rev_afrr).sum()
        
        return {
            "Total_H2_Allocated_kg": float(round(total_kg, 2)),
            "Mass_Flows_kg": { "Truck_Filling": float(round(truck_mass, 2)), "Gas_Injection": float(round(gas_mass, 2)) },
            "Energy_Flows_MWh": {
                "Grid_Electricity":           float(round(elec_truck_mwh.sum() + elec_gas_mwh.sum(), 2)),
                "Truck_Load":                 float(round(truck_mwh, 2)),
                "Gas_Injection_Electricity":  float(round(gas_elec_mwh_total, 2)),
                "Gas_Injection":              float(round(gas_mass * HHV_H2, 2))
            },
            "Operational_Costs_Eur": { "Grid_Electricity": float(round(cost_truck.sum() + cost_gas.sum(), 2)) },
            "Revenues_Eur": { "Gas_Market_Sales": float(round(rev_gas.sum(), 2)), "aFRR_Capacity": float(round(rev_afrr.sum(), 2)) },
            "Financial_Summary": {
                "Net_Position_Eur":        float(round(net_cost, 2)),
                "Net_Unit_Cost_Eur_per_MWh": float(round(net_cost / truck_mwh, 2)) if truck_mwh > 0 else 0.0,
                "Net_Unit_Cost_Eur_per_kg":  float(round(net_cost / total_kg, 2))  if total_kg > 0 else 0.0
            }
        }

    # Fetch the chronologically ordered JSON files before starting the loop
    # 1. Load the mapped dictionary before the loop
    api_costs_by_date = get_costs_breakdown_api_data_by_date()

    zeros = pd.Series(0.0, index=df.index)

    for current_date, daily_df in df.groupby(df.index.date):
        
        l1_metrics = format_entity(daily_df['Mass_Truck_L1_kg'], daily_df['Mass_Gas_L1_kg'], daily_df['Elec_Truck_L1_MWh'], daily_df['Elec_Gas_L1_MWh'], daily_df['Cost_Truck_L1'], daily_df['Cost_Gas_L1'], daily_df['Rev_Gas_L1'], daily_df['Rev_aFRR_L1'])
        l2_metrics = format_entity(daily_df['Mass_Truck_L2_kg'], daily_df['Mass_Gas_L2_kg'], daily_df['Elec_Truck_L2_MWh'], daily_df['Elec_Gas_L2_MWh'], daily_df['Cost_Truck_L2'], daily_df['Cost_Gas_L2'], daily_df['Rev_Gas_L2'], daily_df['Rev_aFRR_L2'])

        gas_line_l1_metrics = format_entity(zeros, daily_df['Mass_Gas_L1_kg'], zeros, daily_df['Elec_Gas_L1_MWh'], zeros, daily_df['Cost_Gas_L1'], daily_df['Rev_Gas_L1'], daily_df['Rev_aFRR_L1'])
        gas_line_l2_metrics = format_entity(zeros, daily_df['Mass_Gas_L2_kg'], zeros, daily_df['Elec_Gas_L2_MWh'], zeros, daily_df['Cost_Gas_L2'], daily_df['Rev_Gas_L2'], daily_df['Rev_aFRR_L2'])
        
        bay_metrics_dict = {}
        for bay_col in bay_names_found:
            clean_name    = bay_col.replace('Bay_', '').replace('_', ' ')
            mass_bay_kg   = daily_df[f'Mass_{bay_col}_kg']
            share_bay     = mass_bay_kg / (daily_df['Mass_Total_kg'] + 1e-9)
            elec_truck_bay_mwh = daily_df['Energy_Elec_Total_MWh'] * share_bay
            cost_truck_bay     = elec_truck_bay_mwh * daily_df['Price_Elec']
            
            bay_metrics_dict[clean_name] = format_entity(mass_bay_kg, zeros, elec_truck_bay_mwh, zeros, cost_truck_bay, zeros, zeros, zeros)

        plant_metrics = format_entity(daily_df['Mass_Truck_Total_kg'], daily_df['Mass_Gas_Total_kg'], daily_df['Elec_Truck_L1_MWh'] + daily_df['Elec_Truck_L2_MWh'], daily_df['Elec_Gas_Total_MWh'], daily_df['Cost_Truck_L1'] + daily_df['Cost_Truck_L2'], daily_df['Cost_Gas_Total'], daily_df['Rev_Gas_Total'], daily_df['Rev_aFRR_Total'])

        # Filter the trailer log for just today to inject into the daily JSON
        daily_trailer_log = [t for t in trailer_tracking_log if t['end_time'].startswith(str(current_date))]

        output_data = {
            "Date": str(current_date),
            "Price_Elec_Eur_per_MWh": PRICE_ELEC,
            "Price_Gas_Eur_per_MWh":  PRICE_GAS,
            "Trailer_Allocations_Log": daily_trailer_log, # NEW SMART LOGGING
            "Plant_Summary": plant_metrics,
            "Loading_Bays": bay_metrics_dict,
            "Gas_Injection_Lines": { f"Injection_Line_{name_l1}": gas_line_l1_metrics, f"Injection_Line_{name_l2}": gas_line_l2_metrics },
            "Offtakers": { name_l1: l1_metrics, name_l2: l2_metrics }
        }
        
        json_path = output_dir / f"dashboard_metrics_{current_date}.json"
        try:
            with open(json_path, 'w') as f:
                json.dump(output_data, f, indent=4)
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du jour {current_date} : {e}")
            continue

        html_path = output_dir / f"dashboard_{current_date}.html"
        generate_html_dashboard(output_data, html_path)

        # ==========================================
        # 7. COMPARAISON TERMINALE EXACTE PAR DATE
        # ==========================================
        l1_ncc_val = output_data["Offtakers"][name_l1]["Financial_Summary"]["Net_Unit_Cost_Eur_per_kg"]
        l2_ncc_val = output_data["Offtakers"][name_l2]["Financial_Summary"]["Net_Unit_Cost_Eur_per_kg"]
        tp_ncc_val = output_data["Plant_Summary"]["Financial_Summary"]["Net_Unit_Cost_Eur_per_kg"]

        calculated_nccs = {
            name_l1: l1_ncc_val,
            name_l2: l2_ncc_val,
            "Total Planning": tp_ncc_val
        }

        # 2. Extract exactly the API payload for this exact date
        current_date_str = str(current_date)
        current_api_data = api_costs_by_date.get(current_date_str)
        
        compare_ncc_terminal_daily(current_api_data, calculated_nccs, current_date_str)

    print(f"\nSuccès ! Fichiers quotidiens sauvegardés dans '{output_dir}'.")


if __name__ == "__main__":
    calculate_metrics()