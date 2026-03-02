import os
import re
import csv
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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
# Test catalog (pour Summary)
# =========================
TESTS = {
    "STEP2_IMPORT": {
        "id": "T-02",
        "desc": "Import nominations CSV",
        "expected": "Modal accepte le fichier, upload OK et fermeture du modal; la semaine cible s’affiche."
    },
    "STEP3_PROFILES_BEFORE": {
        "id": "T-03A",
        "desc": "Profiles baseline (avant actions)",
        "expected": "La vue Profiles se charge et affiche les données initiales."
    },
    "STEP3_OCCUPANCY_BASELINE": {
        "id": "T-03B",
        "desc": "Occupancy baseline (avant actions)",
        "expected": "La vue Occupancy se charge et montre l'occupation initiale des baies."
    },
    "STEP5_CONFIRM_OVERVIEW": {
        "id": "T-05A",
        "desc": "Confirmer les slots bleus",
        "expected": "Les slots confirmés changent de couleur; l’UI de la semaine reflète l’état confirmé."
    },
    # NOUVEAU : vue Nominations après confirmations
    "STEP5_NOM_AFTER_CONFIRM": {
        "id": "T-05B",
        "desc": "Nominations après confirmations (week)",
        "expected": "La page Nominations (semaine cible) montre les slots confirmés colorés (pas bleus)."
    },
    "STEP5_PROFILES_AFTER_CONFIRM": {
        "id": "T-05C",
        "desc": "Profiles après confirmations",
        "expected": "Le graph Profiles reflète les changements après confirmations."
    },
    "STEP5_OCCUPANCY_AFTER_CONFIRM": {
        "id": "T-05D",
        "desc": "Occupancy après confirmations",
        "expected": "La vue Occupancy montre l'évolution de l'occupation après les confirmations."
    },
    "STEP6_REJECT_OVERVIEW": {
        "id": "T-06A",
        "desc": "Rejeter les slots bleus",
        "expected": "Les slots rejetés redeviennent gris; l’UI de la semaine reflète l’état rejeté."
    },
    # NOUVEAU : vue Nominations après rejets
    "STEP6_NOM_AFTER_REJECT": {
        "id": "T-06B",
        "desc": "Nominations après rejets (week)",
        "expected": "La page Nominations (semaine cible) montre les slots rejetés redevenus gris."
    },
    "STEP6_PROFILES_AFTER_REJECT": {
        "id": "T-06C",
        "desc": "Profiles après rejets",
        "expected": "Le graph Profiles reflète les changements après rejets."
    },
    "STEP6_OCCUPANCY_AFTER_REJECT": {
        "id": "T-06D",
        "desc": "Occupancy après rejets",
        "expected": "La vue Occupancy montre l'évolution de l'occupation après les rejets."
    },
    "STEP7_CONFIRM_WEEK": {
        "id": "T-07A",
        "desc": "Confirm Week button",
        "expected": "Le bouton Confirm Week fonctionne."
    },
    "STEP7_PROFILES_FINAL": {
        "id": "T-07B",
        "desc": "Final Profiles check",
        "expected": "Le graph Profiles final est correct."
    },
    "STEP7_OCCUPANCY_FINAL": {
        "id": "T-07C",
        "desc": "Final Occupancy check",
        "expected": "La vue Occupancy finale est correcte."
    },
    "STEP8_OCCUPANCY": {
        "id": "T-08",
        "desc": "Check Occupancy Graph",
        "expected": "La vue Occupancy se charge correctement pour la semaine cible."
    }
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
                body = response.json()
                
                method = response.request.method
                
                # Determine "short name" for the URL
                url = response.url
                # Simplify URL to a slug
                parsed = re.sub(r"^https?://[^/]+/", "", url)
                
                # Filter out noise
                if "app-config" in parsed or "assets" in parsed:
                     return

                slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", parsed)[:60] # shorter slug
                
                # Prepare data wrapper
                data_wrapper = {
                    "url": url,
                    "method": method,
                    "status": response.status,
                    "headers": dict(response.headers),
                    "data": body
                }
                
                # Deduplication: Ignore if identical to last capture (prevents double-fetch noise)
                # We compare 'url', 'method', 'data'.
                sig = (url, method, json.dumps(body, sort_keys=True))
                if hasattr(self, '_last_sig') and self._last_sig == sig:
                    return
                self._last_sig = sig

                self._request_counter += 1
                
                # Deterministic filename: {Index:03d}_{Method}_{Slug}.json
                idx = self._request_counter
                filename = f"{idx:03d}_{method}_{slug}.json"
                
                save_dir = self.root / self.current_step / "data"
                save_dir.mkdir(parents=True, exist_ok=True)
                file_path = save_dir / filename
                
                with file_path.open("w", encoding="utf-8") as f:
                    json.dump(data_wrapper, f, indent=2)

        except Exception as e:
            # We don't want to crash the test, but knowing why it failed is good
            # print(f"[DataCapturer] Error capturing: {e}")
            pass

    def start_capturing(self, page):
        page.on("response", self._on_response)

    def stop_capturing(self, page):
        page.remove_listener("response", self._on_response)

# =========================
# Baseline Manager (Regression Test)
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
            print("[Baseline] ❌ REGRESSION DETECTED:")
            for d in diffs:
                print(f"  - {d}")
            return {"status": "FAIL", "diffs": diffs}
        else:
            print("[Baseline] ✅ SUCCESS: Verified against baseline (No discrepancies).")
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
    HEADERS = ["TestID", "Description", "Expected", "Success", "Note", "RunID", "Step"]
    def __init__(self, excel_path: Path | None):
        self.excel_path = excel_path

    def append(self, test_id: str, description: str, expected: str,
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
                test_id, description, expected,
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
# Test Logger (CSV + Excel Détaillé & Formatté)
# =========================
class TestLogger:
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
        self.run_csv = artifacts_dir / run_id / "test_log.csv"
        self.global_csv = global_csv
        self.severity_default = severity_default
        
        # Si aucun chemin Excel n'est fourni, on en crée un par défaut dans le dossier du run
        # pour garantir que l'utilisateur ait toujours son rapport Excel.
        if excel_log:
            self.excel_path = Path(excel_log)
        else:
            self.excel_path = artifacts_dir / run_id / "test_report.xlsx"

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
            if ws.title != "Test Log":
                # Si c'est un nouveau workbook, on renomme la sheet par défaut
                ws.title = "Test Log"
            
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
                ws = wb["Test Log"] if "Test Log" in wb.sheetnames else wb.active
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
            ws = wb["Test Log"] if "Test Log" in wb.sheetnames else wb.active
            
            max_row = ws.max_row
            max_col = ws.max_column
            if max_row < 2: return 

            # Nettoyer les tables existantes pour éviter duplication
            if ws.tables:
                del ws.tables[next(iter(ws.tables))]

            last_col_letter = get_column_letter(max_col)
            ref = f"A1:{last_col_letter}{max_row}"
            
            # Créer l'objet Table
            tab = Table(displayName="TestLogTable", ref=ref)
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
        # Navigation logic with year wrap-around (shortest path)
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
        h = locator.element_handle(timeout=1200)
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
# Prompts testeur (peu intrusifs)
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
def add_nomination_ui(page, offtaker_label: str, shots: Shot, tlog: TestLogger, prompt_enabled: bool, waiter: Waiter):
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
# Étapes 5 & 6 — agir sur les slots bleus
# =========================
def _snapshot_blue_slots(page, waiter: Waiter):
    waiter.ui_quiet(page)
    blue = page.locator("div.slot.status-new")
    total = blue.count()
    seen = {}
    for i in range(total):
        try:
            el = blue.nth(i)
            bb = el.bounding_box(timeout=1500)
            if not bb:
                continue
            key = (round(bb["y"], 1), round(bb["x"], 1))
            if key not in seen:
                seen[key] = bb
        except Exception:
            continue
    return [seen[k] for k in sorted(seen.keys(), key=lambda t: (t[0], t[1]))]

def _act_on_blue_slots(page, shots: Shot, tlog: TestLogger, max_n: int, action: str, prompt_enabled: bool, waiter: Waiter):
    assert action in {"confirm","reject"}
    step = "step5_confirm" if action == "confirm" else "step6_reject"
    bbs = _snapshot_blue_slots(page, waiter)
    initial_n = len(bbs)

    done = 0
    for idx, bb in enumerate(bbs, start=1):
        if done >= max_n:
            break

        slot_folder = shots.slotdir(step, idx)
        page.mouse.move(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2)
        page.mouse.down(); page.mouse.up()
        waiter.s()

        try:
            modal = get_open_dialog(page, waiter)
        except PlaywrightTimeout:
            continue

        shot_modal = shots.save_to(page, slot_folder, "01_modal_open")
        shots.save_to(page, slot_folder, "02_before_click")

        feature = "Confirm blue slot" if action == "confirm" else "Reject blue slot"
        result, notes = ask_feedback(prompt_enabled, "Step 5" if action=="confirm" else "Step 6", f"{feature} #{idx}")

        try:
            if action == "confirm":
                try:
                    btn = modal.get_by_role("button", name=re.compile("^Confirm$", re.I))
                    _safe_click(page, btn, waiter, "Confirm")
                except Exception:
                    _safe_click(page, modal.locator("button.button.button-confirm"), waiter, "Confirm")
            else:
                try:
                    btn = modal.get_by_role("button", name=re.compile("^Reject$", re.I))
                    _safe_click(page, btn, waiter, "Reject")
                except Exception:
                    _safe_click(page, modal.locator("button.button.button-reject"), waiter, "Reject")

            shot_click = shots.save_to(page, slot_folder, "03_after_click")
            try:
                modal.wait_for(state="detached", timeout=10_000)
            finally:
                waiter.ui_quiet(page)
                shot_after = shots.save_to(page, slot_folder, "04_after_close")

            tlog.log("Step 5" if action=="confirm" else "Step 6", feature, result, notes=notes,
                     shot_modal=shot_modal, shot_action=shot_click, shot_after=shot_after,
                     shot_folder=str(slot_folder))
        except Exception as e:
            shot_err = shots.save_to(page, slot_folder, "03_error_click")
            tlog.log("Step 5" if action=="confirm" else "Step 6", feature, "FAIL", notes=notes, error=str(e),
                     shot_modal=shot_modal, shot_action=shot_err, shot_after="", shot_folder=str(slot_folder))

        done += 1
        waiter.s()

    # Capture overview plein écran après toutes les actions de l'étape
    overview = capture_week_overview(page, shots, step, "zz_week_overview", waiter)
    shots.save(page, step, "zz_after_all")
    return {"initial": initial_n, "done": done, "overview": overview}

def confirm_blue_slots(page, shots: Shot, tlog: TestLogger, max_to_confirm: int, prompt_enabled: bool, waiter: Waiter):
    return _act_on_blue_slots(page, shots, tlog, max_to_confirm, action="confirm", prompt_enabled=prompt_enabled, waiter=waiter)

def reject_blue_slots(page, shots: Shot, tlog: TestLogger, max_to_reject: int, prompt_enabled: bool, waiter: Waiter):
    return _act_on_blue_slots(page, shots, tlog, max_to_reject, action="reject", prompt_enabled=prompt_enabled, waiter=waiter)

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

    meta = TESTS.get(meta_key)
    if meta:
        summary.append(meta["id"], meta["desc"], meta["expected"], 
                      bool(result_occ.upper() == "PASS"), notes_occ, run_id, step_label)
    return result_occ.upper() == "PASS"

# =========================
# Orchestrateur
# =========================
def run(nominations_url: str,
        profiles_url: str,
        occupancy_url: str,
        csv_path: str,
        slowmo: int = 0,
        start_step: int = 2,
        do_step4: bool = False,
        confirm_n: int = 5,
        reject_n: int = 5,
        env: str = "dev",
        prompt: bool = True,
        excel_log: str | None = None,
        excel_summary: str | None = None,
        severity_default: str = "Medium",
        wait_s: float = 0.2,
        wait_m: float = 0.6,
        wait_l: float = 1.2):

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

    tlog = TestLogger(
        run_id=run_id,
        env=env,
        week=target_week,
        start_step=start_step,
        artifacts_dir=artifacts,
        global_csv=Path("test_log_all.csv"),
        excel_log=Path(excel_log) if excel_log else None,
        severity_default=severity_default
    )
    summary = SummaryWriter(Path(excel_summary) if excel_summary else None)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=slowmo)
            context = browser.new_context(viewport={"width": 1500, "height": 900})
            page = context.new_page()

            # Init Data Capturer
            capturer = DataCapturer(artifacts, run_id)
            capturer.start_capturing(page)

            # ===== ÉTAPE 2 : Import + Nominations (week ciblée)
            step2_success = None
            step2_note = ""
            if start_step <= 2:
                capturer.set_step("step2_import")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m(); waiter.ui_quiet(page)
                shot_before = shots.save(page, "step2_import", "01_nominations_landing")

                try:
                    try:
                        _safe_click(page, page.get_by_role("button", name=re.compile("^Import Nominations Request$", re.I)), waiter, "Import Nominations Request")
                    except PlaywrightTimeout:
                        _safe_click(page, page.locator("text=Import Nominations Request").first, waiter, "Import Nominations Request")

                    modal = get_open_dialog(page, waiter)
                    shot_modal = shots.save(page, "step2_import", "02_import_modal_open")

                    try:
                        modal.locator("select#offtaker").select_option(label=offtaker_label)
                    except Exception:
                        modal.locator("select[name='offtaker']").select_option(label=offtaker_label)
                    waiter.s()

                    modal.locator("input#nominationFile").set_input_files(str(csv_file))
                    waiter.s()
                    shot_ready = shots.save(page, "step2_import", "03_import_ready_before_upload")

                    result, notes = ask_feedback(prompt, "Step 2", "Import nominations — modal ready")
                    step2_note = notes

                    _safe_click(page, modal.get_by_role("button", name=re.compile("^Upload$", re.I)), waiter, "Upload")
                    shot_click = shots.save(page, "step2_import", "04_click_upload")

                    # Attendre la fermeture et la stabilisation avant de bouger
                    try:
                        modal.wait_for(state="detached", timeout=15_000)
                    except PlaywrightTimeout:
                        pass
                    waiter.ui_quiet(page)

                    if target_week is not None:
                        goto_week(page, target_week, shots, "step2_import", waiter)
                    waiter.m()
                    shot_after = shots.save(page, "step2_import", "05_after_upload_on_week")

                    tlog.log("Step 2","Import nominations", result, notes=notes,
                             shot_before=shot_before, shot_modal=shot_modal,
                             shot_action=shot_click, shot_after=shot_after,
                             shot_folder=str(shots.stepdir("step2_import")))
                    step2_success = (result.upper() == "PASS")
                except Exception as e:
                    shot_err = shots.save(page, "step2_import", "04_error_upload")
                    tlog.log("Step 2","Import nominations","FAIL", notes=notes, error=str(e),
                             shot_before=shot_before, shot_modal=shot_modal,
                             shot_action=shot_err, shot_after="",
                             shot_folder=str(shots.stepdir("step2_import")))
                    step2_success = False

            if step2_success is not None:
                meta = TESTS["STEP2_IMPORT"]
                summary.append(meta["id"], meta["desc"], meta["expected"], bool(step2_success), step2_note, run_id, "Step 2")

            # ===== ÉTAPE 3 : Baseline Profiles (avant confirmations)
            if start_step <= 3:
                capturer.set_step("step3_profiles_before")
                page.goto(profiles_url, wait_until="domcontentloaded")
                waiter.m()
                try:
                    page.wait_for_selector("app-line-graph", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                if target_week is not None:
                    goto_week(page, target_week, shots, "step3_profiles_before", waiter)
                waiter.m()
                shot_profiles = shots.save(page, "step3_profiles_before", "01_profiles_baseline")
                result, notes = ask_feedback(prompt, "Step 3", "Profiles baseline")
                tlog.log("Step 3","Profiles baseline", result, notes=notes,
                         shot_after=shot_profiles, shot_folder=str(shots.stepdir("step3_profiles_before")))
                
                # Append to summary for Profiles
                meta_p = TESTS["STEP3_PROFILES_BEFORE"]
                summary.append(meta_p["id"], meta_p["desc"], meta_p["expected"], 
                               bool(result.upper() == "PASS"), notes, run_id, "Step 3")

                # NOUVEAU : Occupancy Baseline
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter, target_week, 
                                "step3_profiles_before", "Step 3", run_id, "STEP3_OCCUPANCY_BASELINE", prompt)

            # ===== ÉTAPE 4 : (optionnelle) Ajout via UI
            if do_step4:
                capturer.set_step("step4_add_ui")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step4_add_ui", waiter)
                add_nomination_ui(page, offtaker_label, shots, tlog, prompt_enabled=prompt, waiter=waiter)

            # ===== ÉTAPE 5 : Confirmer N slots bleus
            step5_success = None
            step5_note = ""
            if start_step <= 5:
                capturer.set_step("step5_confirm")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step5_confirm", waiter)

                # Confirmer
                conf = confirm_blue_slots(page, shots, tlog, max_to_confirm=confirm_n, prompt_enabled=prompt, waiter=waiter)
                # Overview nomin. plein écran après confirm
                overview5 = capture_week_overview(page, shots, "step5_confirm", "zz_week_overview_after_confirm", waiter)

                # >>> NOUVEAU INTERSTEP : Nominations after confirm (week)
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step5_confirm", waiter)
                waiter.m()
                shot_nom_after_conf = capture_week_overview(page, shots, "step5_confirm", "zz_nominations_after_confirm", waiter)
                result_noms5, notes_noms5 = ask_feedback(prompt, "Step 5", "Nominations after confirm (week)")
                tlog.log("Step 5","Nominations after confirm", result_noms5, notes=notes_noms5,
                         shot_after=shot_nom_after_conf, shot_folder=str(shots.stepdir("step5_confirm")))
                # Ajout au Summary
                meta5b = TESTS["STEP5_NOM_AFTER_CONFIRM"]
                summary.append(meta5b["id"], meta5b["desc"], meta5b["expected"],
                               bool(result_noms5.upper() == "PASS"), notes_noms5, run_id, "Step 5")

                # Profiles après confirm
                page.goto(profiles_url, wait_until="domcontentloaded")
                waiter.m()
                try:
                    page.wait_for_selector("app-line-graph", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                if target_week is not None:
                    goto_week(page, target_week, shots, "step5_confirm", waiter)
                waiter.l()
                shot_after_prof = shots.save(page, "step5_confirm", f"zz_profiles_after_confirm_{conf['done']}slots")
                result, notes = ask_feedback(prompt, "Step 5", "Profiles after confirm")
                tlog.log("Step 5","Profiles after confirm", result, notes=notes,
                         shot_after=shot_after_prof, shot_folder=str(shots.stepdir("step5_confirm")))

                # Append to summary for Profiles
                meta_p = TESTS["STEP5_PROFILES_AFTER_CONFIRM"]
                summary.append(meta_p["id"], meta_p["desc"], meta_p["expected"], 
                               bool(result.upper() == "PASS"), notes, run_id, "Step 5")

                # NOUVEAU : Occupancy after confirm
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter, target_week, 
                                "step5_confirm", "Step 5", run_id, "STEP5_OCCUPANCY_AFTER_CONFIRM", prompt)

                # Détermination du succès Step 5 (toutes confirmations prévues + avis profiles)
                step5_success = (conf["done"] >= min(confirm_n, conf["initial"])) and (result.upper() == "PASS")
                step5_note = notes

            if step5_success is not None:
                meta = TESTS["STEP5_CONFIRM_OVERVIEW"]
                summary.append(meta["id"], meta["desc"], meta["expected"], bool(step5_success), step5_note, run_id, "Step 5")

            # ===== ÉTAPE 6 : Rejeter N slots bleus
            step6_success = None
            step6_note = ""
            if start_step <= 6:
                capturer.set_step("step6_reject")
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step6_reject", waiter)

                rej = reject_blue_slots(page, shots, tlog, max_to_reject=reject_n, prompt_enabled=prompt, waiter=waiter)
                # Overview nomin. plein écran après reject
                overview6 = capture_week_overview(page, shots, "step6_reject", "zz_week_overview_after_reject", waiter)

                # >>> NOUVEAU INTERSTEP : Nominations after reject (week)
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                if target_week is not None:
                    goto_week(page, target_week, shots, "step6_reject", waiter)
                waiter.m()
                shot_nom_after_rej = capture_week_overview(page, shots, "step6_reject", "zz_nominations_after_reject", waiter)
                result_noms6, notes_noms6 = ask_feedback(prompt, "Step 6", "Nominations after reject (week)")
                tlog.log("Step 6","Nominations after reject", result_noms6, notes=notes_noms6,
                         shot_after=shot_nom_after_rej, shot_folder=str(shots.stepdir("step6_reject")))
                # Ajout au Summary
                meta6b = TESTS["STEP6_NOM_AFTER_REJECT"]
                summary.append(meta6b["id"], meta6b["desc"], meta6b["expected"],
                               bool(result_noms6.upper() == "PASS"), notes_noms6, run_id, "Step 6")

                # Profiles après rejets
                page.goto(profiles_url, wait_until="domcontentloaded")
                waiter.m()
                try:
                    page.wait_for_selector("app-line-graph", timeout=15_000)
                except PlaywrightTimeout:
                    pass
                if target_week is not None:
                    goto_week(page, target_week, shots, "step6_reject", waiter)
                waiter.l()
                shot_after_prof = shots.save(page, "step6_reject", f"zz_profiles_after_reject_{rej['done']}slots")
                result, notes = ask_feedback(prompt, "Step 6", "Profiles after reject")
                tlog.log("Step 6","Profiles after reject", result, notes=notes,
                         shot_after=shot_after_prof, shot_folder=str(shots.stepdir("step6_reject")))

                # Append to summary for Profiles
                meta_p = TESTS["STEP6_PROFILES_AFTER_REJECT"]
                summary.append(meta_p["id"], meta_p["desc"], meta_p["expected"], 
                               bool(result.upper() == "PASS"), notes, run_id, "Step 6")

                # NOUVEAU : Occupancy after reject
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter, target_week, 
                                "step6_reject", "Step 6", run_id, "STEP6_OCCUPANCY_AFTER_REJECT", prompt)

                # Détermination du succès Step 6 (rejets + avis profiles)
                step6_success = (rej["done"] >= min(reject_n, rej["initial"])) and (result.upper() == "PASS")
                step6_note = notes

            if step6_success is not None:
                meta = TESTS["STEP6_REJECT_OVERVIEW"]
                summary.append(meta["id"], meta["desc"], meta["expected"], bool(step6_success), step6_note, run_id, "Step 6")

            # ===== ÉTAPE 7 : Confirm Week & Final Check
            step7_success = None
            step7_note = ""
            if start_step <= 7:
                capturer.set_step("step7_confirm_week")
                # 1. Retour Nominations
                page.goto(nominations_url, wait_until="domcontentloaded")
                waiter.m()
                
                # FIX WHITE SCREEN: Refresh if week content not visible
                try:
                    page.wait_for_selector("div.week-view", timeout=4000)
                except:
                    print("  [Step 7] White screen detected? Refreshing page...")
                    page.reload(wait_until="domcontentloaded")
                    waiter.l()

                if target_week is not None:
                    # Toggle week fix: if week is correct but display glitchy, switch away and back
                    try:
                        # Quick check visibility
                        page.wait_for_selector("div.slot", timeout=3000)
                    except:
                        print("  [Step 7] Content missing? Toggling week to force refresh...")
                        page.locator("div.date-picker-next > button").first.click()
                        waiter.s()
                        goto_week(page, target_week, shots, "step7_fix", waiter)
                    
                    goto_week(page, target_week, shots, "step7_confirm_week", waiter)

                # 2. Click Confirm Week
                try:
                    btn = page.get_by_role("button", name=re.compile(r"Confirm Week", re.I)).first
                    if not btn.is_visible():
                         # Try alternate selector if button text differs
                         btn = page.locator("button.button-primary", has_text=re.compile("Confirm.*Week", re.I)).first
                    
                    if btn.is_visible() and not btn.is_disabled():
                         _safe_click(page, btn, waiter, "Confirm Week")
                         
                         # Handle potential confirmation modal if it appears (Defensive)
                         try:
                            modal = page.locator("dialog[open]").last
                            if modal.is_visible(timeout=2000):
                                confirm_btn = modal.get_by_role("button", name=re.compile("^Confirm$|^Yes$", re.I))
                                _safe_click(page, confirm_btn, waiter, "Confirm Modal")
                         except:
                            pass
                         
                         shots.save(page, "step7_confirm_week", "01_after_confirm_week_click")
                         result_s7, notes_s7 = ask_feedback(prompt, "Step 7", "Clicked Confirm Week")
                    else:
                         print("  [Step 7] Button 'Confirm Week' not found or disabled.")
                         shots.save(page, "step7_confirm_week", "01_button_missing")
                         result_s7, notes_s7 = "SKIP", "Button not found/disabled"

                except Exception as e:
                    print(f"  [Step 7] Error clicking Confirm Week: {e}")
                    result_s7, notes_s7 = "FAIL", str(e)

                # 3. Final Profiles Check
                page.goto(profiles_url, wait_until="domcontentloaded")
                waiter.l() # Long wait for graph
                if target_week is not None:
                    goto_week(page, target_week, shots, "step7_final_check", waiter)
                
                shot_final = shots.save(page, "step7_final_check", "02_final_profiles_check", full_page=True)
                
                result_final, notes_final = ask_feedback(prompt, "Step 7", "Final Profiles Graph Check")
                tlog.log("Step 7", "Confirm Week & Final Profiles", result_final, notes=f"{notes_s7} | {notes_final}",
                         shot_after=shot_final, shot_folder=str(shots.stepdir("step7_final_check")))

                # Append to summary for Final Profiles
                meta_p = TESTS["STEP7_PROFILES_FINAL"]
                summary.append(meta_p["id"], meta_p["desc"], meta_p["expected"], 
                               bool(result_final.upper() == "PASS"), notes_final, run_id, "Step 7")

                # NOUVEAU : Final Occupancy Check
                check_occupancy(page, occupancy_url, shots, tlog, summary, waiter, target_week, 
                                "step7_final_check", "Step 7", run_id, "STEP7_OCCUPANCY_FINAL", prompt)

                step7_success = (result_final.upper() == "PASS")
                step7_note = notes_final

                if step7_success:
                    meta = TESTS["STEP7_CONFIRM_WEEK"]
                    summary.append(meta["id"], meta["desc"], meta["expected"], True, step7_note, run_id, "Step 7")
            
                if step7_success:
                    meta = TESTS["STEP7_CONFIRM_WEEK"]
                    summary.append(meta["id"], meta["desc"], meta["expected"], True, step7_note, run_id, "Step 7")
            
            # ===== ÉTAPE 8 : Occupancy Check (REDUNDANT - Now integrated)
            # if start_step <= 8:
            #     ...

    finally:
        # C'est ici que la magie opère pour le formatage Excel
        print("Formatting Excel reports...")
        tlog.finalize() 
        summary.finalize()

        # >>> Baseline Check
        print("Checking against baseline...")
        bm = BaselineManager(Path("baseline"))
        res = bm.compare_and_report(artifacts / run_id)
        if res["status"] == "FAIL":
            print("\n⚠️ WARNING: Baseline mismatches found! Check logs above.")
        
