import os
import re
import csv
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import asyncio

# On tente d'importer openpyxl pour le formatage avancé
try:
    import openpyxl
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    print("WARNING: 'openpyxl' n'est pas installé. Le rapport Excel sera basique ou absent.")

# =========================
# dev catalog (pour Summary)
# =========================
devS = {
    "STEP21_YEARLY_IMPORT": {"id": "T-21", "desc": "Import Yearly nominations CSV",
                            "expected": "Upload OK, yearly view updated."},
    "STEP22_EDIT_HISTORY":  {"id": "T-22", "desc": "Edit History & Breakdown",
                            "expected": "New version submitted and visible in history."},
    "STEP23_CHART_VIEW":    {"id": "T-23", "desc": "Chart View",
                            "expected": "Graphs are visible in Chart mode."},
    "STEP24_ADD_COMPANY":   {"id": "T-24", "desc": "Truck Manager - Add Company",
                            "expected": "Company added and visible in list."},
    "STEP25_EDIT_COMPANY":  {"id": "T-25", "desc": "Truck Manager - Edit Company",
                            "expected": "Company modified successfully."},
    "STEP26_ADD_TRUCK":     {"id": "T-26", "desc": "Truck Manager - Add Truck",
                            "expected": "Truck added and visible in list."},
    "STEP27_EDIT_TRUCK":    {"id": "T-27", "desc": "Truck Manager - Edit Truck",
                            "expected": "Truck modified successfully (AA prefix)."},
    "STEP1_IMPORT":        {"id": "T-01", "desc": "Import nominations CSV",
                            "expected": "Upload OK, semaine cible affichee."},
    "STEP2_NOMINATIONS":   {"id": "T-02", "desc": "Baseline nominations grid",
                            "expected": "La grille affiche les creneaux importes."},
    "STEP3_PROFILES":      {"id": "T-03", "desc": "Baseline Profiles",
                            "expected": "Profiles affiche les donnees initiales."},
    "STEP4_OCCUPANCY":     {"id": "T-04", "desc": "Baseline Bay Occupancy",
                            "expected": "Bay Occupancy affiche l'occupation initiale."},
    "STEP5_COSTS":         {"id": "T-05", "desc": "Baseline Costs Breakdown",
                            "expected": "Costs Breakdown affiche les couts initiaux."},
    "STEP6_CONFIRM":       {"id": "T-06", "desc": "Confirm all NEW slots (one by one)",
                            "expected": "Chaque slot NEW est confirme individuellement."},
    "STEP7_PROFILES":      {"id": "T-07", "desc": "Profiles after confirm",
                            "expected": "Profiles reflete les confirmations."},
    "STEP8_OCCUPANCY":     {"id": "T-08", "desc": "Occupancy after confirm",
                            "expected": "Bay Occupancy reflete les confirmations."},
    "STEP9_COSTS":         {"id": "T-09", "desc": "Costs after confirm",
                            "expected": "Costs Breakdown reflete les confirmations."},
    "STEP10_REJECT":       {"id": "T-10", "desc": "Reject all CONFIRMED slots (one by one)",
                            "expected": "Chaque slot confirme est rejete individuellement."},
    "STEP11_PROFILES":     {"id": "T-11", "desc": "Profiles after reject",
                            "expected": "Profiles reflete les rejets."},
    "STEP12_OCCUPANCY":    {"id": "T-12", "desc": "Occupancy after reject",
                            "expected": "Bay Occupancy reflete les rejets."},
    "STEP13_COSTS":        {"id": "T-13", "desc": "Costs after reject",
                            "expected": "Costs Breakdown reflete les rejets."},
    "STEP14_MAINTENANCE_TUES": {"id": "T-14", "desc": "Add Maintenance (Tuesday)",
                                "expected": "Maintenance ajoutee pour le mardi."},
    "STEP15_MAINTENANCE_THU":  {"id": "T-15", "desc": "Add Maintenance (Thursday)",
                                "expected": "Maintenance ajoutee pour le jeudi."},
    "STEP16_MAINTENANCE_SAT":  {"id": "T-16", "desc": "Add Maintenance (Loading Bay 1, full week)",
                                "expected": "Maintenance ajoutee pour toute la semaine."},
    "STEP17_CONFIRM_WEEK":     {"id": "T-17", "desc": "Confirm Week (bulk)",
                                "expected": "Le bouton Confirm Week confirme tous les slots."},
    "STEP18_PROFILES_POST":    {"id": "T-18", "desc": "Profiles after Maintenance + Confirm",
                                "expected": "Profiles reflete les maintenances et confirmations."},
    "STEP19_OCCUPANCY_POST":   {"id": "T-19", "desc": "Occupancy after Maintenance + Confirm",
                                "expected": "Bay Occupancy reflete les maintenances et confirmations."},
    "STEP20_REJECT_WEEK":      {"id": "T-20", "desc": "Reject Week (bulk)",
                                "expected": "Les slots sont rejetees en masse."},
}

# =========================
# Waiter (attentes robustes & réglables)
# =========================
class Waiter:
    def __init__(self, wait_s: float = 0.2, wait_m: float = 0.6, wait_l: float = 1.2):
        self.wait_s = max(0.0, wait_s)
        self.wait_m = max(0.0, wait_m)
        self.wait_l = max(0.0, wait_l)

    # Pauses simples
    def s(self): time.sleep(self.wait_s)
    def m(self): time.sleep(self.wait_m)
    def l(self): time.sleep(self.wait_l)

    # Attente de stabilité réseau/UI (non bloquant si indisponible)
    def wait_network_idle(self, page, timeout_ms: int = 8000):
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    def wait_for_selectors_to_disappear(self, page, selectors: list[str], per_selector_timeout_ms: int = 3000):
        # Essaie de détecter des spinners/gifs de chargement si présents (sans casser si non trouvés)
        for css in selectors:
            try:
                page.wait_for_selector(css, state="detached", timeout=per_selector_timeout_ms)
            except Exception:
                # Essayer "hidden" si "detached" ne marche pas (spinner reste dans le DOM)
                try:
                    page.wait_for_selector(css, state="hidden", timeout=per_selector_timeout_ms)
                except Exception:
                    pass

    def ui_quiet(self, page, timeout_ms: int = 8000):
        # 1) Réseau calme
        self.wait_network_idle(page, timeout_ms)
        # 2) Spinners éventuels communs (liste non bloquante)
        self.wait_for_selectors_to_disappear(
            page,
            selectors=[
                ".spinner", ".loading", ".busy", ".progress", "app-loading",
                ".ngx-spinner-overlay", ".skeleton", ".mat-progress-spinner", ".mat-progress-bar"
            ],
            per_selector_timeout_ms=min(4000, timeout_ms)
        )

# =========================
# Data Capture (JSON Gateway)
# =========================
class DataCapturer:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id
        self.current_step = "unknown"
        self._request_counter = 0

    def set_step(self, step_name: str):
        self.current_step = step_name
        self._request_counter = 0
        # Ensure directory exists
        (self.root / step_name / "data").mkdir(parents=True, exist_ok=True)

    def _on_response(self, response):
        try:
            ct = response.headers.get("content-type", "").lower()
            if "application/json" in ct and response.request.resource_type in ["fetch", "xhr"]:
                try:
                    body = response.json()
                except (Exception, asyncio.CancelledError):
                    return
                
                method = response.request.method

                # Capture request payload to extract dates or parameters
                try:
                    req_body = response.request.post_data_json()
                except:
                    try:
                        req_body = response.request.post_data
                    except:
                        req_body = None
                
                # Determine "short name" for the URL
                url = response.url
                parsed = re.sub(r"^https?://[^/]+/", "", url)
                
                if "app-config" in parsed or "assets" in parsed:
                     return

                slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", parsed)[:60]
                
                data_wrapper = {
                    "url": url,
                    "method": method,
                    "status": response.status,
                    "headers": dict(response.headers),
                    "request_body": req_body,
                    "data": body
                }
                
                sig = (url, method, json.dumps(body, sort_keys=True))
                if hasattr(self, '_last_sig') and self._last_sig == sig:
                    return
                self._last_sig = sig

                self._request_counter += 1
                
                # ========================================================
                # FIX: Timezone-Aware Date Extraction (Overcomes UTC Shift)
                # ========================================================
                date_suffix = ""
                if req_body:
                    req_str = json.dumps(req_body)
                    
                    # Look for ISO timestamp patterns: YYYY-MM-DDTHH:MM:SS
                    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2}):\d{2}:\d{2}", req_str)
                    if iso_match:
                        date_part = iso_match.group(1)
                        hour_part = int(iso_match.group(2))
                        
                        # If hours are 22 or 23, it's a UTC serialization of the NEXT local day
                        if hour_part >= 22:
                            dt = datetime.strptime(date_part, "%Y-%m-%d") + timedelta(days=1)
                            date_suffix = f"_{dt.strftime('%Y-%m-%d')}"
                        else:
                            date_suffix = f"_{date_part}"
                    else:
                        # Fallback for plain dates without timestamps
                        date_match = re.search(r"202\d-\d{2}-\d{2}", req_str)
                        if date_match:
                            date_suffix = f"_{date_match.group(0)}"
                
                if date_suffix:
                    # Clean overwrite entry per local calendar day
                    filename = f"{method}_{slug}{date_suffix}.json"
                else:
                    filename = f"{self._request_counter:03d}_{method}_{slug}.json"
                # ========================================================
                
                save_dir = self.root / self.current_step / "data"
                save_dir.mkdir(parents=True, exist_ok=True)
                
                with (save_dir / filename).open("w", encoding="utf-8") as f:
                    json.dump(data_wrapper, f, indent=2)

        except Exception:
            pass

    def start_capturing(self, page):
        page.on("response", self._on_response)

    def stop_capturing(self, page):
        page.remove_listener("response", self._on_response)

# =========================
# Baseline Manager (Regression dev)
# =========================
class BaselineManager:
    def __init__(self, baseline_root: Path):
        self.baseline_root = baseline_root
        
    def _sanitize(self, obj):
        """
        Recursively traverse obj and replace dynamic values (like UUIDs) with placeholders.
        """
        import re
        # Regex for standard UUID with dashes
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
        
        if isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._sanitize(v) for v in obj]
        elif isinstance(obj, str):
            # Mask UUIDs
            if uuid_pattern.match(obj):
                return "<UUID>"
            return obj
        else:
            return obj

    def _generate_diff(self, base_obj, run_obj, name="data"):
        """
        Generate a human-readable diff between two objects.
        """
        import difflib
        import json
        
        # Pretty print both for line-by-line diff
        s_base = json.dumps(base_obj, indent=2, sort_keys=True).splitlines()
        s_run = json.dumps(run_obj, indent=2, sort_keys=True).splitlines()
        
        diff = difflib.unified_diff(s_base, s_run, fromfile="Baseline", tofile="CurrentRun", lineterm="")
        return "\n".join(list(diff))

    def compare_and_report(self, run_dir: Path) -> dict:
        """
        Logic:
          - If baseline_root doesn't exist (or empty), Copy run_dir -> baseline_root. (First Run)
          - Else, Compare run_dir vs baseline_root.
        Returns a dict summary of diffs.
        """
        import shutil
        import json
        
        # 1. Check if baseline exists
        # We look for at least one subdirectory in baseline to consider it "populated"
        has_baseline = self.baseline_root.exists() and any(self.baseline_root.iterdir())
        
        if not has_baseline:
            print(f"[Baseline] No existing baseline found at {self.baseline_root}.")
            print(f"[Baseline] promoting current run {run_dir} to Baseline...")
            try:
                shutil.copytree(run_dir, self.baseline_root, dirs_exist_ok=True)
                print("[Baseline] Created successfully. Future runs will be compared to this.")
                return {"status": "CREATED", "details": "Baseline created from current run."}
            except Exception as e:
                print(f"[Baseline] Creation failed: {e}")
                return {"status": "ERROR", "details": str(e)}

        # 2. Compare
        print(f"[Baseline] Comparing current run against {self.baseline_root}...")
        diffs = []
        
        def load_json(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)

        # Iterate over steps in run_dir
        for step_dir in run_dir.iterdir():
            if not step_dir.is_dir(): continue
            data_dir = step_dir / "data"
            if not data_dir.exists(): continue
            
            baseline_step_data = self.baseline_root / step_dir.name / "data"
            
            if not baseline_step_data.exists():
                diffs.append(f"[{step_dir.name}] NEW STEP: Present in run but not in baseline.")
                continue
                
            # Compare files
            run_files = sorted(list(data_dir.glob("*.json")))
            base_files = sorted(list(baseline_step_data.glob("*.json")))
            
            run_filenames = {f.name for f in run_files}
            base_filenames = {f.name for f in base_files}
            
            # Check for missing/extra
            for f in base_filenames - run_filenames:
                diffs.append(f"[{step_dir.name}] MISSING FILE: {f} (expected from baseline)")
            
            for f in run_filenames - base_filenames:
                diffs.append(f"[{step_dir.name}] EXTRA FILE: {f} (not in baseline)")
                
            # Content diff for common files
            common = run_filenames.intersection(base_filenames)
            for fname in common:
                f_run = data_dir / fname
                f_base = baseline_step_data / fname
                
                try:
                    vals_run = load_json(f_run)
                    vals_base = load_json(f_base)
                    
                    # Compare 'data' field only (ignore headers/dates)
                    data_run = vals_run.get("data")
                    data_base = vals_base.get("data")

                    # Sanitize before comparison
                    clean_run = self._sanitize(data_run)
                    clean_base = self._sanitize(data_base)
                    
                    j_run = json.dumps(clean_run, sort_keys=True)
                    j_base = json.dumps(clean_base, sort_keys=True)
                    
                    if j_run != j_base:
                        msg = f"[{step_dir.name}] DATA MISMATCH: {fname}"
                        diffs.append(msg)
                        
                        # Pinpoint discrepancy
                        print(f"\n[Baseline] Mismatch found in {step_dir.name}/{fname}:")
                        d_text = self._generate_diff(clean_base, clean_run)
                        # We print first few lines of diff to be helpful
                        lines = d_text.splitlines()
                        for l in lines[:20]: # Show first 20 lines of diff
                             print(f"    {l}")
                        if len(lines) > 20:
                             print(f"    ... (truncated {len(lines)-20} lines)")
                        
                        # Save full diff to run directory
                        diff_log = data_dir / f"{fname}.diff.txt"
                        with diff_log.open("w", encoding="utf-8") as df:
                            df.write(f"Diff for {fname}\n")
                            df.write("="*40 + "\n")
                            df.write(d_text)
                except Exception as e:
                    diffs.append(f"[{step_dir.name}] ERROR reading {fname}: {e}")

        if diffs:
            print("[Baseline] [FAIL] REGRESSION DETECTED:")
            for d in diffs:
                print(f"  - {d}")
            return {"status": "FAIL", "diffs": diffs}
        else:
            print("[Baseline] [PASS] SUCCESS: Verified against baseline (No discrepancies).")
            return {"status": "PASS", "diffs": []}