# =========================
# CLI
# =========================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HERA smoke: import, baseline, confirm (5), reject (6) avec captures overview + Summary Excel (+ intersteps nominations).")
    parser.add_argument("--nominations-url",
                        default=os.getenv("HERA_NOMINATIONS_URL", "https://herawebdev.azurewebsites.net/nominations/weekly"))
    parser.add_argument("--profiles-url",
                        default=os.getenv("HERA_PROFILES_URL", "https://herawebdev.azurewebsites.net/schedules/profiles"))
    parser.add_argument("--occupancy-url",
                        default=os.getenv("HERA_OCCUPANCY_URL", "https://herawebdev.azurewebsites.net/schedules/occupancy"))
    parser.add_argument("--csv",
                        default=os.getenv("HERA_CSV_PATH", r".\Messer_Nomination_Week52.csv"))
    parser.add_argument("--slowmo", type=int,
                        default=int(os.getenv("HERA_SLOWMO", "0")))
    parser.add_argument("--start-step", type=int, choices=[2,3,4,5,6,7,8],
                        default=int(os.getenv("HERA_START_STEP", "2")),
                        help="2=tout; 3=baseline; 4=ajout UI; 5=confirm; 6=reject; 7=finalize; 8=occupancy.")
    parser.add_argument("--do-step4", action="store_true", help="Exécuter aussi l'étape 4 (ajout UI).")
    parser.add_argument("--confirm-n", type=int, default=int(os.getenv("HERA_CONFIRM_N", "5")),
                        help="Nombre max de slots bleus à confirmer (étape 5).")
    parser.add_argument("--reject-n", type=int, default=int(os.getenv("HERA_REJECT_N", "5")),
                        help="Nombre max de slots bleus à rejeter (étape 6).")
    parser.add_argument("--env", default=os.getenv("HERA_ENV","dev"), help="Nom d'environnement (dev/qa/prod/...).")
    parser.add_argument("--no-prompt", action="store_true", help="Désactive les questions testeur (tout marqué PASS).")
    parser.add_argument("--excel-log", default=os.getenv("HERA_EXCEL_LOG",""), help="Excel détaillé (onglet 'Test Log').")
    parser.add_argument("--excel-summary", default=os.getenv("HERA_EXCEL_SUMMARY",""), help="Excel résumé (onglet 'Summary': TestID/Description/Expected/Success/Note).")
    parser.add_argument("--severity-default", default=os.getenv("HERA_SEVERITY_DEFAULT","Medium"), help="Sévérité par défaut (Low/Medium/High).")

    # Nouveaux réglages d'attente
    parser.add_argument("--wait-s", type=float, default=float(os.getenv("HERA_WAIT_S", "0.2")),
                        help="Pause courte après une action (par défaut 0.2s).")
    parser.add_argument("--wait-m", type=float, default=float(os.getenv("HERA_WAIT_M", "0.6")),
                        help="Pause moyenne après navigation / ouverture modal (par défaut 0.6s).")
    parser.add_argument("--wait-l", type=float, default=float(os.getenv("HERA_WAIT_L", "1.2")),
                        help="Pause longue avant une lecture sensible (par défaut 1.2s).")

    args = parser.parse_args()
    run(nominations_url=args.nominations_url,
        profiles_url=args.profiles_url,
        occupancy_url=args.occupancy_url,
        csv_path=args.csv,
        slowmo=args.slowmo,
        start_step=args.start_step,
        do_step4=args.do_step4,
        confirm_n=args.confirm_n,
        reject_n=args.reject_n,
        env=args.env,
        prompt=(not args.no_prompt),
        excel_log=(args.excel_log if args.excel_log else None),
        excel_summary=(args.excel_summary if args.excel_summary else None),
        severity_default=args.severity_default,
        wait_s=args.wait_s,
        wait_m=args.wait_m,
        wait_l=args.wait_l)