# =========================
# Screenshot manager (structuré)
# =========================
class Shot:
    def __init__(self, root: Path, run_id: str):
        self.root = root / run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def stepdir(self, step: str) -> Path:
        d = self.root / step
        d.mkdir(parents=True, exist_ok=True)
        return d

    def slotdir(self, step: str, idx: int) -> Path:
        d = self.stepdir(step) / f"slot_{idx:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, page, step: str, name: str, full_page: bool = False) -> str:
        p = self.stepdir(step) / f"{name}.png"
        try:
            page.screenshot(path=str(p), full_page=full_page)
        except Exception as e:
            print(f"Warning: Screenshot failed {name}: {e}")
        return str(p)

    def save_to(self, page, folder: Path, name: str, full_page: bool = False) -> str:
        p = folder / f"{name}.png"
        try:
            page.screenshot(path=str(p), full_page=full_page)
        except Exception as e:
            print(f"Warning: Screenshot failed {name}: {e}")
        return str(p)

# =========================
# Excel Summary (table simple)
# =========================
class SummaryWriter:
    HEADERS = ["devID", "Description", "Expected", "Success", "Note", "RunID", "Step"]
    def __init__(self, excel_path: Path | None):
        self.excel_path = excel_path

    def append(self, dev_id: str, description: str, expected: str,
               success: bool, note: str, run_id: str, step: str):
        if not self.excel_path or not OPENPYXL_AVAILABLE:
            return
        try:
            from openpyxl import load_workbook, Workbook
            if self.excel_path.exists():
                wb = load_workbook(self.excel_path)
            else:
                wb = Workbook()
            ws = wb["Summary"] if "Summary" in wb.sheetnames else wb.create_sheet("Summary", 0)
            
            # entêtes si feuille vide
            if ws.max_row == 1 and (ws.cell(1,1).value or "") != self.HEADERS[0]:
                ws.delete_rows(1, ws.max_row)
                ws.append(self.HEADERS)
            
            ws.append([
                dev_id, description, expected,
                "YES" if success else "NO",
                note or "",
                run_id, step
            ])
            wb.save(self.excel_path)
        except Exception as e:
            print(f"[Summary] Impossible d’écrire dans l’Excel Summary ({self.excel_path}): {e}")

    def finalize(self):
        """Applique le formatage Table au fichier Summary s'il existe."""
        if not self.excel_path or not self.excel_path.exists() or not OPENPYXL_AVAILABLE:
            return
        
        try:
            wb = openpyxl.load_workbook(self.excel_path)
            if "Summary" not in wb.sheetnames: return
            ws = wb["Summary"]
            
            max_row = ws.max_row
            if max_row < 2: return
            
            # Nettoyage des tables existantes pour éviter les conflits si on réécrit
            if ws.tables:
                del ws.tables[next(iter(ws.tables))]

            ref = f"A1:{get_column_letter(ws.max_column)}{max_row}"
            tab = Table(displayName="SummaryTable", ref=ref)
            style = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True) # Style différent pour summary
            tab.tableStyleInfo = style
            ws.add_table(tab)
            
            for col in ws.columns:
                col_letter = col[0].column_letter
                ws.column_dimensions[col_letter].width = 25 # Largeur fixe pour simplifier
                
            wb.save(self.excel_path)
            print("[Summary] Formatted successfully.")
        except Exception as e:
            print(f"[Summary] Formatting error: {e}")

# =========================
# dev Logger (CSV + Excel Détaillé & Formatté)
# =========================
class devLogger:
    HEADERS = [
        "RunID","Timestamp","Env","Week","StartStep","Step","Feature",
        "Result","Severity","ErrorMessage","Notes",
        "Shot_Before","Shot_Modal","Shot_Action","Shot_After","Shot_Folder"
    ]
    def __init__(self, run_id: str, env: str, week: int|None, start_step: int,
                 artifacts_dir: Path, global_csv: Path, excel_log: Path|None = None,
                 severity_default: str = "Medium"):
        self.run_id = run_id
        self.env = env
        self.week = week
        self.start_step = start_step
        self.artifacts_dir = artifacts_dir
        self.run_csv = artifacts_dir / run_id / "dev_log.csv"
        self.global_csv = global_csv
        self.severity_default = severity_default
        
        # Si aucun chemin Excel n'est fourni, on en crée un par défaut dans le dossier du run
        # pour garantir que l'utilisateur ait toujours son rapport Excel.
        if excel_log:
            self.excel_path = Path(excel_log)
        else:
            self.excel_path = artifacts_dir / run_id / "dev_report.xlsx"

        self._ensure_csv_headers(self.run_csv)
        self._ensure_csv_headers(self.global_csv)
        self._init_excel()

    def _ensure_csv_headers(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(self.HEADERS)

    def _append_csv(self, path: Path, row: dict):
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADERS)
            writer.writerow(row)

    def _init_excel(self):
        if not OPENPYXL_AVAILABLE: return
        try:
            # On crée ou charge le workbook.
            # Attention : si le fichier existe déjà (ex: run précédent), on ajoute à la suite.
            if self.excel_path.exists():
                wb = openpyxl.load_workbook(self.excel_path)
            else:
                wb = openpyxl.Workbook()
            
            ws = wb.active
            if ws.title != "dev Log":
                # Si c'est un nouveau workbook, on renomme la sheet par défaut
                ws.title = "dev Log"
            
            # Si la feuille est vide, on met les headers
            if ws.max_row == 1 and ws.cell(1,1).value != self.HEADERS[0]:
                ws.delete_rows(1, ws.max_row)
                ws.append(self.HEADERS)
            
            wb.save(self.excel_path)
        except Exception as e:
            print(f"[Logger] Init Excel warning: {e}")

    def log(self, step: str, feature: str, result: str, notes: str = "",
            error: str = "", severity: str|None = None,
            shot_before: str = "", shot_modal: str = "", shot_action: str = "",
            shot_after: str = "", shot_folder: str = ""):
        row = {
            "RunID": self.run_id,
            "Timestamp": datetime.now().isoformat(timespec="seconds"),
            "Env": self.env,
            "Week": self.week if self.week is not None else "",
            "StartStep": self.start_step,
            "Step": step,
            "Feature": feature,
            "Result": result.upper(),
            "Severity": (severity or self.severity_default),
            "ErrorMessage": error,
            "Notes": notes,
            "Shot_Before": shot_before,
            "Shot_Modal": shot_modal,
            "Shot_Action": shot_action,
            "Shot_After": shot_after,
            "Shot_Folder": shot_folder
        }
        
        # 1. CSV
        self._append_csv(self.run_csv, row)
        self._append_csv(self.global_csv, row)
        
        # 2. Excel (ajout ligne par ligne)
        if OPENPYXL_AVAILABLE:
            try:
                wb = openpyxl.load_workbook(self.excel_path)
                ws = wb["dev Log"] if "dev Log" in wb.sheetnames else wb.active
                ws.append([row.get(h,"") for h in self.HEADERS])
                wb.save(self.excel_path)
            except Exception as e:
                print(f"[Logger] Impossible d’appender dans Excel ({self.excel_path}): {e}")

    def finalize(self):
        """Transforme le fichier Excel brut en un joli Tableau Excel formatté."""
        if not OPENPYXL_AVAILABLE or not self.excel_path.exists():
            return

        print(f"Finalizing Excel report formatting: {self.excel_path}...")
        try:
            wb = openpyxl.load_workbook(self.excel_path)
            ws = wb["dev Log"] if "dev Log" in wb.sheetnames else wb.active
            
            max_row = ws.max_row
            max_col = ws.max_column
            if max_row < 2: return 

            # Nettoyer les tables existantes pour éviter duplication
            if ws.tables:
                del ws.tables[next(iter(ws.tables))]

            last_col_letter = get_column_letter(max_col)
            ref = f"A1:{last_col_letter}{max_row}"
            
            # Créer l'objet Table
            tab = Table(displayName="devLogTable", ref=ref)
            style = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False,
                                   showLastColumn=False, showRowStripes=True, showColumnStripes=False)
            tab.tableStyleInfo = style
            ws.add_table(tab)

            # Ajustement automatique des largeurs
            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = (max_length + 2)
                ws.column_dimensions[column].width = min(adjusted_width, 60)

            wb.save(self.excel_path)
            print("Excel report formatted successfully.")
        except Exception as e:
            print(f"Error finalizing Excel formatting: {e}")

# =========================
# Helpers génériques
# =========================
def derive_offtaker_from_filename(csv_path: str) -> str | None:
    name = Path(csv_path).name.lower()
    if "messer" in name:
        return "Messer Belgium NV"
    if "virya" in name:
        return "Virya Energy NV"
    return None

def derive_week_from_filename(csv_path: str) -> int | None:
    m = re.search(r"[Ww](?:eek)?\s*_?(\d{1,2})", Path(csv_path).name)
    return int(m.group(1)) if m else None

def get_monday_of_week(year: int, week: int) -> datetime:
    """Calculates the Monday of a given ISO week."""
    # ISO week starts with Monday
    return datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u")

def current_date_in_picker(page, waiter: Waiter) -> datetime | None:
    try:
        # Attente d'une UI stable
        waiter.ui_quiet(page, timeout_ms=4000)
        txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000)
        # Attendu: "27 Apr 2026"
        return datetime.strptime(txt.strip(), "%d %b %Y")
    except Exception:
        return None

def goto_day(page, target_date: datetime, shots: Shot, step_tag: str, waiter: Waiter, max_clicks: int = 100):
    """Navigation jour par jour sur le date picker."""
    try:
        page.wait_for_selector("div.date-picker-current", timeout=8000)
    except:
        pass

    tries = 0
    while tries < max_clicks:
        curr = current_date_in_picker(page, waiter)
        if not curr:
             # Tentative de refresh soft si on lit rien
             waiter.m()
             tries += 1
             continue
             
        if curr.date() == target_date.date():
            print(f"  [Day check] Reached target {target_date.date()}.")
            waiter.ui_quiet(page)
            shots.save(page, step_tag, f"day_{target_date.strftime('%Y_%m_%d')}")
            return

        diff = (target_date - curr).days
        if diff > 0:
            page.locator("div.date-picker-next > button").first.click()
        else:
            page.locator("div.date-picker-previous > button").first.click()
        
        waiter.s()
        tries += 1
    
    shots.save(page, step_tag, "day_nav_failed")
    raise RuntimeError(f"Impossible d'atteindre le jour {target_date} (après {max_clicks} clics)")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def current_week_number(page, waiter: Waiter) -> int | None:
    try:
        # Assure une UI calme avant lecture
        waiter.ui_quiet(page, timeout_ms=6000)
        # Robust selector: parent class + button (ignoring specific button classes)
        # Fallback to get_by_role if needed
        try:
             txt = page.locator("div.date-picker-current > button").first.inner_text(timeout=2000)
        except:
             # Fallback strategy
             txt = page.get_by_role("button", name=re.compile(r"Week\s+\d+", re.I)).first.inner_text(timeout=2000)

        m = re.search(r"Week\s+(\d{1,2})", txt, re.I)
        return int(m.group(1)) if m else None
    except PlaywrightTimeout:
        return None

def goto_week(page, target_week: int | None, shots: Shot, step_tag: str, waiter: Waiter, max_clicks: int = 60):
    if target_week is None:
        return
    # Attente que le date-picker soit chargé
    try:
        page.wait_for_selector("div.date-picker-current", timeout=10_000)
    except:
        pass # On continue, current_week_number gérera la suite

    waiter.m()
    tries = 0
    while tries < max_clicks:
        wk = current_week_number(page, waiter)
        if wk == target_week:
            print(f"  [Week check] Already on Week {wk}, no navigation needed.")
            waiter.ui_quiet(page)
            shots.save(page, step_tag, f"week_{target_week:02d}")
            return
        
        # Navigation
        # Navigation logic with year wrap-around (shordev path)
        diff = target_week - wk
        
        # Heuristic: limit for wrap decision
        # If diff > 26 (e.g. current=2, target=52 => diff=50), we assume wrap backward -> Prev
        # If diff < -26 (e.g. current=52, target=2 => diff=-50), we assume wrap forward -> Next
        
        go_next = False
        if diff > 26:
            go_next = False
        elif diff < -26:
            go_next = True
        elif diff > 0:
            go_next = True
        else:
            go_next = False

        if go_next:
            # Click Next (Selecteur relaxé)
            page.locator("div.date-picker-next > button").first.click()
        else:
            # Click Prev (Selecteur relaxé)
            page.locator("div.date-picker-previous > button").first.click()
        
        waiter.s()
        tries += 1
    waiter.ui_quiet(page)
    shots.save(page, step_tag, "week_nav_failed")
    raise RuntimeError(f"Impossible d'atteindre Week {target_week} (après {max_clicks} clics)")

def capture_week_overview(page, shots: Shot, step_tag: str, name: str, waiter: Waiter) -> str:
    """Capture plein écran (full_page) pour visualiser toute la semaine."""
    waiter.ui_quiet(page)
    return shots.save(page, step_tag, name, full_page=True)

def get_open_dialog(page, waiter: Waiter):
    waiter.m()
    modal = page.locator("dialog[open]").last
    modal.wait_for(state="visible", timeout=10_000)
    waiter.s()
    return modal

def _safe_click(page, locator, waiter: Waiter, what: str = "élément"):
    try:
        locator.click(timeout=2000)
        waiter.s()
        return
    except Exception:
        pass
    try:
        locator.scroll_into_view_if_needed(timeout=1500)
        locator.click(timeout=1500, force=True)
        waiter.s()
        return
    except Exception:
        pass
    try:
        bb = locator.bounding_box(timeout=1200)
        if bb:
            page.mouse.move(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2)
            page.mouse.down(); page.mouse.up()
            waiter.s()
            return
    except Exception:
        pass
    try:
        h = locator.element_handle(timeout=5000)
        if not h:
            raise RuntimeError("handle manquant")
        page.evaluate("(el)=>{ el.click(); el.dispatchEvent(new Event('click',{bubbles:true})); }", h)
        waiter.s()
        return
    except Exception as e:
        raise RuntimeError(f"Impossible de cliquer sur {what}: {e}")

def _select_by_label_robust(page, select_locator, label: str, waiter: Waiter):
    def _norm(s): return re.sub(r"[\s\-_]+","", (s or "").strip().lower())
    try:
        select_locator.select_option(label=label, timeout=4000)
        waiter.s()
        return
    except Exception:
        pass
    try:
        opts = select_locator.locator("option").all_texts()
        match = next((t for t in opts if _norm(t) == _norm(label)), None)
        if match:
            select_locator.select_option(label=match, timeout=3000)
            waiter.s()
            return
    except Exception:
        pass
    val = page.evaluate(
        """(sel, target)=>{
            const norm=s=> (s||'').trim().toLowerCase().replace(/[\\s\\-_]+/g,'');
            const hit=[...sel.options||[]].find(o=>norm(o.textContent)===norm(target));
            return hit? hit.value : null;
        }""",
        select_locator.element_handle(), label
    )
    if not val:
        raise RuntimeError(f"Option '{label}' introuvable.")
    page.evaluate(
        """(sel, v)=>{
            sel.value=v;
            sel.dispatchEvent(new Event('input',{bubbles:true}));
            sel.dispatchEvent(new Event('change',{bubbles:true}));
        }""",
        select_locator.element_handle(), val
    )
    waiter.s()

# =========================
# Prompts deveur (peu intrusifs)
# =========================
def ask_feedback(enabled: bool, step: str, feature: str, default_result: str = "PASS") -> tuple[str,str]:
    if not enabled:
        return default_result, ""
    try:
        r = input(f"[{step} / {feature}] OK ? (O/N, Enter={default_result}): ").strip().lower()
        result = "PASS" if (r in ["", "o", "y", "oui", "yes"]) else ("FAIL" if r in ["n","non","no"] else default_result)
        notes = input("Notes (optionnel) : ").strip()
        return result, notes
    except EOFError:
        return default_result, ""

# =========================
# Étape 4 (facultative) — ajout UI
# =========================
def add_nomination_ui(page, offtaker_label: str, shots: Shot, tlog: devLogger, prompt_enabled: bool, waiter: Waiter):
    step = "step4_add_ui"
    slot = page.locator("div.slot:not(.status-new)").first
    _safe_click(page, slot, waiter, "slot libre")
    modal = get_open_dialog(page, waiter)
    shot_modal = shots.save(page, step, "01_modal_open")

    try:
        modal.locator("select#offtaker").select_option(label=offtaker_label, timeout=4000)
    except Exception:
        modal.locator("select[name='offtaker']").select_option(label=offtaker_label, timeout=4000)
    waiter.s()

    add_btn = modal.locator("button.button.button-secondary", has_text="+ Add Trailer").first
    before = modal.locator("select[name='trailerTypeId']").count()
    while modal.locator("select[name='trailerTypeId']").count() < before + 2:
        _safe_click(page, add_btn, waiter, "+ Add Trailer")
        waiter.s()
    shots.save(page, step, "02_after_add_two_rows")

    trailers = modal.locator("select[name='trailerTypeId']")
    transfers = modal.locator("select[name='transferType']")
    _select_by_label_robust(page, trailers.nth(0), "Type 2", waiter)
    _select_by_label_robust(page, transfers.nth(0), "Drop-Off", waiter)
    _select_by_label_robust(page, trailers.nth(1), "Type 2", waiter)
    _select_by_label_robust(page, transfers.nth(1), "Pick-Up", waiter)

    shots.save(page, step, "03_before_save_filled")
    result, notes = ask_feedback(prompt_enabled, "Step 4", "Add two trailers + fill selects")
    try:
        _safe_click(page, modal.get_by_role("button", name=re.compile("^Save$", re.I)), waiter, "Save")
        shot_click = shots.save(page, step, "04_click_save")
        try:
            modal.wait_for(state="detached", timeout=10_000)
        except Exception:
            pass
        waiter.ui_quiet(page)
        shot_after = shots.save(page, step, "05_after_close")
        tlog.log("Step 4","Add nomination (2 trailers)", result, notes=notes,
                 shot_modal=shot_modal, shot_action=shot_click, shot_after=shot_after,
                 shot_folder=str(shots.stepdir(step)))
    except Exception as e:
        shot_error = shots.save(page, step, "04_error_save")
        tlog.log("Step 4","Add nomination (2 trailers)","FAIL", notes=notes, error=str(e),
                 shot_modal=shot_modal, shot_action=shot_error, shot_after="", shot_folder=str(shots.stepdir(step)))

# =========================
# Étapes 6 & 10 — agir sur les slots
# =========================
def _snapshot_slots(page, waiter: Waiter, mode: str = "new"):
    """
    Snapshot visible nomination slots and return their bounding boxes
    sorted top-to-bottom, left-to-right.

    mode:
      "new"    => Only NEW slots (div.slot.status-new)
      "filled" => All slots with visible content that are NOT rejected
                   (for rejection of confirmed slots)
    """
    waiter.ui_quiet(page)

    if mode == "new":
        found = page.locator("div.slot.status-new")
    else:  # "filled" — confirmed slots have no CSS class, just inner content
        found = page.locator("div.slot:not(.status-rejected)")

    total = found.count()
    seen = {}
    for i in range(total):
        try:
            el = found.nth(i)
            # In "filled" mode, skip empty slots by checking inner HTML.
            # Confirmed slots contain colored bars; empty slots are blank.
            if mode == "filled":
                inner = el.inner_html(timeout=1000).strip()
                if len(inner) < 10:
                    continue
            bb = el.bounding_box(timeout=1500)
            if not bb:
                continue
            if bb["width"] < 20 or bb["height"] < 8:
                continue
            key = (round(bb["y"], 1), round(bb["x"], 1))
            if key not in seen:
                seen[key] = bb
        except Exception:
            continue
    # Sort from top to bottom, then left to right
    return [seen[k] for k in sorted(seen.keys(), key=lambda t: (t[0], t[1]))]

def _act_on_slots(page, shots: Shot, tlog: devLogger, max_n: int,
                  action: str, step_tag: str, step_label: str,
                  prompt_enabled: bool, waiter: Waiter):
    """
    Iterate through visible slots and confirm or reject them one by one.
    action:     "confirm" | "reject"
    step_tag:   folder name (e.g. "step06_confirm")
    step_label: human label (e.g. "Step 6")
    """
    assert action in {"confirm", "reject"}

    if action == "reject":
        waiter.l()  # extra wait for grid to settle after prior confirms
        bbs = _snapshot_slots(page, waiter, mode="filled")
    else:
        bbs = _snapshot_slots(page, waiter, mode="new")

    initial_n = len(bbs)
    print(f"  [{step_label}] Found {initial_n} slots to {action}.")

    done = 0
    for idx, bb in enumerate(bbs, start=1):
        if max_n >= 0 and done >= max_n:
            break

        slot_folder = shots.slotdir(step_tag, idx)
        page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        page.mouse.down()
        page.mouse.up()
        waiter.s()

        try:
            modal = get_open_dialog(page, waiter)
        except PlaywrightTimeout:
            print(f"  [{step_label}] Slot #{idx}: no modal opened, skipping.")
            continue

        shot_modal = shots.save_to(page, slot_folder, "01_modal_open")
        shots.save_to(page, slot_folder, "02_before_click")

        feature = f"{action.capitalize()} slot #{idx}"
        result, notes = ask_feedback(prompt_enabled, step_label, feature)

        try:
            if action == "confirm":
                try:
                    btn = modal.get_by_role("button", name=re.compile("^Confirm$", re.I))
                    _safe_click(page, btn, waiter, "Confirm")
                except Exception:
                    _safe_click(page, modal.locator("button.button.button-confirm"), waiter, "Confirm")
            else:
                try:
                    btn = modal.get_by_role("button", name=re.compile(
                        r"^Reject$|^Cancel$|^Unconfirm$|^Annuler$", re.I)).first
                    _safe_click(page, btn, waiter, "Reject")
                except Exception:
                    _safe_click(page, modal.locator(
                        "button.button-reject, button.btn-danger, button.button-cancel"
                    ), waiter, "Reject Fallback")

            shot_click = shots.save_to(page, slot_folder, "03_after_click")
            try:
                modal.wait_for(state="detached", timeout=10_000)
            finally:
                waiter.ui_quiet(page)
                shot_after = shots.save_to(page, slot_folder, "04_after_close")

            tlog.log(step_label, feature, result, notes=notes,
                     shot_modal=shot_modal, shot_action=shot_click,
                     shot_after=shot_after, shot_folder=str(slot_folder))
        except Exception as e:
            shot_err = shots.save_to(page, slot_folder, "03_error_click")
            tlog.log(step_label, feature, "FAIL", notes=notes, error=str(e),
                     shot_modal=shot_modal, shot_action=shot_err,
                     shot_after="", shot_folder=str(slot_folder))

        done += 1
        waiter.s()

    overview = capture_week_overview(page, shots, step_tag, "zz_week_overview", waiter)
    shots.save(page, step_tag, "zz_after_all")
    print(f"  [{step_label}] Done: {done}/{initial_n} slots {action}ed.")
    return {"initial": initial_n, "done": done, "overview": overview}


def confirm_slots(page, shots, tlog, max_n, prompt_enabled, waiter, step_tag="step06_confirm", step_label="Step 6"):
    return _act_on_slots(page, shots, tlog, max_n, action="confirm",
                         step_tag=step_tag, step_label=step_label,
                         prompt_enabled=prompt_enabled, waiter=waiter)


def reject_slots(page, shots, tlog, max_n, prompt_enabled, waiter, step_tag="step10_reject", step_label="Step 10"):
    return _act_on_slots(page, shots, tlog, max_n, action="reject",
                         step_tag=step_tag, step_label=step_label,
                         prompt_enabled=prompt_enabled, waiter=waiter)

# =========================
# Shared Step Helpers
# =========================
def check_occupancy(page, occupancy_url, shots, tlog, summary, waiter, target_week, step_folder, step_label, run_id, meta_key, prompt):
    """
    Navigue vers la vue Occupancy, attend le graph, capture et log.
    Utilisé pour voir l'évolution en parallèle des profiles.
    """
    print(f"  [{step_label}] Checking Occupancy evolution...")
    page.goto(occupancy_url, wait_until="domcontentloaded")
    waiter.l() # Long wait for graph
    
    if target_week is not None:
        goto_week(page, target_week, shots, step_folder, waiter)
    
    # Check graph visibility
    try:
        page.wait_for_selector("app-line-graph", timeout=12_000)
    except:
        print(f"  [{step_label}] Occupancy graphs not appearing?")
    
    shot_occ = shots.save(page, step_folder, f"zz_occupancy_at_{step_label.lower().replace(' ', '_')}", full_page=True)
    
    result_occ, notes_occ = ask_feedback(prompt, step_label, "Occupancy Graph Check")
    tlog.log(step_label, "Occupancy Check", result_occ, notes=notes_occ,
             shot_after=shot_occ, shot_folder=str(shots.stepdir(step_folder)))

    meta = devS.get(meta_key)
    if meta:
        summary.append(meta["id"], meta["desc"], meta["expected"], 
                      bool(result_occ.upper() == "PASS"), notes_occ, run_id, step_label)
    return result_occ.upper() == "PASS"

def check_costs_breakdown(page, costs_url, shots, tlog, summary, waiter, target_week, step_folder, step_label, run_id, meta_key, prompt):
    print(f"\n" + "="*60)
    print(f"🚀 [HEAVY-DUTY] STARTING FULL 7-DAY COSTS BREAKDOWN CYCLE")
    print("="*60)
    
    page.goto(costs_url, wait_until="networkidle")
    waiter.l()
    
    if target_week is not None:
        monday = get_monday_of_week(2026, target_week)
        
        # 1. Align calendar view to Monday first (Clears navigation noise)
        print(f"👉 Aligning calendar view to starting Monday ({monday.strftime('%Y-%m-%d')})...")
        goto_day(page, monday, shots, step_folder, waiter)
        page.wait_for_load_state("networkidle")
        waiter.l() # Let initial page settle completely
        
        # 2. Sequential 7-day loop with strict network locks
        for i in range(7):
            current_day = monday + timedelta(days=i)
            day_name = current_day.strftime("%A")
            current_date_str = current_day.strftime('%Y-%m-%d')
            
            print(f"\n📅 Processing Day {i+1}/7: {day_name} ({current_date_str})")
            print(f"   -------------------------------------------------------")
            
            if i > 0:
                try:
                    # BLOCKING LAYER 1: Expect clean server response before continuing
                    with page.expect_response(
                        lambda r: "GetCostsBreakdownScreenAsync" in r.url and r.status == 200, 
                        timeout=12000
                    ):
                        page.locator("div.date-picker-next > button").first.click()
                    print("   ✅ Server network response received successfully.")
                except Exception as e:
                    print(f"   ⚠️ Network timeout or cache strike. Forcing safety delay...")
                    page.locator("div.date-picker-next > button").first.click()
                    time.sleep(3)
            else:
                print("   📊 Extracting baseline Monday values...")

            # BLOCKING LAYER 2: Wait for UI spinners, skeletons, or progress bars to clear
            waiter.ui_quiet(page, timeout_ms=8000)
            
            # BLOCKING LAYER 3: Explicit hydration pause for text rendering engine
            time.sleep(1.5)
            
            # BLOCKING LAYER 4: Real-time UI Inspection Log
            # Scrapes what is literally rendering on screen right now to verify alignment
            try:
                ncc_cards = page.locator(".ncc-card, [class*='ncc-forecast'], [class*='ncc-card']").all()
                if ncc_cards:
                    print("   👀 Live UI NCC Forecast inspection:")
                    for card in ncc_cards:
                        text = card.inner_text().replace('\n', ' | ')
                        print(f"      • {text}")
                else:
                    # Generic text check if specific selectors differ
                    vals = page.locator(".ncc-value").all_inner_texts()
                    if vals: print(f"      • Live NCC Values found: {vals}")
            except Exception as e:
                print(f"      • Could not parse live card text: {e}")

            # 3. Save explicit snapshot
            shot_name = f"zz_costs_breakdown_{current_day.strftime('%Y_%m_%d')}"
            shots.save(page, step_folder, shot_name, full_page=True)
            print(f"   📸 Screenshot saved: {shot_name}.png")
            
            # Brief pause to ensure filesystem has closed file handles
            time.sleep(0.5)

    else:
        # Fallback if no week is passed
        waiter.ui_quiet(page)
        shots.save(page, step_folder, "zz_costs_breakdown_current", full_page=True)

    # Use Monday's screenshot for the dev execution log report
    if target_week:
        shot_costs = str(shots.stepdir(step_folder) / f"zz_costs_breakdown_{monday.strftime('%Y_%m_%d')}.png")
    else:
        shot_costs = str(shots.stepdir(step_folder) / "zz_costs_breakdown_current.png")
    
    result, notes = ask_feedback(prompt, step_label, f"Costs Breakdown Check ({step_label} - 7 Days)")
    tlog.log(step_label, "Costs Breakdown (7 Days)", result, notes=notes,
             shot_after=shot_costs, shot_folder=str(shots.stepdir(step_folder)))

    meta = devS.get(meta_key)
    if meta:
        summary.append(meta["id"], meta["desc"], meta["expected"], 
                      bool(result.upper() == "PASS"), notes, run_id, step_label)
                      
    print("="*60 + "\n")
    return result.upper() == "PASS"


def check_profiles(page, profiles_url, shots, tlog, summary, waiter, target_week, step_folder, step_label, run_id, meta_key, prompt):
    """Navigate to Profiles, wait for graph, capture screenshot + data."""
    print(f"  [{step_label}] Checking Profiles...")
    page.goto(profiles_url, wait_until="domcontentloaded")
    waiter.m()
    
    try:
        page.wait_for_selector("app-line-graph", timeout=15_000)
    except:
        pass
        
    if target_week is not None:
        goto_week(page, target_week, shots, step_folder, waiter)
    waiter.l()
    
    # ========================================================
    # NOUVEAU CODE : Bascule sur "g/s" avant la capture
    # ========================================================
    print(f"  [{step_label}] Switching to g/s...")
    try:
        # On cible précisément le bouton g/s dans le toggle
        gs_btn = page.locator("app-toggle button", has_text="g/s").first
        
        # On utilise ton helper existant pour un clic robuste
        _safe_click(page, gs_btn, waiter, "Toggle g/s")
        
        # Attente pour s'assurer que les graphes/API se mettent à jour
        waiter.l() 
        waiter.ui_quiet(page)
    except Exception as e:
        print(f"  [{step_label}] Warning: Impossible de basculer sur g/s : {e}")
    # ========================================================

    shot = shots.save(page, step_folder, "zz_profiles", full_page=True)
    result, notes = ask_feedback(prompt, step_label, "Profiles Check")
    
    tlog.log(step_label, "Profiles Check", result, notes=notes,
             shot_after=shot, shot_folder=str(shots.stepdir(step_folder)))
             
    meta = devS.get(meta_key)
    if meta:
        summary.append(meta["id"], meta["desc"], meta["expected"],
                      bool(result.upper() == "PASS"), notes, run_id, step_label)
                      
    return result.upper() == "PASS"

def add_maintenance_record(page, maintenance_url, assets: list[str], start_date: str, end_date: str,
                           shots, tlog, summary, waiter, target_week, step_tag, step_label, run_id, meta_key, prompt):
    """
    Navigate to Maintenance, select asset, fill dates, and create.
    assets: list of strings to select in order (if nested) or just the final asset.
    start_date/end_date: e.g. "2026-04-28T08:00"
    """
    print(f"  [{step_label}] Adding Maintenance for {assets[-1]} ({start_date} to {end_date})...")
    page.goto(maintenance_url, wait_until="domcontentloaded")
    waiter.m()
    if target_week is not None:
        goto_week(page, target_week, shots, step_tag, waiter)
    
    waiter.ui_quiet(page)
    shots.save(page, step_tag, "01_maintenance_land")

    _safe_click(page, page.get_by_role("button", name=re.compile("^Add Maintenance$", re.I)), waiter, "Add Maintenance")
    modal = get_open_dialog(page, waiter)
    shots.save(page, step_tag, "02_maintenance_modal")

    # Asset selection - it might be a standard select or a custom dropdown
    try:
        # Try finding a select by its proximity to label "Asset" or by tag
        select = modal.locator("select").first
        select.select_option(label=assets[-1])
    except Exception:
        # Fallback for custom dropdowns (click and find text)
        modal.locator("text=- Select -").first.click()
        waiter.s()
        modal.locator(f"text={assets[-1]}").last.click()

    # Fill dates
    # Assuming standard <input type="datetime-local">
    modal.locator("input").nth(0).fill(start_date)
    modal.locator("input").nth(1).fill(end_date)
    waiter.s()
    shots.save(page, step_tag, "03_maintenance_ready")

    _safe_click(page, modal.get_by_role("button", name=re.compile("^Create$", re.I)), waiter, "Create Maintenance")
    
    try:
        modal.wait_for(state="detached", timeout=10_000)
    except Exception:
        pass
    
    waiter.ui_quiet(page)
    shot_after = shots.save(page, step_tag, "04_maintenance_done")

    result, notes = ask_feedback(prompt, step_label, f"Maintenance Added: {assets[-1]}")
    tlog.log(step_label, f"Add Maintenance ({assets[-1]})", result, notes=notes,
             shot_after=shot_after, shot_folder=str(shots.stepdir(step_tag)))
    
    meta = devS.get(meta_key)
    if meta:
        summary.append(meta["id"], meta["desc"], meta["expected"],
                      bool(result.upper() == "PASS"), notes, run_id, step_label)
    return result.upper() == "PASS"


# =========================
# Orchestrateur
# =========================
def run(nominations_url: str,
        profiles_url: str,
        occupancy_url: str,
        costs_url: str,
        maintenance_url: str,
        csv_path: str,
        yearly_nominations_url: str = "",
        yearly_csv_path: str = "",
        slowmo: int = 0,
        env: str = "dev",
        prompt: bool = True,
        excel_log: str | None = None,
        excel_summary: str | None = None,
        severity_default: str = "Medium",
        start_step: int = 1,
        wait_s: float = 0.2,
        wait_m: float = 0.6,
        wait_l: float = 1.2,
        initial_wait: int = 30):

    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV introuvable: {csv_file}")

    offtaker_label = derive_offtaker_from_filename(str(csv_file)) or "Messer Belgium NV"
    target_week = derive_week_from_filename(str(csv_file))

    artifacts = Path("artifacts")
    ensure_dir(artifacts)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    shots = Shot(artifacts, run_id)
    waiter = Waiter(wait_s=wait_s, wait_m=wait_m, wait_l=wait_l)

    tlog = devLogger(
        run_id=run_id, env=env, week=target_week, start_step=start_step,
        artifacts_dir=artifacts, global_csv=Path("dev_log_all.csv"),
        excel_log=Path(excel_log) if excel_log else None,
        severity_default=severity_default
    )
    summary = SummaryWriter(Path(excel_summary) if excel_summary else None)

    try:
        with sync_playwright() as p:
            # Use a persistent context to store and reuse authentication cookies/session
            user_data_dir = artifacts / "playwright_session"
            context = p.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=False,
                slow_mo=slowmo,
                viewport={"width": 1500, "height": 900}
            )
            page = context.pages[0] if context.pages else context.new_page()

            has_checked_login = False

            # Monkey-patch page.goto to handle Microsoft login redirection robustly
            original_goto = page.goto
            def custom_goto(url, wait_until="domcontentloaded", timeout=30000, **kwargs):
                nonlocal has_checked_login
                print(f"  [custom_goto] Called with url = {url}")
                try:
                    res = original_goto(url, wait_until=wait_until, timeout=timeout, **kwargs)
                except Exception as e:
                    print(f"  [custom_goto] Exception caught: {e}")
                    try:
                        curr_url = page.url
                    except Exception:
                        curr_url = ""
                    print(f"  [custom_goto] Current URL during exception: {curr_url}")
                    if "login.microsoftonline.com" not in curr_url:
                        raise e
                    res = None
                
                # If we haven't checked login yet, poll page.url for up to 5 seconds to detect client-side MSAL redirect
                if not has_checked_login:
                    start_check = time.time()
                    while time.time() - start_check < 5.0:
                        try:
                            curr_url = page.url
                        except Exception:
                            curr_url = ""
                        if "login.microsoftonline.com" in curr_url:
                            break
                        page.wait_for_timeout(500)
                    has_checked_login = True
                else:
                    # Quick immediate check
                    try:
                        curr_url = page.url
                    except Exception:
                        curr_url = ""
                
                print(f"  [custom_goto] Current URL after check: {curr_url}")
                
                if "login.microsoftonline.com" in curr_url:
                    print(f"\n[INFO] Redirected to Microsoft login page while navigating to {url}.")
                    print("[INFO] Please perform manual login/MFA in the browser window...")
                    start_time = time.time()
                    logged_in = False
                    while time.time() - start_time < 300: # 5 minutes
                        try:
                            if page.is_closed():
                                print("[ERROR] Browser window was closed.")
                                break
                            curr = page.url
                            if "login.microsoftonline.com" not in curr and "herawebdev.azurewebsites.net" in curr:
                                if "/auth" not in curr:
                                    logged_in = True
                                    break
                        except Exception:
                            pass
                        page.wait_for_timeout(1000)
                    
                    if logged_in:
                        print("[INFO] Login detected! Re-navigating to target URL...")
                        page.wait_for_timeout(3000)
                        return original_goto(url, wait_until=wait_until, timeout=timeout, **kwargs)
                    else:
                        raise RuntimeError("Manual login timed out or failed.")
                return res

            page.goto = custom_goto

            capturer = DataCapturer(artifacts, run_id)
            capturer.start_capturing(page)

            # Navigate first to check auth status and trigger login check
            if initial_wait > 0:
                print(f"\n[INFO] Navigating to trigger login check...")
                target = yearly_nominations_url if start_step == 21 else nominations_url
                try:
                    page.goto(target or "https://herawebdev.azurewebsites.net/nominations/yearly", wait_until="commit", timeout=15000)
                except Exception:
                    pass
                print("[INFO] Resuming automation.")

            # ═══════════════════════════════════════
            # STEP 1 : Import CSV
            # ═══════════════════════════════════════
            if start_step <= 1:
                print("\n" + "=" * 50)
                print("  STEP 1 : Import CSV")
                print("=" * 50)
                capturer.set_step("step01_import")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step01_import", "01_landing")

                try:
                    _safe_click(page, page.get_by_role("button", name=re.compile("^Import Nominations Request$", re.I)), waiter, "Import")
                except Exception:
                    _safe_click(page, page.locator("text=Import Nominations Request").first, waiter, "Import")

                modal = get_open_dialog(page, waiter)
                shots.save(page, "step01_import", "02_modal")

                try:
                    modal.locator("select#offtaker").select_option(label=offtaker_label)
                except Exception:
                    modal.locator("select[name='offtaker']").select_option(label=offtaker_label)
                waiter.s()

                modal.locator("input#nominationFile").set_input_files(str(csv_file))
                waiter.s()
                shots.save(page, "step01_import", "03_ready")

                _safe_click(page, modal.get_by_role("button", name=re.compile("^Upload$", re.I)), waiter, "Upload")
                try:
                    modal.wait_for(state="detached", timeout=15_000)
                except Exception:
                    pass
                waiter.ui_quiet(page)

                if target_week is not None:
                    goto_week(page, target_week, shots, "step01_import", waiter)
                waiter.m()
                shots.save(page, "step01_import", "04_after_import")
                tlog.log("Step 1", "Import CSV", "PASS",
                        shot_folder=str(shots.stepdir("step01_import")))
                meta = devS["STEP1_IMPORT"]
                summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 1")

            # ═══════════════════════════════════════
            # STEP 2 : Baseline Nominations
            # ═══════════════════════════════════════
            if start_step <= 2:
                print("\n" + "=" * 50)
                print("  STEP 2 : Baseline Nominations")
                print("=" * 50)
                capturer.set_step("step02_nominations")
                # If we skipped Step 1, we might need to navigate here
                if start_step == 2:
                    page.goto(nominations_url, wait_until="domcontentloaded")
                    waiter.m()
                    if target_week is not None:
                        goto_week(page, target_week, shots, "step02_nominations", waiter)
                
                waiter.ui_quiet(page)
                capture_week_overview(page, shots, "step02_nominations", "01_grid", waiter)
                tlog.log("Step 2", "Baseline Nominations", "PASS",
                        shot_folder=str(shots.stepdir("step02_nominations")))
                meta = devS["STEP2_NOMINATIONS"]
                summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 2")

            # ═══════════════════════════════════════
            # STEP 3 : Baseline Profiles
            # ═══════════════════════════════════════
            if start_step <= 3:
                print("\n" + "=" * 50)
                print("  STEP 3 : Baseline Profiles")
                print("=" * 50)
                capturer.set_step("step03_profiles")
                check_profiles(page, profiles_url, shots, tlog, summary, waiter,
                            target_week, "step03_profiles", "Step 3", run_id, "STEP3_PROFILES", prompt)

            # ═══════════════════════════════════════
            # STEP 4 : Baseline Bay Occupancy
            # ═══════════════════════════════════════
            if start_step <= 4:
                print("\n" + "=" * 50)
                print("  STEP 4 : Baseline Occupancy")
                print("=" * 50)
                capturer.set_step("step04_occupancy")
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter,
                            target_week, "step04_occupancy", "Step 4", run_id, "STEP4_OCCUPANCY", prompt)

            # ═══════════════════════════════════════
            # STEP 5 : Baseline Costs Breakdown
            # ═══════════════════════════════════════
            if start_step <= 5:
                print("\n" + "=" * 50)
                print("  STEP 5 : Baseline Costs")
                print("=" * 50)
                capturer.set_step("step05_costs")
                check_costs_breakdown(page, costs_url, shots, tlog, summary, waiter,
                                    target_week, "step05_costs", "Step 5", run_id, "STEP5_COSTS", prompt)

            # ═══════════════════════════════════════
            # STEP 6 : Confirm all NEW slots one by one
            # ═══════════════════════════════════════
            if start_step <= 6:
                print("\n" + "=" * 50)
                print("  STEP 6 : Confirm Slots (one by one)")
                print("=" * 50)
                capturer.set_step("step06_confirm")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step06_confirm", waiter)
                conf = confirm_slots(page, shots, tlog, max_n=-1,
                                    prompt_enabled=prompt, waiter=waiter,
                                    step_tag="step06_confirm", step_label="Step 6")
                meta = devS["STEP6_CONFIRM"]
                summary.append(meta["id"], meta["desc"], meta["expected"],
                            conf["done"] > 0, f"{conf['done']}/{conf['initial']} confirmed", run_id, "Step 6")

            # ═══════════════════════════════════════
            # STEP 7 : Profiles after confirm
            # ═══════════════════════════════════════
            if start_step <= 7:
                print("\n" + "=" * 50)
                print("  STEP 7 : Profiles after Confirm")
                print("=" * 50)
                capturer.set_step("step07_profiles")
                check_profiles(page, profiles_url, shots, tlog, summary, waiter,
                            target_week, "step07_profiles", "Step 7", run_id, "STEP7_PROFILES", prompt)

            # ═══════════════════════════════════════
            # STEP 8 : Occupancy after confirm
            # ═══════════════════════════════════════
            if start_step <= 8:
                print("\n" + "=" * 50)
                print("  STEP 8 : Occupancy after Confirm")
                print("=" * 50)
                capturer.set_step("step08_occupancy")
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter,
                            target_week, "step08_occupancy", "Step 8", run_id, "STEP8_OCCUPANCY", prompt)

            # ═══════════════════════════════════════
            # STEP 9 : Costs after confirm
            # ═══════════════════════════════════════
            if start_step <= 9:
                print("\n" + "=" * 50)
                print("  STEP 9 : Costs after Confirm")
                print("=" * 50)
                capturer.set_step("step09_costs")
                check_costs_breakdown(page, costs_url, shots, tlog, summary, waiter,
                                    target_week, "step09_costs", "Step 9", run_id, "STEP9_COSTS", prompt)

            # ═══════════════════════════════════════
            # STEP 10 : Reject all CONFIRMED slots one by one
            # ═══════════════════════════════════════
            if start_step <= 10:
                print("\n" + "=" * 50)
                print("  STEP 10 : Reject Slots (one by one)")
                print("=" * 50)
                capturer.set_step("step10_reject")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step10_reject", waiter)
                rej = reject_slots(page, shots, tlog, max_n=-1,
                                prompt_enabled=prompt, waiter=waiter,
                                step_tag="step10_reject", step_label="Step 10")
                meta = devS["STEP10_REJECT"]
                summary.append(meta["id"], meta["desc"], meta["expected"],
                            rej["done"] > 0, f"{rej['done']}/{rej['initial']} rejected", run_id, "Step 10")

            # ═══════════════════════════════════════
            # STEP 11 : Profiles after reject
            # ═══════════════════════════════════════
            if start_step <= 11:
                print("\n" + "=" * 50)
                print("  STEP 11 : Profiles after Reject")
                print("=" * 50)
                capturer.set_step("step11_profiles")
                check_profiles(page, profiles_url, shots, tlog, summary, waiter,
                            target_week, "step11_profiles", "Step 11", run_id, "STEP11_PROFILES", prompt)

            # ═══════════════════════════════════════
            # STEP 12 : Occupancy after reject
            # ═══════════════════════════════════════
            if start_step <= 12:
                print("\n" + "=" * 50)
                print("  STEP 12 : Occupancy after Reject")
                print("=" * 50)
                capturer.set_step("step12_occupancy")
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter,
                            target_week, "step12_occupancy", "Step 12", run_id, "STEP12_OCCUPANCY", prompt)

            # ═══════════════════════════════════════
            # STEP 13 : Costs after reject
            # ═══════════════════════════════════════
            if start_step <= 13:
                print("\n" + "=" * 50)
                print("  STEP 13 : Costs after Reject")
                print("=" * 50)
                capturer.set_step("step13_costs")
                check_costs_breakdown(page, costs_url, shots, tlog, summary, waiter,
                                    target_week, "step13_costs", "Step 13", run_id, "STEP13_COSTS", prompt)

            # Compute maintenance dates dynamically from target_week
            week_monday = get_monday_of_week(2026, target_week) if target_week else get_monday_of_week(2026, 18)
            week_tuesday = week_monday + timedelta(days=1)
            week_thursday = week_monday + timedelta(days=3)
            week_sunday = week_monday + timedelta(days=6)

            # ═══════════════════════════════════════
            # STEP 14 : Add Maintenance (Tuesday)
            # ═══════════════════════════════════════
            if start_step <= 14:
                print("\n" + "=" * 50)
                print("  STEP 14 : Add Maintenance (Tuesday)")
                print("=" * 50)
                capturer.set_step("step14_maintenance_tues")
                add_maintenance_record(page, maintenance_url, ["High Pressure Compressor"],
                                      week_tuesday.strftime("%Y-%m-%dT00:00"),
                                      week_tuesday.strftime("%Y-%m-%dT23:59"),
                                      shots, tlog, summary, waiter, target_week,
                                      "step14_maintenance_tues", "Step 14", run_id, 
                                      "STEP14_MAINTENANCE_TUES", prompt)

            # ═══════════════════════════════════════
            # STEP 15 : Add Maintenance (Thursday)
            # ═══════════════════════════════════════
            if start_step <= 15:
                print("\n" + "=" * 50)
                print("  STEP 15 : Add Maintenance (Thursday)")
                print("=" * 50)
                capturer.set_step("step15_maintenance_thu")
                add_maintenance_record(page, maintenance_url, ["Stack 1"],
                                      week_thursday.strftime("%Y-%m-%dT00:00"),
                                      week_thursday.strftime("%Y-%m-%dT23:59"),
                                      shots, tlog, summary, waiter, target_week,
                                      "step15_maintenance_thu", "Step 15", run_id, 
                                      "STEP15_MAINTENANCE_THU", prompt)

            # ═══════════════════════════════════════
            # STEP 16 : Add Maintenance (Loading Bay 1, Full Week)
            # ═══════════════════════════════════════
            if start_step <= 16:
                print("\n" + "=" * 50)
                print("  STEP 16 : Add Maintenance (Loading Bay 1, Full Week)")
                print("=" * 50)
                capturer.set_step("step16_maintenance_week")
                add_maintenance_record(page, maintenance_url, ["Loading Bay 1"],
                                      week_monday.strftime("%Y-%m-%dT00:00"),
                                      week_sunday.strftime("%Y-%m-%dT23:59"),
                                      shots, tlog, summary, waiter, target_week,
                                      "step16_maintenance_week", "Step 16", run_id, 
                                      "STEP16_MAINTENANCE_SAT", prompt)

            # ═══════════════════════════════════════
            # STEP 17 : Confirm Week (bulk)
            # ═══════════════════════════════════════
            if start_step <= 17:
                print("\n" + "=" * 50)
                print("  STEP 17 : Confirm Week")
                print("=" * 50)
                capturer.set_step("step17_confirm_week")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step17_confirm_week", waiter)

                try:
                    btn = page.get_by_role("button", name=re.compile(r"Confirm Week", re.I)).first
                    if btn.is_visible() and not btn.is_disabled():
                        _safe_click(page, btn, waiter, "Confirm Week")
                        cmodal = get_open_dialog(page, waiter)
                        shots.save(page, "step17_confirm_week", "01_modal_open")
                        _safe_click(page, cmodal.get_by_role("button", name=re.compile("^Confirm$", re.I)), waiter, "Confirm")
                        try:
                            cmodal.wait_for(state="detached", timeout=10_000)
                        except Exception:
                            pass
                        # Give extra time for profiles/occupancy to regenerate
                        waiter.l()
                        waiter.ui_quiet(page)
                        shots.save(page, "step17_confirm_week", "02_after_confirm")
                        tlog.log("Step 17", "Confirm Week (bulk)", "PASS",
                                shot_folder=str(shots.stepdir("step17_confirm_week")))
                        meta = devS["STEP17_CONFIRM_WEEK"]
                        summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 17")
                    else:
                        shots.save(page, "step17_confirm_week", "01_button_disabled")
                        tlog.log("Step 17", "Confirm Week (bulk)", "SKIP", notes="Button not visible/disabled",
                                shot_folder=str(shots.stepdir("step17_confirm_week")))
                except Exception as e:
                    shots.save(page, "step17_confirm_week", "01_error")
                    tlog.log("Step 17", "Confirm Week (bulk)", "FAIL", error=str(e),
                            shot_folder=str(shots.stepdir("step17_confirm_week")))

            # ═══════════════════════════════════════
            # STEP 18 : Profiles Post-Impact
            # ═══════════════════════════════════════
            if start_step <= 18:
                print("\n" + "=" * 50)
                print("  STEP 18 : Profiles Post-Impact")
                print("=" * 50)
                capturer.set_step("step18_profiles_post")
                check_profiles(page, profiles_url, shots, tlog, summary, waiter,
                            target_week, "step18_profiles_post", "Step 18", run_id, "STEP18_PROFILES_POST", prompt)

            # ═══════════════════════════════════════
            # STEP 19 : Occupancy Post-Impact
            # ═══════════════════════════════════════
            if start_step <= 19:
                print("\n" + "=" * 50)
                print("  STEP 19 : Occupancy Post-Impact")
                print("=" * 50)
                capturer.set_step("step19_occupancy_post")
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter,
                            target_week, "step19_occupancy_post", "Step 19", run_id, "STEP19_OCCUPANCY_POST", prompt)

            # ═══════════════════════════════════════
            # STEP 20 : Reject Week (bulk)
            # ═══════════════════════════════════════
            if start_step <= 20:
                print("\n" + "=" * 50)
                print("  STEP 20 : Reject Week")
                print("=" * 50)
                capturer.set_step("step20_reject_week")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step20_reject_week", waiter)

                try:
                    btn = page.get_by_role("button", name=re.compile(r"Confirm Week", re.I)).first
                    if btn.is_visible() and not btn.is_disabled():
                        _safe_click(page, btn, waiter, "Confirm Week")
                        cmodal = get_open_dialog(page, waiter)
                        shots.save(page, "step20_reject_week", "01_modal_open")
                        _safe_click(page, cmodal.get_by_role("button", name=re.compile("^Reject$", re.I)), waiter, "Reject")
                        try:
                            cmodal.wait_for(state="detached", timeout=10_000)
                        except Exception:
                            pass
                        # Give extra time for cleanup
                        waiter.l()
                        waiter.ui_quiet(page)
                        shots.save(page, "step20_reject_week", "02_after_reject")
                        tlog.log("Step 20", "Reject Week (bulk)", "PASS",
                                shot_folder=str(shots.stepdir("step20_reject_week")))
                        meta = devS["STEP20_REJECT_WEEK"]
                        summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 20")
                    else:
                        shots.save(page, "step20_reject_week", "01_button_disabled")
                        tlog.log("Step 20", "Reject Week (bulk)", "SKIP", notes="Button not visible/disabled",
                                shot_folder=str(shots.stepdir("step20_reject_week")))
                except Exception as e:
                    shots.save(page, "step20_reject_week", "01_error")
                    tlog.log("Step 20", "Reject Week (bulk)", "FAIL", error=str(e),
                            shot_folder=str(shots.stepdir("step20_reject_week")))

            # ═══════════════════════════════════════
            # STEP 21 : Yearly Import CSV
            # ═══════════════════════════════════════
            if start_step <= 21:
                print("\n" + "=" * 50)
                print("  STEP 21 : Yearly Import CSV")
                print("=" * 50)
                capturer.set_step("step21_yearly_import")
                
                # Use default if not provided
                y_url = yearly_nominations_url or "https://herawebdev.azurewebsites.net/nominations/yearly"
                y_csv = yearly_csv_path or "Yearly_nomination_2027.csv"
                
                # Only goto if we aren't already there (or if the previous goto failed/interrupted)
                if y_url not in page.url:
                    try:
                        page.goto(y_url, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass # Ignore interruption if we are redirected to auth
                
                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step21_yearly_import", "01_landing")

                try:
                    _safe_click(page, page.get_by_role("button", name=re.compile("^Import Nominations Request$", re.I)), waiter, "Import Button")
                except Exception:
                    _safe_click(page, page.locator("text=Import Nominations Request").first, waiter, "Import Button Fallback")

                modal = get_open_dialog(page, waiter)
                shots.save(page, "step21_yearly_import", "02_modal")

                # Select Offtaker: Messer
                try:
                    _select_by_label_robust(page, modal.locator("select#offtaker"), "Messer Belgium NV", waiter)
                except Exception:
                    # Fallback to name or generic select
                    _select_by_label_robust(page, modal.locator("select[name='offtaker']"), "Messer Belgium NV", waiter)

                # Select Year: 2027
                try:
                    _select_by_label_robust(page, modal.locator("select#year"), "2027", waiter)
                except Exception:
                    # Fallback to name or second select
                    _select_by_label_robust(page, modal.locator("select[name='year']"), "2027", waiter)

                # Upload File
                if not Path(y_csv).exists():
                    # Check if it exists relative to the script
                    script_root = Path(__file__).parent
                    if (script_root / y_csv).exists():
                        y_csv = str(script_root / y_csv)

                try:
                    modal.locator("input#nominationFile").set_input_files(y_csv)
                except Exception:
                    modal.locator("input[type='file']").set_input_files(y_csv)
                waiter.s()
                shots.save(page, "step21_yearly_import", "03_ready")

                _safe_click(page, modal.get_by_role("button", name=re.compile("^Upload$", re.I)), waiter, "Upload Button")
                try:
                    modal.wait_for(state="detached", timeout=15_000)
                except Exception:
                    pass
                waiter.ui_quiet(page)
                
                shots.save(page, "step21_yearly_import", "04_after_import")

                # After upload, click the '>' arrow to navigate to 2027
                # Uses same date-picker pattern as week navigation elsewhere in the script
                print("  [Step 21] Navigating to year 2027...")
                try:
                    page.wait_for_selector("div.date-picker-current", timeout=10000)
                except Exception:
                    pass
                waiter.m()
                
                for _ in range(5):
                    try:
                        current_year = page.locator("div.date-picker-current > button").first.inner_text(timeout=3000).strip()
                    except Exception:
                        waiter.m()
                        continue
                    if current_year == "2027":
                        print(f"    - Already on year {current_year}")
                        break
                    print(f"    - On year {current_year}, clicking next...")
                    page.locator("div.date-picker-next > button").first.click()
                    waiter.s(); waiter.ui_quiet(page)
                
                shots.save(page, "step21_yearly_import", "05_on_2027")
                tlog.log("Step 21", "Yearly Import 2027", "PASS",
                        shot_folder=str(shots.stepdir("step21_yearly_import")))
                meta = devS["STEP21_YEARLY_IMPORT"]
                summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 21")

            # ═══════════════════════════════════════
            # STEP 22 : Edit History & Breakdown
            # ═══════════════════════════════════════
            if start_step <= 22:
                print("\n" + "=" * 50)
                print("  STEP 22 : Edit History & Breakdown")
                print("=" * 50)
                capturer.set_step("step22_edit_history")
                
                # Navigate to yearly if not already there
                if "nominations/yearly" not in page.url:
                    try:
                        page.goto(yearly_nominations_url or "https://herawebdev.azurewebsites.net/nominations/yearly", wait_until="domcontentloaded")
                    except Exception:
                        pass
                    waiter.ui_quiet(page)

                # Ensure we're on 2027 (should already be from Step 21)
                print("  [Step 22] Verifying year is 2027...")
                try:
                    page.wait_for_selector("div.date-picker-current", timeout=10000)
                except Exception:
                    pass
                waiter.m()
                
                try:
                    current_year = page.locator("div.date-picker-current > button").first.inner_text(timeout=3000).strip()
                    if current_year != "2027":
                        print(f"    - On year {current_year}, navigating to 2027...")
                        for _ in range(5):
                            page.locator("div.date-picker-next > button").first.click()
                            waiter.s(); waiter.ui_quiet(page)
                            current_year = page.locator("div.date-picker-current > button").first.inner_text(timeout=3000).strip()
                            if current_year == "2027":
                                break
                    print(f"    - Year confirmed: {current_year}")
                except Exception as e:
                    print(f"    - Year verification notice: {e}")

                # 1. Select Offtaker on page (use the VISIBLE select: id='yearly-offtaker')
                print("  [Step 22] Selecting Messer on page...")
                waiter.l(); waiter.ui_quiet(page)
                try:
                    offtaker_sel = page.locator("select#yearly-offtaker")
                    offtaker_sel.select_option(label="Messer Belgium NV")
                except Exception:
                    # Fallback
                    page.locator("select[name='offtaker']").last.select_option(label="Messer Belgium NV")
                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step22_edit_history", "01_offtaker_selected")

                # 2. Click Validate Year (button text='Validate Year', class='button button-primary')
                print("  [Step 22] Clicking Validate Year...")
                validate_btn = page.locator("button", has_text="Validate Year").first
                validate_btn.scroll_into_view_if_needed()
                _safe_click(page, validate_btn, waiter, "Validate Year")
                waiter.m()
                
                # A "Year Validation" popup appears with Reject/Confirm/Close buttons
                print("  [Step 22] Confirming Year Validation...")
                confirm_modal = get_open_dialog(page, waiter)
                shots.save(page, "step22_edit_history", "02a_validation_popup")
                confirm_btn = confirm_modal.locator("button", has_text="Confirm").first
                _safe_click(page, confirm_btn, waiter, "Confirm Year Validation")
                waiter.l(); waiter.ui_quiet(page)
                shots.save(page, "step22_edit_history", "02_after_validate")

                # 3. Click View History (button text='View History', class='button button-secondary')
                print("  [Step 22] Opening View History...")
                history_btn = page.locator("button", has_text="View History").first
                history_btn.scroll_into_view_if_needed()
                _safe_click(page, history_btn, waiter, "View History")
                waiter.m(); waiter.ui_quiet(page)
                modal = get_open_dialog(page, waiter)
                shots.save(page, "step22_edit_history", "03_history_modal")

                # 4. Click on the line with the lowest version (V1)
                print(f"  [Step 22] Selecting lowest version (V1)...")
                try:
                    # Look for V1 specifically or the last row in the table
                    v1_row = modal.locator("tr", has_text=re.compile(r"^V1$|^V1\s")).last
                    _safe_click(page, v1_row, waiter, "Version V1")
                except Exception:
                    # Fallback: click the last row of the table
                    _safe_click(page, modal.locator("tr").last, waiter, "Last Version Row")
                
                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step22_edit_history", "04_version_selected")

                # 5. Modify Breakdown (Jan and W01)
                # Cards use: div.card > span.card__data-title + span.card__data-value
                # Values show "V: 20" format
                print("  [Step 22] Modifying Jan and W01...")
                
                def reduce_card_value(label: str):
                    try:
                        # 1. Find the card container
                        card = page.locator("div.card, .breakdown-item, .item, [class*='card']").filter(has_text=re.compile(f"^{label}$", re.I)).first
                        card.scroll_into_view_if_needed()
                        
                        # 2. Find the value and the element to click
                        # First, check if there is an input already visible (based on latest screenshot)
                        input_el = card.locator("input[type='number'], input").first
                        val_el = None
                        current_val = None
                        
                        if input_el.count() > 0 and input_el.is_visible():
                            val_text = input_el.input_value()
                            m = re.search(r"(\d+)", val_text)
                            if m:
                                current_val = int(m.group(1))
                                val_el = input_el
                                print(f"    - {label}: Found current value {current_val} in existing input.")
                        
                        # If no input, search for digits in text elements
                        if val_el is None:
                            all_elements = card.locator("*:not(:has(*))").all()
                            for el in all_elements:
                                t = (el.inner_text() or el.text_content() or "").strip()
                                m = re.search(r"(\d+)", t)
                                if m:
                                    current_val = int(m.group(1))
                                    val_el = el
                                    if re.fullmatch(r"V:\s*\d+|\d+", t):
                                        break
                        
                        if val_el is None:
                            # Final fallback: just try to click the card center
                            print(f"    - {label}: No numeric element found, will try clicking card center.")
                            val_el = card
                            current_val = 0 # Dummy value if we can't read it
                        
                        new_val = max(0, current_val - 2)
                        print(f"    - {label}: Target {new_val}")
                        
                        # 3. Precise Interaction
                        box = val_el.bounding_box()
                        if box:
                            page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                            time.sleep(0.2)
                            page.mouse.dblclick(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        else:
                            val_el.click(force=True)
                        
                        waiter.s()
                        
                        # 4. Keyboard modification
                        page.keyboard.press("Control+A")
                        waiter.s()
                        page.keyboard.press("Backspace")
                        waiter.s()
                        page.keyboard.type(str(new_val))
                        waiter.s()
                        page.keyboard.press("Enter")
                        
                        print(f"    - {label}: Value updated to {new_val}")
                        waiter.m()
                    except Exception as e:
                        print(f"    - Failed to edit {label}: {e}")

                reduce_card_value("Jan")
                reduce_card_value("W01")
                
                shots.save(page, "step22_edit_history", "05_after_edits")

                # 6. Submit New Version (button text='Submit new version', class='button button-secondary')
                print("  [Step 22] Submitting new version...")
                submit_btn = page.locator("button", has_text="Submit new version").first
                submit_btn.scroll_into_view_if_needed()
                _safe_click(page, submit_btn, waiter, "Submit New Version")
                waiter.ui_quiet(page)
                waiter.l() # Wait for submission
                shots.save(page, "step22_edit_history", "06_after_submit")

                # 7. Verify in View History
                print("  [Step 22] Verifying new version in history...")
                history_btn2 = page.locator("button", has_text="View History").first
                history_btn2.scroll_into_view_if_needed()
                _safe_click(page, history_btn2, waiter, "View History")
                waiter.m()
                modal_v = get_open_dialog(page, waiter)
                shots.save(page, "step22_edit_history", "07_history_final")
                
                # Close using the 'close' button inside the modal
                _safe_click(page, modal_v.locator("button", has_text="close").first, waiter, "Close History")
                waiter.ui_quiet(page)
                
                tlog.log("Step 22", "Edit History & Breakdown", "PASS",
                        shot_folder=str(shots.stepdir("step22_edit_history")))
                meta = devS["STEP22_EDIT_HISTORY"]
                summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 22")

            # ═══════════════════════════════════════
            # STEP 23 : Chart View
            # ═══════════════════════════════════════
            if start_step <= 23:
                print("\n" + "=" * 50)
                print("  STEP 23 : Chart View")
                print("=" * 50)
                capturer.set_step("step23_chart_view")
                
                print("  [Step 23] Switching to Chart tab...")
                # chart tab: button text='chart', class='button button-secondary'
                chart_tab = page.locator("button", has_text="chart").first
                chart_tab.scroll_into_view_if_needed()
                _safe_click(page, chart_tab, waiter, "Chart Tab")
                
                waiter.l() # Wait for charts to render
                waiter.ui_quiet(page)
                shots.save(page, "step23_chart_view", "01_charts_visible", full_page=True)
                
                tlog.log("Step 23", "Chart View", "PASS",
                        shot_folder=str(shots.stepdir("step23_chart_view")))
                meta = devS["STEP23_CHART_VIEW"]
                summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 23")

            # ═══════════════════════════════════════
            # STEP 24 : Truck Manager - Add Company
            # ═══════════════════════════════════════
            if start_step <= 24:
                print("\n" + "=" * 50)
                print("  STEP 24 : Truck Manager - Add Company")
                print("=" * 50)
                capturer.set_step("step24_add_company")
                
                print("  [Step 24] Navigating to Transport Companies...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                target_url = f"{base_url}/truck-manager/transport-companies"
                
                try:
                    page.goto(target_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 24] Navigation warning: {e}")
                
                # If redirected to login, wait
                if "login.microsoftonline.com" in page.url:
                    print("  [Step 24] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)
                
                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step24_add_company", "01_page_loaded")

                # 1. Click Add New Company
                print("  [Step 24] Clicking Add New Company...")
                add_btn = page.locator("button", has_text="Add New Company").first
                _safe_click(page, add_btn, waiter, "Add New Company Button")
                waiter.m()
                
                modal = get_open_dialog(page, waiter)
                shots.save(page, "step24_add_company", "02_add_modal_open")

                # 2. Fill Details
                company_name = f"AutoTrans_{int(time.time())}"
                company_vat = f"BE0{int(time.time()) % 1000000000:09d}"
                
                print(f"  [Step 24] Filling details for {company_name}...")
                modal.locator("input#company-name").fill(company_name)
                modal.locator("input#vat-number").fill(company_vat)
                
                # Address field
                modal.locator("input#address").fill("123 Logistics Way, Antwerp 2000")
                
                # 3. Offtaker Mapping (Messer is offtaker_1)
                print("  [Step 24] Selecting Messer Belgium NV...")
                messer_cb = modal.locator("input#offtaker_1")
                messer_label = modal.locator("label[for='offtaker_1']")
                
                # Force click the checkbox directly
                _safe_click(page, messer_cb, waiter, "Messer Checkbox")
                time.sleep(2) # Wait to see if selection was right
                
                # Verification and fallback to label
                if not messer_cb.is_checked():
                    print("  [Step 24] Checkbox not toggled, clicking label instead...")
                    _safe_click(page, messer_label, waiter, "Messer Label")
                    time.sleep(2)
                
                if messer_cb.is_checked():
                    print("  [Step 24] Messer selection confirmed.")
                else:
                    print("  [WARNING] Messer selection could not be confirmed.")
                
                shots.save(page, "step24_add_company", "03_modal_filled")

                # 4. Save
                print("  [Step 24] Saving...")
                save_btn = modal.locator("button", has_text="Save").first
                _safe_click(page, save_btn, waiter, "Save Button")
                waiter.l(); waiter.ui_quiet(page)
                
                shots.save(page, "step24_add_company", "04_after_save")
                
                # Verify it appeared in the list
                if page.locator("td", has_text=company_name).count() > 0:
                    print(f"  [Step 24] SUCCESS: Company {company_name} found in list.")
                
                tlog.log("Step 24", "Truck Manager - Add Company", "PASS",
                        shot_folder=str(shots.stepdir("step24_add_company")))
                summary.append("24", "Truck Manager - Add Company", "Company added and mapped", True, "", run_id, "Step 24")

            # ═══════════════════════════════════════
            # STEP 25 : Truck Manager - Edit Company
            # ═══════════════════════════════════════
            if start_step <= 25:
                print("\n" + "=" * 50)
                print("  STEP 25 : Truck Manager - Edit Company")
                print("=" * 50)
                capturer.set_step("step25_edit_company")
                
                # We need the company name from step 24 or a fallback
                # In a real run, we'd have it. For standalone, we can look for 'AutoTrans_'
                target_name = ""
                rows = page.locator("tr").filter(has_text="AutoTrans_").all()
                if rows:
                    # Get the name from the first cell of the last added company
                    target_name = rows[-1].locator("td").first.inner_text().strip()
                
                if not target_name:
                    print("  [Step 24] WARNING: No 'AutoTrans_' company found to edit. Skipping.")
                else:
                    print(f"  [Step 25] Editing company by clicking on name: {target_name}...")
                    # The user says the rows are clickable and we should click on the name
                    name_cell = page.locator("tr").filter(has_text=target_name).locator("td").first
                    name_cell.scroll_into_view_if_needed()
                    
                    # Click on the name cell to open the modal
                    _safe_click(page, name_cell, waiter, f"Name Cell for {target_name}")
                    
                    # Wait a bit and check if modal opened, if not, click again
                    time.sleep(1)
                    if page.locator("dialog[open], .modal.show").count() == 0:
                        print("  [Step 25] Modal didn't open, retrying click...")
                        _safe_click(page, name_cell, waiter, f"Name Cell for {target_name} (Retry)")
                    
                    waiter.m()
                    modal = get_open_dialog(page, waiter)
                    shots.save(page, "step25_edit_company", "01_edit_modal_open")
                    
                    # Modify Fields to end with '11' (replace last 2 chars)
                    print(f"  [Step 25] Modifying fields to end with '11'...")
                    
                    # Replace last 2 chars of name
                    new_name = target_name[:-2] + "11" if len(target_name) >= 2 else target_name + "11"
                    modal.locator("input#company-name").fill(new_name)
                    
                    curr_vat = modal.locator("input#vat-number").input_value()
                    # Replace last 2 chars of VAT
                    new_vat = curr_vat[:-2] + "11" if len(curr_vat) >= 2 else curr_vat + "11"
                    modal.locator("input#vat-number").fill(new_vat)
                    
                    modal.locator("input#address").fill("456 Modified Logistics Way, Ghent 9000")
                    
                    shots.save(page, "step25_edit_company", "02_fields_modified")
                    
                    # Save
                    print("  [Step 25] Saving modifications...")
                    save_btn = modal.locator("button", has_text="Save").first
                    _safe_click(page, save_btn, waiter, "Save Button")
                    waiter.l(); waiter.ui_quiet(page)
                    
                    shots.save(page, "step25_edit_company", "03_after_edit_save")
                    
                    # Verify
                    if page.locator("td", has_text=f"{target_name}_Modified").count() > 0:
                        print(f"  [Step 25] SUCCESS: Modified company found in list.")
                    
                    tlog.log("Step 25", "Truck Manager - Edit Company", "PASS",
                            shot_folder=str(shots.stepdir("step25_edit_company")))
                    summary.append("25", "Truck Manager - Edit Company", "Company modified successfully", True, "", run_id, "Step 25")
# ═══════════════════════════════════════
            # STEP 26 : Truck Manager - Add Truck
            # ═══════════════════════════════════════
            if start_step <= 26:
                print("\n" + "=" * 50)
                print("  STEP 26 : Truck Manager - Add Truck")
                print("=" * 50)
                capturer.set_step("step26_add_truck")

                print("  [Step 26] Navigating to Trucks...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                trucks_url = f"{base_url}/truck-manager/trucks"

                try:
                    page.goto(trucks_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 26] Navigation warning: {e}")

                if "login.microsoftonline.com" in page.url:
                    print("  [Step 26] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)

                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step26_add_truck", "01_page_loaded")

                # Generate a unique plate: 2ABY + 3-digit suffix derived from timestamp
                rand_suffix = int(time.time()) % 900 + 100   # 100-999
                truck_plate = f"2ABY{rand_suffix}"

                # 1. Open Add New Truck modal
                print("  [Step 26] Clicking Add New Truck...")
                add_btn = page.locator("button", has_text="Add New Truck").first
                _safe_click(page, add_btn, waiter, "Add New Truck Button")
                waiter.m()

                modal = get_open_dialog(page, waiter)
                shots.save(page, "step26_add_truck", "02_add_modal_open")

                # 2. License Plate
                print(f"  [Step 26] Filling license plate: {truck_plate}...")
                modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(truck_plate)
                waiter.s()

                # 3. Country Code
                print("  [Step 26] Filling country code: B...")
                modal.get_by_label("Country Code").fill("B")
                waiter.s()

                # 4. Transport Company (first real option, skipping "- SELECT -")
                print("  [Step 26] Selecting Transport Company...")
                company_select = modal.locator("select").first
                company_select.select_option(index=1)
                waiter.s()

                # 5. Qualification State → Pre-Qualified
                print("  [Step 26] Setting Qualification State to Pre-Qualified...")
                qual_select = modal.locator("select").nth(1)
                _select_by_label_robust(page, qual_select, "Pre-Qualified", waiter)
                waiter.s()

                shots.save(page, "step26_add_truck", "03_modal_filled")

                # 6. Save
                print("  [Step 26] Saving...")
                waiter.s()

                # On cherche le bouton Save (méthode souple)
                save_btn = modal.locator("button", has_text="Save").first
                _safe_click(page, save_btn, waiter, "Save Button")
                
                # Très important : on attend que le modal disparaisse avant de continuer
                try:
                    modal.wait_for(state="hidden", timeout=10000)
                except Exception:
                    print("  [Step 26] Warning: Modal took a long time to close...")
                
                waiter.l(); waiter.ui_quiet(page)
                shots.save(page, "step26_add_truck", "04_after_save")

                # Vérification avec un petit délai pour le rechargement du tableau
                waiter.m()
                if page.locator("td", has_text=truck_plate).count() > 0:
                    print(f"  [Step 26] SUCCESS: Truck {truck_plate} found in list.")
                else:
                    print(f"  [Step 26] WARNING: Truck {truck_plate} not found in list after save.")

                tlog.log("Step 26", "Truck Manager - Add Truck", "PASS",
                        shot_folder=str(shots.stepdir("step26_add_truck")))
                meta = devS["STEP26_ADD_TRUCK"]
                summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 26")

            # ═══════════════════════════════════════
            # STEP 27 : Truck Manager - Edit Truck
            # ═══════════════════════════════════════
            if start_step <= 27:
                print("\n" + "=" * 50)
                print("  STEP 27 : Truck Manager - Edit Truck")
                print("=" * 50)
                capturer.set_step("step27_edit_truck")

                # Ensure we are on the trucks page
                print("  [Step 27] Navigating to Trucks...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                trucks_url = f"{base_url}/truck-manager/trucks"

                try:
                    page.goto(trucks_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 27] Navigation warning: {e}")

                if "login.microsoftonline.com" in page.url:
                    print("  [Step 27] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)

                waiter.m(); waiter.ui_quiet(page)

                # Resolve the plate to edit: use step 26's variable if available,
                # otherwise fall back to the last '2ABY' row on the current page.
                try:
                    target_plate = truck_plate
                except NameError:
                    target_plate = ""
                    rows = page.locator("tr").filter(has_text="2ABY").all()
                    if rows:
                        raw = rows[-1].locator("td").first.inner_text().strip()
                        target_plate = raw.split(" ")[0]  # strip "(B)" suffix if present

                if not target_plate:
                    print("  [Step 27] WARNING: No '2ABY' truck found to edit. Skipping.")
                else:
                    updated_plate = "1AA" + target_plate[3:]   # e.g. 2ABY893 → AABY893
                    print(f"  [Step 27] Editing {target_plate} → {updated_plate}...")

                    # On cible la première cellule (td) de la ligne qui contient la plaque
                    plate_cell = page.locator("tr").filter(has_text=target_plate).locator("td").first
                    plate_cell.scroll_into_view_if_needed()

                    # On clique directement sur le texte de la plaque
                    _safe_click(page, plate_cell, waiter, f"Plate Cell for {target_plate}")

                    time.sleep(1)
                    # Si le modal n'est pas ouvert (dialog ou .modal), on retente
                    if page.locator("dialog[open], .modal.show").count() == 0:
                        print("  [Step 27] Modal didn't open, retrying click...")
                        _safe_click(page, plate_cell, waiter, f"Plate Cell for {target_plate} (Retry)")

                    waiter.m()
                    modal = get_open_dialog(page, waiter)
                    shots.save(page, "step27_edit_truck", "01_edit_modal_open")

                    # Update license plate: replace first 2 chars with 'AA'
                    print(f"  [Step 27] Updating license plate to {updated_plate}...")
                    plate_input = modal.locator("input[placeholder='e.g. 1-ABC-123']")
                    plate_input.fill(updated_plate)
                    waiter.s()

                    # Update Qualification State to Qualified
                    print("  [Step 27] Updating Qualification State to Qualified...")
                    qual_select = modal.locator("select").nth(1)
                    _select_by_label_robust(page, qual_select, "Qualified", waiter)
                    waiter.s()

                    # Date of 1st Qualification → today (Frappe séquentielle au clavier)
                    today_digits = datetime.now().strftime("%d%m%Y") 
                    print(f"  [Step 27] Typing Date of 1st Qualification sequentially: {today_digits}...")
                    try:
                        date_input = modal.locator("input[placeholder*='dd/mm/yyyy']").first
                        date_input.click(timeout=3000)
                    except Exception:
                        date_input = modal.locator("input").last
                        date_input.click()

                    waiter.s()
                    page.keyboard.press("Home")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.type(today_digits, delay=100)
                    waiter.s()

                    shots.save(page, "step27_edit_truck", "02_plate_modified")

                    # Save
                    print("  [Step 27] Saving modifications...")
                    page.keyboard.press("Tab")
                    waiter.s()
                    save_btn = modal.locator("button", has_text="Save").first
                    _safe_click(page, save_btn, waiter, "Save Button")
                    waiter.l(); waiter.ui_quiet(page)

                    shots.save(page, "step27_edit_truck", "03_after_edit_save")

                    if page.locator("td", has_text=updated_plate).count() > 0:
                        print(f"  [Step 27] SUCCESS: Updated truck {updated_plate} found in list.")
                    else:
                        print(f"  [Step 27] WARNING: {updated_plate} not visible (may need scroll).")

                    tlog.log("Step 27", "Truck Manager - Edit Truck", "PASS",
                            shot_folder=str(shots.stepdir("step27_edit_truck")))
                    meta = devS["STEP27_EDIT_TRUCK"]
                    summary.append(meta["id"], meta["desc"], meta["expected"], True, "", run_id, "Step 27")

            # ═══════════════════════════════════════
            # STEP 28 : Truck Manager - Add Trailer
            # ═══════════════════════════════════════
            if start_step <= 28:
                print("\n" + "=" * 50)
                print("  STEP 28 : Truck Manager - Add Trailer")
                print("=" * 50)
                capturer.set_step("step28_add_trailer")

                print("  [Step 28] Navigating to Trailers...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                trailers_url = f"{base_url}/truck-manager/trailers"

                try:
                    page.goto(trailers_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 28] Navigation warning: {e}")

                if "login.microsoftonline.com" in page.url:
                    print("  [Step 28] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)

                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step28_add_trailer", "01_page_loaded")

                # Generate a unique plate: 3TRL + 3-digit suffix
                rand_suffix = int(time.time()) % 900 + 100   # 100-999
                trailer_plate = f"3TRL{rand_suffix}"

                # 1. Open Add New Trailer modal
                print("  [Step 28] Clicking Add New Trailer...")
                add_btn = page.locator("button", has_text="Add New Trailer").first
                _safe_click(page, add_btn, waiter, "Add New Trailer Button")
                waiter.m()

                modal = get_open_dialog(page, waiter)
                shots.save(page, "step28_add_trailer", "02_add_modal_open")

                # 2. License Plate
                print(f"  [Step 28] Filling license plate: {trailer_plate}...")
                modal.locator("input[placeholder='e.g. 1-ABC-123']").fill(trailer_plate)
                waiter.s()

                # 3. Country Code
                print("  [Step 28] Filling country code: B...")
                try:
                    modal.get_by_label("Country Code").fill("B")
                except:
                    # Fallback au cas où get_by_label échoue
                    modal.locator("input").nth(1).fill("B")
                waiter.s()

                # 4. Offtaker Company (1er select)
                print("  [Step 28] Selecting Offtaker Company...")
                offtaker_select = modal.locator("select").nth(0)
                offtaker_select.select_option(index=1)
                waiter.s()

                # 5. Trailer Type (2ème select)
                print("  [Step 28] Selecting Trailer Type...")
                type_select = modal.locator("select").nth(1)
                type_select.select_option(index=1)
                waiter.s()

                # 6. Global Max Flow Rate
                print("  [Step 28] Filling Global Max Flow Rate...")
                try:
                    modal.locator("input[placeholder='e.g. 32.5']").fill("30.5")
                except:
                    modal.locator("input").nth(2).fill("30.5")
                waiter.s()

                # 7. Qualification Status → Pre-Qualified (3ème select)
                print("  [Step 28] Setting Qualification State to Pre-Qualified...")
                qual_select = modal.locator("select").nth(2)
                _select_by_label_robust(page, qual_select, "Pre-Qualified", waiter)
                waiter.s()

                shots.save(page, "step28_add_trailer", "03_modal_filled")

                # 8. Save
                print("  [Step 28] Saving...")
                waiter.s()

                save_btn = modal.locator("button", has_text="Save").first
                _safe_click(page, save_btn, waiter, "Save Button")
                
                try:
                    modal.wait_for(state="hidden", timeout=10000)
                except Exception:
                    print("  [Step 28] Warning: Modal took a long time to close...")
                
                waiter.l(); waiter.ui_quiet(page)
                shots.save(page, "step28_add_trailer", "04_after_save")

                waiter.m()
                if page.locator("td", has_text=trailer_plate).count() > 0:
                    print(f"  [Step 28] SUCCESS: Trailer {trailer_plate} found in list.")
                else:
                    print(f"  [Step 28] WARNING: Trailer {trailer_plate} not found in list after save.")

                tlog.log("Step 28", "Truck Manager - Add Trailer", "PASS",
                        shot_folder=str(shots.stepdir("step28_add_trailer")))
                summary.append("T-28", "Truck Manager - Add Trailer", "Trailer added successfully", True, "", run_id, "Step 28")

            # ═══════════════════════════════════════
            # STEP 29 : Truck Manager - Edit Trailer
            # ═══════════════════════════════════════
            if start_step <= 29:
                print("\n" + "=" * 50)
                print("  STEP 29 : Truck Manager - Edit Trailer")
                print("=" * 50)
                capturer.set_step("step29_edit_trailer")

                # Ensure we are on the trailers page
                print("  [Step 29] Navigating to Trailers...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                trailers_url = f"{base_url}/truck-manager/trailers"

                try:
                    page.goto(trailers_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 29] Navigation warning: {e}")

                if "login.microsoftonline.com" in page.url:
                    print("  [Step 29] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)

                waiter.m(); waiter.ui_quiet(page)

                # Récupère la plaque créée ou la dernière plaque "3TRL" visible
                try:
                    target_trailer = trailer_plate
                except NameError:
                    target_trailer = ""
                    rows = page.locator("tr").filter(has_text="3TRL").all()
                    if rows:
                        raw = rows[-1].locator("td").first.inner_text().strip()
                        target_trailer = raw.split(" ")[0]

                if not target_trailer:
                    print("  [Step 29] WARNING: No '3TRL' trailer found to edit. Skipping.")
                else:
                    # On remplace le '3' du début par un '1' pour l'édition
                    updated_trailer = "1TRL" + target_trailer[4:]
                    print(f"  [Step 29] Editing {target_trailer} → {updated_trailer}...")

                    # Clic sur la plaque pour ouvrir
                    plate_cell = page.locator("tr").filter(has_text=target_trailer).locator("td").first
                    plate_cell.scroll_into_view_if_needed()
                    _safe_click(page, plate_cell, waiter, f"Plate Cell for {target_trailer}")

                    time.sleep(1)
                    if page.locator("dialog[open], .modal.show").count() == 0:
                        print("  [Step 29] Modal didn't open, retrying click...")
                        _safe_click(page, plate_cell, waiter, f"Plate Cell for {target_trailer} (Retry)")

                    waiter.m()
                    modal = get_open_dialog(page, waiter)
                    shots.save(page, "step29_edit_trailer", "01_edit_modal_open")

                    # Modification de la plaque
                    print(f"  [Step 29] Updating license plate to {updated_trailer}...")
                    plate_input = modal.locator("input[placeholder='e.g. 1-ABC-123']")
                    plate_input.fill(updated_trailer)
                    waiter.s()

                    # Update Qualification State to Qualified
                    print("  [Step 29] Updating Qualification State to Qualified...")
                    qual_select = modal.locator("select").nth(2)
                    _select_by_label_robust(page, qual_select, "Qualified", waiter)
                    waiter.s()

                    # Date of 1st Qualification → today (Frappe séquentielle)
                    today_digits = datetime.now().strftime("%d%m%Y") 
                    print(f"  [Step 29] Typing Date of 1st Qualification sequentially: {today_digits}...")
                    try:
                        date_input = modal.locator("input[placeholder*='dd/mm/yyyy']").first
                        date_input.click(timeout=3000)
                    except Exception:
                        date_input = modal.locator("input").last
                        date_input.click()

                    waiter.s()
                    page.keyboard.press("Home")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.type(today_digits, delay=100)
                    waiter.s()

                    shots.save(page, "step29_edit_trailer", "02_plate_modified")

                    # Sauvegarde
                    print("  [Step 29] Saving modifications...")
                    page.keyboard.press("Tab")
                    waiter.s()
                    save_btn = modal.locator("button", has_text="Save").first
                    _safe_click(page, save_btn, waiter, "Save Button")
                    
                    try:
                        modal.wait_for(state="hidden", timeout=10000)
                    except Exception:
                        pass

                    waiter.l(); waiter.ui_quiet(page)
                    shots.save(page, "step29_edit_trailer", "03_after_edit_save")

                    if page.locator("td", has_text=updated_trailer).count() > 0:
                        print(f"  [Step 29] SUCCESS: Updated trailer {updated_trailer} found in list.")
                    else:
                        print(f"  [Step 29] WARNING: {updated_trailer} not visible (may need scroll).")

                    tlog.log("Step 29", "Truck Manager - Edit Trailer", "PASS",
                            shot_folder=str(shots.stepdir("step29_edit_trailer")))
                    summary.append("T-29", "Truck Manager - Edit Trailer", "Trailer modified successfully", True, "", run_id, "Step 29")

# ═══════════════════════════════════════
            # STEP 30 : Truck Manager - Add Driver
            # ═══════════════════════════════════════
            if start_step <= 30:
                print("\n" + "=" * 50)
                print("  STEP 30 : Truck Manager - Add Driver")
                print("=" * 50)
                capturer.set_step("step30_add_driver")

                print("  [Step 30] Navigating to Drivers...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                drivers_url = f"{base_url}/truck-manager/drivers"

                try:
                    page.goto(drivers_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 30] Navigation warning: {e}")

                if "login.microsoftonline.com" in page.url:
                    print("  [Step 30] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)

                waiter.m(); waiter.ui_quiet(page)
                shots.save(page, "step30_add_driver", "01_page_loaded")

                # Variables uniques
                rand_suffix = int(time.time()) % 9000 + 1000   # 1000-9999
                driver_last_name = f"AutoDriver{rand_suffix}"
                badge_id = f"000000{rand_suffix}" # Toujours 10 chars
                adr_card = f"ADR-999{rand_suffix}"

                # 1. Open Add New Driver modal
                print("  [Step 30] Clicking Add New Driver...")
                add_btn = page.locator("button", has_text="Add New Driver").first
                _safe_click(page, add_btn, waiter, "Add New Driver Button")
                waiter.m()

                modal = get_open_dialog(page, waiter)
                shots.save(page, "step30_add_driver", "02_add_modal_open")

                # 2. Last Name & First Name
                print(f"  [Step 30] Filling names: {driver_last_name} John...")
                modal.locator("input").nth(0).fill(driver_last_name)
                modal.locator("input").nth(1).fill("John")
                waiter.s()

                # 3. Transport Company (1er select)
                print("  [Step 30] Selecting Transport Company...")
                company_select = modal.locator("select").nth(0)
                company_select.select_option(index=1)
                waiter.s()

                # 4. Badge ID
                print("  [Step 30] Filling Badge ID...")
                modal.locator("input[placeholder='e.g. 0123456789']").fill(badge_id)
                waiter.s()

                # 5. ADR Card Number
                print("  [Step 30] Filling ADR Card...")
                modal.locator("input[placeholder='e.g. ADR-9876543']").fill(adr_card)
                waiter.s()

                # 6. Qualification Status → Pre-Qualified (2ème select)
                print("  [Step 30] Setting Qualification State to Pre-Qualified...")
                qual_select = modal.locator("select").nth(1)
                _select_by_label_robust(page, qual_select, "Pre-Qualified", waiter)
                waiter.s()

                shots.save(page, "step30_add_driver", "03_modal_filled")

                # 7. Save
                print("  [Step 30] Saving...")
                waiter.s()

                save_btn = modal.locator("button", has_text="Save").first
                _safe_click(page, save_btn, waiter, "Save Button")
                
                try:
                    modal.wait_for(state="hidden", timeout=10000)
                except Exception:
                    print("  [Step 30] Warning: Modal took a long time to close...")
                
                waiter.l(); waiter.ui_quiet(page)
                shots.save(page, "step30_add_driver", "04_after_save")

                waiter.m()
                if page.locator("td", has_text=driver_last_name).count() > 0:
                    print(f"  [Step 30] SUCCESS: Driver {driver_last_name} found in list.")
                else:
                    print(f"  [Step 30] WARNING: Driver {driver_last_name} not found in list after save.")

                tlog.log("Step 30", "Truck Manager - Add Driver", "PASS", shot_folder=str(shots.stepdir("step30_add_driver")))
                summary.append("D-30", "Truck Manager - Add Driver", "Driver added successfully", True, "", run_id, "Step 30")

            # ═══════════════════════════════════════
            # STEP 31 : Truck Manager - Edit Driver
            # ═══════════════════════════════════════
            if start_step <= 31:
                print("\n" + "=" * 50)
                print("  STEP 31 : Truck Manager - Edit Driver")
                print("=" * 50)
                capturer.set_step("step31_edit_driver")

                # Ensure we are on the drivers page
                print("  [Step 31] Navigating to Drivers...")
                base_url = yearly_nominations_url.split("/nominations")[0]
                drivers_url = f"{base_url}/truck-manager/drivers"

                try:
                    page.goto(drivers_url, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [Step 31] Navigation warning: {e}")

                if "login.microsoftonline.com" in page.url:
                    print("  [Step 31] Redirected to login. Waiting for session...")
                    waiter.ui_quiet(page)
                    if "login.microsoftonline.com" in page.url:
                        time.sleep(5)

                waiter.m(); waiter.ui_quiet(page)

                # Récupère le nom créé
                try:
                    target_driver = driver_last_name
                except NameError:
                    target_driver = ""
                    rows = page.locator("tr").filter(has_text="AutoDriver").all()
                    if rows:
                        # En général le nom de famille est la première chose dans la cellule
                        raw = rows[-1].locator("td").first.inner_text().strip()
                        target_driver = raw.split("\n")[0].split()[0]

                if not target_driver:
                    print("  [Step 31] WARNING: No 'AutoDriver' found to edit. Skipping.")
                else:
                    updated_driver = target_driver + "Mod"
                    print(f"  [Step 31] Editing {target_driver} → {updated_driver}...")

                    # Clic sur le nom pour ouvrir
                    name_cell = page.locator("tr").filter(has_text=target_driver).locator("td").first
                    name_cell.scroll_into_view_if_needed()
                    _safe_click(page, name_cell, waiter, f"Name Cell for {target_driver}")

                    time.sleep(1)
                    if page.locator("dialog[open], .modal.show").count() == 0:
                        print("  [Step 31] Modal didn't open, retrying click...")
                        _safe_click(page, name_cell, waiter, f"Name Cell for {target_driver} (Retry)")

                    waiter.m()
                    modal = get_open_dialog(page, waiter)
                    shots.save(page, "step31_edit_driver", "01_edit_modal_open")

                    # Modification du Last Name
                    print(f"  [Step 31] Updating Last Name to {updated_driver}...")
                    modal.locator("input").nth(0).fill(updated_driver)
                    waiter.s()

                    # Update Qualification State to Qualified
                    print("  [Step 31] Updating Qualification State to Qualified...")
                    qual_select = modal.locator("select").nth(1)
                    _select_by_label_robust(page, qual_select, "Qualified", waiter)
                    waiter.s()

                    # Dates (1st Qualification & Last Validated Training) -> today
                    today_digits = datetime.now().strftime("%d%m%Y") 
                    print(f"  [Step 31] Typing dates sequentially: {today_digits}...")
                    
                    # On cible TOUS les champs input. Les dates sont systématiquement les deux derniers.
                    all_inputs = modal.locator("input:not([type='hidden'])")
                    
                    # Première date (avant-dernier input)
                    print("  [Step 31] Filling Date of 1st Qualification...")
                    all_inputs.nth(-2).click(timeout=3000)
                    waiter.s()
                    page.keyboard.press("Home")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.type(today_digits, delay=100)
                    waiter.s()

                    # Deuxième date (dernier input)
                    print("  [Step 31] Filling Date of Last Validated Training...")
                    all_inputs.nth(-1).click(timeout=3000)
                    waiter.s()
                    page.keyboard.press("Home")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.press("ArrowLeft")
                    page.keyboard.type(today_digits, delay=100)
                    waiter.s()

                    shots.save(page, "step31_edit_driver", "02_name_modified")

                    # Sauvegarde
                    print("  [Step 31] Saving modifications...")
                    page.keyboard.press("Tab")
                    waiter.s()
                    save_btn = modal.locator("button", has_text="Save").first
                    _safe_click(page, save_btn, waiter, "Save Button")
                    
                    try:
                        modal.wait_for(state="hidden", timeout=10000)
                    except Exception:
                        pass

                    waiter.l(); waiter.ui_quiet(page)
                    shots.save(page, "step31_edit_driver", "03_after_edit_save")

                    if page.locator("td", has_text=updated_driver).count() > 0:
                        print(f"  [Step 31] SUCCESS: Updated driver {updated_driver} found in list.")
                    else:
                        print(f"  [Step 31] WARNING: {updated_driver} not visible (may need scroll).")

                    tlog.log("Step 31", "Truck Manager - Edit Driver", "PASS", shot_folder=str(shots.stepdir("step31_edit_driver")))
                    summary.append("D-31", "Truck Manager - Edit Driver", "Driver modified successfully", True, "", run_id, "Step 31")

            print("\n" + "=" * 50)
            print("  ALL 31 STEPS COMPLETE")
            print("=" * 50)
            
            # On coupe le listener pour éviter l'erreur CancelledError
            capturer.stop_capturing(page)

    finally:
        print("Formatting Excel reports...")
        tlog.finalize()
        summary.finalize()

        print("Checking against baseline...")
        bm = BaselineManager(Path("baseline"))
        res = bm.compare_and_report(artifacts / run_id)
        if res["status"] == "FAIL":
            print("\n WARNING: Baseline mismatches found! Check logs above.")


# =========================
# CLI
# =========================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HERA 15-step smoke dev: import, baseline, confirm, reject, bulk actions.")
    parser.add_argument("--nominations-url",
                        default=os.getenv("HERA_NOMINATIONS_URL",
                                          "https://herawebdev.azurewebsites.net/nominations/weekly"))
    parser.add_argument("--profiles-url",
                        default=os.getenv("HERA_PROFILES_URL",
                                          "https://herawebdev.azurewebsites.net/schedules/profiles"))
    parser.add_argument("--occupancy-url",
                        default=os.getenv("HERA_OCCUPANCY_URL",
                                          "https://herawebdev.azurewebsites.net/schedules/occupancy"))
    parser.add_argument("--costs-url",
                        default=os.getenv("HERA_COSTS_URL",
                                          "https://herawebdev.azurewebsites.net/schedules/costs-breakdown"))
    parser.add_argument("--maintenance-url",
                        default=os.getenv("HERA_MAINTENANCE_URL",
                                          "https://herawebdev.azurewebsites.net/schedules/maintenance"))
    parser.add_argument("--yearly-nominations-url",
                        default=os.getenv("HERA_YEARLY_NOMINATIONS_URL",
                                          "https://herawebdev.azurewebsites.net/nominations/yearly"))
    parser.add_argument("--yearly-csv",
                        default=os.getenv("HERA_YEARLY_CSV_PATH", "Yearly_nomination_2027.csv"))
    parser.add_argument("--csv",
                        default=os.getenv("HERA_CSV_PATH", r".\Messer_Nomination_Week18.csv"))
    parser.add_argument("--slowmo", type=int,
                        default=int(os.getenv("HERA_SLOWMO", "0")))
    parser.add_argument("--env",
                        default=os.getenv("HERA_ENV", "dev"))
    parser.add_argument("--no-prompt", action="store_true",
                        help="Disable dever prompts (auto-PASS).")
    parser.add_argument("--start-step", type=int, default=1,
                        help="Start from a specific step (1-27).")
    parser.add_argument("--excel-log", default=os.getenv("HERA_EXCEL_LOG", ""))
    parser.add_argument("--excel-summary", default=os.getenv("HERA_EXCEL_SUMMARY", ""))
    parser.add_argument("--severity-default",
                        default=os.getenv("HERA_SEVERITY_DEFAULT", "Medium"))
    parser.add_argument("--wait-s", type=float,
                        default=float(os.getenv("HERA_WAIT_S", "0.2")))
    parser.add_argument("--wait-m", type=float,
                        default=float(os.getenv("HERA_WAIT_M", "0.6")))
    parser.add_argument("--wait-l", type=float,
                        default=float(os.getenv("HERA_WAIT_L", "1.2")))
    parser.add_argument("--initial-wait", type=int, default=30,
                        help="Initial pause (seconds) for manual login.")

    args = parser.parse_args()
    run(nominations_url=args.nominations_url,
        profiles_url=args.profiles_url,
        occupancy_url=args.occupancy_url,
        costs_url=args.costs_url,
        maintenance_url=args.maintenance_url,
        csv_path=args.csv,
        yearly_nominations_url=args.yearly_nominations_url,
        yearly_csv_path=args.yearly_csv,
        slowmo=args.slowmo,
        env=args.env,
        prompt=(not args.no_prompt),
        excel_log=(args.excel_log if args.excel_log else None),
        excel_summary=(args.excel_summary if args.excel_summary else None),
        severity_default=args.severity_default,
        start_step=args.start_step,
        wait_s=args.wait_s,
        wait_m=args.wait_m,
        wait_l=args.wait_l,
        initial_wait=args.initial_wait)
